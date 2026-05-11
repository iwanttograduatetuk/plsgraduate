"""
Fault Diagnosis Service (SHAP + Causal Graph Backtracking)
──────────────────────────────────────────────────────────────────────
anomaly-events-critical / anomaly-events-low 토픽 컨슘
→ XGBoost 모델 기반 SHAP TreeExplainer로 기여 변수 추출
→ Expert Graph 역추적으로 Root Cause 도출
→ fault-diagnosis-results 토픽 발행 + PostgreSQL 저장

FastAPI 엔드포인트:
  GET  /health
  GET  /model/status
  POST /diagnose            동기 REST 진단 (테스트/디버그용)
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
from fastapi import FastAPI, HTTPException
from kafka import KafkaConsumer, KafkaProducer

from .config import settings
from .engine.graph_loader import ExpertGraph

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("fault-diagnosis-service")

# ── XGBoost에 사용되는 59개 피처 (edge-agent와 동일 순서) ─────────────────────
XGBOOST_FEATURES = [
    # Numeric (11)
    "Hyd_Pressure", "M_PowerOnDuration",
    "Prog_CuttingTime", "Prog_CycleTime", "Prog_LineNo",
    "Spd_ActPos_C", "Spd_ActPos_X", "Spd_ActPos_Z",
    "Spd_ActSpeed_C", "Spd_ActSpeed_X", "Spd_ActSpeed_Z",
    # Alarm (18)
    "CLF_A_700307", "CLT_A_700310", "F_A_700313",
    "HP_A_700304", "LP_A_700301", "LT_A_700317",
    "Hyd_A_700202", "Hyd_A_700203", "Hyd_A_700204",
    "Hyd_A_700205", "Hyd_A_700206", "Hyd_A_700207", "Hyd_A_700208",
    "MPA_A_701124", "MPA_A_701125",
    "Prog_A_701330", "SR_A_67040", "T_A_701309",
    # Binary (30)
    "CBC_Closed", "CBC_close", "CBC_isOpen", "CBC_open",
    "CLF_Filter_Ok", "CLT_Level_lt_Min",
    "ExU_On", "ExU_isOff",
    "F_Filter_Ok",
    "HP_Pump_Ok", "HP_Pump_isOff",
    "Hyd_Filter_Ok", "Hyd_IsEnabled", "Hyd_Level_Ok",
    "Hyd_Pump_Ok", "Hyd_Pump_On", "Hyd_Pump_isOff",
    "Hyd_Temp_lt_70", "Hyd_Temp_lt_80", "Hyd_Valve_P_Up",
    "LP_Pump_Ok", "LP_Pump_On",
    "LT_Level_Ok", "LT_Pump_Ok",
    "M_ErrorActive", "M_WarnActive", "M_WarnWithStacklight",
    "SL_Green", "SL_Red", "SL_Yellow",
]

CLASS_NAMES = {0: "normal", 1: "coolant", 2: "hydraulics", 3: "probe"}

# ── 전역 상태 ──────────────────────────────────────────────────────────────────
expert_graph: Optional[ExpertGraph] = None
xgb_model = None
shap_explainer = None
kafka_producer: Optional[KafkaProducer] = None
_processed_count = 0
_running = True


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")


# ── 인과그래프 역추적 ────────────────────────────────────────────────────────────

def backtrack_root_causes(
    important_features: list[str],
    graph: ExpertGraph,
    max_depth: int = 5,
) -> list[dict]:
    """
    SHAP top features를 인과그래프에서 역추적하여 root cause 도출.
    """
    if graph is None or graph.graph.number_of_nodes() == 0:
        return [{"root_cause": f, "path": [f]} for f in important_features]

    visited = set()
    root_causes = []

    def dfs(node, path, depth):
        if depth > max_depth or node in visited:
            return
        visited.add(node)
        predecessors = list(graph.graph.predecessors(node)) if node in graph.graph else []
        if not predecessors:
            root_causes.append({
                "root_cause": node,
                "path": list(path),
            })
            return
        for parent in predecessors:
            dfs(parent, path + [parent], depth + 1)

    for feat in important_features:
        dfs(feat, [feat], 0)

    # 중복 제거
    seen = set()
    unique = []
    for rc in root_causes:
        if rc["root_cause"] not in seen:
            seen.add(rc["root_cause"])
            unique.append(rc)
    return unique


# ── SHAP 기반 진단 ───────────────────────────────────────────────────────────

def _diagnose(request: dict) -> dict:
    """
    anomaly event 하나를 받아 SHAP + 인과그래프 역추적 수행.
    """
    global _processed_count

    event_id = request.get("event_id", "")
    site_id = request.get("site_id", "")
    machine_id = request.get("machine_id", "")
    subsystem = request.get("subsystem", "unknown")
    priority = request.get("priority", "critical")
    xgboost_label = request.get("xgboost_label", "unknown")
    feature_values = request.get("feature_values", {})

    top_k = settings.top_k_causes

    # feature_values에서 59개 피처 벡터 구성
    feat_vector = np.array(
        [float(feature_values.get(f, 0.0)) for f in XGBOOST_FEATURES],
        dtype=np.float32,
    ).reshape(1, -1)

    # SHAP 분석
    shap_results = []
    if shap_explainer is not None and xgb_model is not None:
        try:
            shap_values = shap_explainer.shap_values(feat_vector)

            # 해당 클래스의 SHAP values 추출
            class_idx = {"coolant": 1, "hydraulics": 2, "probe": 3}.get(
                xgboost_label, 0
            )

            if isinstance(shap_values, list):
                sample_shap = shap_values[class_idx][0]
            elif shap_values.ndim == 3:
                sample_shap = shap_values[0, :, class_idx]
            else:
                sample_shap = shap_values[0]

            # Top-K features by absolute SHAP value
            top_idx = np.argsort(np.abs(sample_shap))[::-1][:top_k]
            shap_results = [
                {
                    "variable": XGBOOST_FEATURES[i],
                    "shap_value": round(float(sample_shap[i]), 6),
                    "feature_value": round(float(feat_vector[0, i]), 4),
                    "rank": rank + 1,
                }
                for rank, i in enumerate(top_idx)
            ]
        except Exception as e:
            logger.error("SHAP 분석 오류: %s", e)
    else:
        # SHAP unavailable — feature_values 기반 단순 랭킹 (fallback)
        sorted_feats = sorted(
            feature_values.items(), key=lambda x: abs(float(x[1])), reverse=True
        )[:top_k]
        shap_results = [
            {
                "variable": f,
                "shap_value": 0.0,
                "feature_value": round(float(v), 4),
                "rank": i + 1,
            }
            for i, (f, v) in enumerate(sorted_feats)
        ]

    # 인과그래프 역추적
    top_features = [r["variable"] for r in shap_results[:5]]
    root_causes = backtrack_root_causes(top_features, expert_graph)

    # 신뢰도: top-1 SHAP의 절대값 기반
    confidence = abs(shap_results[0]["shap_value"]) if shap_results else 0.0

    _processed_count += 1

    return {
        "anomaly_event_id": event_id,
        "site_id": site_id,
        "machine_id": machine_id,
        "subsystem": subsystem,
        "priority": priority,
        "xgboost_label": xgboost_label,
        "diagnosis_timestamp": _now_iso(),
        "model": "SHAP_TreeExplainer+CausalGraph",
        "shap_top_features": shap_results,
        "root_causes": root_causes[:top_k],
        "confidence": round(confidence, 4),
    }


# ── Kafka 소비 루프 ───────────────────────────────────────────────────────────

async def _kafka_loop() -> None:
    """fault-diagnosis-requests 토픽을 소비하여 SHAP RCA 수행
    (anomaly-consumer가 anomaly_events DB 저장 완료 후 발행하는 토픽)
    """
    global _running

    topics = [settings.kafka_topic_requests]

    try:
        consumer = KafkaConsumer(
            *topics,
            bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
            group_id=settings.kafka_group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            value_deserializer=lambda b: json.loads(b.decode("utf-8")),
        )
    except Exception as e:
        logger.error("Kafka Consumer 연결 실패: %s", e)
        return

    logger.info("Kafka Consumer 시작: topics=%s", topics)

    try:
        while _running:
            records = consumer.poll(timeout_ms=500)
            for tp, msgs in records.items():
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
                            "[%s] 진단 완료: event=%s subsystem=%s xgb=%s top1=%s(shap=%.4f)",
                            result["priority"].upper(),
                            result["anomaly_event_id"][:8],
                            result["subsystem"],
                            result["xgboost_label"],
                            result["shap_top_features"][0]["variable"] if result["shap_top_features"] else "N/A",
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
    global expert_graph, xgb_model, shap_explainer, kafka_producer

    logger.info("=" * 55)
    logger.info("Fault Diagnosis Service 시작 (SHAP + CausalGraph)")
    logger.info("=" * 55)

    # 1. Expert Graph 로드
    expert_graph = ExpertGraph(settings.graph_dir)

    # 2. XGBoost 모델 + SHAP Explainer 로드
    try:
        import xgboost as xgb
        import shap

        model_path = settings.model_dir / "xgboost_fault_classifier.json"
        if model_path.exists():
            xgb_model = xgb.XGBClassifier()
            xgb_model.load_model(str(model_path))
            shap_explainer = shap.TreeExplainer(xgb_model)
            logger.info("XGBoost + SHAP TreeExplainer 로드 완료: %s", model_path)
        else:
            logger.warning("XGBoost 모델 파일 없음: %s — fallback 모드", model_path)
    except ImportError as e:
        logger.warning("xgboost/shap 미설치: %s — fallback 모드", e)

    # 3. Kafka 프로듀서
    try:
        kafka_producer = KafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
            value_serializer=_json_bytes,
            acks="all",
            retries=3,
        )
    except Exception as e:
        logger.warning("Kafka 프로듀서 초기화 실패 (오프라인 모드): %s", e)

    # 4. Kafka 소비 루프 시작
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
    description="SHAP TreeExplainer + Causal Graph Backtracking 기반 Root Cause Analysis",
    version="2.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "processed_count": _processed_count,
        "model": "SHAP_TreeExplainer+CausalGraph",
        "xgboost_loaded": xgb_model is not None,
        "shap_loaded": shap_explainer is not None,
        "graph_nodes": expert_graph.graph.number_of_nodes() if expert_graph else 0,
    }


@app.get("/model/status")
async def model_status():
    return {
        "version": settings.model_version,
        "model_type": "XGBoost + SHAP TreeExplainer",
        "xgboost_loaded": xgb_model is not None,
        "shap_loaded": shap_explainer is not None,
        "graph_nodes": expert_graph.graph.number_of_nodes() if expert_graph else 0,
        "graph_edges": expert_graph.graph.number_of_edges() if expert_graph else 0,
        "features": len(XGBOOST_FEATURES),
    }


@app.post("/diagnose")
async def diagnose_sync(request: dict):
    """REST 동기 진단 (테스트/디버그용)"""
    try:
        result = _diagnose(request)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
