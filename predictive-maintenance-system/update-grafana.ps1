# Grafana 대시보드 ConfigMap 업데이트 스크립트
# BOM 자동 제거 후 ConfigMap 재적용

$dashboardDir = "infra\grafana\dashboards"

Write-Host "[1/3] BOM 제거 중..." -ForegroundColor Cyan

Get-ChildItem -Path $dashboardDir -Filter "*.json" | ForEach-Object {
    $path = $_.FullName
    $bytes = [System.IO.File]::ReadAllBytes($path)

    # BOM 감지 (EF BB BF)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        $noBom = $bytes[3..($bytes.Length - 1)]
        [System.IO.File]::WriteAllBytes($path, $noBom)
        Write-Host "  BOM 제거: $($_.Name)" -ForegroundColor Yellow
    } else {
        Write-Host "  정상: $($_.Name)" -ForegroundColor Green
    }
}

Write-Host "[2/3] ConfigMap 재생성 중..." -ForegroundColor Cyan
kubectl delete configmap grafana-dashboards -n monitoring
kubectl create configmap grafana-dashboards --from-file=$dashboardDir -n monitoring

Write-Host "[3/3] Grafana 재시작 중..." -ForegroundColor Cyan
kubectl rollout restart deployment/grafana -n monitoring

Write-Host "완료! 30초 후 Grafana 새로고침하세요." -ForegroundColor Green
