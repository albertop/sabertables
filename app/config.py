"""
Configuration constants for Run Expectancy Tables application.
"""
from pathlib import Path

# Setup paths
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

# Download tracking log file (JSONL format)
DOWNLOADS_LOG = DATA_DIR / "downloads.jsonl"

# GZIP compression minimum size (bytes)
GZIP_MINIMUM_SIZE = 1000

# Display labels for base states
BASE_STATE_LABELS: dict[str, str] = {
    "000": "Empty",
    "100": "1st",
    "010": "2nd",
    "110": "1st & 2nd",
    "001": "3rd",
    "101": "1st & 3rd",
    "011": "2nd & 3rd",
    "111": "Loaded",
    "AVG": "Average"
}

# Season bounds for validation
MIN_REASONABLE_SEASON = 1871  # First professional baseball season
MAX_REASONABLE_SEASON = 2030  # Far future bound

# RESPALDO, no la fuente de verdad.
#
# Este suelo lo decide el pipeline: es MIN_PER_PITCH_SEASON en utils/seasons.py.
# La app se despliega sola --el Dockerfile solo copia app/-- asi que no puede
# importarlo, y lo que hace es DERIVARLO del propio parquet de re288 al arrancar
# (ver _suelo_re288 en main.py). Derivar es mejor que copiar el numero: no hay
# dos sitios que puedan discrepar, y si el pipeline mueve ese suelo la app lo
# sigue sola.
#
# Este valor solo se usa si el parquet falta. Hay un test que comprueba que
# nunca le gana al derivado.
#
# Historia: estaba en 1988, que no corresponde a ninguna fuente --r288Values
# arranca en 1989--, asi que era un corte arbitrario que dejaba publicada en
# re288_frequencies una temporada sin contrapartida en el resto de tablas RE288.
RE288_MIN_SEASON = 1989

# Los tres tipos de tabla que salen de r288Values y comparten el suelo de
# temporada. Estaba escrito como `== "RE288"` en dos sitios distintos, asi que
# RE288_FREQ y RE288_TRANS se saltaban la regla al llamar la API directamente.
RE288_MATRIX_TYPES = frozenset({"RE288", "RE288_FREQ", "RE288_TRANS"})


# Valor del selector de la UI -> valor de la columna `type` en
# breakeven_points.parquet. No coinciden: la UI usa "swing-take-zone" con guion y
# el dato "swing_take_zone" con guion bajo. Y "sb_by_count" no vive en ese
# parquet, sino en sb_breakeven_by_count, por eso se mapea a None.
BREAKEVEN_TYPE_TO_DATA_TYPE = {
    "stealing":         "stealing",
    "running":          "running",
    "tagging":          "tagging",
    "bunt-for-hit":     "bunt-for-hit",
    "swing-take":       "swing-take",
    "swing-take-zone":  "swing_take_zone",
    "sb_by_count":      None,
}
