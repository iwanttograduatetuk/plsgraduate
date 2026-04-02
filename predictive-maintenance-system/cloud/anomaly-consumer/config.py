"""Anomaly Consumer 설정"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Kafka (Consumer)
    kafka_bootstrap_servers: str = Field(default="localhost:9092")
    kafka_topic_anomaly_events: str = Field(default="anomaly-events")
    kafka_topic_fault_requests: str = Field(default="fault-diagnosis-requests")
    kafka_topic_notification:   str = Field(default="notification-events")
    kafka_topic_fault_results:  str = Field(default="fault-diagnosis-results")
    kafka_group_id: str = Field(default="anomaly-consumer-group")

    # PostgreSQL (asyncpg)
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/predictive_maintenance"
    )

    # Dead Letter Queue 토픽
    kafka_dlq_topic: str = Field(default="anomaly-events-dlq")

    log_level: str = Field(default="INFO")


settings = Settings()
