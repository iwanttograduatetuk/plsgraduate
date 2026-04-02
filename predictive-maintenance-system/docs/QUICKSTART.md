# 빠른 시작 가이드 — WSL Ubuntu 환경

## 사전 요구사항

| 도구 | 최소 버전 | 설치 명령 |
|------|----------|----------|
| Python | 3.12+ | `sudo apt install python3.12 python3.12-venv` |
| Docker | 27+ | [Docker Desktop for Windows](https://docs.docker.com/desktop/windows/) |
| Docker Compose | 2.29+ | Docker Desktop에 포함 |
| Java (JDK) | 21 | `sudo apt install openjdk-21-jdk` |
| Gradle | 8.11+ | [Gradle 공식 설치](https://gradle.org/install/) |

---

## 1단계: 모델 파일 준비

```bash
cd /mnt/c/Users/u1041/Desktop/dataset_causRCA/predictive-maintenance-system/edge-agent
bash setup_models.sh
```

> `models/` 디렉터리에 coolant, hydraulics, probe LSTM 모델이 복사됩니다.

---

## 2단계: Edge Agent 로컬 실행 (Kafka 없이 테스트)

```bash
cd /mnt/c/Users/u1041/Desktop/dataset_causRCA/predictive-maintenance-system/edge-agent

# 가상환경 생성 및 의존성 설치
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# .env 설정 (Kafka 없이 오프라인 모드)
cp .env.example .env
# .env 에서 KAFKA_BOOTSTRAP_SERVERS 는 그대로 두면
# 연결 실패 시 자동으로 오프라인 모드로 동작

# 실행
DATA_PROCESSED_DATA_DIR=../../anomaly_detection/processed_data_v2 \
DATA_REPLAY_CSV_DIR=../../real_op \
MODEL_DIR=./models \
uvicorn app.main:app --reload --port 8000
```

**확인:**
- http://localhost:8000/health → 추론 카운트 확인
- http://localhost:8000/scores → 실시간 이상 점수 확인
- http://localhost:8000/model/status → 모델 버전/임계값 확인

---

## 3단계: 전체 스택 Docker Compose 실행

```bash
cd /mnt/c/Users/u1041/Desktop/dataset_causRCA/predictive-maintenance-system/infra

# 인프라 (Kafka, PostgreSQL, InfluxDB, Grafana) 먼저 시작
docker compose up -d zookeeper kafka kafka-init postgres influxdb grafana

# 상태 확인 (모두 healthy 될 때까지 대기)
docker compose ps

# Python 서비스 빌드 & 시작
docker compose up -d --build edge-agent telemetry-consumer anomaly-consumer fault-diagnosis-service
```

> **SpringBoot 서비스 빌드 (notification-service, monitoring-api):**
> ```bash
> # notification-service
> cd ../cloud/notification-service
> ./gradlew bootJar
> docker build -t notification-service:latest .
>
> # monitoring-api
> cd ../monitoring-api
> ./gradlew bootJar
> docker build -t monitoring-api:latest .
>
> # 서비스 시작
> cd ../../infra
> docker compose up -d notification-service monitoring-api
> ```

---

## 4단계: 대시보드 접속

| 서비스 | URL | 계정 |
|--------|-----|------|
| **Grafana** | http://localhost:3000 | admin / admin |
| **Kafka UI** | http://localhost:8888 | — |
| **InfluxDB** | http://localhost:8086 | admin / adminpassword |
| **Edge Agent API** | http://localhost:8000/docs | — |
| **Fault Diagnosis API** | http://localhost:8001/docs | — |

---

## 5단계: 이상 감지 확인 방법

Edge Agent가 `real_op/` CSV 파일을 재생하면서 LSTM 추론을 수행합니다.
이상 감지 시 다음 흐름이 자동으로 동작합니다:

```
Edge Agent (추론)
  → anomaly-events (Kafka)
  → anomaly-consumer (PostgreSQL 저장)
  → fault-diagnosis-requests (Kafka)
  → fault-diagnosis-service (CausReg + CausTR 추론)
  → fault-diagnosis-results (Kafka)
  → Grafana 대시보드 갱신 (10초 주기)
```

**로그 확인:**
```bash
docker compose logs -f edge-agent
docker compose logs -f fault-diagnosis-service
docker compose logs -f anomaly-consumer
```

---

## 모델 Hot Reload (재배포 시)

새 모델 가중치를 `edge-agent/models/` 에 복사한 후:

```bash
# 특정 서브시스템만 교체
curl -X POST "http://localhost:8000/model/reload?name=hydraulics&version=v2.0.0"

# 전체 교체
curl -X POST "http://localhost:8000/model/reload?version=v2.0.0"

# 임계값 런타임 조정 (재학습 없이)
curl -X POST "http://localhost:8000/model/threshold?subsystem=hydraulics&threshold=0.025"
```

---

## 프로젝트 구조 요약

```
predictive-maintenance-system/
├── edge-agent/             Python FastAPI — LSTM 추론 + Kafka 발행
├── cloud/
│   ├── telemetry-consumer/ Python — Kafka → InfluxDB
│   ├── anomaly-consumer/   Python — Kafka → PostgreSQL → 하위 토픽 발행
│   ├── fault-diagnosis-service/  Python FastAPI — CausReg + CausTR
│   ├── notification-service/     SpringBoot — FCM + WebSocket 알림
│   └── monitoring-api/           SpringBoot — REST API + Grafana 연동
├── infra/
│   ├── docker-compose.yml  로컬 개발용 전체 스택
│   ├── postgres/init.sql   DB 초기화
│   ├── grafana/            대시보드 & 데이터소스 프로비저닝
│   └── k8s/                Kubernetes 매니페스트 (Kafka, 모니터링, 서비스)
└── docs/
    └── QUICKSTART.md       ← 이 문서
```
