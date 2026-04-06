"""
Telemetry Consumer
──────────────────────────────────────────────────────────────────
sensor-telemetry 토픽 → InfluxDB 2.x 저장

메시지 포맷:
{
  "event_type": "TELEMETRY",
  "site_id": "site-A",
  "machine_id": "cnc-001",
  "timestamp": "2026-04-01T10:23:45.000Z",
  "subsystem_scores": {
    "coolant":    {"error": 0.003, "threshold": 0.016, "is_anomaly": false},
    "hydraulics": {"error": 0.042, "threshold": 0.029, "is_anomaly": true},
    "probe":      {"error": 0.001, "threshold": 0.026, "is_anomaly": false}
  },
  "machine_status": "ANOMALY"
}
"""

from __future__ import annotations

import json
import logging
import signal
import sys
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from kafka import KafkaConsumer
from kafka.errors import KafkaError

from config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("telemetry-consumer")

_running = True


def _signal_handler(sig, frame):
    global _running
    logger.info("종료 신호 수신")
    _running = False


def _parse_ts(ts_str: str) -> datetime:
    """ISO 8601 → datetime (UTC)"""
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def _build_points(msg: dict) -> list[Point]:
    """
    Kafka 메시지 → InfluxDB Point 목록.
    Measurement: subsystem_scores
    Tags: site_id, machine_id, subsystem
    Fields: reconstruction_error, threshold, is_anomaly
    """
    ts = _parse_ts(msg.get("timestamp", ""))
    site_id    = msg.get("site_id", "unknown")
    machine_id = msg.get("machine_id", "unknown")
    points = []

    for subsystem, data in msg.get("subsystem_scores", {}).items():
        p = (
            Point("subsystem_scores")
            .tag("site_id",    site_id)
            .tag("machine_id", machine_id)
            .tag("subsystem",  subsystem)
            .field("reconstruction_error", float(data.get("error", 0.0)))
            .field("threshold",            float(data.get("threshold", 0.0)))
            .field("is_anomaly",           bool(data.get("is_anomaly", False)))
            .time(ts, "ns")
        )
        points.append(p)

    # 머신 전체 상태도 별도 측정값으로 저장
    machine_point = (
        Point("machine_status")
        .tag("site_id",    site_id)
        .tag("machine_id", machine_id)
        .field("status", 1 if msg.get("machine_status") == "ANOMALY" else 0)
        .time(ts, "ns")
    )
    points.append(machine_point)
    return points


def main():
    signal.signal(signal.SIGINT,  _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info("Telemetry Consumer 시작")
    logger.info("Kafka: %s | Topic: %s", settings.kafka_bootstrap_servers, settings.kafka_topic_sensor_telemetry)
    logger.info("InfluxDB: %s | Bucket: %s", settings.influxdb_url, settings.influxdb_bucket)

    # InfluxDB 클라이언트
    influx_client = InfluxDBClient(
        url=settings.influxdb_url,
        token=settings.influxdb_token,
        org=settings.influxdb_org,
    )
    write_api = influx_client.write_api(write_options=SYNCHRONOUS)

    # Kafka Consumer
    consumer = KafkaConsumer(
        settings.kafka_topic_sensor_telemetry,
        bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
        group_id=settings.kafka_group_id,
        auto_offset_reset=settings.kafka_auto_offset_reset,
        enable_auto_commit=True,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
        key_deserializer=lambda b: b.decode("utf-8") if b else None,
    )

    logger.info("메시지 소비 시작...")
    try:
        while _running:
            records = consumer.poll(timeout_ms=1000)
            for tp, msgs in records.items():
                for msg in msgs:
                    try:
                        points = _build_points(msg.value)
                        write_api.write(
                            bucket=settings.influxdb_bucket,
                            org=settings.influxdb_org,
                            record=points,
                        )
                        logger.debug(
                            "InfluxDB 저장: %s/%s (%d points)",
                            msg.value.get("site_id"),
                            msg.value.get("machine_id"),
                            len(points),
                        )
                    except Exception as e:
                        logger.error("포인트 저장 실패: %s — %s", msg.value, e)
    finally:
        consumer.close()
        write_api.close()
        influx_client.close()
        logger.info("Telemetry Consumer 종료")


if __name__ == "__main__":
    main()
