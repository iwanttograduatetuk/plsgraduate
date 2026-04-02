"""
Fault Diagnosis Service
──────────────────────────────────────────────────────────────────────
fault-diagnosis-requests 토픽 → CausReg + CausTR 추론
→ fault-diagnosis-results 토픽 발행

FastAPI 엔드포인트:
  GET  /health
  GET  /model/status
  POST /model/reload        재학습 후 hot-reload
  POST /diagnose            동기 REST 진단 (테스트/디버그용)
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError

from .config import settings
from .engine.graph_loader import ExpertGraph
from .models.caustr import CausTRInferencer
from .models.causreg import CausRegInferencer

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("fault-diagnosis-service")

# ── 서브시스템별 피처 이름 (meta.json에서 로드해도 무방)
SUBSYSTEM_FEATURES = {
    "coolant":    ["CLF_A_700307","CLF_Filter_Ok","CLT_A_700310","CLT_Level_lt_Min","F_A_700313","F_Filter_Ok","HP_A_700304","HP_Pump_Ok","HP_Pump_isOff","LP_A_700301","LP_Pump_Ok","LP_Pump_On","LT_A_700317","LT_Level_Ok","LT_Pump_Ok"],
    "hydraulics": ["Hyd_A_700202","Hyd_A_700203","Hyd_A_700204","Hyd_A_700205","Hyd_A_700206","Hyd_A_700207","Hyd_A_700208","Hyd_Filter_Ok","Hyd_IsEnabled","Hyd_Level_Ok","Hyd_Pressure","Hyd_Pump_Ok","Hyd_Pump_On","Hyd_Pump_isOff","Hyd_Temp_lt_70","Hyd_Temp_lt_80","Hyd_Valve_P_Up","Lubr_On","Lubr_P_Ok"],
    "probe":      ["MPA_A_701124","MPA_A_701125","MPA_InitPos","MPA_WorkPos","MPA_toInitPos","MPA_toWorkPos","MPC_Closed","MPC_close","MPC_isOpen","MPC_open","MP_Inactive"],
}

# ── 전역 상태 ──────────────────────────────────────────────────────────────────
expert_graph: Optional[ExpertGraph]       = None
caustr_engines: dict[str, CausTRInferencer]  = {}
causreg_engines: dict[str, CausRegInferencer] = {}
kafka_producer: Optional[KafkaProducer]   = None
_processed_count = 0
_running = True


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


# ── 추론 파이프라인 ───────────────────────────────────────────────────────────

def _diagnose(request: dict) -> dict:
    """
    단일 fault-diagnosis-request 처리 → 결과 dict 반환
    CausTR + CausReg 앙상블 (평균 score)
    """
    global _processed_count
    subsystem      = request.get("subsystem", "unknown")
    event_id       = request.get("anomaly_event_id", "")
    feat_snapshot  = request.get("feature_snapshot", {})
    feat_names     = SUBSYSTEM_FEATURES.get(subsystem, list(feat_snapshot.keys()))

    top_k = settings.top_k_causes

    # CausTR
    caustr  = caustr_engines.get(subsystem)
    tr_causes = caustr.predict(feat_snapshot, feat_names, top_k) if caustr else []

    # CausReg
    causreg = causreg_engines.get(subsystem)
    reg_causes = causreg.predict(feat_snapshot, feat_names, top_k) if causreg else []

    # 앙상블: score 평균 후 재랭킹
    score_map: dict[str, list[float]] = {}
    for c in tr_causes + reg_causes:
        score_map.setdefault(c["variable"], []).append(c["score"])
    ensemble = sorted(
        [{"variable": v, "score": sum(ss) / len(ss)} for v, ss in score_map.items()],
        key=lambda x: -x["score"],
    )
    for i, c in enumerate(ensemble):
        c["rank"] = i + 1

    # 신뢰도: top-1 점수 기준 (단순화)
    confidence = float(ensemble[0]["score"]) if ensemble else 0.0

    _processed_count += 1

    return {
        "anomaly_event_id":    event_id,
        "site_id":             request.get("site_id", ""),
        "machine_id":          request.get("machine_id", ""),
        "subsystem":           subsystem,
        "diagnosis_timestamp": _now_iso(),
        "model":               "CausTR+CausReg",
        "root_causes":         ensemble[:top_k],
        "caustr_causes":       tr_causes,
        "causreg_causes":      reg_causes,
        "confidence":          round(confidence, 4),
    }


# ── Kafka 소비 루프 ───────────────────────────────────────────────────────────

async def _kafka_loop() -> None:
    global _running

    consumer = KafkaConsumer(
        settings.kafka_topic_requests,
        bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
        group_id=settings.kafka_group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
    )

    try:
        while _running:
            records = consumer.poll(timeout_ms=500)
            for _, msgs in records.items():
                for m in msgs:
                    try:
                        result = _diagnose(m.value)
                        if kafka_producer:
                            kafka_producer.send(
                                settings.kafka_topic_results,
                                key=result["site_id"].encode("utf-8"),
                                value=result,
                            )
                            kafka_producer.flush()
                        logger.info(
                            "진단 완료: %s subsystem=%s top1=%s(%.3f)",
                            result["anomaly_event_id"],
                            result["subsystem"],
                            result["root_causes"][0]["variable"] if result["root_causes"] else "N/A",
                            result["confidence"],
                        )
                    except Exception as e:
                        logger.error("진단 처리 오류: %s", e)
            await asyncio.sleep(0.01)
    finally:
        consumer.close()


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global expert_graph, caustr_engines, causreg_engines, kafka_producer

    logger.info("Fault Diagnosis Service 시작")

    # Expert Graph 로드
    expert_graph = ExpertGraph(settings.graph_dir)

    # 서브시스템별 추론 엔진 초기화
    for subsys, feat_names in SUBSYSTEM_FEATURES.items():
        caustr_engines[subsys]  = CausTRInferencer(
            n_features=len(feat_names),
            model_dir=settings.model_dir,
        )
        causreg_engines[subsys] = CausRegInferencer(
            model_dir=settings.model_dir,
        )

    # Kafka 프로듀서
    try:
        kafka_producer = KafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
            value_serializer=_json_bytes,
            acks="all",
            retries=3,
        )
    except Exception as e:
        logger.warning("Kafka 프로듀서 초기화 실패 (오프라인 모드): %s", e)

    task = asyncio.create_task(_kafka_loop())
    logger.info("Kafka 추론 루프 시작")

    yield

    global _running
    _running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    if kafka_producer:
        kafka_producer.close()


# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Fault Diagnosis Service",
    description="CausReg + CausTR 기반 Root Cause Analysis",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {
        "status":          "ok",
        "processed_count": _processed_count,
        "models":          {
            "caustr":  list(caustr_engines.keys()),
            "causreg": list(causreg_engines.keys()),
        },
    }


@app.get("/model/status")
async def model_status():
    return {
        "version": settings.model_version,
        "subsystems": list(SUBSYSTEM_FEATURES.keys()),
        "graph_nodes": expert_graph.graph.number_of_nodes() if expert_graph else 0,
        "graph_edges": expert_graph.graph.number_of_edges() if expert_graph else 0,
    }


@app.post("/model/reload")
async def model_reload(subsystem: str | None = None):
    """
    재학습 후 모델 파일을 models_fd/ 에 넣고 이 API 호출.
    서비스 무중단으로 모델 교체.
    """
    reloaded = []
    targets = [subsystem] if subsystem else list(SUBSYSTEM_FEATURES.keys())
    for s in targets:
        pt_path  = settings.model_dir / "caustr.pt"
        pkl_path = settings.model_dir / "causreg.pkl"
        if s in caustr_engines and pt_path.exists():
            caustr_engines[s].load_weights(pt_path)
            reloaded.append(f"caustr/{s}")
        if s in causreg_engines and pkl_path.exists():
            causreg_engines[s].load_weights(pkl_path)
            reloaded.append(f"causreg/{s}")
    return {"reloaded": reloaded}


@app.post("/diagnose")
async def diagnose_sync(request: dict):
    """REST 동기 진단 (테스트/디버그용)"""
    try:
        result = _diagnose(request)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
