#!/bin/bash
# Pre-deployment validation script for Run Expectancy Docker deployment

set -e

echo "🔍 Starting pre-deployment validation..."
echo ""

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check Docker
echo "✓ Checking Docker installation..."
if ! command -v docker &> /dev/null; then
    echo -e "${RED}✗ Docker not found. Please install Docker.${NC}"
    exit 1
fi
DOCKER_VERSION=$(docker --version)
echo -e "${GREEN}✓ Docker found: $DOCKER_VERSION${NC}"
echo ""

# Check Docker Compose
echo "✓ Checking Docker Compose installation..."
if ! command -v docker-compose &> /dev/null; then
    echo -e "${RED}✗ Docker Compose not found. Please install Docker Compose.${NC}"
    exit 1
fi
COMPOSE_VERSION=$(docker-compose --version)
echo -e "${GREEN}✓ Docker Compose found: $COMPOSE_VERSION${NC}"
echo ""

# Check required files
echo "✓ Checking required files..."
FILES=(
    "Dockerfile"
    "docker-compose.yml"
    "nginx.conf"
    ".dockerignore"
    "app/main.py"
    "app/requirements.txt"
    "app/config.py"
    "app/gunicorn.conf.py"
    "app/data"
)

for file in "${FILES[@]}"; do
    if [ ! -e "$file" ]; then
        echo -e "${RED}✗ Missing required file: $file${NC}"
        exit 1
    fi
done
echo -e "${GREEN}✓ All required files present${NC}"
echo ""

# Check data files
echo "✓ Checking data files..."
DATA_FILES=$(find app/data -maxdepth 1 -type f | wc -l)
if [ "$DATA_FILES" -lt 10 ]; then
    echo -e "${YELLOW}⚠ Warning: Only $DATA_FILES data files found (expected ~18)${NC}"
    echo -e "${YELLOW}⚠ Please ensure app/data is populated before deployment${NC}"
else
    echo -e "${GREEN}✓ Found $DATA_FILES data files${NC}"
fi
echo ""

# Check environment files
echo "✓ Checking environment files..."
if [ -f ".env.production" ]; then
    echo -e "${GREEN}✓ .env.production found${NC}"
else
    echo -e "${YELLOW}⚠ .env.production not found (optional)${NC}"
fi
echo ""

# Validate Dockerfile
echo "✓ Validating Dockerfile..."
if docker build --no-cache -t run-expectancy:test . > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Dockerfile builds successfully${NC}"
    docker rmi run-expectancy:test > /dev/null 2>&1
else
    echo -e "${RED}✗ Dockerfile build failed${NC}"
    exit 1
fi
echo ""

# Check ports
echo "✓ Checking required ports..."
PORTS=(80 443 8000)
for port in "${PORTS[@]}"; do
    if netstat -tuln 2>/dev/null | grep -q ":$port "; then
        echo -e "${YELLOW}⚠ Port $port is already in use${NC}"
    else
        echo -e "${GREEN}✓ Port $port available${NC}"
    fi
done
echo ""

# Check disk space
echo "✓ Checking disk space..."
DISK_USAGE=$(df . | awk 'NR==2 {print $4}')
if [ "$DISK_USAGE" -lt 1000000 ]; then
    echo -e "${RED}✗ Low disk space: $(($DISK_USAGE/1024))MB available${NC}"
    exit 1
else
    echo -e "${GREEN}✓ Sufficient disk space: $(($DISK_USAGE/1024))MB available${NC}"
fi
echo ""

# El TLS lo termina NGINX, no este contenedor.
#
# Aqui habia una comprobacion de ./certs/cert.pem y key.pem que avisaba en
# amarillo cuando faltaban. Era un falso positivo: pedia certificados que no
# hacen falta. En produccion nginx recibe HTTPS y proxya HTTP interno -- por eso
# HTTPSEnforcementMiddleware mira X-Forwarded-Proto en vez del esquema real.
# Un contenedor que terminara TLS por su cuenta no necesitaria esa cabecera:
# son dos disenos incompatibles y el desplegado es el de nginx.

echo "✅ Pre-deployment validation passed!"
echo ""
echo "Next steps:"
echo "1. Review the configuration in docker-compose.yml"
echo "2. TLS lo termina nginx: este contenedor sirve HTTP interno"
echo "3. Run: docker-compose up -d"
echo "4. Monitor logs: docker-compose logs -f"
echo "5. Test health: curl http://localhost:8000/healthz"
