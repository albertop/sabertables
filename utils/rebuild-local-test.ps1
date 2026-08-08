$ErrorActionPreference = 'Continue'

$ContainerName = "sabertables-local-test"
$ImageTag      = "sabertables:local-test"
$HostPort      = 3001
$HealthUrl     = "http://127.0.0.1:$HostPort/healthz"

Write-Host "==> Stopping container '$ContainerName'..." -ForegroundColor Cyan
docker stop $ContainerName 2>$null

Write-Host "==> Removing container '$ContainerName'..." -ForegroundColor Cyan
docker rm $ContainerName 2>$null

Write-Host "==> Building image '$ImageTag'..." -ForegroundColor Cyan
$projectRoot = Split-Path $PSScriptRoot -Parent
docker build -t $ImageTag $projectRoot
if ($LASTEXITCODE -ne 0) {
    Write-Error "docker build failed. Aborting."
    exit 1
}

Write-Host "==> Starting container '$ContainerName'..." -ForegroundColor Cyan
docker run -d `
    --name $ContainerName `
    -e ENVIRONMENT=development `
    -p "${HostPort}:8000" `
    $ImageTag
if ($LASTEXITCODE -ne 0) {
    Write-Error "docker run failed."
    exit 1
}

Write-Host "==> Waiting for container to become healthy..." -ForegroundColor Cyan
$maxAttempts = 15
$attempt = 0
$healthy = $false
Start-Sleep -Seconds 2
while ($attempt -lt $maxAttempts) {
    $attempt++
    try {
        $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            Write-Host "==> Healthy after $attempt attempt(s)." -ForegroundColor Green
            $healthy = $true
            break
        }
    } catch { }
    Write-Host "   Attempt $attempt/$maxAttempts — not ready yet, retrying..."
    Start-Sleep -Seconds 2
}

if (-not $healthy) {
    Write-Warning "Container did not respond to health check at $HealthUrl after $maxAttempts attempts."
}

docker ps --filter "name=$ContainerName"
