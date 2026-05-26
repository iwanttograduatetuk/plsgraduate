# 부하테스트 초기화 스크립트
Write-Host "[1/4] KEDA 일시정지..." -ForegroundColor Yellow
kubectl patch scaledobject anomaly-consumer-scaledobject -n predictive-maintenance --type merge -p '{\"metadata\":{\"annotations\":{\"autoscaling.keda.sh/paused\":\"true\"}}}'
kubectl scale deployment anomaly-consumer --replicas=0 -n predictive-maintenance

Write-Host "[2/4] 팟 종료 대기..." -ForegroundColor Yellow
do {
    Start-Sleep -Seconds 3
    $count = (kubectl get pods -n predictive-maintenance -l app=anomaly-consumer --no-headers 2>$null | Measure-Object -Line).Lines
} while ($count -gt 0)
Write-Host "  팟 0개 확인" -ForegroundColor Green

Write-Host "[3/4] 오프셋 리셋..." -ForegroundColor Yellow
kubectl exec -n kafka kafka-0 -- kafka-consumer-groups --bootstrap-server localhost:9092 --group anomaly-consumer-group --all-topics --reset-offsets --to-latest --execute

Write-Host "[4/4] KEDA 복구 (1/1 고정)..." -ForegroundColor Yellow
kubectl patch scaledobject anomaly-consumer-scaledobject -n predictive-maintenance --type merge -p '{\"metadata\":{\"annotations\":{\"autoscaling.keda.sh/paused\":\"false\"}},\"spec\":{\"minReplicaCount\":1,\"maxReplicaCount\":1}}'
kubectl scale deployment anomaly-consumer --replicas=1 -n predictive-maintenance

Write-Host ""
Write-Host "완료! 이제 부하테스트 실행하세요:" -ForegroundColor Green
Write-Host 'kubectl cp scripts/load_test_150tps_3min.py predictive-maintenance/$(kubectl get pod -n predictive-maintenance -l app=anomaly-consumer -o jsonpath=''{.items[0].metadata.name}''):/tmp/load_test.py -n predictive-maintenance' -ForegroundColor Cyan
Write-Host 'kubectl exec -it -n predictive-maintenance $(kubectl get pod -n predictive-maintenance -l app=anomaly-consumer -o jsonpath=''{.items[0].metadata.name}'') -- python3 /tmp/load_test.py' -ForegroundColor Cyan
Write-Host ""
Write-Host "lag 쌓이면 KEDA 해제:" -ForegroundColor Green
Write-Host 'kubectl patch scaledobject anomaly-consumer-scaledobject -n predictive-maintenance --type merge -p ''{\"spec\":{\"minReplicaCount\":1,\"maxReplicaCount\":6}}''' -ForegroundColor Cyan
