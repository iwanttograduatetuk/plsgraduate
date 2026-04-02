#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Edge Agent 모델 셋업 스크립트
#
# anomaly_detection/models_lstm/ 의 학습된 .pt 파일을
# edge-agent/models/ 로 복사합니다.
#
# 사용법:
#   cd predictive-maintenance-system/edge-agent
#   bash setup_models.sh
# ─────────────────────────────────────────────────────────────────────────────

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"    # dataset_causRCA 루트
SRC_DIR="$PROJECT_ROOT/anomaly_detection/models_lstm"
DST_DIR="$SCRIPT_DIR/models"

echo "======================================"
echo " Edge Agent 모델 셋업"
echo "======================================"
echo " 소스: $SRC_DIR"
echo " 대상: $DST_DIR"
echo ""

# models 디렉토리 생성
mkdir -p "$DST_DIR"

# 모델 파일 복사
for model in coolant hydraulics probe; do
    PT_SRC="$SRC_DIR/${model}_lstm.pt"
    INFO_SRC="$SRC_DIR/${model}_lstm_info.json"

    if [ -f "$PT_SRC" ] && [ -f "$INFO_SRC" ]; then
        cp "$PT_SRC"   "$DST_DIR/"
        cp "$INFO_SRC" "$DST_DIR/"
        echo "  ✓ $model 모델 복사 완료"
    else
        echo "  ✗ $model 모델 파일 없음 (건너뜀): $PT_SRC"
    fi
done

echo ""
echo "모델 목록:"
ls -lh "$DST_DIR"

echo ""
echo "======================================"
echo " 셋업 완료!"
echo "======================================"
