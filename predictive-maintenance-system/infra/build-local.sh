#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# 로컬 통합 테스트 빌드 & 실행 스크립트
# 실행: bash infra/build-local.sh [up|down|logs|ps]
# ─────────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLOUD_DIR="$SCRIPT_DIR/../cloud"
COMPOSE="docker compose -f $SCRIPT_DIR/docker-compose.yml -f $SCRIPT_DIR/docker-compose.local.yml"

CMD="${1:-up}"

build_jar() {
  local name="$1"
  local dir="$CLOUD_DIR/$name"
  echo "▶ Gradle bootJar — $name"
  (cd "$dir" && ./gradlew bootJar -q --no-daemon)
  echo "  ✓ $name 빌드 완료"
}

case "$CMD" in
  up)
    echo "═══════════════════════════════════════"
    echo " CNC 예지보전 — 로컬 통합 테스트 시작"
    echo "═══════════════════════════════════════"

    # 1. Spring Boot JAR 빌드
    build_jar "monitoring-api"
    build_jar "notification-service"

    # 2. Docker Compose 실행
    echo ""
    echo "▶ Docker Compose up"
    $COMPOSE up -d --build

    # 3. 접속 정보 출력
    echo ""
    echo "─────────────────────────────────────────"
    echo "  서비스 접속 주소"
    echo "─────────────────────────────────────────"
    echo "  Kafka UI        http://localhost:8888"
    echo "  Grafana         http://localhost:3000  (admin/admin)"
    echo "  InfluxDB        http://localhost:8086"
    echo "  Edge Agent      http://localhost:8000/docs"
    echo "  Fault Diagnosis http://localhost:8001/docs"
    echo "  Notification    http://localhost:8080"
    echo "  Monitoring API  http://localhost:8082/api/health"
    echo "  Control Page    http://localhost:8082/control.html"
    echo "─────────────────────────────────────────"
    ;;

  down)
    echo "▶ Docker Compose down"
    $COMPOSE down
    ;;

  logs)
    SERVICE="${2:-}"
    $COMPOSE logs -f $SERVICE
    ;;

  ps)
    $COMPOSE ps
    ;;

  restart)
    SERVICE="${2:?'서비스 이름 필요: bash build-local.sh restart monitoring-api'}"
    echo "▶ $SERVICE 재시작"
    $COMPOSE restart "$SERVICE"
    ;;

  rebuild)
    SERVICE="${2:?'서비스 이름 필요: bash build-local.sh rebuild monitoring-api'}"
    echo "▶ $SERVICE 재빌드"
    if [[ "$SERVICE" == "monitoring-api" || "$SERVICE" == "notification-service" ]]; then
      build_jar "$SERVICE"
    fi
    $COMPOSE up -d --build "$SERVICE"
    ;;

  *)
    echo "사용법: bash build-local.sh [up|down|logs [service]|ps|restart <service>|rebuild <service>]"
    exit 1
    ;;
esac
