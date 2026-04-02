"""
Edge Agent 설정 모듈
─────────────────────────────────────
pydantic-settings 기반 환경변수 주입.
재학습/재배포 대비: model_version, model_dir을 동적으로 주입할 수 있도록 설계.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelConfig(BaseSettings):
    """모델 관련 설정 (재배포 시 hot-reload 가능하도록 분리)"""

    model_config = SettingsConfigDict(env_prefix="MODEL_", env_file=".env", extra="ignore")

    # 모델 디렉터리 — 재배포 시 새 경로만 바꿔주면 됨
    model_dir: Path = Field(
        default=Path(__file__).resolve().parent.parent / "models",
        description="학습된 .pt 모델 및 _info.json 파일 위치",
    )
    # 모델 버전 태그 — 모니터링·로깅용
    version: str = Field(default="v1.0.0", description="현재 배포된 모델 버전")

    # LSTM 하이퍼파라미터 (info.json 로드 실패 시 fallback)
    window_size: int = Field(default=30)
    hidden_size: int = Field(default=64)
    latent_dim: int = Field(default=32)
    n_layers: int = Field(default=2)

    # 임계값 오버라이드 (None이면 info.json의 threshold_3sigma 사용)
    threshold_override_coolant: float | None = Field(default=None)
    threshold_override_hydraulics: float | None = Field(default=None)
    threshold_override_probe: float | None = Field(default=None)


class KafkaConfig(BaseSettings):
    """Kafka 설정"""

    model_config = SettingsConfigDict(env_prefix="KAFKA_", env_file=".env", extra="ignore")

    bootstrap_servers: str = Field(
        default="localhost:9092",
        description="Kafka 브로커 주소 (쉼표로 구분한 여러 개 가능)",
    )
    topic_anomaly_events: str = Field(default="anomaly-events")
    topic_sensor_telemetry: str = Field(default="sensor-telemetry")

    # Producer 설정
    acks: str = Field(default="all")
    retries: int = Field(default=3)
    linger_ms: int = Field(default=5)
    compression_type: Literal["none", "gzip", "snappy", "lz4"] = Field(default="gzip")

    # 텔레메트리 발행 간격 (초)
    telemetry_interval_sec: int = Field(default=30)


class DataConfig(BaseSettings):
    """데이터 수집/전처리 설정"""

    model_config = SettingsConfigDict(env_prefix="DATA_", env_file=".env", extra="ignore")

    # 전처리 메타 (scaler_info.pkl, meta.json 위치)
    processed_data_dir: Path = Field(
        default=Path(__file__).resolve().parent.parent.parent.parent
        / "anomaly_detection"
        / "processed_data_v2",
        description="scaler_info.pkl, meta.json 위치",
    )
    resample_interval: float = Field(default=1.0, description="데이터 수집 간격 (초)")

    # 슬라이딩 윈도우
    window_size: int = Field(default=30)
    stride: int = Field(default=3)

    # CSV replay 모드 (실제 OPC-UA 없을 때 테스트용)
    replay_mode: bool = Field(default=True, description="True=CSV 파일 재생, False=OPC-UA/MQTT")
    replay_csv_dir: Path = Field(
        default=Path(__file__).resolve().parent.parent.parent.parent / "real_op",
        description="replay 모드에서 읽을 CSV 디렉터리",
    )
    replay_speed_factor: float = Field(default=10.0, description="재생 속도 배율 (10=10배속)")


class AgentConfig(BaseSettings):
    """Edge Agent 전체 설정"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    site_id: str = Field(default="site-A", description="지점 식별자")
    machine_id: str = Field(default="cnc-001", description="기계 식별자")

    # FastAPI 서버
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)

    log_level: str = Field(default="INFO")

    # 서브모듈 설정 (환경변수로 개별 override 가능)
    model: ModelConfig = Field(default_factory=ModelConfig)
    kafka: KafkaConfig = Field(default_factory=KafkaConfig)
    data: DataConfig = Field(default_factory=DataConfig)


# 싱글턴 인스턴스 (애플리케이션 전체에서 공유)
settings = AgentConfig()
