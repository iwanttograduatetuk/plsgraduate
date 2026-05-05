# GKE Cluster Resume & Full Deploy Script
# Usage: powershell -ExecutionPolicy Bypass -File resume-cluster.ps1
#
# Flags:
#   -SkipBuild    : Skip image build (images already pushed)
#   -SkipCluster  : Skip cluster creation (cluster exists)
#   -BuildOnly    : Build images only, no deploy
#   -Teardown     : Delete cluster + leftover disks and exit

param(
    [switch]$SkipBuild,
    [switch]$SkipCluster,
    [switch]$BuildOnly,
    [switch]$Teardown
)

$ErrorActionPreference = "Continue"
$ROOT = $PSScriptRoot

# -- Config ---------------------------------------------------------------
$PROJECT      = "cnc-predictive"
$ZONE         = "asia-northeast3-a"
$CLUSTER      = "cnc-predictive"
$REGISTRY     = "asia-northeast3-docker.pkg.dev/$PROJECT/cnc-registry"
$NODE_COUNT   = 2
$MACHINE_TYPE = "e2-standard-4"

$SERVICES = @(
    @{ Name = "monitoring-api";          Path = "$ROOT\cloud\monitoring-api" }
    @{ Name = "notification-service";    Path = "$ROOT\cloud\notification-service" }
    @{ Name = "telemetry-consumer";      Path = "$ROOT\cloud\telemetry-consumer" }
    @{ Name = "anomaly-consumer";        Path = "$ROOT\cloud\anomaly-consumer" }
    @{ Name = "fault-diagnosis-service"; Path = "$ROOT\cloud\fault-diagnosis-service" }
)

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " CNC Predictive Maintenance - Cluster Resume" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

# -- Teardown mode ---------------------------------------------------------
if ($Teardown) {
    Write-Host "[TEARDOWN] Deleting cluster + leftover disks..." -ForegroundColor Red

    # Delete cluster
    $existing = gcloud container clusters list --zone $ZONE --format="value(name)" 2>$null | Where-Object { $_ -eq $CLUSTER }
    if ($existing) {
        Write-Host "  Deleting cluster '$CLUSTER'..." -ForegroundColor Yellow
        gcloud container clusters delete $CLUSTER --zone $ZONE --quiet
        Write-Host "  Cluster deleted" -ForegroundColor Green
    } else {
        Write-Host "  No cluster found" -ForegroundColor DarkGray
    }

    # Delete leftover PVC disks
    Write-Host "  Scanning leftover disks..." -ForegroundColor Yellow
    $disks = gcloud compute disks list --filter="zone:$ZONE" --format="value(name)" 2>$null
    $deleted = 0
    foreach ($disk in $disks) {
        if ($disk -match "^pvc-" -or $disk -match "^gke-") {
            Write-Host "  Deleting $disk" -ForegroundColor DarkGray
            gcloud compute disks delete $disk --zone $ZONE --quiet 2>$null
            $deleted++
        }
    }
    if ($deleted -eq 0) {
        Write-Host "  No leftover disks found" -ForegroundColor DarkGray
    } else {
        Write-Host "  $deleted disk(s) deleted" -ForegroundColor Green
    }

    Write-Host ""
    Write-Host "=== Teardown complete ===" -ForegroundColor Green
    exit 0
}

# -- 0. Pre-check ---------------------------------------------------------
Write-Host "[0/5] Pre-check..." -ForegroundColor Yellow

$account = gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>$null
if (-not $account) {
    Write-Host "  gcloud auth required. Opening browser..." -ForegroundColor Red
    gcloud auth login
}
Write-Host "  Account: $account" -ForegroundColor Green

$currentProject = gcloud config get-value project 2>$null
if ($currentProject -ne $PROJECT) {
    gcloud config set project $PROJECT
}
Write-Host "  Project: $PROJECT" -ForegroundColor Green

# -- 1. GKE Cluster -------------------------------------------------------
if (-not $SkipCluster) {
    Write-Host ""
    Write-Host "[1/5] GKE cluster check/create..." -ForegroundColor Yellow

    $existing = gcloud container clusters list --zone $ZONE --format="value(name)" 2>$null | Where-Object { $_ -eq $CLUSTER }
    if ($existing) {
        # Check if cluster is in ERROR state
        $status = gcloud container clusters describe $CLUSTER --zone $ZONE --format="value(status)" 2>$null
        if ($status -eq "ERROR") {
            Write-Host "  Cluster is in ERROR state - deleting..." -ForegroundColor Red
            gcloud container clusters delete $CLUSTER --zone $ZONE --quiet
            Write-Host "  ERROR cluster deleted" -ForegroundColor Green
            $existing = $null
        } else {
            Write-Host "  Cluster '$CLUSTER' already exists (status: $status) - skip" -ForegroundColor Green
        }
    }

    # Clean orphan disks before creating cluster
    if (-not $existing) {
        Write-Host "  Cleaning orphan disks before cluster creation..." -ForegroundColor Yellow
        $disks = gcloud compute disks list --filter="zone:$ZONE" --format="value(name)" 2>$null
        $cleaned = 0
        foreach ($disk in $disks) {
            if ($disk -match "^pvc-" -or $disk -match "^gke-") {
                Write-Host "    Deleting orphan disk: $disk" -ForegroundColor DarkGray
                gcloud compute disks delete $disk --zone $ZONE --quiet 2>$null
                $cleaned++
            }
        }
        if ($cleaned -gt 0) { Write-Host "    $cleaned orphan disk(s) deleted" -ForegroundColor Green }

        Write-Host "  Creating cluster (3-5 min)..." -ForegroundColor White
        gcloud container clusters create $CLUSTER `
            --zone $ZONE `
            --num-nodes $NODE_COUNT `
            --machine-type $MACHINE_TYPE `
            --disk-size=50 `
            --enable-autorepair `
            --enable-autoupgrade
        Write-Host "  Cluster created" -ForegroundColor Green
    }

    gcloud container clusters get-credentials $CLUSTER --zone $ZONE
    Write-Host "  kubectl context configured" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "[1/5] Cluster creation skipped (-SkipCluster)" -ForegroundColor DarkGray
}

# -- 2. Artifact Registry -------------------------------------------------
Write-Host ""
Write-Host "[2/5] Artifact Registry check..." -ForegroundColor Yellow

$repoExists = gcloud artifacts repositories list --location=asia-northeast3 --format="value(name)" 2>$null | Where-Object { $_ -eq "cnc-registry" }
if ($repoExists) {
    Write-Host "  cnc-registry exists" -ForegroundColor Green
} else {
    Write-Host "  Creating cnc-registry..." -ForegroundColor White
    gcloud artifacts repositories create cnc-registry `
        --repository-format=docker `
        --location=asia-northeast3 `
        --description="CNC Predictive Maintenance images"
    Write-Host "  cnc-registry created" -ForegroundColor Green
}

gcloud auth configure-docker asia-northeast3-docker.pkg.dev --quiet 2>$null

# -- 3. Image Build --------------------------------------------------------
if (-not $SkipBuild) {
    Write-Host ""
    Write-Host "[3/5] Building container images (Cloud Build)..." -ForegroundColor Yellow

    foreach ($svc in $SERVICES) {
        $name = $svc.Name
        $path = $svc.Path
        $tag  = "$REGISTRY/${name}:latest"

        Write-Host "  > $name ..." -ForegroundColor White
        Push-Location $path
        try {
            gcloud builds submit --tag $tag .
            Write-Host "  OK $name" -ForegroundColor Green
        } catch {
            Write-Host "  FAIL $name : $_" -ForegroundColor Red
        }
        Pop-Location
    }
} else {
    Write-Host ""
    Write-Host "[3/5] Image build skipped (-SkipBuild)" -ForegroundColor DarkGray
}

if ($BuildOnly) {
    Write-Host ""
    Write-Host "=== Build complete (-BuildOnly) ===" -ForegroundColor Green
    exit 0
}

# -- 4. K8s Deploy ---------------------------------------------------------
Write-Host ""
Write-Host "[4/5] Deploying K8s resources..." -ForegroundColor Yellow

$K8S = "$ROOT\infra\k8s"

Write-Host "  KEDA..." -ForegroundColor White
$kedaNs = kubectl get namespace keda --no-headers 2>$null
if (-not $kedaNs) {
    helm repo add kedacore https://kedacore.github.io/charts
    helm repo update
    helm install keda kedacore/keda --namespace keda --create-namespace
    Write-Host "  KEDA installed (waiting 20s)" -ForegroundColor Green
    Start-Sleep -Seconds 20
} else {
    Write-Host "  KEDA already installed" -ForegroundColor Green
}

Write-Host "  Namespace + ConfigMap + Secret..." -ForegroundColor White
kubectl apply -f "$K8S\predictive-maintenance\namespace-and-configmap.yml"

Write-Host "  Kafka (waiting 30s)..." -ForegroundColor White
kubectl apply -f "$K8S\kafka\kafka.yml"
Start-Sleep -Seconds 30

Write-Host "  Monitoring (InfluxDB + Prometheus + Grafana)..." -ForegroundColor White
kubectl apply -f "$K8S\monitoring\influxdb.yml"
kubectl apply -f "$K8S\monitoring\prometheus.yml"

Write-Host "  Grafana dashboard ConfigMap..." -ForegroundColor White
kubectl delete configmap grafana-dashboards -n monitoring 2>$null
kubectl create configmap grafana-dashboards -n monitoring `
    "--from-file=engineering.json=$ROOT\infra\grafana\dashboards\engineering.json" `
    "--from-file=executive.json=$ROOT\infra\grafana\dashboards\executive.json" `
    "--from-file=infra.json=$ROOT\infra\grafana\dashboards\infra.json" `
    "--from-file=operations.json=$ROOT\infra\grafana\dashboards\operations.json"

kubectl apply -f "$K8S\monitoring\grafana.yml"

if (Test-Path "$K8S\monitoring\kafka-exporter.yml") {
    kubectl apply -f "$K8S\monitoring\kafka-exporter.yml"
}

Write-Host "  App services + KEDA ScaledObjects..." -ForegroundColor White
kubectl apply -f "$K8S\predictive-maintenance\deployments.yml"

# edge-agent runs locally, not in GKE

kubectl apply -f "$K8S\predictive-maintenance\keda-scaledobjects.yml"

if (Test-Path "$K8S\predictive-maintenance\ingress.yml") {
    kubectl apply -f "$K8S\predictive-maintenance\ingress.yml"
}

Write-Host "  Deploy done - waiting 60s for pods..." -ForegroundColor Green
Start-Sleep -Seconds 60

# -- 5. Status -------------------------------------------------------------
Write-Host ""
Write-Host "[5/5] Status check..." -ForegroundColor Yellow

Write-Host ""
Write-Host "--- predictive-maintenance ---" -ForegroundColor Cyan
kubectl get pods,svc -n predictive-maintenance
Write-Host ""
Write-Host "--- monitoring ---" -ForegroundColor Cyan
kubectl get pods,svc -n monitoring
Write-Host ""
Write-Host "--- kafka ---" -ForegroundColor Cyan
kubectl get pods,svc -n kafka

$monApiIp  = kubectl get svc monitoring-api -n predictive-maintenance -o jsonpath="{.status.loadBalancer.ingress[0].ip}" 2>$null
$grafanaIp = kubectl get svc grafana -n monitoring -o jsonpath="{.status.loadBalancer.ingress[0].ip}" 2>$null

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " External IPs" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan

if ($monApiIp) {
    Write-Host "  Monitoring API:  http://${monApiIp}:8082" -ForegroundColor Green
} else {
    Write-Host "  Monitoring API:  pending... (check: kubectl get svc -n predictive-maintenance)" -ForegroundColor Yellow
}

if ($grafanaIp) {
    Write-Host "  Grafana:         http://${grafanaIp}:3000  (admin/admin)" -ForegroundColor Green
} else {
    Write-Host "  Grafana:         pending... (check: kubectl get svc -n monitoring)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Next ===" -ForegroundColor Cyan
Write-Host "  1. When IPs are ready:  .\start-demo.ps1" -ForegroundColor White
Write-Host "  2. Watch pods:          kubectl get pods -A -w" -ForegroundColor White
Write-Host ""
