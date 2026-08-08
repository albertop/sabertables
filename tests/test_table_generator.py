"""
Generacion de tablas, contra los parquets reales.

Sin mocks: estas funciones son las que arman lo que ve el usuario, asi que se
llaman con los datos publicados. Lo que se cubre aqui son los caminos que la
suite no pisaba: los rangos de VARIAS temporadas --que activan la media
ponderada, una rama distinta a la de temporada suelta-- y las rutas de error.
"""
import pandas as pd
import pytest
from app.services import table_generator as tg
from fastapi.testclient import TestClient

# Los datasets que usan estos tests. Hay que pedirlos EXPLICITAMENTE: la
# aplicacion los carga de forma perezosa, en la primera peticion que los
# necesita, asi que arrancarla no basta -- quedan a None y todo falla por la
# razon equivocada.
DATASETS = (
    "re24", "re288", "re288_transitions", "re24_percentages", "re24_transitions",
    "re24_event_types", "win_expectancy", "park_factors", "ballparks",
    "linear_weights", "r24_frequencies", "re288_frequencies",
)


@pytest.fixture
def datos():
    """
    Arranca la aplicacion y fuerza la carga de lo que hace falta.

    El modulo se importa DENTRO y en cada test, no una vez arriba: otros
    ficheros de la suite recargan app.main para probar el modo produccion, y eso
    crea un data_store nuevo. Una referencia capturada al importar se queda
    apuntando al viejo, y los tests pasan en solitario y fallan en la suite --
    que es exactamente lo que ocurrio.
    """
    import app.main as m

    with TestClient(m.app):
        for nombre in DATASETS:
            m.ensure_data_loaded(nombre)
        yield m.data_store


@pytest.fixture
def temporadas(datos):
    lo = int(datos["seasons"]["min_season"].iloc[0])
    hi = int(datos["seasons"]["max_season"].iloc[0])
    return lo, hi


# ---------------------------------------------------------------------------
# La media ponderada
# ---------------------------------------------------------------------------

def _celdas():
    return pd.DataFrame({
        "season": [2023, 2024, 2023, 2024],
        "runners": ["100", "100", "010", "010"],
        "run_value": [0.80, 0.90, 1.00, 1.20],
        "n": [100, 300, 50, 50],
    })


def test_con_una_sola_temporada_el_valor_va_tal_cual():
    """Promediar un solo dato consigo mismo solo anade oportunidades de error."""
    df = _celdas()
    df = df[df.season == 2024]

    salida = tg.calculate_weighted_mean(df, 2024, 2024, "run_value", ["runners"])

    assert sorted(salida["run_value"]) == [0.90, 1.20]


def test_con_varias_temporadas_se_pondera_por_n_y_no_se_promedia_a_secas():
    """
    Una temporada con 300 eventos no vale lo mismo que una con 100. El promedio
    simple de 0.80 y 0.90 seria 0.85; el ponderado es 0.875.
    """
    salida = tg.calculate_weighted_mean(_celdas(), 2023, 2024, "run_value", ["runners"])
    fila = salida[salida.runners == "100"].iloc[0]

    assert fila["run_value"] == pytest.approx((0.80 * 100 + 0.90 * 300) / 400)
    assert fila["run_value"] != pytest.approx(0.85), "eso seria el promedio simple"


def test_la_media_ponderada_no_ensucia_el_dataframe_de_quien_llama():
    """
    Sin la copia, esta funcion pegaba la columna weighted_value al DataFrame del
    llamante. Hoy no rompe porque los siete llamantes reciben el suyo ya copiado,
    pero basta con que alguien pase uno de data_store para que se quede pegada a
    la cache.
    """
    df = _celdas()
    columnas_antes = list(df.columns)

    tg.calculate_weighted_mean(df, 2023, 2024, "run_value", ["runners"])

    assert list(df.columns) == columnas_antes


def test_las_columnas_de_orden_sobreviven_a_la_agregacion():
    """
    bucket_sort_order y sort_order_count deciden el orden de las filas en la
    tabla. Si se pierden al agregar, la tabla sale desordenada -- que es un fallo
    que se ve pero no da error.
    """
    df = pd.DataFrame({
        "season": [2024], "runners": ["100"], "run_value": [0.8], "n": [10],
        "bucket_sort_order": [3], "sort_order_count": [7],
    })

    salida = tg.calculate_weighted_mean(df, 2024, 2024, "run_value", ["runners"])

    assert "bucket_sort_order" in salida.columns
    assert "sort_order_count" in salida.columns


# ---------------------------------------------------------------------------
# Rangos de varias temporadas en las tablas por entrada
# ---------------------------------------------------------------------------

def test_wpa_pondera_al_pedir_un_rango(datos, temporadas):
    """
    WPA de un rango es la media ponderada por n, no la suma ni la ultima
    temporada. Es una rama distinta de la de temporada suelta.
    """
    _, hi = temporadas

    rango = tg.generate_win_probability_added_table(datos, hi - 3, hi, 1)
    suelta = tg.generate_win_probability_added_table(datos, hi, hi, 1)

    assert rango["rows"] and suelta["rows"]
    assert rango["headers"] == suelta["headers"]
    assert rango["total_events"] > suelta["total_events"]


def test_leverage_index_pondera_al_pedir_un_rango(datos, temporadas):
    _, hi = temporadas

    rango = tg.generate_leverage_index_table(datos, hi - 3, hi, 0)

    assert rango["rows"]
    assert rango["total_events"] > 0


def test_win_expectancy_pondera_al_pedir_un_rango(datos, temporadas):
    _, hi = temporadas

    rango = tg.generate_win_expectancy_table(datos, hi - 3, hi, 1)

    assert rango["rows"]
    assert "Tie" in rango["headers"]


@pytest.mark.parametrize("equipo,nombre", [(1, "Home"), (0, "Away")])
def test_las_tablas_por_entrada_distinguen_local_de_visitante(datos, temporadas, equipo, nombre):
    """
    Mezclar los dos equipos en una sola tabla fue un fallo real: la ventaja de
    batear ultimo hace que sus curvas sean distintas.
    """
    _, hi = temporadas

    tabla = tg.generate_win_expectancy_table(datos, hi, hi, equipo)

    assert tabla["team_name"] == nombre


# ---------------------------------------------------------------------------
# Las tablas con parametro
# ---------------------------------------------------------------------------

def test_re24_filtra_por_tipo_de_evento(datos, temporadas):
    _, hi = temporadas

    todos = tg.generate_re24_table(datos, hi, hi, event_cd=-1)
    jonron = tg.generate_re24_table(datos, hi, hi, event_cd=23)

    assert todos["rows"] and jonron["rows"]
    assert todos["rows"] != jonron["rows"]


def test_re24_probabilities_filtra_por_entrada(datos, temporadas):
    _, hi = temporadas

    todas = tg.generate_re24_probabilities_table(datos, hi, hi, inning=-1)
    primera = tg.generate_re24_probabilities_table(datos, hi, hi, inning=1)

    assert todas["rows"] and primera["rows"]


def test_re288_transitions_pondera_al_pedir_un_rango(datos, temporadas):
    _, hi = temporadas

    tabla = tg.generate_re288_transitions_table(datos, hi - 2, hi, "S", 1)

    assert tabla["rows"]


def test_re24_transitions_admite_los_tres_tipos_de_valor(datos, temporadas):
    """
    Los tipos son 1, 2 y 3 --delta de RE, probabilidad y el producto--, no 0, 1
    y 2. El `else` final del generador asume el 3, asi que un valor fuera de
    rango daria KeyError; no llega nunca porque el modelo lo valida con
    ge=1, le=3.
    """
    _, hi = temporadas
    vistos = []

    for tipo in (1, 2, 3):
        tabla = tg.generate_re24_transitions_table(datos, hi, hi, 0, -1, tipo)
        vistos.append(tabla["rows"][0] if tabla["rows"] else None)

    assert all(v is not None for v in vistos)
    assert len({str(v) for v in vistos}) == 3, "los tres tipos deberian dar tablas distintas"


def test_park_factors_admite_las_tres_ventanas(datos, temporadas):
    _, hi = temporadas

    for ventana in (1, 3, 5):
        tabla = tg.generate_park_factors_table(datos, hi, ventana)
        assert tabla["rows"], f"sin filas con average={ventana}"


def test_linear_weights_con_y_sin_filtro_de_outs(datos, temporadas):
    _, hi = temporadas

    sin_filtro = tg.generate_linear_weights_table(datos, hi, hi)
    con_filtro = tg.generate_linear_weights_table(datos, hi, hi, outs=1)

    assert sin_filtro["rows"] and con_filtro["rows"]


# ---------------------------------------------------------------------------
# Rutas de error
# ---------------------------------------------------------------------------

def test_un_rango_sin_datos_da_un_error_que_dice_el_rango(datos):
    """
    Sin el rango en el mensaje, un 400 en la web no dice si el problema es la
    temporada pedida o que falta el parquet.
    """
    with pytest.raises(ValueError, match="1850"):
        tg.generate_re24_table(datos, 1850, 1851)


def test_un_tipo_de_evento_inexistente_no_devuelve_una_tabla_vacia(datos, temporadas):
    """
    Una tabla vacia se pinta en blanco y parece que no hubo jugadas. Un error
    dice que el filtro no existe.
    """
    _, hi = temporadas

    with pytest.raises(ValueError):
        tg.generate_re24_table(datos, hi, hi, event_cd=999)


def test_una_entrada_inexistente_tampoco(datos, temporadas):
    _, hi = temporadas

    with pytest.raises(ValueError):
        tg.generate_re24_probabilities_table(datos, hi, hi, inning=99)


def test_una_temporada_sin_park_factors_da_error(datos):
    with pytest.raises(ValueError, match="1850"):
        tg.generate_park_factors_table(datos, 1850, 1)


def test_un_tipo_de_transicion_que_no_existe_da_error(datos, temporadas):
    _, hi = temporadas

    with pytest.raises(ValueError):
        tg.generate_re288_transitions_table(datos, hi, hi, "no_existe", 1)


def test_re288_antes_de_1989_no_tiene_datos(datos):
    """
    El detalle de lanzamiento no existe antes de 1989. La app lo bloquea antes,
    pero el generador tambien debe negarse.
    """
    with pytest.raises(ValueError):
        tg.generate_re288_table(datos, 1950, 1950)


def test_falta_una_columna_obligatoria_y_se_dice_cual(datos, temporadas):
    """
    filter_and_validate_data es la puerta comun de todas las tablas.

    Con una columna que no esta lanza KeyError crudo, no un ValueError con
    mensaje. Se deja asi a proposito: las columnas exigidas las escriben los
    llamantes, no llegan del usuario, y un parquet al que le falte una la caza
    antes validate_dataframe_schema al arrancar. Este camino es defensa en
    profundidad y en la practica no se alcanza.
    """
    _, hi = temporadas

    with pytest.raises(KeyError, match="columna_que_no_existe"):
        tg.filter_and_validate_data(
            datos, "re24", hi, hi, ["columna_que_no_existe"], "RE24"
        )


# ---------------------------------------------------------------------------
# Rutas de error de las tablas por entrada
#
# Todas dicen si el equipo era local o visitante. Sin eso, un 400 en la web no
# distingue "no hay datos de esa temporada" de "no hay datos de ese equipo".
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("generador", [
    "generate_win_expectancy_table",
    "generate_win_probability_added_table",
    "generate_leverage_index_table",
])
@pytest.mark.parametrize("equipo,nombre", [(1, "Home"), (0, "Away")])
def test_sin_datos_el_error_dice_el_equipo_y_el_rango(datos, generador, equipo, nombre):
    with pytest.raises(ValueError, match=f"{nombre}.*1850-1851"):
        getattr(tg, generador)(datos, 1850, 1851, equipo)


def test_si_falta_la_fila_agregada_de_re24_se_dice_asi(datos):
    """
    event_cd=-1 es la fila YA AGREGADA de todos los eventos, que calcula el
    pipeline. Si faltara, la tabla principal del sitio saldria vacia; el mensaje
    tiene que distinguir eso de "no hay datos de esa temporada".

    Hace falta un data_store sintetico: en los parquets reales el -1 esta
    siempre, asi que por ahi este camino no se alcanza nunca.
    """
    sin_agregado = dict(datos)
    df = datos["re24"]
    sin_agregado["re24"] = df[df["event_cd"] != -1].copy()

    # SIN pasar event_cd: ese es el camino que cae al -1 por defecto y el que
    # da el mensaje con "All Events". Pidiendolo explicitamente se llega a otro.
    temporada = int(sin_agregado["re24"]["season"].max())
    with pytest.raises(ValueError, match="All Events"):
        tg.generate_re24_table(sin_agregado, temporada, temporada)


def test_si_falta_la_fila_agregada_de_entradas_se_dice_asi(datos):
    """Mismo caso con inning=-1, la fila de todas las entradas."""
    sin_agregado = dict(datos)
    df = datos["re24_percentages"]
    sin_agregado["re24_percentages"] = df[df["inning"] != -1].copy()

    temporada = int(sin_agregado["re24_percentages"]["season"].max())
    with pytest.raises(ValueError, match="All Innings"):
        tg.generate_re24_probabilities_table(sin_agregado, temporada, temporada)


def test_sin_datos_las_frecuencias_re24_lo_dicen(datos):
    with pytest.raises(ValueError, match="1850-1851"):
        tg.generate_r24_frequencies_table(datos, 1850, 1851)


def test_sin_datos_las_frecuencias_re288_lo_dicen(datos):
    with pytest.raises(ValueError, match="1850-1851"):
        tg.generate_re288_frequencies_table(datos, 1850, 1851)


def test_sin_datos_las_transiciones_re24_dicen_los_filtros(datos, temporadas):
    """El mensaje lleva from_outs y event_cd: son los dos que pudo elegir mal."""
    _, hi = temporadas

    with pytest.raises(ValueError, match="from_outs=2"):
        tg.generate_re24_transitions_table(datos, 1850, 1851, 2, -1, 1)


# ---------------------------------------------------------------------------
# Datasets ausentes
# ---------------------------------------------------------------------------

def test_sin_park_factors_cargados_se_dice_cual_falta(datos):
    """
    Distinguir "falta el parquet" de "no hay datos de esa temporada" es la
    diferencia entre revisar el despliegue y revisar el selector.
    """
    incompleto = dict(datos)
    incompleto["park_factors"] = None

    with pytest.raises(ValueError, match="Park factors data not available"):
        tg.generate_park_factors_table(incompleto, 2024, 1)


def test_sin_ballparks_cargados_se_dice_cual_falta(datos):
    incompleto = dict(datos)
    incompleto["ballparks"] = None

    with pytest.raises(ValueError, match="Ballparks data not available"):
        tg.generate_park_factors_table(incompleto, 2024, 1)


# ---------------------------------------------------------------------------
# Ramas que solo aparecen con varias temporadas
# ---------------------------------------------------------------------------

def test_las_transiciones_re24_ponderan_al_pedir_un_rango(datos, temporadas):
    _, hi = temporadas

    rango = tg.generate_re24_transitions_table(datos, hi - 2, hi, 0, -1, 1)

    assert rango["rows"]


def test_las_transiciones_re288_calculan_la_re_esperada(datos, temporadas):
    """
    El modo 2 multiplica probabilidad por valor: es el que dice cuanto aporta de
    verdad una transicion, no solo cuanto vale cuando ocurre.
    """
    _, hi = temporadas

    modo_1 = tg.generate_re288_transitions_table(datos, hi, hi, "S", 1)
    modo_2 = tg.generate_re288_transitions_table(datos, hi, hi, "S", 2)

    assert modo_1["rows"] and modo_2["rows"]
    assert modo_1["rows"] != modo_2["rows"]


def test_las_frecuencias_re24_agregan_todos_los_eventos(datos, temporadas):
    """
    Aqui el filtro va sobre EVENT_CD en mayusculas, que es una columna distinta
    de la `event_cd` del resto de tablas.
    """
    _, hi = temporadas

    tabla = tg.generate_r24_frequencies_table(datos, hi, hi)

    assert tabla["rows"]


def test_el_orden_de_los_cubos_sobrevive_a_la_media_ponderada():
    """
    Con varias temporadas hay que arrastrar bucket_sort_order por la agregacion.
    Si se pierde, las filas salen alfabeticas en vez de en su orden logico.
    """
    df = pd.DataFrame({
        "season": [2023, 2024], "bucket": ["0-1", "0-1"],
        "percentage": [10.0, 20.0], "n": [100, 100], "bucket_sort_order": [2, 2],
    })

    salida = tg.calculate_weighted_mean(df, 2023, 2024, "percentage", ["bucket"])

    assert "bucket_sort_order" in salida.columns
    assert salida["bucket_sort_order"].iloc[0] == 2


def test_las_transiciones_re288_tienen_un_tercer_modo(datos, temporadas):
    """
    El modo 3 es el producto probabilidad x valor. Los tres modos responden a
    preguntas distintas: cuanto vale, cuanto pasa, y cuanto aporta.
    """
    _, hi = temporadas

    modos = [tg.generate_re288_transitions_table(datos, hi, hi, "S", m)["rows"]
             for m in (1, 2, 3)]

    assert all(modos)
    assert len({str(m) for m in modos}) == 3, "los tres modos deberian diferir"


def test_sin_datos_los_pesos_lineales_lo_dicen(datos):
    with pytest.raises(ValueError, match="1850-1851"):
        tg.generate_linear_weights_table(datos, 1850, 1851)


def test_re24_rellena_las_columnas_de_outs_que_falten():
    """
    La tabla siempre ensena tres columnas: 0, 1 y 2 outs. Si faltara alguna,
    debe salir vacia y no desaparecer -- una tabla con dos columnas donde
    siempre hay tres se lee como otra cosa.

    Y tiene que rellenarse en los DOS cuadros: el de valores y el de tamanos de
    muestra. Si solo se rellenara uno, las cabeceras y los marcadores de
    fiabilidad se desalinearian.

    Con un data_store minimo y no con el parquet real, porque este solo trae
    celdas para los tres estados de out.
    """
    df = pd.DataFrame({
        "season": [2024] * 4,
        "runners": ["000", "000", "100", "100"],
        "outs": pd.array([0, 1, 0, 1], dtype="uint8"),
        "run_value": [0.5, 0.3, 0.9, 0.6],
        "n": [10, 10, 10, 10],
        "event_cd": [-1] * 4,
    })

    tabla = tg.generate_re24_table({"re24": df}, 2024, 2024)
    fila = tabla["rows"][0]

    assert tabla["headers"] == ["Runners", "0 Outs", "1 Out", "2 Outs"]
    assert fila["out_2"] is None, "la celda de valor tiene que quedar vacia"
    assert fila["out_2_n"] is None, "y tambien la de tamano de muestra"


def test_los_pesos_lineales_usan_las_filas_agregadas_si_existen(datos):
    """
    Algunos datasets traen filas de resumen con EVENT_CD = -1 y otros solo
    eventos sueltos. Mezclarlas contaria dos veces. El parquet de hoy no las
    tiene, asi que hace falta una sintetica para probar esa rama.
    """
    # El parquet trae SEASON en mayusculas; el renombrado a minusculas ocurre
    # dentro del generador.
    df = datos["linear_weights"].copy()
    temporada = int(df["SEASON"].max())
    fila = df[df["SEASON"] == temporada].iloc[0].copy()
    fila["EVENT_CD"] = -1
    con_resumen = dict(datos)
    con_resumen["linear_weights"] = pd.concat(
        [df, pd.DataFrame([fila])], ignore_index=True
    )

    tabla = tg.generate_linear_weights_table(con_resumen, temporada, temporada)

    assert tabla["rows"]


def test_sin_filas_AVG_el_total_suma_todas(datos):
    """
    El pie de la tabla dice cuantas jugadas la sostienen. Con filas AVG se usan
    esas para no contar dos veces; sin ellas hay que sumar todo, y si no se
    sumara el pie diria cero.
    """
    df = datos["linear_weights"]
    temporada = int(df["SEASON"].max())
    sin_avg = dict(datos)
    sin_avg["linear_weights"] = df[
        (df["SEASON"] == temporada) & (df["RUNNERS"] != "AVG")
    ].copy()

    tabla = tg.generate_linear_weights_table(sin_avg, temporada, temporada)

    assert tabla["total_events"] > 0
