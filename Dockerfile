# Multi-stage build for minimal final image
FROM python:3.13-slim AS builder

WORKDIR /build

# Install build dependencies, upgrade pip, and install packages in one layer
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --upgrade pip>=26.0

# Copy requirements and install Python packages
#
# constraints.txt fija las versiones EXACTAS validadas; requirements.txt sigue
# diciendo los minimos que el codigo necesita. Sin el constraints, dos builds del
# mismo Dockerfile pueden traer versiones distintas sin que cambie una linea del
# repositorio -- y en una app de tablas, una regresion de pandas o pyarrow no da
# un error, da otro numero.
COPY app/requirements.txt constraints.txt ./
RUN pip install --user --no-cache-dir -r requirements.txt -c constraints.txt

# Final stage - minimal runtime image
FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Copy Python dependencies from builder to system location
COPY --from=builder /root/.local /usr/local

# Se ELIMINA pip de la imagen final.
#
# Aqui habia un `pip install --upgrade pip>=26.0` para tapar CVE-2025-8869 y
# CVE-2026-1703. Costaba 18.1 MB de capa (medido con docker history) para una
# herramienta que en ejecucion no se usa: las dependencias ya vienen instaladas
# desde la etapa builder, y el contenedor solo arranca gunicorn.
#
# Quitarlo elimina esas CVEs de raiz en vez de perseguir versiones, y ademas
# reduce la superficie: sin pip, quien logre ejecutar codigo dentro del
# contenedor no tiene con que instalar nada.
#
# CONTRAPARTIDA: ya no se puede instalar nada dentro del contenedor en caliente.
# Para depurar hace falta reconstruir la imagen, que es como deberia ser.
RUN pip uninstall -y pip setuptools 2>/dev/null || true;     rm -rf /usr/local/lib/python*/site-packages/pip*            /usr/local/lib/python*/site-packages/setuptools*            /usr/local/lib/python*/site-packages/pkg_resources            /usr/local/bin/pip*            /usr/lib/python*/site-packages/pip* 2>/dev/null || true

# Create app user and set permissions (logs go to stdout/stderr in production)
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

# Variables de entorno por defecto del contenedor.
#
# AQUI se deciden de verdad: el .env NO entra en la imagen -- el COPY de abajo
# trae app/ y el fichero esta en la raiz del proyecto --, asi que estas son las
# que ve el contenedor salvo que el despliegue las sobreescriba con -e.
#
# WORKERS=2 y no 4: los workers NO comparten memoria, cada uno carga su copia de
# los parquets. Medido en este contenedor tras pedir tres tablas pesadas: 427 MB
# con 4 workers, 226 MB con 2. Y no se pierde concurrencia -- worker_class es
# UvicornWorker, asincrono: 4 workers no dan 4x de capacidad, dan 4 copias.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOG_LEVEL=INFO \
    ENVIRONMENT=production \
    HOST=0.0.0.0 \
    PORT=8000 \
    WORKERS=2

# Copy application code and set ownership in one layer
COPY --chown=appuser:appuser app/ /app/app/

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check
#
# SIN barra final: /healthz/ devuelve un 307 a /healthz. urlopen lo sigue, asi que
# funcionaba, pero era una ida y vuelta de mas en cada sondeo.
#
# urlopen lanza excepcion con 4xx/5xx, asi que el 503 que devuelve /healthz cuando
# faltan los datos hace fallar el healthcheck, que es lo que se quiere.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=5)" || exit 1

# Run application with gunicorn
CMD ["gunicorn", "app.main:app", "-c", "/app/app/gunicorn.conf.py"]
