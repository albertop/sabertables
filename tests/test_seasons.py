"""
El rango de temporadas de la extraccion.

Estos tests no tocan la base: comprueban que las tres fechas de utils/seasons.py
se aplican de verdad en TODOS los caminos, y que ninguna consulta se queda sin
acotar por olvido. Es justo lo que fallaba antes -- cada consulta decidia su
propio rango y una de ellas, la de swing/take por zona, no decidia ninguno.
"""
import cli
import create_break_even_points as be
import extract_re_data as extract
import pandas as pd
import pytest
import seasons


@pytest.fixture(autouse=True)
def _restaurar_rango():
    """
    Los tres valores son globales de modulo y varios tests los mueven. Sin esto,
    el primero que cambie el techo se lo cambia a todos los que vengan detras.
    """
    previo = (seasons.MIN_SEASON, seasons.MIN_PER_PITCH_SEASON, seasons.MAX_SEASON)
    yield
    (seasons.MIN_SEASON, seasons.MIN_PER_PITCH_SEASON, seasons.MAX_SEASON) = previo


# ---------------------------------------------------------------------------
# Los tres valores
# ---------------------------------------------------------------------------

def test_los_valores_por_defecto_son_los_acordados():
    assert (seasons.MIN_SEASON, seasons.MIN_PER_PITCH_SEASON, seasons.MAX_SEASON) == (1910, 1989, 2025)


def test_hay_dos_suelos_y_el_de_conteo_es_posterior():
    """
    Retrosheet publica los eventos desde 1910, pero el detalle de cada
    lanzamiento no existe hasta 1989. Mezclarlos daria tablas con dos decadas de
    celdas vacias que parecen ausencia de juego y son ausencia de dato.
    """
    assert seasons.rango() == (1910, 2025)
    assert seasons.rango(por_lanzamiento=True) == (1989, 2025)
    assert seasons.rango(True)[0] > seasons.rango()[0]


def test_un_rango_invertido_se_detecta_al_arrancar():
    """
    No da error por si solo: da parquets vacios. Y un parquet vacio deja la
    tabla en blanco en la web sin que nada falle -- se descubre mirando el sitio.
    """
    with pytest.raises(seasons.RangoDeTemporadasInvalido):
        seasons.validar(minimo=1910, per_pitch=1989, maximo=1950)

    with pytest.raises(seasons.RangoDeTemporadasInvalido):
        seasons.validar(minimo=1999, per_pitch=1989, maximo=2025)


def test_el_rango_valido_no_protesta():
    seasons.validar(minimo=1910, per_pitch=1989, maximo=2025)


# ---------------------------------------------------------------------------
# El recorte
# ---------------------------------------------------------------------------

def _df(anios):
    return pd.DataFrame({"season": anios, "x": range(len(anios))})


def test_el_recorte_quita_por_los_dos_lados():
    df, fuera = seasons.recortar(_df([1909, 1910, 2025, 2026]))

    assert df["season"].tolist() == [1910, 2025]
    assert fuera == 2


def test_el_recorte_dice_cuantas_tiro():
    """El contador es lo que hace que el aviso del log sea accionable."""
    _, fuera = seasons.recortar(_df([2026, 2026, 2027]))

    assert fuera == 3


def test_el_recorte_no_copia_si_no_hace_falta():
    """Estos DataFrames son grandes; copiar sin necesidad cuesta memoria."""
    original = _df([1910, 2000, 2025])

    df, fuera = seasons.recortar(original)

    assert fuera == 0
    assert df is original


def test_sin_columna_de_temporada_no_revienta():
    """
    Hay datasets sin temporada -- el catalogo de innings, los tipos de evento.
    Tienen que pasar de largo, no fallar.
    """
    df = pd.DataFrame({"otra": [1, 2]})

    fuera_df, fuera = seasons.recortar(df)

    assert fuera == 0
    assert fuera_df is df


def test_el_suelo_de_conteo_es_mas_estricto():
    df, _ = seasons.recortar(_df([1910, 1988, 1989, 2025]), por_lanzamiento=True)

    assert df["season"].tolist() == [1989, 2025]


# ---------------------------------------------------------------------------
# Que ninguna consulta se quede sin acotar
# ---------------------------------------------------------------------------

def test_todos_los_datasets_pasan_por_el_recorte():
    """
    La garantia no es que cada consulta lleve su WHERE --eso se olvida--, sino
    que TODOS los datasets con temporada pasan por el filtro central de
    _extract_one. Si alguno declarara season_col=None, se saltaria el rango.
    """
    sin_temporada = {"innings", "re24_event_types", "ballparks"}

    for d in extract.DATASETS:
        if d.key in sin_temporada:
            continue
        assert d.season_col, f"{d.key} no declara season_col: se saltaria el rango"


def test_los_datasets_de_conteo_estan_marcados():
    """
    RE288 es la matriz por conteo: sin el marcado le tocaria el suelo de 1910 y
    publicaria dos decadas de celdas vacias.
    """
    marcados = {d.key for d in extract.DATASETS if d.por_lanzamiento}

    assert marcados == {"re288_values", "re288_frequencies", "re288_transitions"}


def test_las_consultas_no_dejan_marcadores_sin_resolver():
    """
    Un `{SEASON_MAX}` que llegue a SQL sin sustituir es un error de sintaxis en
    plena extraccion anual.
    """
    for d in extract.DATASETS:
        resuelta = extract._resolver_temporadas(d.query)
        assert "{SEASON" not in resuelta, f"{d.key} deja marcadores sin resolver"


def test_los_marcadores_se_sustituyen_por_el_valor_vigente():
    seasons.MAX_SEASON = 2024
    seasons.MIN_SEASON = 1920

    sql = extract._resolver_temporadas(
        "WHERE season BETWEEN {SEASON_MIN} AND {SEASON_MAX} "
        "OR season BETWEEN {SEASON_PP_MIN} AND {SEASON_MAX}"
    )

    assert sql == "WHERE season BETWEEN 1920 AND 2024 OR season BETWEEN 1989 AND 2024"


def test_ninguna_consulta_deriva_ya_el_suelo_de_la_base():
    """
    Antes cuatro consultas hacian `>= (SELECT MIN(season) FROM retroGamesReg)`.
    Parecia elegante y era fragil: el rango publicado cambiaba solo en cuanto
    alguien cargara temporadas nuevas, sin tocar una linea de codigo y sin que
    nadie se enterara.
    """
    import re

    # El patron exacto, no la mencion de la tabla: `ballparks` la nombra en un
    # comentario y eso no es derivar nada.
    derivar = re.compile(r"\(\s*select\s+min\s*\(\s*season\s*\)[^)]*from\s+dbo\.retroGamesReg",
                         re.IGNORECASE | re.DOTALL)

    for d in extract.DATASETS:
        assert not derivar.search(d.query), (
            f"{d.key} sigue derivando el suelo de dbo.retroGamesReg"
        )


def test_lo_que_se_alinea_con_la_matriz_lleva_ademas_el_rango():
    """
    `events_counts` si mira `MIN(season) FROM r24Values`, y eso es correcto: lo
    que hace es alinearse con el rango de la PROPIA matriz para no contar
    eventos de temporadas que la matriz no cubre. Pero eso no le exime del
    rango: los dos filtros tienen que estar.
    """
    q = next(d.query for d in extract.DATASETS if d.key == "events_counts")

    assert "r24_min" in q, "perdio la alineacion con la matriz"
    assert "{SEASON_MIN}" in q and "{SEASON_MAX}" in q, "no lleva el rango encima"


def test_el_tope_de_swing_take_ya_no_sale_del_parquet():
    """
    Era `season_cap=max(seasons)` leido del parquet de re24. Mismo problema:
    dato mandando sobre configuracion.
    """
    import ast
    import inspect

    fuente = inspect.getsource(be.main)

    assert "seasons.MAX_SEASON" in fuente
    assert "season_cap=max(" not in fuente

    # Y que el modulo no este TAPADO por una local con su mismo nombre.
    #
    # Comprobar solo el texto no basta, y esto costo un pipeline: `main()` tenia
    # una local `seasons = sorted(re24["season"].unique())` que ocultaba al
    # modulo, asi que `seasons.MAX_SEASON` --presente en el texto, y por eso el
    # test pasaba-- reventaba en ejecucion con "'list' object has no attribute
    # 'MAX_SEASON'", a los 20 minutos de consultas.
    arbol = ast.parse(inspect.getsource(be)).body
    funcion = next(n for n in arbol if isinstance(n, ast.FunctionDef) and n.name == "main")
    tapadas = [
        t.id
        for nodo in ast.walk(funcion)
        if isinstance(nodo, ast.Assign)
        for t in nodo.targets
        if isinstance(t, ast.Name) and t.id == "seasons"
    ]

    assert not tapadas, "main() asigna una local llamada `seasons` y tapa al modulo"


# ---------------------------------------------------------------------------
# La linea de comandos
# ---------------------------------------------------------------------------

def test_los_tres_parametros_se_pueden_pasar_por_linea_de_comandos():
    cli.setup_logging(quiet=True)
    args = cli.base_parser("t").parse_args(
        ["--min-season", "1920", "--min-per-pitch-season", "1995", "--max-season", "2024"]
    )

    cli.aplicar_temporadas(args)

    assert seasons.rango() == (1920, 2024)
    assert seasons.rango(True) == (1995, 2024)


def test_sin_argumentos_se_quedan_los_valores_del_entorno():
    cli.setup_logging(quiet=True)
    previo = seasons.rango()

    cli.aplicar_temporadas(cli.base_parser("t").parse_args([]))

    assert seasons.rango() == previo


def test_la_linea_de_comandos_valida_antes_de_tocar_la_base():
    """
    Validar despues de conectar significa descubrir el error a los veinte
    minutos de consultas.
    """
    cli.setup_logging(quiet=True)
    args = cli.base_parser("t").parse_args(["--max-season", "1950"])

    with pytest.raises(seasons.RangoDeTemporadasInvalido):
        cli.aplicar_temporadas(args)


# ---------------------------------------------------------------------------
# Que no vuelvan a aparecer anios escritos a mano
#
# seasons.py es la unica fuente de verdad. La app se despliega sola --el
# Dockerfile solo copia app/-- asi que NO puede importarlo: lo que hace es
# derivar del dato, que el pipeline ya acota. Eso es mejor que duplicar el
# numero, porque no hay dos sitios que puedan discrepar.
# ---------------------------------------------------------------------------

import pathlib
import re as _re

RAIZ = pathlib.Path(__file__).resolve().parent.parent

# Los anios que son configuracion y solo pueden vivir en seasons.py.
_ANIOS = _re.compile(r"(?<![\d.])(1910|1989|1901|2025)(?![\d.])")


def _sin_comentarios_ni_cadenas_de_doc(texto: str, estilo: str) -> str:
    """
    Quita comentarios y docstrings. Lo que se persigue son valores EFECTIVOS;
    un comentario que dice "desde 1989 porque la cobertura llega al 95.8%" es
    documentacion util y tiene que poder quedarse.
    """
    if estilo == "py":
        texto = _re.sub(r'(?s)"""..*?"""', " ", texto)
        texto = _re.sub(r"(?m)#.*$", " ", texto)
    else:  # js / html
        texto = _re.sub(r"(?s)/\*.*?\*/", " ", texto)
        texto = _re.sub(r"(?m)//.*$", " ", texto)
        texto = _re.sub(r"(?s)<!--.*?-->", " ", texto)
    return texto


def test_la_plantilla_no_lleva_ningun_anio_a_mano():
    """
    Los rangos de las meta tags iban escritos --"1910-2025" en cuatro sitios--
    mientras el texto visible ya salia de {{ min_season }}. Dos versiones del
    mismo dato, y la que ven los buscadores era la que se quedaba atras.
    """
    html = (RAIZ / "app" / "templates" / "index.html").read_text(encoding="utf-8")

    encontrados = _ANIOS.findall(_sin_comentarios_ni_cadenas_de_doc(html, "html"))

    assert not encontrados, f"anios a mano en la plantilla: {sorted(set(encontrados))}"


def test_el_frontend_solo_lleva_esos_anios_como_respaldo():
    """
    RE288_MIN_SEASON estaba escrito en app.js: una tercera copia de un numero
    que decide el pipeline. Ahora llega inyectado; el literal solo puede
    aparecer como respaldo por si la plantilla no lo manda.
    """
    js = (RAIZ / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    codigo = _sin_comentarios_ni_cadenas_de_doc(js, "js")

    for linea in codigo.splitlines():
        if not _ANIOS.search(linea):
            continue
        assert "??" in linea, f"anio a mano y sin respaldo inyectado: {linea.strip()}"


def test_los_scripts_del_pipeline_no_repiten_los_valores():
    """
    Fuera de seasons.py, ningun script puede llevar los anios en codigo. Habia
    un `== 2025` en el resumen final de create_break_even_points que habria
    seguido mirando 2025 para siempre.
    """
    for py in sorted((RAIZ / "utils").glob("*.py")):
        if py.name == "seasons.py":  # la fuente de verdad
            continue
        codigo = _sin_comentarios_ni_cadenas_de_doc(py.read_text(encoding="utf-8"), "py")
        encontrados = _ANIOS.findall(codigo)
        assert not encontrados, f"{py.name} lleva {sorted(set(encontrados))} en codigo"


def test_la_app_deriva_el_suelo_de_re288_del_parquet():
    """
    app/config.py conserva el 1989 SOLO como respaldo si falta el parquet. El
    valor efectivo sale del dato, que el pipeline ya acota con
    MIN_PER_PITCH_SEASON.

    OJO CON EL ARRANQUE: hay que entrar en el lifespan (el `with TestClient`).
    Sin el, data_store esta vacio y _suelo_re288() cae al respaldo -- el test
    pasaria por el camino equivocado y no probaria la derivacion.
    """
    import pandas as pd
    from fastapi.testclient import TestClient

    from app import main as app_main

    esperado = int(
        pd.read_parquet(RAIZ / "app" / "data" / "re288_values.parquet", columns=["season"])["season"].min()
    )

    with TestClient(app_main.app):
        derivado = app_main._suelo_re288()

    assert derivado == esperado
    assert esperado == seasons.MIN_PER_PITCH_SEASON, (
        "el parquet no cuadra con MIN_PER_PITCH_SEASON: hay que rehacer el pipeline"
    )


def test_el_suelo_de_re288_no_es_el_respaldo_de_config():
    """
    Control del anterior: si config.py dijera otra cosa que el parquet, tiene
    que ganar el parquet. Es lo que hace que el numero de config sea un respaldo
    y no una segunda fuente de verdad.
    """
    from fastapi.testclient import TestClient

    from app import config
    from app import main as app_main

    previo = config.RE288_MIN_SEASON
    try:
        app_main.RE288_MIN_SEASON = previo + 40
        with TestClient(app_main.app):
            assert app_main._suelo_re288() == seasons.MIN_PER_PITCH_SEASON
    finally:
        app_main.RE288_MIN_SEASON = previo


def test_el_barrido_del_repo_solo_deja_las_excepciones_documentadas():
    """
    Barrido final: fuera de seasons.py no puede quedar ningun anio de
    configuracion en codigo efectivo. Solo se admiten tres excepciones, y cada
    una por un motivo escrito:

    - `app/config.py`: respaldo si falta el parquet; nunca le gana al derivado,
      y eso lo comprueba otro test de este mismo fichero.
    - `_stretch` y `_advance`: arrancan en 1989 por cobertura de localizacion del
      batazo --95.8% ese anio frente al 89.9% de 1988 y el 45% de 1987--, NO por
      el suelo de conteo. Coincide el anio y no el motivo, asi que no deben
      seguir a MIN_PER_PITCH_SEASON si ese parametro se mueve.

    Si aparece una cuarta, o es un descuido o hace falta escribir por que.
    """
    permitidas = {
        pathlib.Path("app/config.py"),
        pathlib.Path("utils/sql/16_v_re_analysis_stretch.sql"),
        pathlib.Path("utils/sql/17_v_re_analysis_advance.sql"),
    }

    def limpiar(texto, estilo):
        if estilo == "py":
            texto = _re.sub(r'(?s)"""..*?"""', " ", texto)
            return _re.sub(r"(?m)#.*$", " ", texto)
        if estilo == "sql":
            return _re.sub(r"(?m)--.*$", " ", texto)
        texto = _re.sub(r"(?s)/\*.*?\*/", " ", texto)
        texto = _re.sub(r"(?m)//.*$", " ", texto)
        return _re.sub(r"(?s)<!--.*?-->", " ", texto)

    culpables = set()
    for patron, estilo in (("app/**/*.py", "py"), ("utils/*.py", "py"),
                           ("app/**/*.js", "js"), ("app/templates/*.html", "js"),
                           ("utils/sql/*.sql", "sql")):
        for f in RAIZ.glob(patron):
            rel = f.relative_to(RAIZ)
            if "_not_in_use" in str(rel) or f.name == "seasons.py" or rel in permitidas:
                continue
            for linea in limpiar(f.read_text(encoding="utf-8", errors="ignore"), estilo).splitlines():
                # `??` marca un respaldo inyectado desde el servidor.
                if _ANIOS.search(linea) and "??" not in linea:
                    culpables.add(f"{rel}: {linea.strip()[:70]}")

    detalle = "\n".join(sorted(culpables))
    assert not culpables, f"anios de configuracion a mano:\n{detalle}"


def test_ninguna_funcion_tapa_el_modulo_seasons_y_lo_usa_a_la_vez():
    """
    Barrido general del patron que tumbó el pipeline anual.

    `seasons` es a la vez el nombre del módulo y un nombre natural para una lista
    de temporadas. Cuando una función hace `seasons = sorted(...)` **y** más abajo
    usa `seasons.MAX_SEASON`, el módulo queda tapado y revienta en ejecución con
    `'list' object has no attribute 'MAX_SEASON'` — no al importar, sino en mitad
    del trabajo, después de veinte minutos de consultas.

    Tapar el módulo sin usarlo es inofensivo y hay siete casos así (parámetros
    llamados `seasons` en funciones que no lo necesitan). Lo que no puede pasar es
    tapar Y usar.
    """
    import ast

    culpables = []
    for f in sorted((RAIZ / "utils").glob("*.py")):
        arbol = ast.parse(f.read_text(encoding="utf-8"))
        importado = any(
            isinstance(n, ast.Import) and any(a.name == "seasons" and a.asname is None for a in n.names)
            for n in ast.walk(arbol)
        )
        if not importado:
            continue

        for fn in (n for n in ast.walk(arbol) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
            tapa = [
                t.id for nodo in ast.walk(fn) if isinstance(nodo, ast.Assign)
                for t in nodo.targets if isinstance(t, ast.Name) and t.id == "seasons"
            ] + [a.arg for a in fn.args.args if a.arg == "seasons"]
            usa = [
                nodo.attr for nodo in ast.walk(fn)
                if isinstance(nodo, ast.Attribute)
                and isinstance(nodo.value, ast.Name)
                and nodo.value.id == "seasons"
            ]
            if tapa and usa:
                culpables.append(f"{f.name}:{fn.lineno} {fn.name}() tapa seasons y usa {sorted(set(usa))}")

    detalle = "\n".join(culpables)
    assert not culpables, f"el modulo seasons queda tapado donde se usa:\n{detalle}"


# ---------------------------------------------------------------------------
# El fichero de log
# ---------------------------------------------------------------------------

def test_el_log_a_fichero_se_escribe_y_lleva_marca_de_tiempo(tmp_path):
    """
    El pipeline tarda minutos y se lanza con --log-file para poder revisarlo
    despues. Sin la marca de tiempo no se sabe que paso tardo.
    """
    destino = tmp_path / "pipeline.log"

    log = cli.setup_logging(log_file=str(destino))
    log.info("paso uno")

    for h in log.handlers:
        h.flush()

    contenido = destino.read_text(encoding="utf-8")
    assert "paso uno" in contenido
    assert "INFO" in contenido
    assert "2" in contenido.split("INFO")[0], "falta la fecha delante del nivel"


def test_el_log_crea_el_directorio_que_falte(tmp_path):
    """
    `--log-file logs/pipeline-2026.log` con logs/ inexistente reventaria al
    arrancar, despues de haber escrito el comando y antes de hacer nada.
    """
    destino = tmp_path / "sin" / "crear" / "p.log"

    cli.setup_logging(log_file=str(destino))

    assert destino.exists()


def test_cada_ejecucion_empieza_un_fichero_limpio(tmp_path):
    """
    Se abre en modo 'w': un log que acumula ejecuciones hace que revisar la
    ultima sea buscar donde empieza.
    """
    destino = tmp_path / "p.log"

    log = cli.setup_logging(log_file=str(destino))
    log.info("ejecucion vieja")
    for h in log.handlers:
        h.flush()

    log = cli.setup_logging(log_file=str(destino))
    log.info("ejecucion nueva")
    for h in log.handlers:
        h.flush()

    contenido = destino.read_text(encoding="utf-8")
    assert "ejecucion nueva" in contenido
    assert "ejecucion vieja" not in contenido
