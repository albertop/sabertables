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
    from pathlib import Path

    import app.main as m

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

    with pytest.raises(FileNotFoundError):
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


@pytest.fixture
def app_en_produccion(monkeypatch):
    """
    Recarga app.main con ENVIRONMENT=production y LO DEVUELVE A SU SITIO al salir.

    monkeypatch restaura la variable, no el modulo: sin la vuelta atras, app.main
    se queda en produccion para todos los tests que vengan despues. Paso al
    activar API_REQUIRE_ORIGIN y dejo 30 tests en 403 por contaminacion.
    """
    import importlib

    import app.main

    monkeypatch.setenv("ENVIRONMENT", "production")
    yield importlib.reload(app.main)
    monkeypatch.undo()
    importlib.reload(app.main)


def test_en_produccion_se_redirige_a_https_conservando_la_query(app_en_produccion):
    """
    Perder la query en el redirect convertiria un enlace con parametros en la
    portada sin ellos, y el usuario no sabria por que.
    """
    with TestClient(app_en_produccion.app) as c:
        r = c.get("http://sabertables.com/?x=1&y=2", follow_redirects=False)

    assert r.status_code in (301, 307, 308)
    destino = r.headers["location"]
    assert destino.startswith("https://")
    assert "x=1" in destino and "y=2" in destino


def test_en_produccion_el_origen_permitido_lleva_esquema(app_en_produccion):
    """
    PRODUCTION_ORIGIN puede venir como 'sabertables.com' a secas. Sin normalizar,
    la comparacion contra la cabecera Origin --que siempre trae esquema-- no
    casaria nunca y el guard rechazaria a todo el mundo.
    """
    assert all(o.startswith("http") for o in app_en_produccion.ALLOWED_ORIGINS)


# ---------------------------------------------------------------------------
# El suelo de RE288
# ---------------------------------------------------------------------------

def test_sin_datasets_cargados_el_suelo_cae_al_valor_de_respaldo(monkeypatch):
    """
    El suelo se DERIVA del parquet. El valor de config.py es solo respaldo para
    cuando aun no hay datos cargados -- por ejemplo si se valida antes del
    arranque--, y nunca debe ganarle al derivado.
    """
    import app.main as m

    monkeypatch.setitem(m.data_store, "seasons", None)

    assert m._suelo_re288() == m.RE288_MIN_SEASON


# ---------------------------------------------------------------------------
# Datasets que no se pueden cargar
# ---------------------------------------------------------------------------

def test_si_falta_el_parquet_de_un_dataset_se_dice_cual(client, monkeypatch):
    """
    Un fichero ausente es un despliegue incompleto. El mensaje tiene que llevar
    el nombre o hay que ir a comparar la carpeta con el catalogo a mano.
    """
    import app.main as m

    monkeypatch.setitem(m.data_store, "pythag_exponents", None)
    monkeypatch.setattr(m.pd, "read_parquet",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no esta")))

    with pytest.raises(Exception, match="pythag_exponents"):
        m._load_dataset("pythag_exponents")


@pytest.mark.parametrize("dataset,texto", [
    ("pythag_exponents", "Pythagorean Exponents"),
    ("runs_per_win", "Runs Per Win"),
    ("breakeven_points", "Break-Even Points"),
    ("sb_breakeven_by_count", "SB Break-Even by Count"),
    ("re_impact", "RE Impact"),
])
def test_un_parquet_al_que_le_falta_una_columna_no_se_publica(client, monkeypatch, dataset, texto):
    """
    Es el contrato entre el pipeline y la web. Ya paso una vez:
    re288_frequencies servia `frequency` mientras la app exigia
    `pa_final_count_share`, y ese endpoint quedo roto sin que nadie lo supiera.
    """
    import pandas as pd

    import app.main as m

    monkeypatch.setitem(m.data_store, dataset, None)
    monkeypatch.setattr(m.pd, "read_parquet",
                        lambda *a, **k: pd.DataFrame({"columna_que_no_toca": [1]}))

    with pytest.raises(Exception, match=texto):
        m._load_dataset(dataset)


# ---------------------------------------------------------------------------
# Los ultimos bordes
# ---------------------------------------------------------------------------

def test_una_temporada_absurdamente_grande_se_rechaza(client):
    """
    Un entero de 64 bits en `season` desbordaria el int32 de las comparaciones
    aguas abajo. Se corta en el modelo, antes de tocar ningun parquet.
    """
    r = client.post("/api/generate-table", json={
        "matrix_type": "RE24", "from_season": 2**40, "to_season": 2**40,
    })

    assert r.status_code == 422


def test_pedir_antes_del_primer_anio_disponible_lo_dice(client):
    r = client.post("/api/generate-table", json={
        "matrix_type": "RE24", "from_season": 1850, "to_season": 1900,
    })

    assert r.status_code == 400
    assert "before available data" in r.json()["detail"]


def test_pedir_despues_del_ultimo_anio_disponible_lo_dice(client):
    """
    Es el mensaje que vera quien pida la temporada en curso antes de que el
    pipeline la publique.
    """
    r = client.post("/api/generate-table", json={
        "matrix_type": "RE24", "from_season": 2024, "to_season": 2099,
    })

    assert r.status_code == 400
    assert "after available data" in r.json()["detail"]


def test_una_url_que_no_se_puede_parsear_no_da_origen():
    """
    urlparse lanza ValueError con un corchete sin cerrar en el host. Dejar que
    suba convertiria una cabecera malformada en un 500.
    """
    from app.main import _origen_de

    assert _origen_de("http://[sin-cerrar") is None


def test_park_factors_exige_la_temporada(client):
    r = client.post("/api/generate-table", json={
        "matrix_type": "PARK_FACTORS", "average": 1,
    })

    assert r.status_code == 400
    assert "Season is required" in r.json()["detail"]


def test_las_demas_tablas_exigen_el_rango_completo(client):
    r = client.post("/api/generate-table", json={"matrix_type": "RE24"})

    assert r.status_code == 400
    assert "from_season" in r.json()["detail"]


def test_un_dataset_cargado_se_devuelve_tal_cual(client):
    """Control del caso de error: con el dataset bien, sale el DataFrame."""
    import pandas as pd

    import app.main as m

    assert isinstance(m.dataset("re24"), pd.DataFrame)


def test_un_fallo_inesperado_al_arrancar_se_propaga(monkeypatch, tmp_path):
    """
    No es FileNotFoundError sino cualquier otra cosa --un parquet corrupto, por
    ejemplo--. Arrancar igual dejaria la app sirviendo tablas a medias.
    """
    import app.main as m

    monkeypatch.setattr(m.pd, "read_parquet",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("parquet ilegible")))

    with pytest.raises(RuntimeError, match="ilegible"):
        m.load_essential_data()


@pytest.mark.parametrize("analisis,marca", [("ph_position", False), ("ph_pitcher", True)])
def test_el_esquema_viejo_de_bateador_emergente_se_sigue_leyendo(client, monkeypatch, analisis, marca):
    """
    El parquet nuevo trae `type` con ph_position y ph_pitcher separados; el
    viejo traia un solo `ph` con una bandera `ph_for_pitcher`. Los dos tienen
    que seguir funcionando, o un despliegue con datos anteriores serviria la
    tabla vacia.
    """
    import pandas as pd

    import app.main as m

    # 2021 y no 2024: ph_pitcher lleva un tope propio en <= 2021, porque es el
    # ultimo anio en que los lanzadores batearon con regularidad antes del
    # bateador designado universal.
    viejo = pd.DataFrame({
        "season": [2021, 2021], "type": ["ph", "ph"],
        "ph_for_pitcher": [False, True],
        "scenario": ["a", "b"], "label": ["A", "B"], "outs": [0, 0],
        "delta_re": [0.1, 0.2], "n": [100, 200],
    })
    monkeypatch.setitem(m.data_store, "re_impact", viejo)

    r = client.post("/api/generate-table", json={
        "matrix_type": "RE_IMPACT", "from_season": 2021, "to_season": 2021,
        "analysis_type": analisis,
    })

    assert r.status_code == 200
    filas = r.json()["table_data"]
    assert len(filas) == 1
    assert filas[0]["scenario"] == ("b" if marca else "a")


def test_el_bateador_emergente_lanzador_se_corta_en_2021(client, monkeypatch):
    """
    2021 fue el ultimo anio en que los lanzadores batearon con regularidad; en
    2022 la Liga Nacional adopto el bateador designado. Desde entonces un
    lanzador en el cajon es una rareza --una entrada de paliza, un partido sin
    banco-- y esas apariciones no describen lo que este analisis mide.

    OJO: ese 2021 esta escrito a mano en TRES sitios --la vista SQL, el regex de
    app.js y aqui-- porque no es una de las tres fechas de utils/seasons.py: es
    una regla del juego, no un rango de extraccion. Este test es lo que hay para
    que las tres no se separen sin que nadie lo note.
    """
    import pandas as pd

    import app.main as m

    monkeypatch.setitem(m.data_store, "re_impact", pd.DataFrame({
        "season": [2021, 2022], "type": ["ph_pitcher", "ph_pitcher"],
        "scenario": ["a", "b"], "label": ["A", "B"], "outs": [0, 0],
        "delta_re": [0.1, 0.2], "n": [100, 200],
    }))

    r = client.post("/api/generate-table", json={
        "matrix_type": "RE_IMPACT", "from_season": 2021, "to_season": 2022,
        "analysis_type": "ph_pitcher",
    })

    temporadas = {f["season"] for f in r.json()["table_data"]}
    assert temporadas == {2021}, "2022 en adelante no deberia aparecer"


def test_un_tipo_de_tabla_invalido_se_rechaza_en_el_modelo(client):
    """
    El contrato vive en el patron de Pydantic, no en una lista dentro del
    endpoint: por eso es 422 y no 400.

    Habia ademas una segunda lista en el cuerpo de la funcion con los mismos
    dieciocho tipos y un raise 400 que no podia ejecutarse nunca. Se borro: dos
    listas iguales se separan tarde o temprano y la de dentro no avisaria.
    """
    r = client.post("/api/generate-table", json={
        "matrix_type": "NO_EXISTE_ESTE_TIPO", "from_season": 2024, "to_season": 2024,
    })

    assert r.status_code == 422


def _origenes_de_cors(m):
    """
    Los origenes que se le pasaron a CORSMiddleware.

    OJO, hay DOS listas de nombre casi igual y proposito distinto:
    `ALLOWED_ORIGINS` (mayusculas) es la del guard anti-abuso de verify_origin, y
    `allowed_origins` (minusculas) es la de CORS. Mirar la que no toca hace que
    el test compruebe otra cosa y pase o falle por la razon equivocada.
    """
    for mw in m.app.user_middleware:
        if "CORS" in mw.cls.__name__:
            return list(mw.kwargs.get("allow_origins", []))
    raise AssertionError("no hay CORSMiddleware montado")


def test_un_origen_de_produccion_sin_esquema_se_normaliza_a_https(monkeypatch):
    """
    PRODUCTION_ORIGIN puede venir como 'sabertables.com' a secas. En produccion
    solo se admiten origenes HTTPS, asi que se le anade el esquema; sin eso, el
    navegador --que siempre manda el origen completo-- no casaria nunca.
    """
    import importlib

    import app.main

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PRODUCTION_ORIGIN", "ejemplo.invalid")
    m = importlib.reload(app.main)
    try:
        assert "https://ejemplo.invalid" in _origenes_de_cors(m)
    finally:
        monkeypatch.undo()
        importlib.reload(app.main)



def test_confiar_en_cualquier_proxy_deja_un_aviso(monkeypatch, capsys):
    """
    Con PROXY_TRUSTED_IPS='*' cualquiera puede falsear X-Forwarded-For y saltarse
    el rate limit. Es una configuracion legitima detras de un proxy controlado,
    pero tiene que quedar dicho en el log.
    """
    import importlib

    import app.main

    monkeypatch.setenv("PROXY_TRUSTED_IPS", "*")
    importlib.reload(app.main)
    try:
        salida = capsys.readouterr()
        assert "PROXY_TRUSTED_IPS" in (salida.out + salida.err)
    finally:
        monkeypatch.undo()
        importlib.reload(app.main)


def test_un_cuerpo_troceado_y_enorme_se_corta_mientras_llega(client):
    """
    Sin Content-Length hay que contar lo que llega. Si solo se mirara la
    cabecera, bastaria con enviar el cuerpo troceado --sin declararla-- para
    saltarse el limite entero.

    El envio como generador hace que httpx use Transfer-Encoding: chunked y no
    ponga Content-Length.
    """
    from app.main import MAX_REQUEST_BODY_BYTES

    trozo = b"x" * 65536
    veces = (MAX_REQUEST_BODY_BYTES // len(trozo)) + 4

    def troceado():
        yield b'{"matrix_type": "RE24", "relleno": "'
        for _ in range(veces):
            yield trozo
        yield b'"}'

    r = client.post(
        "/api/generate-table",
        content=troceado(),
        headers={"Content-Type": "application/json"},
    )

    assert r.status_code == 413
