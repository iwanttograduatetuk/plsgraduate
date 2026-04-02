"""
Kafka Producer 모듈
──────────────────────────────────────────────
- 이상 이벤트(anomaly-events) 즉시 발행
- 집계 텔레메트리(sensor-telemetry) 주기 발행
- kafka-python 기반 (confluent-kafka로 교체 시 이 파일만 수정)
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from kafka import KafkaProducer
from kafka.errors import KafkaError

from ..inference.anomaly_scorer import SubsystemScore, MachineScore

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _json_serializer(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


class EdgeKafkaProducer:
    """
    Edge Agent 전용 Kafka 프로듀서.
    send_anomaly_event / send_telemetry 두 메서드만 공개.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        topic_anomaly: str = "anomaly-events",
        topic_telemetry: str = "sensor-telemetry",
        acks: str = "all",
        retries: int = 3,
        linger_ms: int = 5,
        compression_type: str = "gzip",
    ):
        self._topic_anomaly   = topic_anomaly
        self._topic_telemetry = topic_telemetry
        self._connected = False

        try:
            self._producer = KafkaProducer(
                bootstrap_servers=bootstrap_servers.split(","),
                value_serializer=_json_serializer,
                acks=acks,
                retries=retries,
                linger_ms=linger_ms,
                compression_type=compression_type,
            )
            self._connected = True
            logger.info("Kafka Producer 연결 성공: %s", bootstrap_servers)
        except Exception as e:
            logger.warning("Kafka Producer 연결 실패 (오프라인 모드): %s", e)
            self._producer = None

    # ── 이상 이벤트 ────────────────────────────────────────────────────────────

    def send_anomaly_event(
        self,
        site_id: str,
        machine_id: str,
        score: SubsystemScore,
    ) -> str:
        """
        이상 감지 즉시 발행.
        반환값: event_id (UUID)
        """
        event_id = str(uuid.uuid4())
        payload = {
            "event_id":             event_id,
            "event_type":           "ANOMALY_DETECTED",
            "site_id":              site_id,
            "machine_id":           machine_id,
            "timestamp":            _now_iso(),
            "subsystem":            score.name,
            "reconstruction_error": round(score.reconstruction_error, 6),
            "threshold_3sigma":     round(score.threshold, 6),
            "anomaly_score":        round(score.anomaly_score, 4),
            "severity":             score.severity,
            "feature_values":       {k: round(v, 4) for k, v in score.feature_values.items()},
        }
        self._send(self._topic_anomaly, key=site_id, payload=payload)
        logger.warning(
            "[이상감지] site=%s machine=%s subsystem=%s score=%.3f severity=%s",
            site_id, machine_id, score.name, score.anomaly_score, score.severity,
        )
        return event_id

    # ── 집계 텔레메트리 ────────────────────────────────────────────────────────

    def send_telemetry(
        self,
        site_id: str,
        machine_id: str,
        machine_score: MachineScore,
    ) -> None:
        """30초마다 집계 상태 발행"""
        key = f"{site_id}#{machine_id}"
        payload = {
            "event_type":    "TELEMETRY",
            "site_id":       site_id,
            "machine_id":    machine_id,
            "timestamp":     _now_iso(),
            **machine_score.to_telemetry_payload(),
        }
        self._send(self._topic_telemetry, key=key, payload=payload)
        logger.debug("텔레메트리 발행: %s/%s status=%s", site_id, machine_id, machine_score.machine_status)

    # ── 내부 ──────────────────────────────────────────────────────────────────

    def _send(self, topic: str, key: str, payload: Dict) -> None:
        if not self._connected or self._producer is None:
            logger.debug("Kafka 오프라인 — 로그만 출력: %s", payload)
            return
        try:
            self._producer.send(
                topic,
                key=key.encode("utf-8"),
                value=payload,
            )
        except KafkaError as e:
            logger.error("Kafka 발행 오류 (topic=%s): %s", topic, e)

    def flush(self) -> None:
        if self._producer:
            self._producer.flush()

    def close(self) -> None:
        if self._producer:
            self._producer.close()
        logger.info("Kafka Producer 종료")
