kubectl patch scaledobject anomaly-consumer-scaledobject -n predictive-maintenance --type merge -p '{\"metadata\":{\"annotations\":{\"autoscaling.keda.sh/paused\":\"true\"}}}'
kubectl scale deployment anomaly-consumer --replicas=0 -n predictive-maintenance

do {
    Start-Sleep -Seconds 3
    $count = (kubectl get pods -n predictive-maintenance -l app=anomaly-consumer --no-headers 2>$null | Measure-Object -Line).Lines
} while ($count -gt 0)

kubectl exec -n kafka kafka-0 -- kafka-consumer-groups --bootstrap-server localhost:9092 --group anomaly-consumer-group --all-topics --reset-offsets --to-latest --execute

kubectl patch scaledobject anomaly-consumer-scaledobject -n predictive-maintenance --type merge -p '{\"metadata\":{\"annotations\":{\"autoscaling.keda.sh/paused\":\"false\"}},\"spec\":{\"minReplicaCount\":1,\"maxReplicaCount\":1}}'
kubectl scale deployment anomaly-consumer --replicas=1 -n predictive-maintenance

do {
    Start-Sleep -Seconds 3
    $ready = kubectl get pods -n predictive-maintenance -l app=anomaly-consumer --no-headers 2>$null | Select-String "Running"
} while (-not $ready)
Start-Sleep -Seconds 3

$POD = kubectl get pod -n predictive-maintenance -l app=anomaly-consumer -o jsonpath='{.items[0].metadata.name}'
kubectl cp scripts/load_test_150tps_3min.py predictive-maintenance/${POD}:/tmp/load_test.py -n predictive-maintenance
kubectl exec -it -n predictive-maintenance $POD -- python3 /tmp/load_test.py
