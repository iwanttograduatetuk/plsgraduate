#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# K8s 전체 배포 스크립트
# 사용법: bash infra/k8s/deploy.sh [up|down]
# ─────────────────────────────────────────────────────────────────
set -e

K8S="$(cd "$(dirname "$0")" && pwd)"
CMD="${1:-up}"

apply() { kubectl apply -f "$1"; }
delete() { kubectl delete -f "$1" --ignore-not-found; }

case "$CMD" in
  up)
    echo "▶ 1/6  KEDA 설치 (이미 있으면 skip)"
    if ! kubectl get namespace keda &>/dev/null; then
      helm repo add kedacore https://kedacore.github.io/charts
      helm repo update
      helm install keda kedacore/keda --namespace keda --create-namespace
      echo "  KEDA 준비 대기 (20s)..."
      sleep 20
    else
      echo "  KEDA 이미 설치됨, skip"
    fi

    echo "▶ 2/6  네임스페이스 + ConfigMap + Secret"
    apply "$K8S/predictive-maintenance/namespace-and-configmap.yml"

    echo "▶ 3/6  Kafka"
    apply "$K8S/kafka/kafka.yml"
    echo "  Kafka 준비 대기 (30s)..."
    sleep 30

    echo "▶ 4/6  InfluxDB + Prometheus + Grafana"
    apply "$K8S/monitoring/influxdb.yml"
    apply "$K8S/monitoring/prometheus.yml"
    apply "$K8S/monitoring/grafana.yml"

    echo "▶ 5/6  애플리케이션 서비스 + KEDA ScaledObjects"
    apply "$K8S/predictive-maintenance/deployments.yml"
    apply "$K8S/predictive-maintenance/edge-agent.yml"
    apply "$K8S/predictive-maintenance/keda-scaledobjects.yml"

    echo "▶ 6/6  Ingress"
    apply "$K8S/predictive-maintenance/ingress.yml"

    echo ""
    echo "─────────────────────────────────────────"
    echo "  배포 완료. 상태 확인:"
    echo "  kubectl get pods -n predictive-maintenance"
    echo "  kubectl get pods -n monitoring"
    echo ""
    echo "  Grafana NodePort: http://<node-ip>:30300"
    echo "  /etc/hosts 에 추가: <node-ip> cnc.local"
    echo "─────────────────────────────────────────"
    ;;

  down)
    echo "▶ 전체 리소스 삭제"
    delete "$K8S/predictive-maintenance/ingress.yml"
    delete "$K8S/predictive-maintenance/keda-scaledobjects.yml"
    delete "$K8S/predictive-maintenance/edge-agent.yml"
    delete "$K8S/predictive-maintenance/deployments.yml"
    delete "$K8S/monitoring/grafana.yml"
    delete "$K8S/monitoring/prometheus.yml"
    delete "$K8S/monitoring/influxdb.yml"
    delete "$K8S/kafka/kafka.yml"
    delete "$K8S/predictive-maintenance/namespace-and-configmap.yml"
    echo "완료"
    ;;

  status)
    echo "=== predictive-maintenance ==="
    kubectl get pods,svc,hpa -n predictive-maintenance
    echo ""
    echo "=== monitoring ==="
    kubectl get pods,svc -n monitoring
    ;;

  *)
    echo "사용법: bash deploy.sh [up|down|status]"
    exit 1
    ;;
esac
