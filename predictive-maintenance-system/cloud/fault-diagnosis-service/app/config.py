"""Fault Diagnosis Service 설정"""
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Kafka
    kafka_bootstrap_servers: str = Field(default="localhost:9092")
    kafka_topic_requests: str  = Field(default="fault-diagnosis-requests")
    kafka_topic_results:  str  = Field(default="fault-diagnosis-results")
    kafka_group_id: str        = Field(default="fault-diagnosis-group")

    # 모델 경로 (CausReg / CausTR 가중치)
    model_dir: Path = Field(
        default=Path(__file__).resolve().parent.parent / "models_fd",
        description="CausReg, CausTR 모델 파일 위치",
    )
    model_version: str = Field(default="v1.0.0")

    # Expert Graph (인과 그래프 경로)
    graph_dir: Path = Field(
        default=Path(__file__).resolve().parent.parent.parent.parent.parent
        / "expert_graph",
        description="all_nodes.csv, all_edges.csv 위치",
    )

    # FastAPI
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8001)
    log_level: str = Field(default="INFO")

    # CausTR 추론 설정
    top_k_causes: int = Field(default=5, description="상위 몇 개 원인 변수 반환")


settings = Settings()
