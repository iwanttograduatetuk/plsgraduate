# 다중 거점 CNC 선반 예지보전 중앙관리 시스템 — 아키텍처 설계

> **프로젝트 범위:** Anomaly Detection + Fault Diagnosis (모델 재학습·재배포 제외)
> **기반 데이터/모델:** causRCA 데이터셋, LSTM Autoencoder (coolant / hydraulics / probe), CausReg / CausTR

---

## 1. 전체 시스템 개요

```
┌──────────────────────────────────────────────────────────────────────┐
│                         CLOUD (중앙관리)                              │
│                                                                      │
│  ┌─────────┐   ┌───────────────────────────────┐   ┌─────────────┐  │
│  │  Kafka  │──▶│         Kubernetes Cluster     │──▶│   Grafana   │  │
│  │ Cluster │   │  (Consumers / SpringBoot / AI) │   │ Dashboard   │  │
│  └─────────┘   └───────────────────────────────┘   └─────────────┘  │
│       ▲                       │                                      │
└───────┼───────────────────────┼──────────────────────────────────────┘
        │ (이상 이벤트 + 텔레메트리)  │ (모바일 푸시 알림)
        │                       ▼
┌───────┴─────┐         ┌─────────────┐
│  Edge #1    │         │ 지점 관리자  │
│  공장 A     │         │ 스마트폰    │
│  (CNC 선반) │ ...     └─────────────┘
└─────────────┘
┌─────────────┐
│  Edge #N    │
│  공장 N     │
│  (CNC 선반) │
└─────────────┘
```

### 핵심 설계 원칙

| 원칙 | 설명 |
|------|------|
| **Edge AI Inference** | 3개 LSTM 모델을 Edge에서 직접 추론 → 원시 데이터 대량 전송 불필요 |
| **이벤트 기반 알림** | 이상 감지 시에만 Cloud로 이벤트 발행 → 트래픽 대폭 절감 |
| **Kafka 파티셔닝** | 지점 ID 기준 파티셔닝 → 수백 개 지점도 병렬 처리 가능 |
| **K8s HPA** | Consumer Pod 자동 스케일 → 트래픽 폭증 대응 |
| **서브시스템별 독립 진단** | coolant / hydraulics / probe 독립 탐지 → 장애 격리 용이 |

---

## 2. Edge 레이어 설계 (각 공장 지점)

### 2.1 구성 요소

```
CNC 선반 (OPC-UA / MQTT)
        │
        ▼
┌───────────────────────────────────────────────┐
│              Edge Agent (Python FastAPI)       │
│                                               │
│  ┌─────────────────────────────────────────┐  │
│  │           데이터 수집 모듈               │  │
│  │  - 89개 피처 수집 (1초 간격)            │  │
│  │  - 전처리: 정규화, NaN 처리             │  │
│  │  - 슬라이딩 윈도우 (window=30, stride=3)│  │
│  └──────────────┬──────────────────────────┘  │
│                 │                             │
│  ┌──────────────▼──────────────────────────┐  │
│  │         LSTM 추론 모듈 (PyTorch)         │  │
│  │                                         │  │
│  │  coolant_lstm.pt    (15 features, 3σ)   │  │
│  │  hydraulics_lstm.pt (19 features, 3σ)   │  │
│  │  probe_lstm.pt      (11 features, 3σ)   │  │
│  │                                         │  │
│  │  재구성 오차 → 임계값 비교 → 이상 판정  │  │
│  └──────────────┬──────────────────────────┘  │
│                 │                             │
│  ┌──────────────▼──────────────────────────┐  │
│  │         Kafka Producer 모듈              │  │
│  │  - 이상 이벤트 즉시 발행                │  │
│  │  - 집계 텔레메트리 주기 발행 (30초)     │  │
│  └─────────────────────────────────────────┘  │
└───────────────────────────────────────────────┘
```

### 2.2 Edge 디렉터리 구조

```
edge-agent/
├── app/
│   ├── main.py                 # FastAPI 앱 진입점
│   ├── collector.py            # CNC 센서 데이터 수집 (OPC-UA/MQTT)
│   ├── preprocessor.py         # 슬라이딩 윈도우, 정규화
│   ├── inference/
│   │   ├── lstm_engine.py      # 3개 모델 로드 및 추론
│   │   └── anomaly_scorer.py   # 재구성 오차 계산, 임계값 비교
│   ├── producer/
│   │   └── kafka_producer.py   # Kafka 이벤트 발행
│   └── config.py               # 설정 (지점 ID, Kafka 주소, 임계값 등)
├── models/
│   ├── coolant_lstm.pt
│   ├── coolant_lstm_info.json
│   ├── hydraulics_lstm.pt
│   ├── hydraulics_lstm_info.json
│   ├── probe_lstm.pt
│   └── probe_lstm_info.json
├── Dockerfile
└── requirements.txt
```

### 2.3 Kafka 발행 메시지 포맷

**이상 이벤트 (anomaly-events 토픽):**
```json
{
  "event_type": "ANOMALY_DETECTED",
  "site_id": "site-A",
  "machine_id": "cnc-001",
  "timestamp": "2026-04-01T10:23:45.123Z",
  "subsystem": "hydraulics",
  "reconstruction_error": 0.0421,
  "threshold_3sigma": 0.02870,
  "anomaly_score": 1.47,
  "feature_values": { "Hyd_Pressure": 0.83, "Hyd_Pump_Ok": 0.0, "..." : "..." }
}
```

**집계 텔레메트리 (sensor-telemetry 토픽):**
```json
{
  "event_type": "TELEMETRY",
  "site_id": "site-A",
  "machine_id": "cnc-001",
  "timestamp": "2026-04-01T10:23:45.000Z",
  "subsystem_scores": {
    "coolant":    { "error": 0.0031, "threshold": 0.01581, "is_anomaly": false },
    "hydraulics": { "error": 0.0421, "threshold": 0.02870, "is_anomaly": true  },
    "probe":      { "error": 0.0009, "threshold": 0.02623, "is_anomaly": false }
  },
  "machine_status": "ANOMALY"
}
```

---

## 3. Cloud 레이어 설계 (중앙관리 시스템)

### 3.1 Kafka 클러스터 구성

```
Kafka Cluster (K8s StatefulSet)
│
├── Topic: sensor-telemetry       [파티션 = max_sites × 2]
│   └── 키: "{site_id}#{machine_id}"  → 동일 기계의 메시지 순서 보장
│
├── Topic: anomaly-events         [파티션 = 24, 고가용성]
│   └── 키: "{site_id}"           → 지점별 순서 보장
│
├── Topic: fault-diagnosis-requests  [파티션 = 12]
│   └── 이상 탐지 후 원인 분석 요청
│
├── Topic: fault-diagnosis-results   [파티션 = 12]
│   └── CausReg / CausTR 결과
│
└── Topic: notification-events    [파티션 = 24]
    └── 알림 발송 요청
```

**트래픽 규모 예측 (참고):**
- 지점 50개 × CNC 5대 = 250대
- 텔레메트리: 250대 × (30초마다) = ~8 msg/s (매우 경량)
- 이상 이벤트: 산발적 발생 → 피크 기준 최대 수십 msg/s
- → Kafka 기본 구성으로 충분히 처리 가능, HPA로 확장 용이

### 3.2 Kubernetes 클러스터 구성

```
Kubernetes Cluster
│
├── Namespace: kafka
│   ├── kafka-broker (StatefulSet, 3 replicas)
│   └── zookeeper 또는 KRaft 모드
│
├── Namespace: monitoring
│   ├── prometheus (Deployment)
│   ├── grafana (Deployment)
│   └── influxdb (StatefulSet) — 시계열 DB
│
├── Namespace: predictive-maintenance
│   │
│   ├── [Deployment] telemetry-consumer        ← sensor-telemetry 소비
│   │   └── InfluxDB에 시계열 저장
│   │
│   ├── [Deployment] anomaly-consumer          ← anomaly-events 소비
│   │   ├── PostgreSQL에 이벤트 저장
│   │   └── notification-events 토픽 발행
│   │
│   ├── [Deployment] fault-diagnosis-service   ← fault-diagnosis-requests 소비
│   │   ├── CausReg / CausTR 모델 서빙
│   │   └── 결과를 fault-diagnosis-results 발행
│   │
│   ├── [Deployment] notification-service      ← SpringBoot
│   │   ├── notification-events 소비
│   │   ├── FCM / APNs 모바일 푸시 발송
│   │   └── WebSocket 실시간 중앙관리자 알림
│   │
│   ├── [Deployment] monitoring-api-service    ← SpringBoot REST API
│   │   ├── Grafana 커스텀 데이터소스
│   │   └── 지점별/기계별 상태 조회 API
│   │
│   ├── [StatefulSet] postgresql               ← 이벤트, 장비, 사용자 DB
│   └── [StatefulSet] influxdb                 ← 센서 시계열 데이터
│
└── Namespace: ingress
    └── nginx-ingress-controller
```

### 3.3 서비스별 상세 설계

#### 3.3.1 anomaly-consumer (Python)

```
Kafka Consumer (anomaly-events)
        │
        ├─ PostgreSQL 저장 (anomaly_events 테이블)
        │     site_id, machine_id, subsystem, timestamp,
        │     reconstruction_error, anomaly_score, feature_snapshot
        │
        ├─ fault-diagnosis-requests 발행
        │     (Fault Diagnosis 파이프라인 트리거)
        │
        └─ notification-events 발행
              (알림 서비스 트리거)
```

#### 3.3.2 fault-diagnosis-service (Python + FastAPI)

```
Kafka Consumer (fault-diagnosis-requests)
        │
        ▼
┌─────────────────────────────────────┐
│  Fault Diagnosis 엔진               │
│                                     │
│  ┌──────────┐    ┌──────────────┐   │
│  │ CausReg  │    │    CausTR    │   │
│  │ (인과    │    │ (Transformer │   │
│  │  회귀)   │    │  기반 RCA)   │   │
│  └──────────┘    └──────────────┘   │
│         │               │           │
│         └───────┬───────┘           │
│                 │                   │
│         원인 변수 랭킹 + 진단        │
└─────────────────┼───────────────────┘
                  │
        fault-diagnosis-results 발행
```

**진단 결과 메시지 포맷:**
```json
{
  "site_id": "site-A",
  "machine_id": "cnc-001",
  "anomaly_event_id": "evt-20260401-001",
  "subsystem": "hydraulics",
  "diagnosis_timestamp": "2026-04-01T10:23:47.500Z",
  "model": "CausTR",
  "root_causes": [
    { "variable": "Hyd_Pressure",  "score": 0.87, "rank": 1 },
    { "variable": "Hyd_Pump_Ok",   "score": 0.71, "rank": 2 },
    { "variable": "Hyd_Filter_Ok", "score": 0.43, "rank": 3 }
  ],
  "confidence": 0.84
}
```

#### 3.3.3 notification-service (SpringBoot)

**담당 기능:**
- `notification-events` 토픽 Kafka Consumer
- **지점 관리자 모바일 알림**: FCM(Android) / APNs(iOS) 푸시 발송
  - 알림 내용: "공장 A CNC-001: 유압 서브시스템 이상 감지 (이상 점수: 1.47)"
- **중앙관리자 WebSocket**: 실시간 이상 이벤트 스트림
- **알림 이력 관리**: PostgreSQL 저장 + 조회 REST API

**알림 우선순위 분류:**
```
anomaly_score < 1.5  → INFO    (주의)
anomaly_score < 3.0  → WARNING (경고)
anomaly_score ≥ 3.0  → CRITICAL (긴급)
```

**SpringBoot 주요 컴포넌트:**
```
com.predictive.maintenance
├── kafka/
│   ├── NotificationEventConsumer.java
│   └── FaultDiagnosisResultConsumer.java
├── service/
│   ├── PushNotificationService.java   (FCM / APNs)
│   ├── WebSocketBroadcastService.java
│   └── AlertHistoryService.java
├── controller/
│   ├── AlertController.java            (REST API)
│   └── MonitoringWebSocketHandler.java
└── repository/
    ├── AlertRepository.java
    └── SiteRepository.java
```

#### 3.3.4 monitoring-api-service (SpringBoot)

**Grafana 연동 방식:** Grafana JSON Datasource Plugin 활용

**제공 API:**

| 엔드포인트 | 설명 |
|------------|------|
| `GET /api/sites` | 전체 지점 목록 + 현재 상태 |
| `GET /api/sites/{siteId}/machines` | 지점 내 기계 목록 + 상태 |
| `GET /api/machines/{machineId}/telemetry` | 시계열 텔레메트리 조회 |
| `GET /api/anomalies/recent` | 최근 이상 이벤트 조회 |
| `GET /api/anomalies/{eventId}/diagnosis` | 이상 원인 분석 결과 |
| `GET /api/metrics/summary` | 전체 지점 요약 지표 |

---

## 4. 데이터 흐름 전체 시퀀스

```
CNC 선반 센서
    │  (89 features, 1초 간격)
    ▼
Edge Agent
    │  슬라이딩 윈도우 생성 (30 steps)
    │  LSTM 3종 병렬 추론
    │
    ├──[정상]──▶ 집계 텔레메트리 (30초마다)
    │                │
    │                ▼ sensor-telemetry topic
    │           telemetry-consumer
    │                │
    │                ▼
    │           InfluxDB (시계열 저장)
    │                │
    │                ▼
    │           Grafana (실시간 그래프)
    │
    └──[이상]──▶ 이상 이벤트 즉시 발행
                     │
                     ▼ anomaly-events topic
                anomaly-consumer
                     │
                     ├──▶ PostgreSQL (이벤트 저장)
                     │
                     ├──▶ fault-diagnosis-requests topic
                     │         │
                     │         ▼
                     │    fault-diagnosis-service
                     │    (CausReg / CausTR 추론)
                     │         │
                     │         ▼ fault-diagnosis-results topic
                     │    anomaly-consumer (결과 수신)
                     │         │
                     │         ▼
                     │    PostgreSQL (진단 결과 저장)
                     │         │
                     │         ▼
                     │    Grafana (원인 변수 표시)
                     │
                     └──▶ notification-events topic
                               │
                               ▼
                          notification-service
                               │
                               ├──▶ 지점 관리자 모바일 푸시 알림
                               │       (FCM / APNs)
                               │
                               └──▶ 중앙관리자 WebSocket 실시간 알림
```

---

## 5. Grafana 대시보드 설계

### 5.1 중앙관리 메인 대시보드

```
┌─────────────────────────────────────────────────────────────┐
│             전체 지점 현황 (Overview)                        │
│                                                             │
│  정상: 47개  경고: 2개  이상: 1개  [지점 맵 / 그리드]       │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  최근 이상 이벤트 (실시간 테이블)                     │  │
│  │  시각  | 지점  | 기계  | 서브시스템 | 점수 | 원인 1위 │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 지점별 상세 대시보드

```
┌─────────────────────────────────────────────────────────────┐
│  [지점 A] CNC-001 실시간 모니터링                            │
│                                                             │
│  ┌───────────────┐ ┌───────────────┐ ┌───────────────┐     │
│  │   Coolant     │ │  Hydraulics   │ │     Probe     │     │
│  │  이상 점수    │ │  이상 점수    │ │  이상 점수    │     │
│  │  시계열 그래프│ │  시계열 그래프│ │  시계열 그래프│     │
│  │  임계선 표시  │ │  임계선 표시  │ │  임계선 표시  │     │
│  └───────────────┘ └───────────────┘ └───────────────┘     │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Fault Diagnosis 결과: 원인 변수 Bar Chart            │  │
│  │  Hyd_Pressure ████████████████ 0.87                  │  │
│  │  Hyd_Pump_Ok  ███████████      0.71                  │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 5.3 Grafana 데이터소스 구성

| 데이터소스 | 용도 |
|-----------|------|
| InfluxDB | 이상 점수 시계열, 센서 텔레메트리 |
| PostgreSQL | 이상 이벤트 이력, 진단 결과, 지점/기계 정보 |
| JSON API (monitoring-api-service) | 실시간 상태, 커스텀 집계 |

---

## 6. 대규모 트래픽 대응 전략

### 6.1 Edge 단계에서의 트래픽 절감

| 구분 | 방식 | 효과 |
|------|------|------|
| **로컬 추론** | Edge에서 LSTM 3종 직접 실행 | 89 피처 × N Hz 원시 데이터 미전송 |
| **집계 발행** | 30초 단위 집계 텔레메트리만 발행 | 1/30 트래픽 감소 |
| **이벤트 기반** | 이상 감지 시에만 즉시 이벤트 발행 | 정상 운전 중 이상 이벤트 0 |

### 6.2 Cloud 단계에서의 스케일아웃

```
Kafka 파티션 전략:
  sensor-telemetry  → 파티션 키: site_id#machine_id
  anomaly-events    → 파티션 키: site_id

K8s HPA 설정:
  telemetry-consumer:   min=2, max=10  (CPU 50% 기준)
  anomaly-consumer:     min=2, max=6   (Kafka lag 기준)
  fault-diagnosis:      min=1, max=4   (GPU 사용 시 별도 Node Pool)
  notification-service: min=2, max=8   (처리량 기준)
```

### 6.3 장애 격리 및 내결함성

- **Circuit Breaker**: 알림 서비스 장애 시 이벤트 큐잉 (Kafka retention)
- **Dead Letter Queue**: 처리 실패 메시지 별도 토픽 저장 후 재처리
- **Graceful Degradation**: Fault Diagnosis 서비스 장애 시 Anomaly 탐지 결과만 알림 발송 유지
- **Kafka 리텐션**: `anomaly-events` 7일, `sensor-telemetry` 1일

---

## 7. 데이터베이스 스키마 (주요 테이블)

### PostgreSQL

```sql
-- 지점 정보
CREATE TABLE sites (
    site_id     VARCHAR PRIMARY KEY,
    site_name   VARCHAR,
    location    VARCHAR,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 기계 정보
CREATE TABLE machines (
    machine_id  VARCHAR PRIMARY KEY,
    site_id     VARCHAR REFERENCES sites,
    machine_name VARCHAR,
    status      VARCHAR DEFAULT 'NORMAL' -- NORMAL / WARNING / CRITICAL
);

-- 관리자 정보 (알림 수신)
CREATE TABLE managers (
    manager_id  SERIAL PRIMARY KEY,
    site_id     VARCHAR REFERENCES sites,  -- NULL이면 중앙관리자
    name        VARCHAR,
    fcm_token   VARCHAR,   -- Android 푸시 토큰
    apns_token  VARCHAR    -- iOS 푸시 토큰
);

-- 이상 이벤트
CREATE TABLE anomaly_events (
    event_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id             VARCHAR,
    machine_id          VARCHAR,
    subsystem           VARCHAR,  -- coolant / hydraulics / probe
    detected_at         TIMESTAMPTZ,
    reconstruction_error FLOAT,
    anomaly_score       FLOAT,
    severity            VARCHAR,  -- INFO / WARNING / CRITICAL
    feature_snapshot    JSONB,
    is_resolved         BOOLEAN DEFAULT FALSE
);

-- Fault Diagnosis 결과
CREATE TABLE fault_diagnosis_results (
    result_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id        UUID REFERENCES anomaly_events,
    model_name      VARCHAR,  -- CausReg / CausTR
    diagnosed_at    TIMESTAMPTZ,
    root_causes     JSONB,    -- [{"variable": "...", "score": 0.87, "rank": 1}, ...]
    confidence      FLOAT
);

-- 알림 이력
CREATE TABLE notification_history (
    notif_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id    UUID REFERENCES anomaly_events,
    manager_id  INT REFERENCES managers,
    sent_at     TIMESTAMPTZ,
    channel     VARCHAR,  -- FCM / APNS / WEBSOCKET
    status      VARCHAR   -- SENT / FAILED
);
```

### InfluxDB (시계열)

```
Measurement: subsystem_scores
Tags:    site_id, machine_id, subsystem
Fields:  reconstruction_error (float), threshold (float), is_anomaly (boolean)
Time:    Unix timestamp (나노초)
```

---

## 8. 프로젝트 레포지토리 구조 (권장)

```
predictive-maintenance-system/
│
├── edge-agent/               # Edge AI 추론 에이전트 (Python)
│   ├── app/
│   ├── models/               # .pt 모델 파일
│   ├── Dockerfile
│   └── requirements.txt
│
├── cloud/
│   ├── telemetry-consumer/   # Python Kafka Consumer
│   ├── anomaly-consumer/     # Python Kafka Consumer
│   ├── fault-diagnosis-service/ # Python FastAPI + CausReg/CausTR
│   ├── notification-service/ # SpringBoot (Java)
│   └── monitoring-api/       # SpringBoot (Java)
│
├── infra/
│   ├── k8s/                  # Kubernetes 매니페스트
│   │   ├── kafka/
│   │   ├── monitoring/
│   │   └── predictive-maintenance/
│   └── grafana/              # 대시보드 JSON 설정
│
└── docs/
    ├── ARCHITECTURE.md       # 이 문서
    └── API_SPEC.md
```

---

## 9. 기술 스택 요약

| 레이어 | 기술 | 역할 |
|--------|------|------|
| **Edge** | Python 3.11, PyTorch, FastAPI | LSTM 추론, Kafka 발행 |
| **메시징** | Apache Kafka 3.x | 이벤트 스트리밍, 버퍼링 |
| **오케스트레이션** | Kubernetes (K8s) | 컨테이너 관리, 자동 스케일 |
| **AI 서빙** | Python FastAPI | CausReg / CausTR 추론 API |
| **알림** | SpringBoot 3.x, Firebase FCM | 모바일 푸시 알림 |
| **모니터링 API** | SpringBoot 3.x | Grafana 데이터소스 |
| **시계열 DB** | InfluxDB 2.x | 센서 점수 저장 |
| **관계형 DB** | PostgreSQL 15 | 이벤트, 진단, 이력 |
| **대시보드** | Grafana 10.x | 실시간 모니터링 시각화 |
| **컨테이너** | Docker | 이미지 빌드/배포 |

---

## 10. 개발 단계 로드맵

### Phase 1 — Edge Agent (우선 개발)
- [ ] LSTM 모델 로더 및 실시간 추론 엔진 구현
- [ ] 슬라이딩 윈도우 전처리기 구현
- [ ] Kafka Producer 구현
- [ ] Docker 이미지 빌드

### Phase 2 — Cloud 백엔드 기반
- [ ] Kafka 클러스터 K8s 배포
- [ ] InfluxDB / PostgreSQL K8s 배포
- [ ] telemetry-consumer, anomaly-consumer 구현

### Phase 3 — AI 서빙 및 진단
- [ ] fault-diagnosis-service (CausReg / CausTR) 구현
- [ ] Kafka 기반 요청/응답 파이프라인 연결

### Phase 4 — 알림 및 모니터링
- [ ] notification-service (SpringBoot) 구현
- [ ] FCM 연동 및 모바일 알림 테스트
- [ ] monitoring-api-service (SpringBoot) 구현
- [ ] Grafana 대시보드 구성

### Phase 5 — 통합 테스트
- [ ] 다중 Edge 시뮬레이션 (여러 지점 동시 트래픽)
- [ ] Kafka HPA 스케일 테스트
- [ ] 장애 시나리오 테스트 (서비스 다운, 네트워크 단절)
