# CNC CNC 예지보전 데모 시작 스크립트 (원스톱 자동화)
# 실행: powershell -ExecutionPolicy Bypass -File start-demo.ps1
#
# Flags:
#   -SkipBuild     : 이미지 빌드 생략 (이미 push 된 경우)
#   -ForceRedeploy : 클러스터 있어도 K8s 리소스 재배포

param(
    [switch]$SkipBuild,
    [switch]$ForceRedeploy
)

$ErrorActionPreference = "Continue"
$ROOT = $PSScriptRoot

$PROJECT = "cnc-predictive"
$ZONE    = "asia-northeast3-a"
$CLUSTER = "cnc-predictive"

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  CNC 예지보전 데모 -- 자동 시작" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

# Helper: 포트 점유 프로세스 종료
function Kill-Port {
    param([int]$port)
    $lines = netstat -ano 2>$null
    foreach ($line in $lines) {
        if ($line -match ":$port\s") {
            $parts = $line.Trim() -split '\s+'
            $p = $parts[-1]
            if ($p -match '^\d+$' -and $p -ne '0') {
                try { Stop-Process -Id ([int]$p) -Force -ErrorAction SilentlyContinue } catch {}
                Write-Host "  port $port PID $p terminated" -ForegroundColor DarkGray
            }
        }
    }
}

# Helper: External IP 대기
function Wait-ExternalIP {
    param([string]$svc, [string]$ns, [int]$maxWait)
    $elapsed = 0
    $ip = ""
    while ($elapsed -lt $maxWait) {
        $ip = kubectl get svc $svc -n $ns -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>$null
        if ($ip -and $ip -ne "") { return $ip }
        Write-Host "  $svc IP 대기... ($elapsed s)" -ForegroundColor DarkGray
        Start-Sleep -Seconds 10
        $elapsed += 10
    }
    return ""
}

# Helper: Pod Ready 대기
function Wait-Pods {
    param([string]$ns, [string]$label, [int]$timeoutSec)
    Write-Host "  [$ns] Pod 준비 대기..." -ForegroundColor DarkGray
    if ($label -ne "") {
        kubectl wait pod -n $ns -l $label --for=condition=Ready --timeout="${timeoutSec}s" 2>$null | Out-Null
    } else {
        kubectl wait pod -n $ns --all --for=condition=Ready --timeout="${timeoutSec}s" 2>$null | Out-Null
    }
}

# =============================================================================
# STEP 0 -- 기존 프로세스 정리
# =============================================================================
Write-Host "[0/5] 기존 프로세스 정리..." -ForegroundColor Yellow
Kill-Port -port 8000
Kill-Port -port 9093
Start-Sleep -Seconds 1

# =============================================================================
# STEP 1 -- GKE 클러스터 상태 확인 및 자동 배포
# =============================================================================
Write-Host ""
Write-Host "[1/5] GKE 클러스터 상태 확인..." -ForegroundColor Yellow

$account = gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>$null
if (-not $account) {
    Write-Host "  gcloud 인증 필요 -- 브라우저 인증 시작..." -ForegroundColor Red
    gcloud auth login
    $account = gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>$null
}
Write-Host "  gcloud 계정: $account" -ForegroundColor Green

$currentProject = gcloud config get-value project 2>$null
if ($currentProject -ne $PROJECT) {
    gcloud config set project $PROJECT 2>$null
}

$clusterList = gcloud container clusters list --zone $ZONE --format="value(name)" 2>$null
$clusterExists = $clusterList -contains $CLUSTER

if (-not $clusterExists) {
    Write-Host "  클러스터 없음 -- resume-cluster.ps1 실행..." -ForegroundColor Yellow
    $resumeFile = Join-Path $ROOT "resume-cluster.ps1"
    $resumeArgs = @("-ExecutionPolicy", "Bypass", "-File", $resumeFile)
    if ($SkipBuild) { $resumeArgs += "-SkipBuild" }
    powershell @resumeArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [오류] 클러스터 배포 실패." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "  클러스터 존재 확인" -ForegroundColor Green
    gcloud container clusters get-credentials $CLUSTER --zone $ZONE 2>$null
    Write-Host "  kubectl context 설정 완료" -ForegroundColor Green

    $podLines = kubectl get pods -n predictive-maintenance --no-headers 2>$null
    $podCount = if ($podLines) { ($podLines | Measure-Object -Line).Lines } else { 0 }

    if ($ForceRedeploy -or $podCount -eq 0) {
        Write-Host "  Pod 없음 또는 -ForceRedeploy -- K8s 재배포..." -ForegroundColor Yellow
        $K8S = Join-Path $ROOT "infra\k8s"

        $kedaNs = kubectl get namespace keda --no-headers 2>$null
        if (-not $kedaNs) {
            helm repo add kedacore https://kedacore.github.io/charts 2>$null
            helm repo update 2>$null
            helm install keda kedacore/keda --namespace keda --create-namespace
            Write-Host "  KEDA 설치 완료 (20s 대기)..." -ForegroundColor Green
            Start-Sleep -Seconds 20
        }

        kubectl apply -f (Join-Path $K8S "predictive-maintenance\namespace-and-configmap.yml")
        kubectl apply -f (Join-Path $K8S "kafka\kafka.yml")
        Wait-Pods -ns "kafka" -label "app=kafka" -timeoutSec 120

        kubectl apply -f (Join-Path $K8S "monitoring\influxdb.yml")
        kubectl apply -f (Join-Path $K8S "monitoring\prometheus.yml")

        $dashDir = Join-Path $ROOT "infra\grafana\dashboards"
        kubectl delete configmap grafana-dashboards -n monitoring --ignore-not-found 2>$null
        kubectl create configmap grafana-dashboards -n monitoring `
            ("--from-file=engineering.json=" + (Join-Path $dashDir "engineering.json")) `
            ("--from-file=executive.json="   + (Join-Path $dashDir "executive.json")) `
            ("--from-file=infra.json="       + (Join-Path $dashDir "infra.json")) `
            ("--from-file=operations.json="  + (Join-Path $dashDir "operations.json"))

        kubectl apply -f (Join-Path $K8S "monitoring\grafana.yml")

        $kafkaExporter = Join-Path $K8S "monitoring\kafka-exporter.yml"
        if (Test-Path $kafkaExporter) { kubectl apply -f $kafkaExporter }

        kubectl apply -f (Join-Path $K8S "predictive-maintenance\deployments.yml")
        kubectl apply -f (Join-Path $K8S "predictive-maintenance\keda-scaledobjects.yml")

        $ingress = Join-Path $K8S "predictive-maintenance\ingress.yml"
        if (Test-Path $ingress) { kubectl apply -f $ingress }

        Wait-Pods -ns "predictive-maintenance" -label "" -timeoutSec 180
        Wait-Pods -ns "monitoring"             -label "" -timeoutSec 180
    } else {
        Write-Host "  Pod $podCount 개 확인됨" -ForegroundColor Green
    }
}

# =============================================================================
# STEP 1b -- PostgreSQL 시드 데이터 (machines / sites)
# =============================================================================
Write-Host ""
Write-Host "[1b/5] PostgreSQL 시드 데이터 확인..." -ForegroundColor Yellow

$pgPod = kubectl get pod -n predictive-maintenance -l app=postgres --no-headers 2>$null |
    Where-Object { $_ -match "Running" } |
    ForEach-Object { ($_ -split '\s+')[0] } |
    Select-Object -First 1

if ($pgPod) {
    $machineCount = kubectl exec $pgPod -n predictive-maintenance -- `
        psql -U cnc_user -d predictive_maintenance -tAc "SELECT COUNT(*) FROM machines;" 2>$null
    $machineCount = $machineCount.Trim()

    if ($machineCount -eq "0" -or $machineCount -eq "") {
        Write-Host "  machines 테이블 비어있음 -- 시드 데이터 삽입..." -ForegroundColor Yellow
        $initSql = Join-Path $ROOT "infra\postgres\init.sql"
        Get-Content $initSql -Raw | kubectl exec -i $pgPod -n predictive-maintenance -- psql -U cnc_user -d predictive_maintenance 2>$null | Out-Null
        Write-Host "  시드 데이터 삽입 완료 (sites 3개, machines 4개)" -ForegroundColor Green
    } else {
        Write-Host "  machines 테이블 확인: $machineCount 개" -ForegroundColor Green
    }
} else {
    Write-Host "  [경고] postgres Pod 없음 -- 시드 생략" -ForegroundColor DarkGray
}

# =============================================================================
# STEP 2 -- Kafka 포트포워딩
# =============================================================================
Write-Host ""
Write-Host "[2/5] Kafka 포트포워딩 (localhost:9093 -> GKE)..." -ForegroundColor Yellow

$kafkaCmd = "kubectl port-forward svc/kafka-external -n kafka 9093:9093"
Start-Process powershell -ArgumentList @("-NoExit", "-Command", $kafkaCmd) -WindowStyle Minimized

Start-Sleep -Seconds 3
Write-Host "  Kafka 포트포워딩 시작됨" -ForegroundColor Green

# =============================================================================
# STEP 3 -- External IP 감지
# =============================================================================
Write-Host ""
Write-Host "[3/5] External IP 감지 중 (최대 3분)..." -ForegroundColor Yellow

$apiIp     = Wait-ExternalIP -svc "monitoring-api" -ns "predictive-maintenance" -maxWait 180
$grafanaIp = Wait-ExternalIP -svc "grafana"        -ns "monitoring"             -maxWait 180

if ($apiIp)     { Write-Host "  monitoring-api : $apiIp"     -ForegroundColor Green }
else            { Write-Host "  [경고] monitoring-api IP 없음" -ForegroundColor Red }

if ($grafanaIp) { Write-Host "  Grafana        : $grafanaIp" -ForegroundColor Green }
else            { Write-Host "  [경고] Grafana IP 없음"       -ForegroundColor Red }

# =============================================================================
# STEP 4 -- Grafana operations.json 패치 + ConfigMap 재적용
# =============================================================================
Write-Host ""
Write-Host "[4/5] Grafana 대시보드 패치..." -ForegroundColor Yellow

if ($apiIp) {
    $dashboardPath = Join-Path $ROOT "infra\grafana\dashboards\operations.json"

    $raw = [System.IO.File]::ReadAllBytes($dashboardPath)
    if ($raw.Length -ge 3 -and $raw[0] -eq 0xEF -and $raw[1] -eq 0xBB -and $raw[2] -eq 0xBF) {
        $raw = $raw[3..($raw.Length - 1)]
    }
    $text    = [System.Text.Encoding]::UTF8.GetString($raw)
    $newUrl  = "http://" + $apiIp + ":8082"
    $patched = $text -replace 'http://[\d\.]+:8082', $newUrl
    [System.IO.File]::WriteAllText($dashboardPath, $patched, [System.Text.Encoding]::UTF8)

    Write-Host "  operations.json 패치 완료" -ForegroundColor Green

    $dashDir = Join-Path $ROOT "infra\grafana\dashboards"
    kubectl delete configmap grafana-dashboards-data -n monitoring --ignore-not-found 2>$null
    kubectl create configmap grafana-dashboards-data -n monitoring ("--from-file=" + $dashDir)
    kubectl rollout restart deployment/grafana -n monitoring
    kubectl rollout status deployment/grafana -n monitoring --timeout=60s 2>$null

    Write-Host "  Grafana ConfigMap 재적용 완료" -ForegroundColor Green
} else {
    Write-Host "  monitoring-api IP 없음 -- 패치 생략" -ForegroundColor DarkGray
}

# =============================================================================
# STEP 5 -- Edge Agent 시작
# =============================================================================
Write-Host ""
Write-Host "[5/5] Edge Agent 시작 (localhost:8000)..." -ForegroundColor Yellow

$edgeDir = Join-Path $ROOT "edge-agent"
$edgeCmd = "python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
Start-Process powershell -ArgumentList @("-NoExit", "-Command", $edgeCmd) -WorkingDirectory $edgeDir -WindowStyle Normal

Start-Sleep -Seconds 2

# =============================================================================
# 완료
# =============================================================================
Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  데모 준비 완료!" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Write-Host ""

if ($grafanaIp) {
    $u = "http://" + $grafanaIp + ":3000"
    Write-Host "  Grafana        : $u  (admin / admin)" -ForegroundColor Cyan
}
if ($apiIp) {
    $u2 = "http://" + $apiIp + ":8082/api/health"
    $u3 = "http://" + $apiIp + ":8082/control.html"
    Write-Host "  Monitoring API : $u2" -ForegroundColor Cyan
    Write-Host "  CNC 제어 패널  : $u3" -ForegroundColor Cyan
}
Write-Host "  Edge Agent     : http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host ""
Write-Host "  KEDA 확인      : kubectl get pods -n predictive-maintenance -w" -ForegroundColor White
Write-Host ""

if ($grafanaIp) {
    Write-Host "  브라우저에서 Grafana 를 엽니다..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 1
    $openUrl = "http://" + $grafanaIp + ":3000"
    Start-Process $openUrl
} elseif ($apiIp) {
    $openUrl = "http://" + $apiIp + ":8082/control.html"
    Start-Process $openUrl
}
