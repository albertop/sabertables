"""
MLB Run Expectancy Tables - FastAPI Application
Single-page web application displaying RE24, RE288, and percentage data.

Memory Optimization Strategy:
1. Lazy Loading: Only 3 essential datasets loaded at startup (event_types, seasons, last_updated, innings)
   - 11 data-heavy datasets loaded on-demand when first requested
   - Reduces startup memory footprint by ~80-90%

2. Optimized Data Types:
   - int8 for small integers (event_cd, outs, bucket_sort_order, inning, sort_order_count, inn_ct, bat_home_id, runs_diff)
   - int16 for seasons (covers range 1000-32767)
   - float32 instead of float64 for all decimal values (50% memory reduction)
   - category dtype for repetitive string columns (50-70% memory reduction)
   - string dtype for text columns

3. Copy Elimination:
   - Removed unnecessary .copy() operations where data is immediately converted to dict
   - Filter operations create views until absolutely necessary

4. Expected Memory Usage:
   - Startup: ~10-20 MB (essential data only)
   - Per loaded dataset: varies by size, but typically 5-30 MB each
   - Total with all datasets: ~150-200 MB (down from ~300-400 MB with old approach)
"""

import hashlib
import json
import os
import secrets
import sys
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import pandas as pd
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, ORJSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

# Import from new modules
from app.config import (
    BASE_DIR,
    BREAKEVEN_TYPE_TO_DATA_TYPE,
    DATA_DIR,
    DOWNLOADS_LOG,
    GZIP_MINIMUM_SIZE,
    LOGS_DIR,
    RE288_MATRIX_TYPES,
    RE288_MIN_SEASON,
    STATIC_DIR,
    TEMPLATES_DIR,
)
from app.services import (
    generate_park_factors_table,
    generate_re24_probabilities_table,
    generate_re24_table,
    generate_re288_frequencies_table,
    generate_re288_table,
    generate_re288_transitions_table,
)
from app.services.table_generator import (
    generate_leverage_index_table,
    generate_linear_weights_table,
    generate_r24_frequencies_table,
    generate_re24_transitions_table,
    generate_win_expectancy_table,
    generate_win_probability_added_table,
)
from app.validators import validate_dataframe_schema
from app.validators.data_validator import sanitize_error_message

# Load environment variables from .env in project root
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Ensure logs directory exists

# Get log level from environment variable (default to WARNING)
LOG_LEVEL = os.getenv("LOG_LEVEL", "WARNING").upper()
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "7"))

# Server configuration from environment
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# El directorio de logs solo se crea si se va a escribir en el.
#
# En produccion no se escribe ninguno: el handler de fichero solo se anade con
# ENVIRONMENT == "development" (mas abajo), porque los logs salen por stdout y los
# recoge Docker. Crearlo igualmente dejaba un directorio vacio en la imagen y, mas
# importante, un `mkdir` al IMPORTAR que reventaria el arranque si algun dia el
# contenedor corre con --read-only.
#
# No hace falta crearlo en el Dockerfile: appuser es dueño de /app, asi que puede
# crearlo el solo cuando toca.
if ENVIRONMENT == "development":
    LOGS_DIR.mkdir(exist_ok=True)
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
# 2 y no 4: los workers NO comparten memoria, cada uno carga su propia copia
# de los parquets. Medido: 427 MB con 4 workers tras pedir tres tablas
# pesadas, 226 MB con 2. Y no se pierde concurrencia -- worker_class es
# UvicornWorker, asincrono: 4 workers no dan 4x de capacidad, dan 4 copias.
#
# El Dockerfile y el compose ya ponian WORKERS=2, pero el defecto del codigo
# seguia en 4: un arranque sin esas variables se llevaba las cuatro copias.
WORKERS = int(os.getenv("WORKERS", "2"))
# Proxies de los que se acepta X-Forwarded-For.
#
# El defecto era "*", o sea confiar en cualquiera. Como el rate limit de slowapi
# se indexa por IP remota, con "*" basta con mandar una cabecera X-Forwarded-For
# distinta en cada peticion para saltarselo entero, y de paso envenenar los logs
# de acceso y lo que vea fail2ban.
#
# El defecto de ahora son loopback y los tres rangos privados de RFC 1918, que es
# donde vive un proxy inverso propio -- nginx en Docker sale por 172.16/12. Un
# despliegue detras de proxy sigue funcionando igual sin tocar nada; lo que deja
# de funcionar es falsear la cabecera desde fuera.
#
# uvicorn >= 0.52 acepta redes CIDR ademas de IPs sueltas. Si el proxy esta en
# otro sitio (una CDN, un balanceador gestionado), hay que listarlo en
# PROXY_TRUSTED_IPS: son sus rangos publicados, no "*".
# Exigir Origin/Referer conocido en la API de tablas.
#
# Por defecto solo en produccion, igual que HTTPSEnforcementMiddleware: en
# desarrollo estorba -- rompe curl, Postman y los tests, que no mandan esas
# cabeceras. Se puede forzar en cualquier entorno con API_REQUIRE_ORIGIN=true.
#
# QUE PARA Y QUE NO. Para que otra web use esta API desde el navegador de sus
# visitantes, y el raspado perezoso. NO para a un script: Origin y Referer las
# pone el cliente y se falsifican en dos minutos con curl. Es imposible hacer una
# API publica de uso exclusivo del frontend -- el frontend corre en el navegador
# del visitante, asi que todo lo que puede enviar se puede reproducir. Esto sube
# el coste, no cierra la puerta; quien la cierra de verdad es el rate limit por
# IP (que ahora si funciona, tras arreglar PROXY_TRUSTED_IPS='*') y una capa tipo
# Cloudflare delante.
API_REQUIRE_ORIGIN = os.getenv(
    "API_REQUIRE_ORIGIN", "true" if os.getenv("ENVIRONMENT", "development") == "production" else "false"
).strip().lower() in ("1", "true", "yes")

# Hosts que se aceptan en la cabecera Host.
#
# TrustedHostMiddleware se importaba y NUNCA se registraba, asi que la cabecera no
# se validaba. Host la pone el CLIENTE, y cualquier codigo que derive una URL de
# ella confia en un atacante -- ya paso una vez: el 308 permanente a
# https://evil.com/ del open redirect (ver 5.6 en docs/TODO.md). Ese punto ya
# esta cerrado, asi que esto protege al codigo FUTURO que vuelva a fiarse.
#
# La lista sale de lo que llega de verdad, comprobado en el VPS:
#
#   sabertables.com / www.  nginx usa `proxy_set_header Host $host`, o sea que
#                           pasa el Host original del visitante sin reescribir.
#   localhost               el HEALTHCHECK del contenedor llama a
#                           http://localhost:8000/healthz/. SIN ESTO Docker marca
#                           el contenedor como no sano.
#   127.0.0.1               depuracion contra el puerto publicado.
#
# Starlette compara SIN puerto (hace host.split(":")[0]), asi que no hay que
# listarlos. Admite comodin de sufijo tipo *.sabertables.com.
#
# Estan los dos dominios a proposito: con solo el www, una peticion al dominio
# desnudo recibiria un REDIRECT a www en vez de pasar, porque www_redirect viene
# activado por defecto, y eso puede pelearse con lo que ya haga nginx.
DEFAULT_ALLOWED_HOSTS = "sabertables.com,www.sabertables.com,localhost,127.0.0.1"
ALLOWED_HOSTS = [
    h.strip() for h in os.getenv("ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS).split(",") if h.strip()
]

DEFAULT_PROXY_TRUSTED_IPS = "127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
PROXY_TRUSTED_IPS_RAW = os.getenv("PROXY_TRUSTED_IPS", DEFAULT_PROXY_TRUSTED_IPS)
PROXY_TRUSTED_IPS = (
    ["*"] if PROXY_TRUSTED_IPS_RAW.strip() == "*"
    else [ip.strip() for ip in PROXY_TRUSTED_IPS_RAW.split(",") if ip.strip()]
)

# Allowed origins for API protection
ALLOWED_ORIGINS = [
    f"http://localhost:{PORT}",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    f"http://127.0.0.1:{PORT}",
    "https://www.sabertables.com",
    "https://sabertables.com"
]

# Hosts a los que se permite redirigir.
#
# HTTPSEnforcementMiddleware construia el destino del 308 con la cabecera
# x-forwarded-host, que va antes que host y que no filtra nadie:
# ProxyHeadersMiddleware gestiona X-Forwarded-For y X-Forwarded-Proto, no esta.
# Una peticion HTTP con "X-Forwarded-Host: evil.com" devolvia un 308 PERMANENTE a
# https://evil.com/..., con metodo y query preservados -- un open redirect que
# ademas el navegador y las caches se quedan.
#
# La lista sale de ALLOWED_ORIGINS y no de una constante nueva: ahi ya estaban
# declarados los hosts reales, y tenerlos en dos sitios es como la regla de RE288
# acabo duplicada y divergente.
ALLOWED_REDIRECT_HOSTS = frozenset(
    netloc for netloc in (urlparse(origin).netloc for origin in ALLOWED_ORIGINS) if netloc
)

# A donde se redirige cuando el host pedido no esta en la lista. No se puede
# reflejar lo que mande el cliente, que es justo el fallo que se corrige.
# El apex y NO www: nginx redirige www -> sabertables.com con 301, asi que
# mandar aqui a www anadiria un rebote a cada redireccion. Ver 7.3 en
# docs/TODO.md.
CANONICAL_HOST = os.getenv("CANONICAL_HOST", "sabertables.com")

# Configure loguru
logger.remove()  # Remove default handler
logger.add(
    sys.stderr,
    level=LOG_LEVEL,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | {message}"
)

# Only add file logging in development (not in production - use stderr via Docker)
if ENVIRONMENT == "development":
    logger.add(
        LOGS_DIR / "app_{time:YYYY-MM-DD}.log",
        rotation="00:00",  # Rotate at midnight
        retention=f"{LOG_RETENTION_DAYS} days",  # Keep logs for configured days
        compression="zip",  # Compress old logs
        level=LOG_LEVEL,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
        enqueue=True  # Enable async logging to prevent file locking issues
    )

# Global dataframes - only essential data loaded at startup
# El almacen NO es homogeneo: guarda DataFrames, pero tambien un dict
# ("last_updated") y una lista ("innings"). Estaba anotado como
# dict[str, DataFrame | None], que era falso y ademas obligaba a mypy a protestar
# en cada acceso -- 35 de los 56 errores salian de ahi.
#
# Se declara por lo que es, y para los DataFrames se usa dataset() mas abajo, que
# ademas comprueba que este cargado en vez de dar un AttributeError sobre None
# tres funciones mas adelante.
data_store: dict[str, Any] = {
    "re24": None,  # Loaded on-demand
    "re288": None,  # Loaded on-demand
    "re288_frequencies": None,  # Loaded on-demand
    "re288_transitions": None,  # Loaded on-demand
    "re24_percentages": None,  # Loaded on-demand
    "re24_event_types": None,  # Loaded at startup
    "seasons": None,  # Loaded at startup
    "last_updated": None,  # Loaded at startup
    "woba_coeffs": None,  # Loaded on-demand
    "fip": None,  # Loaded on-demand
    "ballparks": None,  # Loaded on-demand
    "park_factors": None,  # Loaded on-demand
    "linear_weights": None,  # Loaded on-demand
    "r24_frequencies": None,  # Loaded on-demand
    "re24_transitions": None,  # Loaded on-demand
    "win_expectancy": None,  # Loaded on-demand
    "events_counts": None,  # Loaded on-demand
    "innings": None,  # Loaded at startup
    "pythag_exponents": None,  # Loaded on-demand
    "runs_per_win": None,  # Loaded on-demand
    "breakeven_points": None,  # Loaded on-demand
    "sb_breakeven_by_count": None,  # Loaded on-demand
    "re_impact": None  # Loaded on-demand
}


def load_essential_data() -> None:
    """Load only essential data at startup - event types, seasons, and last_updated."""

    try:
        # Load only the essential files needed for the UI
        data_store["re24_event_types"] = pd.read_parquet(DATA_DIR / "re24_eventTypes.parquet")

        # Convert to memory-efficient types
        data_store["re24_event_types"]["event_cd"] = data_store["re24_event_types"]["event_cd"].astype('int8')
        data_store["re24_event_types"]["event"] = data_store["re24_event_types"]["event"].astype('string')

        # Validate schema
        validate_dataframe_schema(
            data_store["re24_event_types"],
            "RE24 Event Types",
            ["event_cd", "event"]
        )

        # Load innings JSON file
        with open(DATA_DIR / "innings.json") as f:
            data_store["innings"] = json.load(f)

        # Load a minimal dataframe to get season info (just read re24 season column)
        re24_seasons = pd.read_parquet(DATA_DIR / "re24_values.parquet", columns=["season"])
        min_season = re24_seasons["season"].min()
        max_season = re24_seasons["season"].max()
        all_seasons = sorted(re24_seasons["season"].unique())
        del re24_seasons  # Free memory immediately

        # Load swing-take seasons from breakeven parquet (only season + type columns)
        swing_take_seasons: list[int] = []
        bp_path = DATA_DIR / "breakeven_points.parquet"
        if bp_path.exists():
            bp_seasons_df = pd.read_parquet(bp_path, columns=["season", "type"])
            swing_take_seasons = sorted(
                int(s) for s in bp_seasons_df.loc[bp_seasons_df["type"] == "swing-take", "season"].unique()
            )
            del bp_seasons_df

        # El suelo de RE288 se DERIVA del parquet, no se escribe aqui.
        #
        # Este valor lo decide el pipeline (MIN_PER_PITCH_SEASON en
        # utils/seasons.py), pero la app se despliega sola --el Dockerfile solo
        # copia app/-- asi que no puede importarlo. Leerlo del dato es mejor que
        # duplicar el numero: no hay dos sitios que puedan discrepar, y si algun
        # dia el pipeline baja ese suelo, la app lo respeta sola.
        # El valor de config.py queda solo como respaldo si falta el parquet.
        re288_min_season = RE288_MIN_SEASON
        re288_path = DATA_DIR / "re288_values.parquet"
        if re288_path.exists():
            re288_seasons = pd.read_parquet(re288_path, columns=["season"])
            if len(re288_seasons):
                re288_min_season = int(re288_seasons["season"].min())
            del re288_seasons

        data_store["seasons"] = pd.DataFrame({
            "min_season": [min_season],
            "max_season": [max_season],
            "all_seasons": [all_seasons],
            "swing_take_seasons": [swing_take_seasons],
            "re288_min_season": [re288_min_season],
        })

        # Load last_updated.json
        last_updated_file = DATA_DIR / "last_updated.json"
        if last_updated_file.exists():
            with open(last_updated_file) as f:
                data_store["last_updated"] = json.load(f)
        else:
            data_store["last_updated"] = {"last_updated_utc": None}
            logger.warning("last_updated.json not found")

    except FileNotFoundError as e:
        logger.error(f"Data file not found - {e}")
        logger.error("Please run the data extraction script first")
        raise
    except Exception as e:
        logger.error(f"Error loading essential data: {e}")
        raise


# Un cerrojo por dataset para la carga perezosa.
#
# ensure_data_loaded comprobaba "esta cargado?" y leia el parquet sin cerrojo.
# FastAPI ejecuta los endpoints sincronos en un pool de hilos, asi que dos
# peticiones al mismo dataset frio entran a la vez y lo leen las dos. No corrompe
# nada -- asignar al dict es atomico bajo el GIL y ambas versiones son iguales --
# pero duplica lectura de disco y pico de memoria justo en el arranque en frio,
# que es donde mas duele y con los datasets mas pesados (WE, WPA, LI).
#
# Se usa un cerrojo POR DATASET y no uno global para que cargar win_expectancy no
# bloquee a quien pide re24.
_load_locks: dict[str, threading.Lock] = {}
_load_locks_guard = threading.Lock()


def _lock_for(dataset_name: str) -> threading.Lock:
    lock = _load_locks.get(dataset_name)
    if lock is None:
        with _load_locks_guard:
            lock = _load_locks.setdefault(dataset_name, threading.Lock())
    return lock


def ensure_data_loaded(dataset_name: str) -> None:
    """Carga un dataset bajo demanda, una sola vez aunque lo pidan a la vez."""
    if data_store.get(dataset_name) is not None:
        logger.debug(f"{dataset_name} already loaded")
        return

    with _lock_for(dataset_name):
        # Segunda comprobacion: otro hilo pudo cargarlo mientras esperabamos aqui.
        # Sin ella el cerrojo solo serializaria la doble carga en vez de evitarla.
        if data_store.get(dataset_name) is not None:
            logger.debug(f"{dataset_name} lo cargo otro hilo mientras esperabamos")
            return
        _load_dataset(dataset_name)


def dataset(dataset_name: str) -> pd.DataFrame:
    """
    Devuelve un dataset ya cargado, garantizando que es un DataFrame.

    Lo carga si hace falta. Si algo fue mal, falla AQUI con el nombre del dataset
    en vez de dejar que un None viaje y reviente mas adelante con un
    AttributeError que no dice de donde vino.
    """
    ensure_data_loaded(dataset_name)
    valor = data_store.get(dataset_name)
    if not isinstance(valor, pd.DataFrame):
        raise HTTPException(
            status_code=500,
            detail=f"Dataset '{dataset_name}' no esta disponible",
        )
    return valor


def _load_dataset(dataset_name: str) -> None:
    """Lee el parquet y lo deja en data_store. Llamar solo con el cerrojo tomado."""
    try:
        if dataset_name == "re24":
            df = pd.read_parquet(DATA_DIR / "re24_values.parquet")
            validate_dataframe_schema(df, "RE24", ["season", "runners", "outs", "run_value", "n", "event_cd"])
            df["runners"] = df["runners"].astype('category')
            df["outs"] = df["outs"].astype('category')
            df["season"] = df["season"].astype('int16')
            df["event_cd"] = df["event_cd"].astype('int8')
            df["run_value"] = df["run_value"].astype('float64')
            data_store["re24"] = df
            logger.info(f"Loaded RE24: {len(df):,} rows")

        elif dataset_name == "re288":
            df = pd.read_parquet(DATA_DIR / "re288_values.parquet")
            validate_dataframe_schema(df, "RE288", ["season", "runners", "outs", "count", "run_value", "n", "sort_order_count"])
            df["runners"] = df["runners"].astype('category')
            df["outs"] = df["outs"].astype('category')
            df["count"] = df["count"].astype('category')
            df["season"] = df["season"].astype('int16')
            df["run_value"] = df["run_value"].astype('float64')
            df["sort_order_count"] = df["sort_order_count"].astype('int8')
            data_store["re288"] = df

        elif dataset_name == "re288_frequencies":
            df = pd.read_parquet(DATA_DIR / "re288_frequencies.parquet")
            validate_dataframe_schema(df, "RE288 Frequencies", ["season", "runners", "outs", "count", "pa_final_count_share", "n", "sort_order_count"])
            df["runners"] = df["runners"].astype('category')
            df["outs"] = df["outs"].astype('category')
            df["count"] = df["count"].astype('category')
            df["season"] = df["season"].astype('int16')
            df["pa_final_count_share"] = df["pa_final_count_share"].astype('float64')
            df["sort_order_count"] = df["sort_order_count"].astype('int8')
            data_store["re288_frequencies"] = df
            logger.info(f"Loaded RE288 Frequencies: {len(df):,} rows")

        elif dataset_name == "re288_transitions":
            df = pd.read_parquet(DATA_DIR / "re288_transitions.parquet")
            validate_dataframe_schema(df, "RE288 Transitions", ["season", "runners", "outs", "count", "transition_type", "transition_value", "transition_prob", "n", "sort_order_count"])
            df["runners"] = df["runners"].astype('category')
            df["outs"] = df["outs"].astype('category')
            df["count"] = df["count"].astype('category')
            df["transition_type"] = df["transition_type"].astype('category')
            df["season"] = df["season"].astype('int16')
            df["transition_value"] = df["transition_value"].astype('float64')
            df["transition_prob"] = df["transition_prob"].astype('float64')
            df["sort_order_count"] = df["sort_order_count"].astype('int8')
            data_store["re288_transitions"] = df

        elif dataset_name == "re24_percentages":
            df = pd.read_parquet(DATA_DIR / "re24_probabilities.parquet")
            validate_dataframe_schema(df, "RE24 Percentages", ["season", "runners", "outs", "bucket", "percentage", "n", "bucket_sort_order", "inning"])
            df["runners"] = df["runners"].astype('category')
            df["outs"] = df["outs"].astype('category')
            df["bucket"] = df["bucket"].astype('category')
            df["season"] = df["season"].astype('int16')
            df["percentage"] = df["percentage"].astype('float64')
            df["bucket_sort_order"] = df["bucket_sort_order"].astype('int8')
            df["inning"] = df["inning"].astype('int8')
            data_store["re24_percentages"] = df
            logger.info(f"Loaded RE24 Percentages: {len(df):,} rows")

        elif dataset_name == "woba_coeffs":
            df = pd.read_parquet(DATA_DIR / "wOBA_Coeffs.parquet")
            validate_dataframe_schema(df, "wOBA Coefficients", ["season", "League_Avg_OBP", "WOBA_Scale", "wuBB", "wHBP", "w1B", "w2B", "w3B", "wHR"])
            df["season"] = df["season"].astype('int16')
            for col in ["League_Avg_OBP", "WOBA_Scale", "wuBB", "wHBP", "w1B", "w2B", "w3B", "wHR"]:
                df[col] = df[col].astype('float64')
            data_store["woba_coeffs"] = df

        elif dataset_name == "fip":
            df = pd.read_parquet(DATA_DIR / "fip.parquet")
            validate_dataframe_schema(df, "FIP Constants", ["season", "lgERA", "FIP_Component", "FIP_Constant", "lgIP", "lgHR", "lgBB", "lgHBP", "lgK", "lgER"])
            df["season"] = df["season"].astype('int16')
            for col in ["lgERA", "FIP_Component", "FIP_Constant", "lgIP", "lgER"]:
                df[col] = df[col].astype('float64')
            data_store["fip"] = df
            logger.info(f"Loaded FIP Constants: {len(df):,} rows")

        elif dataset_name == "ballparks":
            df = pd.read_parquet(DATA_DIR / "ballparks.parquet")
            validate_dataframe_schema(df, "Ballparks", ["Season", "PARK_ID", "TEAM_ID", "Team_Name", "Park_Name"])
            df["Season"] = df["Season"].astype('int16')
            df["PARK_ID"] = df["PARK_ID"].astype('category')
            df["TEAM_ID"] = df["TEAM_ID"].astype('category')
            df["Team_Name"] = df["Team_Name"].astype('string')
            df["Park_Name"] = df["Park_Name"].astype('string')
            data_store["ballparks"] = df
            logger.info(f"Loaded Ballparks: {len(df):,} rows")

        elif dataset_name == "park_factors":
            df = pd.read_parquet(DATA_DIR / "parkFactors.parquet")
            validate_dataframe_schema(df, "Park Factors", ["PARK_ID", "SEASON", "SEASONS", "PF_R", "PF_1B", "PF_2B", "PF_3B", "PF_HR", "PF_BB", "PF_SO"])
            df["PARK_ID"] = df["PARK_ID"].astype('category')
            df["SEASON"] = df["SEASON"].astype('int16')
            for col in ["PF_R", "PF_1B", "PF_2B", "PF_3B", "PF_HR", "PF_BB", "PF_SO"]:
                df[col] = df[col].astype('float64')
            data_store["park_factors"] = df
            logger.info(f"Loaded Park Factors: {len(df):,} rows")

        elif dataset_name == "linear_weights":
            df = pd.read_parquet(DATA_DIR / "linear_weights.parquet")
            validate_dataframe_schema(df, "Linear Weights", ["SEASON", "OUTS", "RUNNERS", "EVENT_CD", "n", "LINEAR_WEIGHT"])
            df["RUNNERS"] = df["RUNNERS"].astype('category')
            df["SEASON"] = df["SEASON"].astype('int16')
            df["OUTS"] = df["OUTS"].astype('int8')
            df["EVENT_CD"] = df["EVENT_CD"].astype('int8')
            df["LINEAR_WEIGHT"] = df["LINEAR_WEIGHT"].astype('float64')
            data_store["linear_weights"] = df

        elif dataset_name == "r24_frequencies":
            df = pd.read_parquet(DATA_DIR / "re24_frequencies.parquet")
            validate_dataframe_schema(df, "R24 Frequencies", ["SEASON", "RUNNERS", "OUTS", "EVENT_CD", "n", "FREQUENCY"])
            df["RUNNERS"] = df["RUNNERS"].astype('category')
            df["OUTS"] = df["OUTS"].astype('category')
            df["SEASON"] = df["SEASON"].astype('int16')
            df["EVENT_CD"] = df["EVENT_CD"].astype('int16')
            df["FREQUENCY"] = df["FREQUENCY"].astype('float64')
            data_store["r24_frequencies"] = df

        elif dataset_name == "pythag_exponents":
            df = pd.read_parquet(DATA_DIR / "pythag_exponents.parquet")
            all_cols = ["season", "1 year", "2 years", "3 years", "5 years", "8 years", "10 years", "12 years", "Pythagenpat"]
            missing = set(all_cols) - set(df.columns)
            if missing:
                raise ValueError(f"Pythagorean Exponents: Missing required columns: {missing}")
            validate_dataframe_schema(df, "Pythagorean Exponents", ["season", "1 year"])
            df["season"] = df["season"].astype('int16')
            for col in ["1 year", "2 years", "3 years", "5 years", "8 years", "10 years", "12 years", "Pythagenpat"]:
                df[col] = df[col].astype('float64')
            data_store["pythag_exponents"] = df
            logger.info(f"Loaded Pythagorean Exponents: {len(df):,} rows")

        elif dataset_name == "runs_per_win":
            df = pd.read_parquet(DATA_DIR / "runs_per_win.parquet")
            all_cols = ["season", "1 year", "2 years", "3 years", "5 years", "8 years", "10 years", "12 years", "Pythagenpat", "FanGraphs"]
            missing = set(all_cols) - set(df.columns)
            if missing:
                raise ValueError(f"Runs Per Win: Missing required columns: {missing}")
            validate_dataframe_schema(df, "Runs Per Win", ["season", "1 year"])
            df["season"] = df["season"].astype('int16')
            for col in ["1 year", "2 years", "3 years", "5 years", "8 years", "10 years", "12 years", "Pythagenpat", "FanGraphs"]:
                df[col] = df[col].astype('float64')
            data_store["runs_per_win"] = df
            logger.info(f"Loaded Runs Per Win: {len(df):,} rows")

        elif dataset_name == "breakeven_points":
            df = pd.read_parquet(DATA_DIR / "breakeven_points.parquet")
            all_cols = ["season", "type", "scenario", "outs", "breakeven_pct", "n"]
            missing = set(all_cols) - set(df.columns)
            if missing:
                raise ValueError(f"Break-Even Points: Missing required columns: {missing}")
            validate_dataframe_schema(df, "Break-Even Points", ["season", "scenario"])
            df["season"] = df["season"].astype('int16')
            df["type"] = df["type"].astype('string')
            df["scenario"] = df["scenario"].astype('string')
            if "label" in df.columns:
                df["label"] = df["label"].astype('string')
            df["outs"] = df["outs"].astype('uint8')
            df["breakeven_pct"] = df["breakeven_pct"].astype('float64')
            df["n"] = df["n"].astype('int32')
            data_store["breakeven_points"] = df
            logger.info(f"Loaded Break-Even Points: {len(df):,} rows")

        elif dataset_name == "sb_breakeven_by_count":
            df = pd.read_parquet(DATA_DIR / "sb_breakeven_by_count.parquet")
            all_cols = ["season", "scenario", "count", "outs", "breakeven_pct", "n"]
            missing = set(all_cols) - set(df.columns)
            if missing:
                raise ValueError(f"SB Break-Even by Count: Missing required columns: {missing}")
            validate_dataframe_schema(df, "SB Break-Even by Count", ["season", "scenario"])
            df["season"] = df["season"].astype('int16')
            df["scenario"] = df["scenario"].astype('string')
            if "label" in df.columns:
                df["label"] = df["label"].astype('string')
            df["count"] = df["count"].astype('string')
            df["outs"] = df["outs"].astype('uint8')
            df["breakeven_pct"] = df["breakeven_pct"].astype('float64')
            df["n"] = df["n"].astype('int32')
            data_store["sb_breakeven_by_count"] = df
            logger.info(f"Loaded SB Break-Even by Count: {len(df):,} rows")

        elif dataset_name == "re_impact":
            df = pd.read_parquet(DATA_DIR / "re_impact.parquet")
            all_cols = ["season", "type", "scenario", "outs", "delta_re", "n"]
            missing = set(all_cols) - set(df.columns)
            if missing:
                raise ValueError(f"RE Impact: Missing required columns: {missing}")
            validate_dataframe_schema(df, "RE Impact", ["season", "type", "scenario"])
            df["season"] = df["season"].astype('int16')
            df["type"] = df["type"].astype('string')
            df["scenario"] = df["scenario"].astype('string')
            df["outs"] = df["outs"].astype('uint8')
            df["delta_re"] = df["delta_re"].astype('float64')
            df["n"] = df["n"].astype('int32')
            if "handedness_split" in df.columns:
                df["handedness_split"] = df["handedness_split"].astype('string')
            if "tto_pass" in df.columns:
                df["tto_pass"] = df["tto_pass"].astype('string')
            data_store["re_impact"] = df
            logger.info(f"Loaded RE Impact: {len(df):,} rows")

        elif dataset_name == "re24_transitions":
            df = pd.read_parquet(DATA_DIR / "re24_transitions.parquet")
            validate_dataframe_schema(df, "RE24 Transitions", ["season", "from_outs", "from_runners", "event_cd", "to_outs", "to_runners", "n", "avg_prob", "avg_delta_run_value"])
            df["season"] = df["season"].astype('int16')
            df["from_outs"] = df["from_outs"].astype('int8')
            df["from_runners"] = df["from_runners"].astype('string')
            df["event_cd"] = df["event_cd"].astype('int16')
            df["to_outs"] = df["to_outs"].astype('int8')
            df["to_runners"] = df["to_runners"].astype('string')
            df["n"] = df["n"].astype('int32')
            df["avg_prob"] = df["avg_prob"].astype('float64')
            df["avg_delta_run_value"] = df["avg_delta_run_value"].astype('float64')
            data_store["re24_transitions"] = df

        elif dataset_name == "win_expectancy":
            df = pd.read_parquet(DATA_DIR / "win_expectancy.parquet")
            validate_dataframe_schema(df, "Win Expectancy", ["SEASON", "INN_CT", "BAT_HOME_ID", "OUTS_CT", "RUNNERS", "RUNS_DIFF", "n", "WE", "LI", "WPA"])
            df["SEASON"] = df["SEASON"].astype('int16')
            df["INN_CT"] = df["INN_CT"].astype('int8')
            df["BAT_HOME_ID"] = df["BAT_HOME_ID"].astype('int8')
            df["OUTS_CT"] = df["OUTS_CT"].astype('int8')
            df["RUNNERS"] = df["RUNNERS"].astype('string')
            df["RUNS_DIFF"] = df["RUNS_DIFF"].astype('int8')
            df["WE"] = df["WE"].astype('float64')
            df["LI"] = df["LI"].astype('float64')
            df["WPA"] = df["WPA"].astype('float64')
            data_store["win_expectancy"] = df

        elif dataset_name == "events_counts":
            df = pd.read_parquet(DATA_DIR / "events_counts.parquet")
            validate_dataframe_schema(df, "Events Counts", ["season", "r24_n", "r288_n"])
            df["season"] = df["season"].astype('int16')
            data_store["events_counts"] = df
            logger.info(f"Loaded Events Counts: {len(df):,} rows")

        else:
            logger.error(f"Unknown dataset: {dataset_name}")
            raise ValueError(f"Unknown dataset: {dataset_name}")

    except FileNotFoundError as e:
        logger.error(f"Data file not found for {dataset_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Data file not found for {dataset_name}") from e
    except Exception as e:
        logger.error(f"Error loading {dataset_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Error loading data: {sanitize_error_message(str(e))}") from e


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load essential data on startup, cleanup on shutdown."""
    # Startup: Load only essential data into memory
    logger.warning("Application starting...")
    load_essential_data()
    yield
    # Shutdown: Cleanup (if needed)
    logger.warning("Application shutting down...")


# Initialize rate limiter and a no-op decorator for non-production
if ENVIRONMENT == 'production':
    limiter = Limiter(key_func=get_remote_address)
    RATE_LIMIT = os.getenv("RATE_LIMIT", "60/hour")
    rate_limit_decorator = limiter.limit(RATE_LIMIT)
else:
    class _NoOpLimiter:
        def limit(self, _rate: str):
            def _decorator(func):
                return func
            return _decorator
    limiter = _NoOpLimiter()
    RATE_LIMIT = ""
    rate_limit_decorator = limiter.limit(RATE_LIMIT)

# Initialize FastAPI app with lifespan
app = FastAPI(
    title="MLB Run Expectancy Tables",
    lifespan=lifespan,
)

# Tamano maximo del cuerpo de una peticion.
#
# Antes esto era max_request_size=1_000_000 pasado a FastAPI() con el comentario
# "prevent DoS attacks". NO EXISTIA: FastAPI no tiene ese parametro, asi que caia
# en **extra y acababa en app.extra, un diccionario que va al esquema OpenAPI y no
# corta nada. La proteccion se leia en el codigo pero no estaba en ninguna parte.
#
# El corte se hace por Content-Length -- que es donde se rechaza sin leer un solo
# byte de cuerpo -- y ademas contando lo leido, porque Content-Length se puede
# omitir con Transfer-Encoding: chunked.
MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", str(1_000_000)))


class MaxBodySizeMiddleware:
    """
    Rechaza cuerpos por encima de MAX_REQUEST_BODY_BYTES.

    Es ASGI puro y no BaseHTTPMiddleware a proposito: con BaseHTTPMiddleware hay
    que leer request.stream() para contar los bytes, y eso deja al endpoint sin
    cuerpo -- FastAPI responde 422 "Field required" a todo. Aqui se envuelve
    `receive`, asi que los bytes se cuentan al pasar y el endpoint los recibe
    igual.

    Se comprueban las dos cosas:
      - Content-Length, que permite rechazar sin leer un solo byte.
      - Lo realmente recibido, porque Content-Length se puede omitir con
        Transfer-Encoding: chunked, o mentir.
    """

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cabeceras = {k.decode("latin-1").lower(): v.decode("latin-1")
                     for k, v in scope.get("headers", [])}
        declarado = cabeceras.get("content-length")
        if declarado is not None:
            try:
                if int(declarado) > self.max_bytes:
                    await self._rechazar(send, 413, "Request body too large")
                    return
            except ValueError:
                await self._rechazar(send, 400, "Invalid Content-Length")
                return

        recibido = 0
        excedido = False

        async def receive_contando():
            nonlocal recibido, excedido
            mensaje = await receive()
            if mensaje["type"] == "http.request":
                recibido += len(mensaje.get("body", b""))
                if recibido > self.max_bytes:
                    excedido = True
                    # Se corta el cuerpo para que el endpoint no lo procese; la
                    # respuesta 413 la da el envoltorio de send.
                    return {"type": "http.disconnect"}
            return mensaje

        async def send_o_413(mensaje):
            if excedido:
                if mensaje["type"] == "http.response.start":
                    return
                if mensaje["type"] == "http.response.body":
                    return
            await send(mensaje)

        try:
            await self.app(scope, receive_contando, send_o_413)
        finally:
            if excedido:
                await self._rechazar(send, 413, "Request body too large")

    @staticmethod
    async def _rechazar(send, status: int, detalle: str) -> None:
        cuerpo = ORJSONResponse(status_code=status, content={"detail": detalle}).body
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(cuerpo)).encode())]})
        await send({"type": "http.response.body", "body": cuerpo})



# Add rate limiter to app state and exception handler only in production
if ENVIRONMENT == 'production':
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# Security Middleware
class HTTPSEnforcementMiddleware(BaseHTTPMiddleware):
    """Enforce HTTPS in production by redirecting HTTP requests."""
    async def dispatch(self, request, call_next):
        # Skip HTTPS enforcement for health checks (they use localhost, no SSL)
        if request.url.path == "/healthz" or request.url.path == "/healthz/":
            return await call_next(request)

        # Only enforce HTTPS in production
        if ENVIRONMENT == 'production':
            # Determine protocol considering reverse proxy headers
            forwarded_proto = request.headers.get('x-forwarded-proto')
            if forwarded_proto:
                # Some proxies send a comma-separated list; take the first hop
                proto = forwarded_proto.split(',')[0].strip()
            else:
                proto = request.url.scheme

            if proto == 'http':
                # El host pedido solo se usa si esta en la lista. Si no, se va al
                # canonico: reflejar la cabecera es el open redirect que esto
                # corrige. Ver ALLOWED_REDIRECT_HOSTS.
                solicitado = (request.headers.get('x-forwarded-host')
                              or request.headers.get('host')
                              or request.url.netloc)
                # Algunos proxies encadenan varios saltos separados por coma.
                solicitado = solicitado.split(',')[0].strip()

                if solicitado in ALLOWED_REDIRECT_HOSTS:
                    host = solicitado
                else:
                    logger.warning(
                        f"Host no permitido en redirect HTTPS: {solicitado!r}; "
                        f"se redirige a {CANONICAL_HOST}"
                    )
                    host = CANONICAL_HOST

                # Construct HTTPS URL and redirect (308 preserves method for POST)
                https_url = f"https://{host}{request.url.path}"
                if request.url.query:
                    https_url += f"?{request.url.query}"
                return RedirectResponse(url=https_url, status_code=308)

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""
    async def dispatch(self, request, call_next):
        # El nonce se genera ANTES del endpoint para que la plantilla lo pueda
        # poner en sus <script>, y despues se repite en la cabecera. Uno nuevo
        # por peticion: reutilizarlo lo volveria adivinable y no valdria de nada.
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce

        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # Allow Buy Me a Coffee widget to open in iframe
        # response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'

        # Only add HSTS in production (forces HTTPS for future requests)
        if ENVIRONMENT == 'production':
            # HSTS: max-age=31536000 (1 year), includeSubDomains, preload
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains; preload'

        # Content Security Policy.
        #
        # script-src usa NONCE en vez de 'unsafe-inline'. Sin eso, un XSS
        # ejecutaria cualquier <script> inyectado; con nonce el navegador solo
        # corre los que llevan el valor de esta peticion, que el atacante no
        # puede adivinar.
        #
        # style-src SIGUE con 'unsafe-inline', y es una decision medida, no un
        # olvido: los nonces cubren bloques <style> y <script>, NO cubren
        # atributos style="...". Este frontend genera 86 desde JavaScript --
        # todo el heatmap, los badges de fiabilidad, los colores de celda-- con
        # valores calculados, asi que ni nonce ni 'unsafe-hashes' sirven. Cerrarlo
        # obligaria a reescribir el renderizado entero a clases CSS.
        #
        # El reparto de riesgo lo justifica: un style= inyectado no EJECUTA
        # codigo. Y la superficie de inyeccion hoy es nula -- los 12 campos de
        # TableRequest son enteros acotados o enums con pattern, no hay ni un
        # campo de texto libre, y lo que se pinta sale de parquets propios.
        # Ademas no hay autenticacion ni cookies de sesion, asi que un XSS no
        # tendria sesion que robar.
        if ENVIRONMENT == 'production':
            response.headers['Content-Security-Policy'] = (
                "default-src 'self'; "
                "script-src 'self' 'nonce-" + nonce + "' https://www.googletagmanager.com https://cdn.jsdelivr.net https://cdnjs.buymeacoffee.com https://www.buymeacoffee.com https://cdn.buymeacoffee.com https://buymeacoffee.com https://*.buymeacoffee.com; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://cdnjs.buymeacoffee.com https://www.buymeacoffee.com https://buymeacoffee.com https://*.buymeacoffee.com; "
                "font-src 'self' https://fonts.gstatic.com https://cdnjs.buymeacoffee.com https://www.buymeacoffee.com https://buymeacoffee.com https://*.buymeacoffee.com; "
                "img-src 'self' data: https:; "
                "frame-src 'self' https://www.buymeacoffee.com https://cdn.buymeacoffee.com https://buymeacoffee.com https://*.buymeacoffee.com; "
                "connect-src 'self' https://www.google-analytics.com https://www.googletagmanager.com https://cdn.jsdelivr.net https://www.buymeacoffee.com https://cdnjs.buymeacoffee.com https://cdn.buymeacoffee.com https://buymeacoffee.com https://*.buymeacoffee.com; "
                "upgrade-insecure-requests"
            )
        else:
            response.headers['Content-Security-Policy'] = (
                "default-src 'self'; "
                "script-src 'self' 'nonce-" + nonce + "' https://www.googletagmanager.com https://cdn.jsdelivr.net https://cdnjs.buymeacoffee.com https://www.buymeacoffee.com https://cdn.buymeacoffee.com https://buymeacoffee.com https://*.buymeacoffee.com; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://cdnjs.buymeacoffee.com https://www.buymeacoffee.com https://buymeacoffee.com https://*.buymeacoffee.com; "
                "font-src 'self' https://fonts.gstatic.com https://cdnjs.buymeacoffee.com https://www.buymeacoffee.com https://buymeacoffee.com https://*.buymeacoffee.com; "
                "img-src 'self' data: https:; "
                "frame-src 'self' https://www.buymeacoffee.com https://cdn.buymeacoffee.com https://buymeacoffee.com https://*.buymeacoffee.com; "
                "connect-src 'self' https://www.google-analytics.com https://www.googletagmanager.com https://cdn.jsdelivr.net https://www.buymeacoffee.com https://cdnjs.buymeacoffee.com https://cdn.buymeacoffee.com https://buymeacoffee.com https://*.buymeacoffee.com;"
            )

        return response


# Add security middleware

app.add_middleware(MaxBodySizeMiddleware, max_bytes=MAX_REQUEST_BODY_BYTES)
app.add_middleware(SecurityHeadersMiddleware)

# Add HTTPS enforcement middleware (must be after security headers)
if ENVIRONMENT == 'production':
    app.add_middleware(HTTPSEnforcementMiddleware)

# Add GZip compression (60-80% smaller responses)
app.add_middleware(GZipMiddleware, minimum_size=GZIP_MINIMUM_SIZE)

# CORS Configuration - restrict to known origins
allowed_origins = []

if ENVIRONMENT == 'production':
    # In production, only allow HTTPS origins
    production_origin = os.getenv("PRODUCTION_ORIGIN")
    if production_origin:
        # Ensure it's HTTPS
        if not production_origin.startswith("https://"):
            production_origin = f"https://{production_origin}"
        allowed_origins.append(production_origin)
else:
    # In development, allow HTTP localhost
    allowed_origins = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Validacion de la cabecera Host. Solo en produccion, como el resto de defensas:
# en desarrollo se accede por IPs y nombres variados y estorbaria.
#
# EL ORDEN IMPORTA y no es el intuitivo: add_middleware ejecuta al REVES de como
# se anade, asi que este va casi al final para correr casi al principio. Puesto
# antes en la lista corria DESPUES del redirect a HTTPS, y una peticion con
# "Host: evil.test" recibia un 308 en vez del 400 -- o sea que la cabecera se
# usaba antes de validarla, que es justo lo que esto viene a impedir.
#
# Solo lo precede ProxyHeaders, que normaliza X-Forwarded-* y debe ir primero.
if ENVIRONMENT == 'production':
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

# IMPORTANT: Trust proxy headers LAST so it processes headers FIRST (middleware executes in reverse order)
if PROXY_TRUSTED_IPS == ["*"]:
    logger.warning(
        "PROXY_TRUSTED_IPS='*': se acepta X-Forwarded-For de cualquier origen. "
        "Cualquiera puede falsear su IP y saltarse el rate limit. Configura los "
        "rangos reales del proxy."
    )
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=PROXY_TRUSTED_IPS)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Setup Jinja2 templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# Ficheros estaticos que llevan ?v= en la plantilla.
VERSIONED_ASSETS = ("js/app.js", "css/styles.css")

_asset_version_cache: dict = {"key": None, "value": None}


def asset_version() -> str:
    """
    Cache-buster derivado del contenido de los assets versionados.

    Antes era una constante escrita a mano en la plantilla y habia que subirla en
    cada cambio de app.js o styles.css. Cuando se olvidaba, un navegador con
    cache seguia sirviendo la version anterior, y comprobarlo con curl sobre el
    fichero servido daba verde igualmente: el fichero estaba bien, lo que no
    ocurria es que el navegador lo pidiera. Paso tres veces durante la revision
    de agosto de 2026.

    Se recalcula solo cuando cambia el mtime o el tamano de algun fichero, asi
    que en produccion es un par de stat() por peticion y en desarrollo el
    navegador recoge la edicion sin reiniciar el servidor.
    """
    try:
        stamp = tuple((p.stat().st_mtime_ns, p.stat().st_size)
                      for p in (STATIC_DIR / name for name in VERSIONED_ASSETS))
    except OSError:
        # Si un asset no esta donde se espera, un cache-buster fijo es preferible
        # a devolver un 500 en la portada.
        return "0"

    if _asset_version_cache["key"] != stamp:
        digest = hashlib.sha256()
        for name in VERSIONED_ASSETS:
            digest.update((STATIC_DIR / name).read_bytes())
        _asset_version_cache["key"] = stamp
        _asset_version_cache["value"] = digest.hexdigest()[:12]

    return _asset_version_cache["value"]


from pydantic import BaseModel, Field


# Pydantic models with validation
class TableRequest(BaseModel):
    from_season: int | None = Field(None, ge=1800, le=2100, description="Starting season")
    to_season: int | None = Field(None, ge=1800, le=2100, description="Ending season")
    matrix_type: str = Field(..., pattern="^(RE24|RE24_PCT|RE288|RE288_FREQ|RE288_TRANS|LINEAR_WEIGHTS|R24_FREQUENCIES|WIN_EXPECTANCY|WIN_PROBABILITY_ADDED|LEVERAGE_INDEX|WOBA_COEFFS|FIP_CONST|PARK_FACTORS|RE24_TRANSITIONS|PYTHAG_EXPONENTS|RUNS_PER_WIN|BREAKEVEN_POINTS|RE_IMPACT)$", description="Matrix type")
    event_cd: int | None = Field(None, description="Event code for RE24 filtering")
    inning: int | None = Field(None, description="Inning for RE24_PCT filtering")
    transition_type: str | None = Field(None, pattern="^(B|S|F|X|H)$", description="Transition type for RE288_TRANS filtering (B=ball, S=strike, F=foul, X=ball in play, H=hit by pitch)")
    transition_mode: int | None = Field(None, ge=1, le=3, description="Transition mode for RE288_TRANS (1=Run Expectancy, 2=Probability, 3=Expected Run Expectancy)")
    # Literal y no int: el contrato real son 1, 3 o 5 -- las ventanas que
    # existen en parkFactors.parquet. Con int suelto, un 7 llegaba al
    # generador y fallaba como "no data", que no dice nada al cliente ni
    # aparece en el esquema OpenAPI.
    average: Literal[1, 3, 5] | None = Field(None, description="Average period for PARK_FACTORS (1, 3, or 5 seasons)")
    team: int | None = Field(None, ge=0, le=1, description="Team for WIN_EXPECTANCY, WIN_PROBABILITY_ADDED, and LEVERAGE_INDEX (0=Away, 1=Home)")
    outs: int | None = Field(None, ge=-1, le=2, description="Outs for LINEAR_WEIGHTS (-1=All, 0=0 Outs, 1=1 Out, 2=2 Outs)" )
    re24_transitions_outs: int | None = Field(None, ge=0, le=2, description="Outs for RE24_TRANSITIONS (0, 1, or 2)")
    re24_transitions_value_type: int | None = Field(None, ge=1, le=3, description="Value type for RE24_TRANSITIONS (1=RunValue, 2=Probability, 3=ExpectedRunValue)")
    breakeven_type: str | None = Field(
        None,
        pattern="^(stealing|running|tagging|bunt-for-hit|swing-take|swing-take-zone|sb_by_count)$",
        description="Break-even family for BREAKEVEN_POINTS; sin el se mandan todas",
    )
    analysis_type: str | None = Field(None, pattern="^(sac_bunt|ibb|ph_position|ph_pitcher|platoon_baseline|tto|steal_runs|running_runs|tagging_runs|gidp)$", description="Analysis type for RE_IMPACT. Los tres *_runs expresan en carreras lo que Break-Even Points da como porcentaje (seccion 10 de docs/TODO.md)")


    # Aqui habia un validador que comprobaba que la temporada cabe en un int32.
    # No podia disparar NUNCA: los campos ya declaran ge=1800, le=2100, que es
    # mucho mas estricto, y en Pydantic esas cotas se aplican ANTES que el
    # validador. Aparentaba una proteccion que no hacia ningun trabajo.


def validate_season_range(from_season: int, to_season: int, matrix_type: str) -> None:
    """Validate season range inputs.

    Args:
        from_season: Starting season
        to_season: Ending season
        matrix_type: Type of matrix (RE24, RE24_PCT, RE288)

    Raises:
        HTTPException: If validation fails
    """
    # Get available season range from data
    min_season = int(data_store["seasons"]["min_season"].iloc[0])
    max_season = int(data_store["seasons"]["max_season"].iloc[0])

    # Only validate from_season <= to_season for tables that use both season dropdowns
    # PARK_FACTORS only uses from_season with an average parameter, so skip this check
    if matrix_type != "PARK_FACTORS":
        if from_season > to_season:
            raise HTTPException(
                status_code=400,
                detail=f"From Season ({from_season}) must be less than or equal to To Season ({to_season})"
            )

    # Check that seasons are within available range
    if from_season < min_season:
        raise HTTPException(
            status_code=400,
            detail=f"From Season ({from_season}) is before available data starts ({min_season})"
        )

    if to_season > max_season:
        raise HTTPException(
            status_code=400,
            detail=f"To Season ({to_season}) is after available data ends ({max_season})"
        )

    # RE288 requires seasons >= 1989
    # Los tres tipos salen de r288Values, no solo RE288. El frontend ya los
    # bloqueaba a la vez; el backend solo miraba RE288, asi que una llamada
    # directa a la API con RE288_FREQ y from_season=1950 se saltaba la regla.
    suelo_re288 = _suelo_re288()
    if matrix_type in RE288_MATRIX_TYPES and from_season < suelo_re288:
        raise HTTPException(
            status_code=400,
            detail=f"{matrix_type} requires seasons from {suelo_re288} or later"
        )


def _suelo_re288() -> int:
    """
    Primera temporada con matriz RE288, tomada del propio parquet.

    Cae al valor de config.py solo si los datasets aun no estan cargados --por
    ejemplo si alguien llama a la validacion antes del arranque--.
    """
    datos = data_store.get("seasons")
    if datos is None or "re288_min_season" not in datos.columns:
        return RE288_MIN_SEASON
    return int(datos["re288_min_season"].iloc[0])


def _origen_de(url: str) -> str | None:
    """
    scheme://netloc de una URL, o None si no se puede leer.

    Comparar asi y no con startswith es la diferencia entre aceptar
    "https://sabertables.com" y aceptar tambien
    "https://sabertables.com.loquesea.invalid".
    """
    try:
        partes = urlparse(url)
    except ValueError:
        return None
    if not partes.scheme or not partes.netloc:
        return None
    return f"{partes.scheme}://{partes.netloc}"


async def verify_origin(
    origin: str | None = Header(None),
    referer: str | None = Header(None)
):
    """
    Verify that the request comes from an allowed origin.
    Checks both Origin and Referer headers to prevent direct API access.

    No-op cuando API_REQUIRE_ORIGIN esta desactivado (el caso de desarrollo).
    """
    if not API_REQUIRE_ORIGIN:
        return True

    # Origin manda. Si viene y NO esta permitido, se rechaza sin mas: antes un
    # Referer parecido podia rescatarlo, o sea que la cabecera mas fiable de las
    # dos quedaba subordinada a la mas debil.
    if origin is not None:
        if origin in ALLOWED_ORIGINS:
            return True
        logger.warning(f"Blocked request - Origin no permitido: {origin!r}")
        raise HTTPException(
            status_code=403,
            detail="Access denied. Please use the web interface."
        )

    # Sin Origin (algunas peticiones no lo mandan) se cae al Referer, comparando
    # scheme://netloc EXACTO.
    #
    # Antes era referer.startswith(allowed), y eso acepta
    # "https://sabertables.com.loquesea.invalid/..." porque empieza igual: basta
    # con registrar un dominio que tenga el tuyo de prefijo.
    if referer and _origen_de(referer) in ALLOWED_ORIGINS:
        return True

    # If neither check passes, deny access
    logger.warning(f"Blocked request - Origin: {origin}, Referer: {referer}")
    raise HTTPException(
        status_code=403,
        detail="Access denied. Please use the web interface."
    )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Render the main page with run expectancy tables."""
    context = {
        "request": request,
        "min_season": int(data_store["seasons"]["min_season"].iloc[0]),
        "max_season": int(data_store["seasons"]["max_season"].iloc[0]),
        "all_seasons": data_store["seasons"]["all_seasons"].iloc[0],
        "swing_take_seasons": data_store["seasons"]["swing_take_seasons"].iloc[0],
        "re288_min_season": _suelo_re288(),
        "last_updated_utc": data_store["last_updated"].get("last_updated_utc"),
        "re24_event_types": data_store["re24_event_types"].sort_values("event_cd").to_dict(orient="records") if data_store.get("re24_event_types") is not None else [],
        "innings": sorted(data_store.get("innings", []), key=lambda x: x["inn_id"]),
        "asset_version": asset_version(),
        "csp_nonce": getattr(request.state, "csp_nonce", ""),
    }
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=context
    )


@app.get("/sitemap.xml")
async def sitemap():
    """Serve sitemap.xml for SEO."""
    from fastapi.responses import FileResponse
    sitemap_path = Path(__file__).parent / "static" / "sitemap.xml"
    if sitemap_path.exists():
        return FileResponse(
            sitemap_path,
            media_type="application/xml",
            headers={"Cache-Control": "public, max-age=3600"}
        )
    raise HTTPException(status_code=404, detail="Sitemap not found")


@app.get("/robots.txt")
async def robots():
    """
    Sirve robots.txt desde el fichero, igual que el sitemap.

    Antes devolvia una cadena escrita aqui a mano MIENTRAS existia
    app/static/robots.txt con su propio contenido. Dos fuentes para lo mismo:
    editar el fichero no cambiaba nada, y nadie lo habria notado hasta que las
    dos versiones dijeran cosas distintas -- por ejemplo un Disallow puesto en el
    fichero que el sitio nunca llegaria a anunciar.
    """
    from fastapi.responses import FileResponse
    robots_path = Path(__file__).parent / "static" / "robots.txt"
    if robots_path.exists():
        return FileResponse(
            robots_path,
            media_type="text/plain",
            headers={"Cache-Control": "public, max-age=3600"}
        )
    raise HTTPException(status_code=404, detail="robots.txt not found")


# Ficheros sin los que la app no puede servir NADA. Se comprueban en /healthz.
#
# No se listan los 24 a proposito: abrirlos todos en cada sondeo cuesta, y estos
# tres bastan para detectar el fallo que importa -- volumen mal montado, copia a
# medias, o un despliegue que se quedo sin datos. re24_values ademas se abre de
# verdad, no solo se comprueba que exista: un parquet truncado pesa y no se lee.
HEALTH_REQUIRED_FILES = ("re24_values.parquet", "innings.json", "last_updated.json")


@app.get("/healthz")
async def healthz(response: Response):
    """
    Sonda de salud para el healthcheck de Docker y para Uptime Kuma.

    Antes devolvia 200 SIEMPRE mientras el proceso estuviera vivo, sin mirar los
    datos. Si faltaba un parquet o se corrompia, todas las tablas daban 500 y el
    monitor seguia en verde: era una sonda de liveness disfrazada de readiness,
    que es justo lo que su docstring decia ser.

    Ahora comprueba que los ficheros clave estan y que el mas grande se puede
    ABRIR -- un parquet truncado existe y pesa, pero no se lee. Si algo falla
    devuelve 503, que es lo que hace que el monitor avise.

    Sin cache: si algun dia hay CDN o nginx delante, un 200 guardado taparia una
    caida real.
    """
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"

    problemas = []
    if not DATA_DIR.is_dir():
        problemas.append(f"falta el directorio de datos: {DATA_DIR}")
    else:
        for nombre in HEALTH_REQUIRED_FILES:
            ruta = DATA_DIR / nombre
            if not ruta.is_file():
                problemas.append(f"falta {nombre}")

        clave = DATA_DIR / "re24_values.parquet"
        if clave.is_file():
            try:
                pd.read_parquet(clave, columns=["season"])
            except Exception as exc:
                problemas.append(f"re24_values.parquet no se puede leer: {type(exc).__name__}")

    ahora = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if problemas:
        logger.error(f"healthz DEGRADADO: {problemas}")
        response.status_code = 503
        return {"status": "unhealthy", "time": ahora, "problems": problemas}

    return {"status": "ok", "time": ahora}


@app.get("/api/download-stats", dependencies=[Depends(verify_origin)])
async def get_download_stats():
    """Get global download statistics."""
    try:
        if not DOWNLOADS_LOG.exists():
            return {
                "total_downloads": 0,
                "by_matrix_type": {},
                "by_file_type": {}
            }

        # Count total and breakdown by type
        total = 0
        by_matrix = {}
        by_file = {}

        with open(DOWNLOADS_LOG, encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        event = json.loads(line)
                        total += 1

                        # Count by matrix type (handle both old and new format)
                        matrix_type = event.get('m_t') or event.get('matrix_type', 'unknown')
                        by_matrix[matrix_type] = by_matrix.get(matrix_type, 0) + 1

                        # Count by file type (handle both old and new format)
                        file_type = event.get('f_t') or event.get('file_type', 'unknown')
                        by_file[file_type] = by_file.get(file_type, 0) + 1
                    except json.JSONDecodeError:
                        continue

        return {
            "total_downloads": total,
            "by_matrix_type": by_matrix,
            "by_file_type": by_file
        }
    except Exception as e:
        logger.error(f"Failed to read download stats: {e}")
        return {
            "total_downloads": 0,
            "by_matrix_type": {},
            "by_file_type": {}
        }


@app.post("/api/generate-table", dependencies=[Depends(verify_origin)])
@rate_limit_decorator
async def generate_table_endpoint(request: Request, table_request: TableRequest):
    """Generate run expectancy table based on parameters."""
    return await generate_table(request, table_request)

async def generate_table(request: Request, table_request: TableRequest):
    """Generate run expectancy table based on parameters."""
    try:
        # Aqui habia una lista con los dieciocho matrix_type validos y un
        # `if not in: raise 400`. No podia ejecutarse NUNCA: el modelo ya valida
        # matrix_type con un patron que enumera exactamente los mismos, asi que
        # cualquier valor invalido se rechaza con 422 antes de entrar aqui.
        # Peor aun, eran dos listas que podian separarse sin que nadie lo notara.
        # El contrato vive en el modelo.

        # WOBA_COEFFS doesn't use season range
        if table_request.matrix_type == "WOBA_COEFFS":
            ensure_data_loaded("woba_coeffs")
            # Get data - send raw values (frontend will format)
            table_data = data_store["woba_coeffs"].to_dict(orient='records')
            result = {
                "table_data": table_data,
                "matrix_type": "WOBA_COEFFS"
            }
            return ORJSONResponse(content=result)

        # FIP_CONST doesn't use season range
        if table_request.matrix_type == "FIP_CONST":
            logger.info("Generating FIP Constants table")
            ensure_data_loaded("fip")
            # Get data - send raw values (frontend will format)
            table_data = data_store["fip"].to_dict(orient='records')
            result = {
                "table_data": table_data,
                "matrix_type": "FIP_CONST"
            }
            return ORJSONResponse(content=result)

        # PYTHAG_EXPONENTS doesn't use season range
        if table_request.matrix_type == "PYTHAG_EXPONENTS":
            logger.info("Generating Pythagorean Exponents table")
            ensure_data_loaded("pythag_exponents")
            df = data_store["pythag_exponents"].copy()

            table_data = df.sort_values("season", ascending=False).to_dict(orient='records')
            result = {
                "table_data": table_data,
                "matrix_type": "PYTHAG_EXPONENTS"
            }
            return ORJSONResponse(content=result)

        # RUNS_PER_WIN doesn't use season range
        if table_request.matrix_type == "RUNS_PER_WIN":
            logger.info("Generating Runs Per Win table")
            ensure_data_loaded("runs_per_win")
            df = data_store["runs_per_win"].copy()

            table_data = df.sort_values("season", ascending=False).to_dict(orient='records')
            result = {
                "table_data": table_data,
                "matrix_type": "RUNS_PER_WIN"
            }
            return ORJSONResponse(content=result)

        # BREAKEVEN_POINTS manda TODAS LAS TEMPORADAS del tipo pedido: la
        # agregacion por rango la hace el frontend con sum(be*n)/sum(n), y es lo
        # que hace instantaneo mover el selector de temporadas.
        #
        # Lo que si se filtra es el TIPO. Antes iban los seis juntos --
        # table_data 12,309 filas / 2,055 KB mas by_count_data 5,328 / 878 KB --
        # y eligiendo "Stolen Bases" (1,392 filas) se descartaba el 90%. No es un
        # coste que se ahorre en cache: cambiar de tipo en la UI llama a
        # hideTableContainers() y obliga a pedir de nuevo, asi que ese 90% se
        # bajaba y se tiraba CADA VEZ.
        #
        # by_count_data solo se manda si se pide, que es el unico caso que lo usa.
        if table_request.matrix_type == "BREAKEVEN_POINTS":
            logger.info("Generating Break-Even Points table")
            ensure_data_loaded("breakeven_points")
            ensure_data_loaded("sb_breakeven_by_count")
            df = data_store["breakeven_points"]
            df_bc = data_store["sb_breakeven_by_count"]

            # Las listas de temporadas describen lo disponible y alimentan los
            # desplegables, asi que salen del conjunto completo, no del filtrado.
            seasons = sorted(df["season"].unique().tolist())
            seasons_by_count = sorted(df_bc["season"].unique().tolist())

            pedido = table_request.breakeven_type
            if pedido is None:
                # Compatibilidad: sin el campo se manda todo, como antes.
                table_data = df.to_dict(orient='records')
                by_count_data = df_bc.to_dict(orient='records')
            else:
                tipo_dato = BREAKEVEN_TYPE_TO_DATA_TYPE[pedido]
                table_data = (
                    [] if tipo_dato is None
                    else df[df["type"] == tipo_dato].to_dict(orient='records')
                )
                by_count_data = (
                    df_bc.to_dict(orient='records') if pedido == "sb_by_count" else []
                )
                logger.info(
                    f"  breakeven_type={pedido}: {len(table_data):,} filas de tabla, "
                    f"{len(by_count_data):,} de by_count"
                )
            result = {
                "table_data": table_data,
                "by_count_data": by_count_data,
                "seasons": seasons,
                "seasons_by_count": seasons_by_count,
                "matrix_type": "BREAKEVEN_POINTS"
            }
            return ORJSONResponse(content=result)

        # RE_IMPACT sends rows from re_impact filtered by analysis_type — frontend handles season filtering + weighted averaging
        if table_request.matrix_type == "RE_IMPACT":
            if table_request.analysis_type is None:
                raise HTTPException(
                    status_code=400,
                    detail="analysis_type is required for RE_IMPACT"
                )

            logger.info(f"Generating RE Impact table - Analysis: {table_request.analysis_type}")
            ensure_data_loaded("re_impact")
            df = data_store["re_impact"].copy()

            # Filter by analysis type
            if table_request.analysis_type == "sac_bunt":
                df = df[df["type"] == "sac_bunt"].drop(columns=["type", "platoon", "ph_for_pitcher"], errors="ignore")
            elif table_request.analysis_type == "ibb":
                df = df[df["type"] == "ibb"].drop(columns=["type", "platoon", "ph_for_pitcher"], errors="ignore")
            elif table_request.analysis_type == "ph_position":
                # Prefer explicit type from newer parquet; keep backward-compat for older "ph" + flag schema.
                if "ph_for_pitcher" in df.columns:
                    df = df[
                        (df["type"] == "ph_position") |
                        ((df["type"] == "ph") & (df["ph_for_pitcher"].eq(False)))
                    ]
                else:
                    df = df[df["type"] == "ph_position"]
                df = df.drop(columns=["type", "ph_for_pitcher"], errors="ignore")
            elif table_request.analysis_type == "ph_pitcher":
                # Prefer explicit type from newer parquet; keep backward-compat for older "ph" + flag schema.
                if "ph_for_pitcher" in df.columns:
                    df = df[
                        (df["type"] == "ph_pitcher") |
                        ((df["type"] == "ph") & (df["ph_for_pitcher"].eq(True)))
                    ]
                else:
                    df = df[df["type"] == "ph_pitcher"]
                df = df.drop(columns=["type", "ph_for_pitcher"], errors="ignore")
                # Restrict ph_pitcher to 2021 and earlier
                df = df[df["season"] <= 2021]
            elif table_request.analysis_type == "platoon_baseline":
                df = df[df["type"] == "platoon_baseline"].drop(columns=["type", "platoon", "ph_for_pitcher"], errors="ignore")
            elif table_request.analysis_type == "tto":
                df = df[df["type"] == "tto"].drop(columns=["type", "platoon", "ph_for_pitcher", "outs"], errors="ignore")

            table_data = df.to_dict(orient='records')
            seasons = sorted(df["season"].unique().tolist(), reverse=True)
            result = {
                "table_data": table_data,
                "seasons": seasons,
                "analysis_type": table_request.analysis_type,
                "matrix_type": "RE_IMPACT"
            }
            return ORJSONResponse(content=result)

        # PARK_FACTORS requires season and average but not season range
        if table_request.matrix_type == "PARK_FACTORS":
            if table_request.from_season is None:
                raise HTTPException(
                    status_code=400,
                    detail="Season is required for Park Factors"
                )
            if table_request.average is None:
                raise HTTPException(
                    status_code=400,
                    detail="Average period is required for Park Factors"
                )
            ensure_data_loaded("ballparks")
            ensure_data_loaded("park_factors")
            result = generate_park_factors_table(
                data_store,
                table_request.from_season,
                table_request.average
            )
            return ORJSONResponse(content=result)

        # Validate season range for other matrix types
        if table_request.from_season is None or table_request.to_season is None:
            raise HTTPException(
                status_code=400,
                detail="from_season and to_season are required for this matrix type"
            )
        validate_season_range(table_request.from_season, table_request.to_season, table_request.matrix_type)

        if table_request.matrix_type == "RE24":
            ensure_data_loaded("re24")
            ensure_data_loaded("events_counts")
            result = generate_re24_table(data_store, table_request.from_season, table_request.to_season, table_request.event_cd)
            return ORJSONResponse(content=result)
        elif table_request.matrix_type == "RE24_PCT":
            ensure_data_loaded("re24_percentages")
            ensure_data_loaded("events_counts")
            result = generate_re24_probabilities_table(data_store, table_request.from_season, table_request.to_season, table_request.inning)
            return ORJSONResponse(content=result)
        elif table_request.matrix_type == "RE288":
            ensure_data_loaded("re288")
            ensure_data_loaded("events_counts")
            result = generate_re288_table(data_store, table_request.from_season, table_request.to_season)
            return ORJSONResponse(content=result)
        elif table_request.matrix_type == "RE288_FREQ":
            ensure_data_loaded("re288_frequencies")
            ensure_data_loaded("events_counts")
            result = generate_re288_frequencies_table(data_store, table_request.from_season, table_request.to_season)
            return ORJSONResponse(content=result)
        elif table_request.matrix_type == "RE288_TRANS":
            if not table_request.transition_type:
                raise HTTPException(
                    status_code=400,
                    detail="Transition type is required for RE288_TRANS"
                )
            ensure_data_loaded("re288_transitions")
            ensure_data_loaded("events_counts")
            transition_mode = table_request.transition_mode if table_request.transition_mode is not None else 1
            result = generate_re288_transitions_table(
                data_store,
                table_request.from_season,
                table_request.to_season,
                table_request.transition_type,
                transition_mode
            )
            return ORJSONResponse(content=result)
        elif table_request.matrix_type == "LINEAR_WEIGHTS":
            ensure_data_loaded("linear_weights")
            result = generate_linear_weights_table(
                data_store,
                table_request.from_season,
                table_request.to_season,
                table_request.outs
            )
            return ORJSONResponse(content=result)
        elif table_request.matrix_type == "R24_FREQUENCIES":
            ensure_data_loaded("r24_frequencies")
            ensure_data_loaded("events_counts")
            result = generate_r24_frequencies_table(data_store, table_request.from_season, table_request.to_season)
            return ORJSONResponse(content=result)
        elif table_request.matrix_type == "WIN_EXPECTANCY":
            if table_request.team is None:
                raise HTTPException(
                    status_code=400,
                    detail="Team is required for WIN_EXPECTANCY (0=Away, 1=Home)"
                )
            ensure_data_loaded("win_expectancy")
            result = generate_win_expectancy_table(data_store, table_request.from_season, table_request.to_season, table_request.team)
            return ORJSONResponse(content=result)
        elif table_request.matrix_type == "WIN_PROBABILITY_ADDED":
            if table_request.team is None:
                raise HTTPException(
                    status_code=400,
                    detail="Team is required for WIN_PROBABILITY_ADDED (0=Away, 1=Home)"
                )
            ensure_data_loaded("win_expectancy")
            result = generate_win_probability_added_table(data_store, table_request.from_season, table_request.to_season, table_request.team)
            return ORJSONResponse(content=result)
        elif table_request.matrix_type == "LEVERAGE_INDEX":
            if table_request.team is None:
                raise HTTPException(
                    status_code=400,
                    detail="Team is required for LEVERAGE_INDEX (0=Away, 1=Home)"
                )
            ensure_data_loaded("win_expectancy")
            result = generate_leverage_index_table(data_store, table_request.from_season, table_request.to_season, table_request.team)
            return ORJSONResponse(content=result)
        elif table_request.matrix_type == "RE24_TRANSITIONS":
            if table_request.re24_transitions_outs is None:
                raise HTTPException(
                    status_code=400,
                    detail="Outs is required for RE24_TRANSITIONS"
                )
            if table_request.event_cd is None:
                raise HTTPException(
                    status_code=400,
                    detail="Event code is required for RE24_TRANSITIONS"
                )
            if table_request.re24_transitions_value_type is None:
                raise HTTPException(
                    status_code=400,
                    detail="Value type is required for RE24_TRANSITIONS (1=RunValue, 2=Probability)"
                )
            ensure_data_loaded("re24_transitions")
            ensure_data_loaded("re24_event_types")
            result = generate_re24_transitions_table(
                data_store,
                table_request.from_season,
                table_request.to_season,
                table_request.re24_transitions_outs,
                table_request.event_cd,
                table_request.re24_transitions_value_type
            )
            return ORJSONResponse(content=result)

    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise
    except ValueError as e:
        # Handle value errors (e.g., no data found)
        logger.error(f"Value error generating table: {e}")
        error_detail = sanitize_error_message(str(e), is_production=True)
        raise HTTPException(status_code=400, detail=error_detail) from e
    except Exception as e:
        # Handle unexpected errors
        logger.error(f"Unexpected error generating table: {e}")
        # Generic error message in production
        raise HTTPException(
            status_code=500,
            detail="An error occurred while processing your request. Please try again later."
        ) from e


if __name__ == "__main__":
    logger.warning(f"Starting server in {ENVIRONMENT} mode...")
    logger.warning(f"Server will run on {HOST}:{PORT}")

    if ENVIRONMENT == "production":
        # Use gunicorn in production
        logger.warning("Starting with gunicorn...")
        logger.warning(f"Workers: {WORKERS}")

        # Change to app directory to ensure correct module path
        os.chdir(BASE_DIR)

        # Ensure uvicorn workers honor proxy headers behind Nginx/ELB/Cloudflare
        # Trust all forwarders by default; override with PROXY_TRUSTED_IPS env if set at OS level
        os.environ.setdefault("FORWARDED_ALLOW_IPS", "*")

        # Build gunicorn command
        cmd = [
            "gunicorn",
            "main:app",
            "--bind", f"{HOST}:{PORT}",
            "--workers", str(WORKERS),
            "--worker-class", "uvicorn.workers.UvicornWorker",
            "--access-logfile", "-",
            "--error-logfile", "-",
            "--log-level", "info"
        ]

        # Use os.execvp to replace current process with gunicorn
        # This is more efficient than subprocess and handles signals properly
        try:
            os.execvp("gunicorn", cmd)
        except FileNotFoundError:
            logger.error("Gunicorn not found. Please ensure it's installed: pip install gunicorn")
            sys.exit(1)
    else:
        # Use uvicorn in development with auto-reload
        import uvicorn

        logger.warning("Starting with uvicorn in development mode...")
        uvicorn.run(
            "app.main:app",
            host=HOST,
            port=PORT,
            reload=True
        )

