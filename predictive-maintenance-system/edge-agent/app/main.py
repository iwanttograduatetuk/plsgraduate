"""
Edge Agent — FastAPI 진입점
────────────────────────────────────────────────────────────────
실행:
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

백그라운드에서 CNC 센서 데이터를 수집하여 LSTM + XGBoost 추론 후
이상 감지 시 Kafka에 이벤트를 발행합니다.

재학습/재배포 확장 포인트:
  POST /model/reload          모델 hot-reload 트리거
  GET  /model/status          현재 배포 모델 버전/메타 확인
"""

from __future__ import annotations

import asyncio
import logging
import pickle
import time
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import settings
from .collector import create_collector
from .preprocessor import Preprocessor
from .inference.lstm_engine import ModelRegistry, infer_window
from .inference.anomaly_scorer import compute_score, MachineScore
from .inference.xgboost_engine import XGBoostEngine, XGBoostPrediction, XGBOOST_FEATURES
from .producer.kafka_producer import EdgeKafkaProducer

# ── 로깅 설정 ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("edge-agent")

# ── 전역 상태 ──────────────────────────────────────────────────────────────────
registry: Optional[ModelRegistry] = None
xgb_engine: Optional[XGBoostEngine] = None
xgb_feat_indices: Optional[list] = None  # preprocessor 89개 중 XGBoost 59개 인덱스
preprocessor: Optional[Preprocessor] = None
producer: Optional[EdgeKafkaProducer] = None
col_min: Optional[np.ndarray] = None    # min-max scaler min (역정규화용)
col_range: Optional[np.ndarray] = None  # min-max scaler range (역정규화용)

# 최근 10초 평균 점수 (헬스 체크용)
_last_scores: dict = {}
_inference_count: int = 0
_anomaly_count: int = 0
_last_telemetry_time: float = 0.0

# 기계 제어 상태 (REPLAY_AUTO_START=true 환경변수로 자동 시작 가능)
_machine_paused: bool = not settings.replay_auto_start


# ── Lifespan (시작/종료) ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global registry, preprocessor, producer, xgb_engine, xgb_feat_indices, col_min, col_range

    logger.info("=" * 55)
    logger.info("Edge Agent 시작  site=%s  machine=%s", settings.site_id, settings.machine_id)
    logger.info("=" * 55)

    # 1. 전처리기 초기화 (scaler 로드)
    preprocessor = Preprocessor(
        processed_data_dir=settings.data.processed_data_dir,
        window_size=settings.data.window_size,
    )

    # scaler_info에서 col_min, col_range 꺼내기 (없으면 identity 정규화)
    import json
    scaler_path = settings.data.processed_data_dir / "scaler_info.pkl"
    if scaler_path.exists():
        with open(scaler_path, "rb") as f:
            scaler = pickle.load(f)
        col_min   = scaler["min"].astype(np.float32)
        col_range = scaler["range"].astype(np.float32)
        col_range[col_range == 0] = 1.0
    else:
        logger.warning("scaler_info.pkl 없음 — identity 정규화 사용 (데모 모드)")
        col_min   = np.zeros(89, dtype=np.float32)
        col_range = np.ones(89, dtype=np.float32)

    # 2. 모델 레지스트리 초기화
    meta_path = settings.data.processed_data_dir / "meta.json"
    if meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)
    else:
        logger.warning("meta.json 없음 — 서브시스템 피처 인덱스 자동 할당 (데모 모드)")
        # 데모 모드: CSV replay의 피처 이름을 모르므로
        # preprocessor의 feature_names가 실제 이름이면 그걸 쓰고,
        # 아니면 89개 더미 이름 사용
        demo_feature_names = preprocessor.feature_names if preprocessor else [f"feat_{i}" for i in range(89)]
        meta = {
            "feature_names": demo_feature_names,
            "subsystem_info": {
                "coolant":    {"indices": list(range(0, 15))},
                "hydraulics": {"indices": list(range(15, 34))},
                "probe":      {"indices": list(range(34, 45))},
            },
        }

    import torch
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("PyTorch device: %s", device)

    registry = ModelRegistry(
        model_dir=settings.model.model_dir,
        meta_info=meta["subsystem_info"],
        device=device,
    )
    registry.load_all(version=settings.model.version)

    # 2-1. XGBoost 엔진 초기화
    xgb_engine = XGBoostEngine(model_path=settings.model.model_dir / "xgboost_fault_classifier.json")
    xgb_engine.load()

    # 2-2. XGBoost 피처 인덱스 매핑 (preprocessor 89개 → XGBoost 59개)
    xgb_feat_indices = []
    feat_name_to_idx = {name: i for i, name in enumerate(preprocessor.feature_names)}
    for xgb_feat in XGBOOST_FEATURES:
        idx = feat_name_to_idx.get(xgb_feat, -1)
        xgb_feat_indices.append(idx)
    matched = sum(1 for i in xgb_feat_indices if i >= 0)
    logger.info("XGBoost 피처 매핑: %d/%d matched", matched, len(XGBOOST_FEATURES))

    # 3. Kafka 프로듀서
    producer = EdgeKafkaProducer(
        bootstrap_servers=settings.kafka.bootstrap_servers,
        topic_anomaly_critical=settings.kafka.topic_anomaly_critical,
        topic_anomaly_low=settings.kafka.topic_anomaly_low,
        topic_telemetry=settings.kafka.topic_sensor_telemetry,
        acks=settings.kafka.acks,
        retries=settings.kafka.retries,
        linger_ms=settings.kafka.linger_ms,
        compression_type=settings.kafka.compression_type,
    )

    # 4. 컬렉터 생성
    collector = create_collector(
        replay_mode=settings.data.replay_mode,
        replay_csv_dir=settings.data.replay_csv_dir,
        feature_names=preprocessor.feature_names,
        col_min=col_min,
        col_range=col_range,
        speed_factor=settings.data.replay_speed_factor,
    )

    # 5. 백그라운드 루프 시작
    task = asyncio.create_task(
        _inference_loop(collector, device)
    )
    logger.info("추론 루프 시작 완료")

    yield   # ← 앱 실행 중

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    if producer:
        producer.close()
    logger.info("Edge Agent 종료")


# ── 추론 루프 ──────────────────────────────────────────────────────────────────

async def _inference_loop(collector, device) -> None:
    """
    CNC 센서 데이터를 읽어 LSTM + XGBoost 추론 후:
    - LSTM 이상 시 Kafka anomaly-events-critical 발행
    - XGBoost만 이상 시 Kafka anomaly-events-low 발행
    - 30초마다 Kafka sensor-telemetry 발행
    """
    global _inference_count, _anomaly_count, _last_scores, _last_telemetry_time

    _last_telemetry_time = time.monotonic()
    telemetry_interval = settings.kafka.telemetry_interval_sec

    try:
        async for row in collector.stream():
            try:
                # STOP 명령 시 추론 일시 중단 (데이터 수집은 유지)
                if _machine_paused:
                    await asyncio.sleep(0.5)
                    continue

                # 전처리기에 row 주입 (이미 정규화된 배열)
                preprocessor.push_row(row)

                if not preprocessor.is_ready():
                    continue

                window = preprocessor.get_window()   # (T, N_FEAT)
                _inference_count += 1

                # 워밍업: 첫 N회 추론은 점수 무시 (버퍼 과도기)
                WARMUP_COUNT = 60
                if _inference_count <= WARMUP_COUNT:
                    if _inference_count == WARMUP_COUNT:
                        logger.info("워밍업 완료 (%d회) — 이상 탐지 시작", WARMUP_COUNT)
                    continue

                if _inference_count % 100 == 0:
                    logger.info("추론 #%d 진행 중...", _inference_count)

                # ── 3개 서브시스템 병렬 추론 (LSTM) ──
                scores = {}
                for name in ("coolant", "hydraulics", "probe"):
                    entry = registry.get(name)
                    if entry is None:
                        continue
                    error = infer_window(entry, window, device)
                    feat_vals = preprocessor.get_last_raw_values(
                        entry.feat_indices is not None
                        and [preprocessor.feature_names[i] for i in entry.feat_indices]
                        or []
                    )
                    thr_override = getattr(
                        settings.model,
                        f"threshold_override_{name}",
                        None,
                    )
                    scores[name] = compute_score(
                        name=name,
                        reconstruction_error=error,
                        threshold=entry.threshold_3sigma,
                        threshold_override=thr_override,
                        feature_values=feat_vals,
                    )

                if len(scores) < 3:
                    continue

                machine_score = MachineScore(
                    coolant=scores["coolant"],
                    hydraulics=scores["hydraulics"],
                    probe=scores["probe"],
                )
                _last_scores = machine_score.to_telemetry_payload()

                # ── XGBoost 추론 ──
                xgb_prediction: Optional[XGBoostPrediction] = None
                if xgb_engine is not None and xgb_engine.is_loaded and xgb_feat_indices is not None:
                    try:
                        last_row = window[-1]
                        last_row_raw = last_row * col_range + col_min
                        xgb_input = np.array(
                            [last_row_raw[i] if i >= 0 else 0.0 for i in xgb_feat_indices],
                            dtype=np.float32,
                        )
                        xgb_prediction = xgb_engine.predict(xgb_input)
                    except Exception as e:
                        logger.debug("XGBoost 추론 실패: %s", e)

                # ── XGBoost 59개 피처 스냅샷 (Cloud SHAP용) ──
                xgb_feature_snapshot = {}
                if xgb_feat_indices is not None:
                    last_row_raw = window[-1] * col_range + col_min
                    for feat_name, idx in zip(XGBOOST_FEATURES, xgb_feat_indices):
                        xgb_feature_snapshot[feat_name] = round(float(last_row_raw[idx] if idx >= 0 else 0.0), 4)

                # ── 의사결정 로직 ──
                lstm_abnormal_subs = machine_score.anomalous_subsystems
                lstm_is_abnormal = len(lstm_abnormal_subs) > 0
                xgb_is_fault = xgb_prediction is not None and xgb_prediction.is_fault

                if lstm_is_abnormal:
                    for sub_score in lstm_abnormal_subs:
                        _anomaly_count += 1
                        logger.warning(
                            "[이상감지][CRITICAL] subsystem=%s score=%.3f xgb=%s",
                            sub_score.name, sub_score.anomaly_score,
                            xgb_prediction.predicted_label if xgb_prediction else "N/A",
                        )
                        if producer:
                            producer.send_anomaly_event(
                                site_id=settings.site_id,
                                machine_id=settings.machine_id,
                                score=sub_score,
                                priority="critical",
                                xgb_prediction=xgb_prediction,
                                xgb_feature_values=xgb_feature_snapshot,
                            )
                elif xgb_is_fault:
                    _anomaly_count += 1
                    logger.warning(
                        "[이상감지][LOW] xgb=%s",
                        xgb_prediction.predicted_label if xgb_prediction else "N/A",
                    )
                    if producer:
                        producer.send_anomaly_event(
                            site_id=settings.site_id,
                            machine_id=settings.machine_id,
                            score=None,
                            priority="low",
                            xgb_prediction=xgb_prediction,
                            xgb_feature_values=xgb_feature_snapshot,
                        )

                # ── 텔레메트리 주기 발행 ──
                now = time.monotonic()
                if now - _last_telemetry_time >= telemetry_interval:
                    if producer:
                        producer.send_telemetry(
                            site_id=settings.site_id,
                            machine_id=settings.machine_id,
                            machine_score=machine_score,
                        )
                    _last_telemetry_time = now

            except Exception as e:
                logger.error("추론 루프 내부 오류: %s", e, exc_info=True)
                await asyncio.sleep(0.1)
                continue

    except Exception as e:
        logger.error("추론 루프 치명적 오류: %s", e, exc_info=True)


# ── FastAPI 앱 ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="CNC Edge Agent",
    description="LSTM + XGBoost 기반 실시간 이상 탐지 & Kafka 이벤트 발행",
    version="1.0.0",
    lifespan=lifespan,
)


# ── 헬스체크 ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status":           "ok",
        "site_id":          settings.site_id,
        "machine_id":       settings.machine_id,
        "inference_count":  _inference_count,
        "anomaly_count":    _anomaly_count,
        "last_scores":      _last_scores,
    }


# ── 모델 상태 조회 ────────────────────────────────────────────────────────────

@app.get("/model/status")
async def model_status():
    if registry is None:
        raise HTTPException(status_code=503, detail="모델 레지스트리 미초기화")
    return registry.status()


# ── 모델 Hot Reload (재배포 대비) ─────────────────────────────────────────────

@app.post("/model/reload")
async def model_reload(name: str | None = None, version: str = "latest"):
    """
    새 모델 가중치 파일을 models/ 디렉터리에 넣은 후 이 API를 호출하면
    서비스 무중단으로 모델을 교체합니다.

    - name: 특정 서브시스템만 교체 (coolant/hydraulics/probe). 생략 시 전체.
    - version: 버전 태그 (로깅/모니터링용).
    """
    if registry is None:
        raise HTTPException(status_code=503, detail="모델 레지스트리 미초기화")
    reloaded = registry.hot_reload(name=name, version=version)
    if not reloaded:
        raise HTTPException(status_code=404, detail="리로드할 모델 없음")
    return {"reloaded": reloaded, "version": version}


# ── 임계값 오버라이드 (재학습 없이 임계값 조정) ───────────────────────────────

@app.post("/model/threshold")
async def set_threshold(subsystem: str, threshold: float):
    """
    임계값을 런타임에서 조정합니다 (재학습 없이 민감도 튜닝).
    subsystem: coolant / hydraulics / probe
    """
    allowed = {"coolant", "hydraulics", "probe"}
    if subsystem not in allowed:
        raise HTTPException(status_code=400, detail=f"subsystem은 {allowed} 중 하나여야 합니다")
    attr = f"threshold_override_{subsystem}"
    setattr(settings.model, attr, threshold)
    return {"subsystem": subsystem, "new_threshold": threshold}


# ── 실시간 점수 조회 ─────────────────────────────────────────────────────────

@app.get("/scores")
async def get_scores():
    if not _last_scores:
        return {"status": "warming_up", "message": "윈도우 채우는 중..."}
    return _last_scores
