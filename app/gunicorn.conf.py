import os

# Get configuration from environment variables
# 2 y no 4: los workers NO comparten memoria, cada uno carga su propia copia
# de los parquets. Medido: 427 MB con 4 workers tras pedir tres tablas
# pesadas, 226 MB con 2. Y no se pierde concurrencia -- worker_class es
# UvicornWorker, asincrono: 4 workers no dan 4x de capacidad, dan 4 copias.
#
# El Dockerfile y el compose ya ponian WORKERS=2, pero el defecto del codigo
# seguia en 4: un arranque sin esas variables se llevaba las cuatro copias.
workers = int(os.getenv("WORKERS", "2"))
bind = f"{os.getenv('HOST', '0.0.0.0')}:{os.getenv('PORT', '8000')}"
worker_class = "uvicorn.workers.UvicornWorker"
timeout = int(os.getenv("WORKER_TIMEOUT", "60"))
loglevel = os.getenv("LOG_LEVEL", "info").lower()

# Disable all access logging (no HTTP request logs)
accesslog = None
errorlog = "-"

# Additional optimizations
worker_connections = 1000
keepalive = 5
max_requests = 1000
max_requests_jitter = 50
