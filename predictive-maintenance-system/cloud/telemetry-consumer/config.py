"""Telemetry Consumer 설정"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Kafka
    kafka_bootstrap_servers: str = Field(default="localhost:9092")
    kafka_topic_sensor_telemetry: str = Field(default="sensor-telemetry")
    kafka_group_id: str = Field(default="telemetry-consumer-group")
    kafka_auto_offset_reset: str = Field(default="earliest")

    # InfluxDB 2.x
    influxdb_url: str = Field(default="http://localhost:8086")
    influxdb_token: str = Field(default="my-token")
    influxdb_org: str = Field(default="predictive-maintenance")
    influxdb_bucket: str = Field(default="sensor-scores")

    log_level: str = Field(default="INFO")


settings = Settings()
