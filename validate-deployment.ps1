# Pre-deployment validation script for Run Expectancy Docker deployment (Windows)

Write-Host "🔍 Starting pre-deployment validation..." -ForegroundColor Cyan
Write-Host ""

# Check Docker
Write-Host "✓ Checking Docker installation..." -ForegroundColor Yellow
$dockerCheck = docker --version 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "✗ Docker not found. Please install Docker Desktop for Windows." -ForegroundColor Red
    exit 1
}
Write-Host "✓ Docker found: $dockerCheck" -ForegroundColor Green
Write-Host ""

# Check Docker Compose
Write-Host "✓ Checking Docker Compose installation..." -ForegroundColor Yellow
$composeCheck = docker-compose --version 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "✗ Docker Compose not found. Please install Docker Compose." -ForegroundColor Red
    exit 1
}
Write-Host "✓ Docker Compose found: $composeCheck" -ForegroundColor Green
Write-Host ""

# Check required files
Write-Host "✓ Checking required files..." -ForegroundColor Yellow
$requiredFiles = @(
    "Dockerfile",
    "docker-compose.yml",
    "nginx.conf",
    ".dockerignore",
    "app\main.py",
    "app\requirements.txt",
    "app\config.py",
    "app\gunicorn.conf.py",
    "app\data"
)

$missingFiles = @()
foreach ($file in $requiredFiles) {
    if (-not (Test-Path $file)) {
        $missingFiles += $file
    }
}

if ($missingFiles.Count -gt 0) {
    Write-Host "✗ Missing required files:" -ForegroundColor Red
    foreach ($file in $missingFiles) {
        Write-Host "  - $file" -ForegroundColor Red
    }
    exit 1
}
Write-Host "✓ All required files present" -ForegroundColor Green
Write-Host ""

# Check data files
Write-Host "✓ Checking data files..." -ForegroundColor Yellow
$dataFiles = @(Get-ChildItem -Path "app\data" -File | Measure-Object).Count
if ($dataFiles -lt 10) {
    Write-Host "⚠ Warning: Only $dataFiles data files found (expected ~18)" -ForegroundColor Yellow
    Write-Host "⚠ Please ensure app\data is populated before deployment" -ForegroundColor Yellow
} else {
    Write-Host "✓ Found $dataFiles data files" -ForegroundColor Green
}
Write-Host ""

# Check environment files
Write-Host "✓ Checking environment files..." -ForegroundColor Yellow
if (Test-Path ".env.production") {
    Write-Host "✓ .env.production found" -ForegroundColor Green
} else {
    Write-Host "⚠ .env.production not found (optional)" -ForegroundColor Yellow
}
Write-Host ""

# Validate Dockerfile
Write-Host "✓ Validating Dockerfile..." -ForegroundColor Yellow
try {
    docker build --no-cache -t run-expectancy:test . *>$null
    docker rmi run-expectancy:test *>$null
    Write-Host "✓ Dockerfile builds successfully" -ForegroundColor Green
} catch {
    Write-Host "✗ Dockerfile build failed" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Check ports
Write-Host "✓ Checking required ports..." -ForegroundColor Yellow
$ports = @(80, 443, 8000)
foreach ($port in $ports) {
    $portInUse = netstat -an | Select-String ":$port " | Measure-Object
    if ($portInUse.Count -gt 0) {
        Write-Host "⚠ Port $port is already in use" -ForegroundColor Yellow
    } else {
        Write-Host "✓ Port $port available" -ForegroundColor Green
    }
}
Write-Host ""

# Check disk space
Write-Host "✓ Checking disk space..." -ForegroundColor Yellow
$drive = (Get-Item ".").PSDrive
$freeSpace = $drive.Free / 1GB
if ($freeSpace -lt 1) {
    Write-Host "✗ Low disk space: $([Math]::Round($freeSpace, 2))GB available" -ForegroundColor Red
    exit 1
} else {
    Write-Host "✓ Sufficient disk space: $([Math]::Round($freeSpace, 2))GB available" -ForegroundColor Green
}
Write-Host ""

# El TLS lo termina NGINX, no este contenedor.
#
# Aqui habia una comprobacion de .\certs\ que avisaba en amarillo cuando
# faltaban los certificados. Era un falso positivo: pedia unos que no hacen
# falta. En produccion nginx recibe HTTPS y proxya HTTP interno -- por eso
# HTTPSEnforcementMiddleware mira X-Forwarded-Proto y no el esquema real.


Write-Host "✅ Pre-deployment validation passed!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Review the configuration in docker-compose.yml"
Write-Host "2. TLS lo termina nginx: este contenedor sirve HTTP interno"
Write-Host "3. Run: docker-compose up -d"
Write-Host "4. Monitor logs: docker-compose logs -f"
Write-Host "5. Test health: curl http://localhost:8000/healthz"
