"""
Edge Agent — FastAPI 진입점
────────────────────────────────────────────────────────────────
실행:
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

백그라운드에서 CNC 센서 데이터를 수집하여 LSTM 추론 후
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

from .config import settings
from .collector import create_collector
from .preprocessor import Preprocessor
from .inference.lstm_engine import ModelRegistry, infer_window
from .inference.anomaly_scorer import compute_score, MachineScore
from .producer.kafka_producer import EdgeKafkaProducer

# ── 로깅 설정 ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("edge-agent")

# ── 전역 상태 ──────────────────────────────────────────────────────────────────
registry: Optional[ModelRegistry] = None
preprocessor: Optional[Preprocessor] = None
producer: Optional[EdgeKafkaProducer] = None

# 최근 10초 평균 점수 (헬스 체크용)
_last_scores: dict = {}
_inference_count: int = 0
_anomaly_count: int = 0
_last_telemetry_time: float = 0.0


# ── Lifespan (시작/종료) ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global registry, preprocessor, producer

    logger.info("=" * 55)
    logger.info("Edge Agent 시작  site=%s  machine=%s", settings.site_id, settings.machine_id)
    logger.info("=" * 55)

    # 1. 전처리기 초기화 (scaler 로드)
    preprocessor = Preprocessor(
        processed_data_dir=settings.data.processed_data_dir,
        window_size=settings.data.window_size,
    )

    # scaler_info에서 col_min, col_range 꺼내기
    scaler_path = settings.data.processed_data_dir / "scaler_info.pkl"
    with open(scaler_path, "rb") as f:
        scaler = pickle.load(f)
    col_min   = scaler["min"].astype(np.float32)
    col_range = scaler["range"].astype(np.float32)
    col_range[col_range == 0] = 1.0

    # 2. 모델 레지스트리 초기화
    import json
    meta_path = settings.data.processed_data_dir / "meta.json"
    with open(meta_path) as f:
        meta = json.load(f)

    import torch
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("PyTorch device: %s", device)

    registry = ModelRegistry(
        model_dir=settings.model.model_dir,
        meta_info=meta["subsystem_info"],
        device=device,
    )
    registry.load_all(version=settings.model.version)

    # 3. Kafka 프로듀서
    producer = EdgeKafkaProducer(
        bootstrap_servers=settings.kafka.bootstrap_servers,
        topic_anomaly=settings.kafka.topic_anomaly_events,
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
    CNC 센서 데이터를 읽어 LSTM 추론 후:
    - 이상 시 Kafka anomaly-events 발행
    - 30초마다 Kafka sensor-telemetry 발행
    """
    global _inference_count, _anomaly_count, _last_scores, _last_telemetry_time

    _last_telemetry_time = time.monotonic()
    telemetry_interval = settings.kafka.telemetry_interval_sec

    async for row in collector.stream():
        # 전처리기에 row 주입 (이미 정규화된 배열)
        preprocessor.push_row(row)

        if not preprocessor.is_ready():
            continue

        window = preprocessor.get_window()   # (T, 89)
        _inference_count += 1

        # ── 3개 서브시스템 병렬 추론 ───────────────────────────────────────────
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

        # ── 이상 이벤트 즉시 발행 ─────────────────────────────────────────────
        for sub_score in machine_score.anomalous_subsystems:
            _anomaly_count += 1
            if producer:
                producer.send_anomaly_event(
                    site_id=settings.site_id,
                    machine_id=settings.machine_id,
                    score=sub_score,
                )

        # ── 텔레메트리 주기 발행 ──────────────────────────────────────────────
        now = time.monotonic()
        if now - _last_telemetry_time >= telemetry_interval:
            if producer:
                producer.send_telemetry(
                    site_id=settings.site_id,
                    machine_id=settings.machine_id,
                    machine_score=machine_score,
                )
            _last_telemetry_time = now


# ── FastAPI 앱 ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="CNC Edge Agent",
    description="LSTM 기반 실시간 이상 탐지 & Kafka 이벤트 발행",
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
