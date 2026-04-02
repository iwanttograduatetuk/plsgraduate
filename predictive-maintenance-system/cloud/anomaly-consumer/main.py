"""
Anomaly Consumer
──────────────────────────────────────────────────────────────────────
anomaly-events 토픽 소비 후:
  1. PostgreSQL anomaly_events 테이블에 저장
  2. fault-diagnosis-requests 토픽 발행 (Fault Diagnosis 트리거)
  3. notification-events 토픽 발행 (알림 서비스 트리거)
  4. fault-diagnosis-results 토픽 소비 → 진단 결과 PostgreSQL 저장

Dead Letter Queue: 처리 실패 메시지 → anomaly-events-dlq 토픽
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import AnomalyEvent, FaultDiagnosisResult
from db.session import AsyncSessionLocal, init_db

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("anomaly-consumer")

_running = True


def _signal_handler(sig, frame):
    global _running
    logger.info("종료 신호 수신")
    _running = False


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def _parse_ts(ts_str: str) -> datetime:
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


# ── PostgreSQL 저장 ────────────────────────────────────────────────────────────

async def _save_anomaly_event(msg: dict) -> AnomalyEvent:
    """anomaly-events 메시지 → anomaly_events 테이블 저장"""
    event = AnomalyEvent(
        event_id=msg.get("event_id"),
        site_id=msg["site_id"],
        machine_id=msg["machine_id"],
        subsystem=msg["subsystem"],
        detected_at=_parse_ts(msg["timestamp"]),
        reconstruction_error=msg["reconstruction_error"],
        anomaly_score=msg["anomaly_score"],
        severity=msg.get("severity", "INFO"),
        feature_snapshot=msg.get("feature_values", {}),
    )
    async with AsyncSessionLocal() as session:
        session.add(event)
        await session.commit()
        await session.refresh(event)
    return event


async def _save_diagnosis_result(msg: dict) -> None:
    """fault-diagnosis-results 메시지 → fault_diagnosis_results 테이블 저장"""
    async with AsyncSessionLocal() as session:
        result = FaultDiagnosisResult(
            event_id=UUID(msg["anomaly_event_id"]),
            model_name=msg.get("model", "unknown"),
            diagnosed_at=_parse_ts(msg.get("diagnosis_timestamp", "")),
            root_causes=msg.get("root_causes", []),
            confidence=float(msg.get("confidence", 0.0)),
        )
        session.add(result)
        await session.commit()
    logger.info("진단 결과 저장: event_id=%s model=%s", msg.get("anomaly_event_id"), msg.get("model"))


# ── Kafka 발행 ────────────────────────────────────────────────────────────────

def _publish(producer: KafkaProducer, topic: str, key: str, payload: dict) -> None:
    try:
        producer.send(topic, key=key.encode("utf-8"), value=payload)
    except KafkaError as e:
        logger.error("발행 실패 (topic=%s): %s", topic, e)


def _build_fault_request(event: AnomalyEvent, original_msg: dict) -> dict:
    return {
        "anomaly_event_id": str(event.event_id),
        "site_id":          event.site_id,
        "machine_id":       event.machine_id,
        "subsystem":        event.subsystem,
        "detected_at":      event.detected_at.isoformat(),
        "feature_snapshot": event.feature_snapshot,
        "severity":         event.severity,
    }


def _build_notification(event: AnomalyEvent) -> dict:
    return {
        "event_id":       str(event.event_id),
        "site_id":        event.site_id,
        "machine_id":     event.machine_id,
        "subsystem":      event.subsystem,
        "anomaly_score":  event.anomaly_score,
        "severity":       event.severity,
        "detected_at":    event.detected_at.isoformat(),
    }


# ── 메인 루프 ─────────────────────────────────────────────────────────────────

async def _process_anomaly_event(msg: dict, producer: KafkaProducer) -> None:
    try:
        event = await _save_anomaly_event(msg)
        logger.info(
            "이상 이벤트 저장: %s | %s/%s subsystem=%s score=%.3f",
            event.event_id, event.site_id, event.machine_id,
            event.subsystem, event.anomaly_score,
        )
        # 하위 토픽 발행
        fault_req = _build_fault_request(event, msg)
        _publish(producer, settings.kafka_topic_fault_requests, event.site_id, fault_req)

        notif = _build_notification(event)
        _publish(producer, settings.kafka_topic_notification, event.site_id, notif)

    except Exception as e:
        logger.error("이상 이벤트 처리 실패: %s — %s", msg, e)
        # Dead Letter Queue
        _publish(producer, settings.kafka_dlq_topic, "dlq", {**msg, "error": str(e)})


async def _process_fault_result(msg: dict) -> None:
    try:
        await _save_diagnosis_result(msg)
    except Exception as e:
        logger.error("진단 결과 처리 실패: %s — %s", msg, e)


async def main_async():
    await init_db()
    logger.info("Anomaly Consumer 시작")

    # Kafka 프로듀서 (downstream 토픽 발행용)
    producer = KafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
        value_serializer=_json_bytes,
        acks="all",
        retries=3,
    )

    # Consumer 1: anomaly-events
    anomaly_consumer = KafkaConsumer(
        settings.kafka_topic_anomaly_events,
        bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
        group_id=settings.kafka_group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
    )

    # Consumer 2: fault-diagnosis-results
    result_consumer = KafkaConsumer(
        settings.kafka_topic_fault_results,
        bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
        group_id=f"{settings.kafka_group_id}-results",
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
    )

    logger.info("메시지 소비 시작...")
    try:
        while _running:
            # anomaly-events 배치 처리
            a_records = anomaly_consumer.poll(timeout_ms=500)
            for _, msgs in a_records.items():
                for m in msgs:
                    await _process_anomaly_event(m.value, producer)

            # fault-diagnosis-results 배치 처리
            r_records = result_consumer.poll(timeout_ms=500)
            for _, msgs in r_records.items():
                for m in msgs:
                    await _process_fault_result(m.value)

            producer.flush()
            await asyncio.sleep(0.01)

    finally:
        anomaly_consumer.close()
        result_consumer.close()
        producer.close()
        logger.info("Anomaly Consumer 종료")


def main():
    signal.signal(signal.SIGINT,  _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
