"""
Pone utils/ en sys.path para que los tests puedan importar los scripts del
pipeline.

Hasta agosto de 2026 esos scripts se llamaban 00_, 01_, 02_ y 03_, y un nombre de
modulo no puede empezar por digito: no eran importables, asi que no habia forma
de testear sus funciones de calculo. Los tests solo podian validar los parquets
ya publicados.
"""
import os
import sys

import pytest
from pathlib import Path

UTILS = Path(__file__).resolve().parent.parent / "utils"
if str(UTILS) not in sys.path:
    sys.path.insert(0, str(UTILS))


# La suite no manda Origin/Referer -- TestClient no es un navegador -- asi que se
# desactiva la comprobacion para el resto de tests, que no van de eso.
#
# Hace falta ponerlo ANTES de importar app.main, porque el valor se lee al cargar
# el modulo. Y hace falta ponerlo: el .env de esta maquina trae
# ENVIRONMENT=production, asi que todo lo que corre fuera de Docker arranca en
# modo produccion y la puerta estaria activa.
#
# La puerta en si se prueba en test_api_regression.py, encendiendola a proposito.
os.environ.setdefault("API_REQUIRE_ORIGIN", "false")


# TestClient manda Host: testserver, que obviamente no esta en la lista real.
# Se anade en vez de desactivar TrustedHostMiddleware, para que la suite lo
# ejercite de verdad; el rechazo de un Host ajeno se prueba aparte.
os.environ.setdefault("ALLOWED_HOSTS", "sabertables.com,www.sabertables.com,localhost,127.0.0.1,testserver")


@pytest.fixture(autouse=True)
def _rango_de_temporadas_aislado():
    """
    Restaura MIN_SEASON / MIN_PER_PITCH_SEASON / MAX_SEASON despues de CADA test.

    Son globales de modulo, y cualquier test que pase --max-season por linea de
    comandos las deja cambiadas para los que vengan detras. Paso: los tests del
    pitagorico dejaban el techo en 2020 y reventaban cinco tests de
    test_seasons.py que se ejecutaban despues -- en su fichero pasaban todos,
    asi que el fallo solo aparecia al correr la suite entera.

    Va en conftest y no en cada fichero porque el problema es cruzado.
    """
    import seasons

    previo = (seasons.MIN_SEASON, seasons.MIN_PER_PITCH_SEASON, seasons.MAX_SEASON)
    yield
    (seasons.MIN_SEASON, seasons.MIN_PER_PITCH_SEASON, seasons.MAX_SEASON) = previo
