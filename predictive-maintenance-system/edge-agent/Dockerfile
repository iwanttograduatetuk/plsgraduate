# Edge Agent Dockerfile
# Python 3.12 slim — 경량 이미지

FROM python:3.12-slim

LABEL maintainer="seungu1204@gmail.com"
LABEL description="CNC 예지보전 Edge Agent — LSTM 이상 탐지"

# 시스템 패키지
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        && rm -rf /var/lib/apt/lists/*

WORKDIR /edge-agent

# 의존성 먼저 복사 (Docker 레이어 캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 코드 복사
COPY app/ ./app/
COPY models/ ./models/

# 환경변수 기본값
ENV SITE_ID=site-A \
    MACHINE_ID=cnc-001 \
    KAFKA_BOOTSTRAP_SERVERS=kafka:9092 \
    DATA_REPLAY_MODE=true \
    MODEL_VERSION=v1.0.0 \
    LOG_LEVEL=INFO

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
