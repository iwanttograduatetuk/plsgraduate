"""
Edge-Cloud Hybrid PdM Pipeline — FastAPI 게이트웨이
Kafka Producer 연결 버전
"""

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Any, Optional
from contextlib import asynccontextmanager
from aiokafka import AIOKafkaProducer
import logging
import json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pdm-gateway")

# ──────────────────────────────────────────────
# 설정값
# ──────────────────────────────────────────────
KAFKA_BROKER = "localhost:9092"
KAFKA_TOPIC  = "pdm-raw-data"

# ──────────────────────────────────────────────
# Kafka Producer 전역 변수
# ──────────────────────────────────────────────
producer = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 서버 시작할 때 Kafka Producer 켜기
    global producer
    logger.info("Kafka Producer 시작 중...")
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BROKER,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    await producer.start()
    logger.info(f"Kafka Producer 연결 완료! 토픽: {KAFKA_TOPIC}")
    yield
    # 서버 종료할 때 Kafka Producer 끄기
    await producer.stop()
    logger.info("Kafka Producer 종료!")

app = FastAPI(title="PdM Gateway", lifespan=lifespan)

# ──────────────────────────────────────────────
# 데이터 형식
# ──────────────────────────────────────────────
class SensorData(BaseModel):
    machine_id:  int
    state:       str
    is_fault:    bool
    fault_type:  Optional[str] = None
    fault_count: int
    timestamp:   str
    row_index:   int
    payload:     dict[str, Any]

# ──────────────────────────────────────────────
# 카운터
# ──────────────────────────────────────────────
total_received = 0
fault_summary  = {}

# ──────────────────────────────────────────────
# 엔드포인트
# ──────────────────────────────────────────────
@app.post("/ingest")
async def ingest(data: SensorData):
    global total_received
    total_received += 1

    # Kafka로 전송
    await producer.send(KAFKA_TOPIC, value=data.dict())

    # 고장 기계 추적
    if data.is_fault and data.fault_type:
        fault_summary[data.machine_id] = data.fault_type

    # 100건마다 요약 출력
    if total_received % 100 == 0:
        logger.info(
            f"✅ 누적={total_received}건 | "
            f"고장기계={len(fault_summary)}대 | "
            f"Kafka 전송 중!"
        )

    return {"status": "ok", "received": total_received}


@app.get("/")
async def root():
    return {
        "status":         "running",
        "total_received": total_received,
        "fault_machines": fault_summary,
    }