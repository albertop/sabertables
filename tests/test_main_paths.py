"""
Caminos de app/main.py que la suite no pisaba.

Sobre todo rutas de error y ramas que solo se dan con el fichero de descargas
presente o ausente. Nada de esto rompe el flujo feliz, y por eso estaba sin
cubrir; tambien por eso, cuando falla, falla en silencio.
"""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    import app.main as m

    with TestClient(m.app) as c:
        yield c


# ---------------------------------------------------------------------------
# El origen de una URL
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,esperado", [
    ("https://sabertables.com/algo?x=1", "https://sabertables.com"),
    ("http://localhost:8000/", "http://localhost:8000"),
    ("https://sabertables.com", "https://sabertables.com"),
])
def test_el_origen_es_esquema_mas_host(url, esperado):
    from app.main import _origen_de

    assert _origen_de(url) == esperado


@pytest.mark.parametrize("url", ["", "no-es-una-url", "/solo/una/ruta", "https://"])
def test_una_url_ilegible_no_da_un_origen_inventado(url):
    """
    Devolver algo aqui seria peor que devolver None: se compararia contra la
    lista de permitidos y podria colar.
    """
    from app.main import _origen_de

    assert _origen_de(url) is None


def test_un_dominio_que_solo_empieza_igual_no_cuela():
    """
    El fallo que arreglo esta funcion: con startswith,
    "https://sabertables.com.loquesea.invalid" pasaba el guard, porque basta con
    registrar un dominio que tenga el tuyo de prefijo.
    """
    from app.main import ALLOWED_ORIGINS, _origen_de

    assert _origen_de("https://sabertables.com.loquesea.invalid/x") not in ALLOWED_ORIGINS


# ---------------------------------------------------------------------------
# Estadisticas de descargas
# ---------------------------------------------------------------------------

def _log_de_descargas(monkeypatch, tmp_path, lineas):
    import app.main as m

    fichero = tmp_path / "downloads.jsonl"
    if lineas is not None:
        fichero.write_text("\n".join(lineas), encoding="utf-8")
    monkeypatch.setattr(m, "DOWNLOADS_LOG", fichero)
    return fichero


def test_sin_fichero_de_descargas_las_estadisticas_salen_a_cero(client, monkeypatch, tmp_path):
    """
    Un despliegue nuevo no tiene el fichero. Devolver 500 ahi convertiria una
    instalacion limpia en un error.
    """
    _log_de_descargas(monkeypatch, tmp_path, None)

    r = client.get("/api/download-stats")

    assert r.status_code == 200
    assert r.json() == {"total_downloads": 0, "by_matrix_type": {}, "by_file_type": {}}


def test_las_descargas_se_cuentan_por_tipo_de_tabla_y_de_fichero(client, monkeypatch, tmp_path):
    _log_de_descargas(monkeypatch, tmp_path, [
        json.dumps({"m_t": "RE24", "f_t": "csv"}),
        json.dumps({"m_t": "RE24", "f_t": "png"}),
        json.dumps({"m_t": "RE288", "f_t": "csv"}),
    ])

    datos = client.get("/api/download-stats").json()

    assert datos["total_downloads"] == 3
    assert datos["by_matrix_type"] == {"RE24": 2, "RE288": 1}
    assert datos["by_file_type"] == {"csv": 2, "png": 1}


def test_se_siguen_leyendo_los_registros_del_formato_viejo(client, monkeypatch, tmp_path):
    """
    Las claves pasaron de `matrix_type` a `m_t` para acortar el fichero. El
    historico anterior tiene que seguir contando, o la cifra da un salto hacia
    abajo sin explicacion.
    """
    _log_de_descargas(monkeypatch, tmp_path, [
        json.dumps({"matrix_type": "RE24", "file_type": "csv"}),
        json.dumps({"m_t": "RE24", "f_t": "csv"}),
    ])

    datos = client.get("/api/download-stats").json()

    assert datos["total_downloads"] == 2
    assert datos["by_matrix_type"] == {"RE24": 2}


def test_una_linea_corrupta_no_tumba_el_recuento(client, monkeypatch, tmp_path):
    """
    El fichero se escribe linea a linea; una escritura interrumpida deja una a
    medias. Perder el recuento entero por eso seria desproporcionado.
    """
    _log_de_descargas(monkeypatch, tmp_path, [
        json.dumps({"m_t": "RE24", "f_t": "csv"}),
        "{esto no es json",
        "",
        json.dumps({"m_t": "RE288", "f_t": "csv"}),
    ])

    datos = client.get("/api/download-stats").json()

    assert datos["total_downloads"] == 2


def test_un_registro_sin_tipo_se_cuenta_como_desconocido(client, monkeypatch, tmp_path):
    _log_de_descargas(monkeypatch, tmp_path, [json.dumps({"otra_cosa": 1})])

    datos = client.get("/api/download-stats").json()

    assert datos["by_matrix_type"] == {"unknown": 1}
    assert datos["by_file_type"] == {"unknown": 1}


def test_si_el_fichero_no_se_puede_leer_se_devuelven_ceros(client, monkeypatch):
    """
    Las estadisticas son accesorias: que fallen no debe dar un 500 en una pagina
    que por lo demas funciona.
    """
    import app.main as m

    class Rota:
        def exists(self):
            raise OSError("disco ilegible")

    monkeypatch.setattr(m, "DOWNLOADS_LOG", Rota())

    r = client.get("/api/download-stats")

    assert r.status_code == 200
    assert r.json()["total_downloads"] == 0


# ---------------------------------------------------------------------------
# Validacion del rango de temporadas
# ---------------------------------------------------------------------------

def test_un_rango_invertido_se_rechaza(client):
    r = client.post("/api/generate-table", json={
        "matrix_type": "RE24", "from_season": 2025, "to_season": 2020,
    })

    assert r.status_code in (400, 422)


def test_re288_antes_del_suelo_se_rechaza_con_el_anio(client):
    """
    El suelo lo decide el parquet, no un numero escrito en el codigo. El mensaje
    tiene que decirlo para que el usuario sepa a partir de cuando puede pedir.
    """
    r = client.post("/api/generate-table", json={
        "matrix_type": "RE288", "from_season": 1950, "to_season": 1960,
    })

    assert r.status_code == 400
    assert "1989" in r.json()["detail"]


@pytest.mark.parametrize("tipo", ["RE288", "RE288_FREQ", "RE288_TRANS"])
def test_los_tres_tipos_de_re288_comparten_el_suelo(client, tipo):
    """
    Estaba escrito como `== "RE288"` en dos sitios, asi que RE288_FREQ y
    RE288_TRANS se saltaban la regla llamando a la API directamente.
    """
    r = client.post("/api/generate-table", json={
        "matrix_type": tipo, "from_season": 1950, "to_season": 1960,
    })

    assert r.status_code == 400


def test_un_tipo_de_tabla_que_no_existe_se_rechaza(client):
    r = client.post("/api/generate-table", json={
        "matrix_type": "NO_EXISTE", "from_season": 2024, "to_season": 2024,
    })

    assert r.status_code in (400, 422)


def test_park_factors_sin_ventana_se_rechaza(client):
    """`average` es obligatorio para esta tabla; sin el no hay nada que calcular."""
    r = client.post("/api/generate-table", json={
        "matrix_type": "PARK_FACTORS", "from_season": 2024, "to_season": 2024,
    })

    assert r.status_code in (400, 422)


@pytest.mark.parametrize("valor", [0, 2, 4, 99])
def test_park_factors_solo_admite_las_ventanas_1_3_y_5(client, valor):
    """
    El contrato esta en el modelo como Literal[1, 3, 5]: un valor invalido tiene
    que dar error de validacion y no un "no hay datos", que confunde el sintoma.
    """
    r = client.post("/api/generate-table", json={
        "matrix_type": "PARK_FACTORS", "from_season": 2024, "to_season": 2024,
        "average": valor,
    })

    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Ficheros estaticos con ruta propia
# ---------------------------------------------------------------------------

def test_el_sitemap_responde_como_xml(client):
    r = client.get("/sitemap.xml")

    assert r.status_code == 200
    assert "xml" in r.headers["content-type"]


def test_si_falta_el_sitemap_se_devuelve_404_y_no_un_500(client, monkeypatch):
    """
    Un fichero estatico ausente es un despliegue incompleto, no una excepcion:
    404 lo dice y 500 no.
    """
    import app.main as m

    original = m.Path.exists
    monkeypatch.setattr(
        m.Path, "exists",
        lambda self: False if self.name == "sitemap.xml" else original(self),
    )

    assert client.get("/sitemap.xml").status_code == 404


def test_si_falta_robots_se_devuelve_404_y_no_un_500(client, monkeypatch):
    import app.main as m

    original = m.Path.exists
    monkeypatch.setattr(
        m.Path, "exists",
        lambda self: False if self.name == "robots.txt" else original(self),
    )

    assert client.get("/robots.txt").status_code == 404


# ---------------------------------------------------------------------------
# El limite de tamano del cuerpo
#
# Es un middleware ASGI puro y no un BaseHTTPMiddleware: la version con
# BaseHTTPMiddleware rompio trece tests, porque leer request.stream() para
# contar bytes vacia el cuerpo y el endpoint recibe un JSON sin campos.
# ---------------------------------------------------------------------------

def test_un_cuerpo_declarado_enorme_se_rechaza_con_413(client):
    """
    Se mira Content-Length ANTES de leer nada: rechazar por la cabecera evita
    gastar memoria y tiempo de worker en algo que se va a tirar.
    """
    r = client.post(
        "/api/generate-table",
        content=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": "99999999"},
    )

    assert r.status_code == 413


def test_un_content_length_que_no_es_un_numero_se_rechaza_con_400(client):
    r = client.post(
        "/api/generate-table",
        content=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": "mucho"},
    )

    assert r.status_code == 400


def test_un_cuerpo_grande_sin_declarar_tambien_se_corta(client):
    """
    Sin Content-Length hay que contar lo que llega. Si solo se mirara la
    cabecera, bastaria con omitirla para saltarse el limite.
    """
    from app.main import MAX_REQUEST_BODY_BYTES

    relleno = "x" * (MAX_REQUEST_BODY_BYTES + 1024)
    r = client.post(
        "/api/generate-table",
        content=json.dumps({"matrix_type": "RE24", "relleno": relleno}).encode(),
        headers={"Content-Type": "application/json"},
    )

    assert r.status_code == 413


def test_un_cuerpo_normal_pasa_sin_tocarlo(client):
    """
    Control del anterior: el middleware no debe consumir el cuerpo. Cuando lo
    hacia, el endpoint recibia un JSON vacio y contestaba 422 'field required'.
    """
    r = client.post("/api/generate-table", json={
        "matrix_type": "RE24", "from_season": 2024, "to_season": 2024,
    })

    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Acceso a los datasets
# ---------------------------------------------------------------------------

def test_pedir_un_dataset_que_no_existe_falla_con_su_nombre(client):
    """
    Sin esto, un None viaja y revienta mas adelante con un AttributeError que no
    dice de donde vino.
    """
    import app.main as m

    with pytest.raises(Exception, match="no_existe"):
        m.dataset("no_existe")


def test_cargar_un_dataset_desconocido_falla_con_su_nombre(client):
    """
    No se registra y se sigue: se aborta. Un dataset que no esta en el catalogo
    es una clave mal escrita en el codigo, y seguir adelante deja un None
    viajando hasta reventar en otro sitio.
    """
    import app.main as m

    with pytest.raises(Exception, match="no_existe_tampoco"):
        m._load_dataset("no_existe_tampoco")


# ---------------------------------------------------------------------------
# La sonda de salud
# ---------------------------------------------------------------------------

def test_sin_directorio_de_datos_healthz_devuelve_503(client, monkeypatch):
    """
    Es lo que hace que el monitor avise. Antes devolvia 200 siempre mientras el
    proceso estuviera vivo, o sea que un despliegue sin datos se veia sano.
    """
    import app.main as m
    from pathlib import Path

    monkeypatch.setattr(m, "DATA_DIR", Path("/directorio/que/no/existe"))

    r = client.get("/healthz")

    assert r.status_code == 503
    assert "problems" in r.json()
    assert any("directorio de datos" in p for p in r.json()["problems"])


def test_si_falta_un_fichero_esencial_healthz_dice_cual(client, monkeypatch, tmp_path):
    import app.main as m

    (tmp_path / "innings.json").write_text("[]")
    monkeypatch.setattr(m, "DATA_DIR", tmp_path)

    r = client.get("/healthz")

    assert r.status_code == 503
    assert any("re24_values.parquet" in p for p in r.json()["problems"])


def test_healthz_no_se_cachea_nunca(client):
    """Con un CDN delante, un 200 guardado taparia una caida real."""
    r = client.get("/healthz")

    assert "no-store" in r.headers["cache-control"]


# ---------------------------------------------------------------------------
# Parametros obligatorios de cada tabla
#
# Cada uno responde 400 diciendo QUE falta. Un "no hay datos" generico haria
# buscar el problema en la temporada elegida en vez de en el parametro.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tipo", ["WIN_EXPECTANCY", "WIN_PROBABILITY_ADDED", "LEVERAGE_INDEX"])
def test_las_tablas_por_entrada_exigen_el_equipo(client, tipo):
    r = client.post("/api/generate-table", json={
        "matrix_type": tipo, "from_season": 2024, "to_season": 2024,
    })

    assert r.status_code == 400
    assert "eam" in r.json()["detail"]


@pytest.mark.parametrize("faltante,payload", [
    ("Outs", {}),
    ("Event code", {"re24_transitions_outs": 0}),
    ("Value type", {"re24_transitions_outs": 0, "event_cd": -1}),
])
def test_las_transiciones_re24_exigen_sus_tres_parametros(client, faltante, payload):
    """
    Se comprueban en orden, asi que el mensaje senala el PRIMERO que falta y no
    los tres a la vez.
    """
    r = client.post("/api/generate-table", json={
        "matrix_type": "RE24_TRANSITIONS", "from_season": 2024, "to_season": 2024,
        **payload,
    })

    assert r.status_code == 400
    assert faltante in r.json()["detail"]


def test_re288_transitions_exige_el_tipo_de_transicion(client):
    r = client.post("/api/generate-table", json={
        "matrix_type": "RE288_TRANS", "from_season": 2024, "to_season": 2024,
    })

    assert r.status_code == 400


def test_re_impact_exige_el_tipo_de_analisis(client):
    r = client.post("/api/generate-table", json={
        "matrix_type": "RE_IMPACT", "from_season": 2024, "to_season": 2024,
    })

    assert r.status_code == 400
    assert "analysis_type" in r.json()["detail"]


def test_breakeven_points_sin_tipo_devuelve_el_conjunto_completo(client):
    """
    A diferencia de las demas, esta NO exige el tipo: sin el sirve todo y el
    frontend filtra. Se fija aqui para que un cambio a "obligatorio" no rompa la
    tabla en silencio.
    """
    r = client.post("/api/generate-table", json={
        "matrix_type": "BREAKEVEN_POINTS", "from_season": 2024, "to_season": 2024,
    })

    assert r.status_code == 200


# ---------------------------------------------------------------------------
# RE Impact: cada analisis filtra y limpia sus propias columnas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("analisis", [
    "sac_bunt", "ibb", "ph_position", "ph_pitcher", "platoon_baseline", "tto",
])
def test_cada_analisis_de_re_impact_devuelve_sus_filas(client, analisis):
    """
    Cada uno se queda con su `type` y tira las columnas que no le aplican. Si
    una se colara, el frontend pintaria una columna vacia sin decir por que.
    """
    r = client.post("/api/generate-table", json={
        "matrix_type": "RE_IMPACT", "from_season": 2024, "to_season": 2024,
        "analysis_type": analisis,
    })

    assert r.status_code == 200
    datos = r.json()
    assert datos["table_data"], f"{analisis} sin filas"
    assert datos["analysis_type"] == analisis


def test_los_dos_analisis_de_bateador_emergente_son_distintos(client):
    """
    ph_position y ph_pitcher salen de la misma tabla y se separan por una
    columna. Si el filtro fallara, los dos mostrarian lo mismo -- y el de
    lanzadores, que ademas termina en 2021 por el DH universal, pasaria por
    actual.
    """
    def pedir(analisis):
        return client.post("/api/generate-table", json={
            "matrix_type": "RE_IMPACT", "from_season": 2024, "to_season": 2024,
            "analysis_type": analisis,
        }).json()

    posicion = pedir("ph_position")
    lanzador = pedir("ph_pitcher")

    assert posicion != lanzador


# ---------------------------------------------------------------------------
# Errores al generar una tabla
# ---------------------------------------------------------------------------

def test_un_error_de_datos_se_convierte_en_400_y_no_en_500(client):
    """
    "No hay datos para ese rango" es culpa de lo que se pidio, no del servidor.
    Un 500 haria buscar el problema en el sitio equivocado.
    """
    r = client.post("/api/generate-table", json={
        "matrix_type": "RE24", "from_season": 1912, "to_season": 1912,
        "event_cd": 999,
    })

    assert r.status_code == 400


def test_el_mensaje_de_error_no_regala_rutas_del_servidor(client, monkeypatch):
    """
    Se sanea antes de responder: una ruta absoluta le dice a un desconocido como
    esta montado el servidor y con que usuario corre.
    """
    import app.main as m

    def revienta(*a, **k):
        raise ValueError(r"fallo leyendo C:\Users\aperd\app\data\re24_values.parquet")

    monkeypatch.setattr(m, "generate_re24_table", revienta)

    r = client.post("/api/generate-table", json={
        "matrix_type": "RE24", "from_season": 2024, "to_season": 2024,
    })

    assert r.status_code == 400
    assert "aperd" not in r.json()["detail"]


def test_un_error_inesperado_no_cuenta_nada_de_dentro(client, monkeypatch):
    """
    Un fallo que no sea de datos es un 500 con mensaje generico: el detalle va
    al log, no a la respuesta.
    """
    import app.main as m

    def revienta(*a, **k):
        raise RuntimeError("KeyError en la fila 3 de re24_values")

    monkeypatch.setattr(m, "generate_re24_table", revienta)

    r = client.post("/api/generate-table", json={
        "matrix_type": "RE24", "from_season": 2024, "to_season": 2024,
    })

    assert r.status_code == 500
    detalle = r.json()["detail"]
    assert "re24_values" not in detalle
    assert "KeyError" not in detalle


# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------

def test_sin_marca_de_frescura_la_app_arranca_igual(monkeypatch, tmp_path):
    """
    last_updated.json falta en un despliegue a medias. No arrancar por eso
    dejaria el sitio caido por un fichero informativo.
    """
    import shutil

    import app.main as m

    # Se copia el directorio entero y se quita SOLO la marca: enumerar a mano
    # lo que carga el arranque haria que este test fallara cada vez que se
    # anadiera un dataset, por una razon que no tiene que ver con lo que prueba.
    copia = tmp_path / "data"
    shutil.copytree(m.DATA_DIR, copia)
    (copia / "last_updated.json").unlink()
    monkeypatch.setattr(m, "DATA_DIR", copia)

    m.load_essential_data()

    assert m.data_store["last_updated"] == {"last_updated_utc": None}


def test_si_falta_un_parquet_esencial_la_app_no_arranca(monkeypatch, tmp_path):
    """
    Arrancar sin datos deja un sitio que responde 200 y no sirve ni una tabla.
    Mejor no levantarse: el healthcheck del contenedor lo reintenta.
    """
    import app.main as m

    monkeypatch.setattr(m, "DATA_DIR", tmp_path)

    with pytest.raises(Exception):
        m.load_essential_data()


# ---------------------------------------------------------------------------
# El accesor de datasets
# ---------------------------------------------------------------------------

def test_si_un_dataset_quedo_a_medias_se_falla_al_pedirlo(client, monkeypatch):
    """
    Sin esto un None viaja y revienta mas adelante con un AttributeError que no
    dice de que dataset venia.
    """
    import app.main as m

    monkeypatch.setitem(m.data_store, "re24", "esto no es un DataFrame")

    with pytest.raises(Exception, match="re24"):
        m.dataset("re24")
