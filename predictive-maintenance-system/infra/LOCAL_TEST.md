# 로컬 통합 테스트 가이드

## 전제 조건
- Docker Desktop 실행 중
- Java 21 (JAVA_HOME 설정)
- 볼륨 경로 확인 (`docker-compose.yml` edge-agent volumes 섹션)

## 실행

```bash
# infra/ 에서 실행
bash build-local.sh up
```

## 헬스체크 순서

### 1. 인프라 (30초 대기)
```bash
curl http://localhost:8086/ping          # InfluxDB
docker exec postgres pg_isready -U postgres
```

### 2. 애플리케이션
```bash
curl http://localhost:8000/health        # Edge Agent
curl http://localhost:8001/health        # Fault Diagnosis
curl http://localhost:8082/api/health    # Monitoring API
```

### 3. 기계 제어 흐름 테스트
```bash
# 기계 목록 조회
curl http://localhost:8082/api/sites

# STOP 명령 (monitoring-api → edge-agent)
curl -X POST http://localhost:8082/api/machines/cnc-001/command \
  -H 'Content-Type: application/json' \
  -d '{"command":"STOP","reason":"테스트 정지"}'

# edge-agent 상태 확인 (paused: true)
curl http://localhost:8000/health

# RESUME
curl -X POST http://localhost:8082/api/machines/cnc-001/command \
  -H 'Content-Type: application/json' \
  -d '{"command":"RESUME"}'
```

### 4. Kafka 이벤트 확인
- Kafka UI → http://localhost:8888
- Topics: `anomaly-events`, `sensor-telemetry`

### 5. 컨트롤 페이지 (Grafana iframe)
- http://localhost:8082/control.html

## 자주 쓰는 명령

```bash
bash build-local.sh logs monitoring-api   # 특정 서비스 로그
bash build-local.sh rebuild monitoring-api # JAR 재빌드 후 재시작
bash build-local.sh ps                    # 전체 컨테이너 상태
bash build-local.sh down                  # 전체 종료
```

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| monitoring-api 시작 실패 | DB 마이그레이션 오류 | `docker logs postgres` 확인 |
| edge-agent STOP 안됨 | EDGE_BASE_URL 미설정 | `.local.yml` EDGE_BASE_URL 확인 |
| Kafka 연결 실패 | 외부 IP 잔존 | `.local.yml` 오버라이드 적용 확인 |
| fault-diagnosis 모델 없음 | MODEL_DIR 경로 오류 | `models_fd/` 하위 파일 6개 확인 |
