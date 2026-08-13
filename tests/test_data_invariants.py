"""
Invariantes sobre los parquets que generan los scripts de utils/.

Estos tests no comprueban la API: comprueban que los DATOS publicados en
app/data/ tienen sentido sabermetrico. Existen porque el pipeline llego a
servir break-evens de -5,239 y "deltas" de run expectancy inflados 50x sin que
nada lo detectara.

Los parquets estan en .gitignore, asi que en un clon limpio estos tests se
saltan en vez de fallar. Para ejecutarlos hay que generar los datos antes:

    python utils/extract_re_data.py
    python utils/create_break_even_points.py
"""
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pytest

# Por defecto se validan los datos publicados. RE_DATA_DIR permite apuntar a otro
# directorio: sirve para comprobar un lote recien generado en staging ANTES de
# promoverlo a app/data, y para verificar que estos tests siguen detectando un
# lote defectuoso conocido.
DATA_DIR = Path(
    os.environ.get('RE_DATA_DIR')
    or Path(__file__).resolve().parent.parent / 'app' / 'data'
)

# Familias de break-even que el pipeline debe producir. Si una desaparece es una
# regresion, no un dato ausente: por eso se parametriza sobre una lista fija en
# vez de sobre lo que haya en el parquet.
BREAKEVEN_TYPES = [
    'stealing',
    'running',
    'tagging',
    'bunt-for-hit',
    'swing-take',
    'swing_take_zone',
]

# Cota inferior DURA. Un break-even negativo implicaria que el intento mejora la
# run expectancy aun fallando siempre: no tiene lectura sabermetrica y siempre
# es un artefacto de dividir por un denominador ~0 o de signo invertido.
BE_MIN = 0.0

# Cota superior BLANDA, y depende de la familia porque "nunca conviene" es raro
# en unas y habitual en otras:
#
#   - Familias RE24 y sb_by_count: un BE > 1 es marginal pero legitimo (robar 3B
#     con 2 outs, donde RE(2out,001) apenas supera a RE(2out,010)). Medido:
#     las filas fuera de [0,1] estan todas entre 1.00 y 1.19, y el p99 de
#     sb_by_count es 1.25.
#
#   - swing/take: decidir si abanicar FUERA de la zona da BE mediano 1.24-2.55,
#     o sea "no abaniques ni haciendo contacto el 100% de las veces". Es el
#     resultado correcto de disciplina de plato, no un error. Maximo legitimo
#     medido: 9.84.
BE_MAX_DEFAULT = 2.0
BE_MAX_BY_TYPE = {'swing-take': 10.0, 'swing_take_zone': 10.0}

# Debe coincidir con MIN_IBB_N en utils/create_break_even_points.py.
MIN_IBB_N = 30


def _load(filename: str) -> pd.DataFrame:
    path = DATA_DIR / filename
    if not path.exists():
        pytest.fail(f'{filename} no existe. Los parquets estan versionados en git, asi que su ausencia significa que algo se borro o que el pipeline fallo -- no es motivo para dar el test por bueno.')
    return pd.read_parquet(path)


@pytest.fixture(scope='module')
def breakeven():
    return _load('breakeven_points.parquet')


@pytest.fixture(scope='module')
def sb_by_count():
    return _load('sb_breakeven_by_count.parquet')


@pytest.fixture(scope='module')
def re_impact():
    return _load('re_impact.parquet')


@pytest.fixture(scope='module')
def re24_values():
    return _load('re24_values.parquet')


@pytest.fixture(scope='module')
def win_expectancy():
    return _load('win_expectancy.parquet')


@pytest.mark.parametrize('be_type', BREAKEVEN_TYPES)
def test_breakeven_type_esta_presente(breakeven, be_type):
    rows = breakeven[breakeven['type'] == be_type]
    assert len(rows) > 0, f'la familia de break-even "{be_type}" desaparecio del parquet'


@pytest.mark.parametrize('be_type', BREAKEVEN_TYPES)
def test_breakeven_nunca_es_negativo(breakeven, be_type):
    values = breakeven.loc[breakeven['type'] == be_type, 'breakeven_pct'].dropna()
    if values.empty:
        pytest.skip(f'sin filas para {be_type}')

    negatives = values[values < BE_MIN]
    assert negatives.empty, (
        f'{be_type}: {len(negatives)} de {len(values)} break-even negativos '
        f'(minimo {negatives.min():.4f}). Revisa el signo del denominador: para '
        f'swing/take el gap EV_contact - EV_miss debe ser significativamente '
        f'positivo, no solo distinto de cero en valor absoluto.'
    )


@pytest.mark.parametrize('be_type', BREAKEVEN_TYPES)
def test_breakeven_bajo_techo_de_cordura(breakeven, be_type):
    values = breakeven.loc[breakeven['type'] == be_type, 'breakeven_pct'].dropna()
    if values.empty:
        pytest.skip(f'sin filas para {be_type}')

    cap = BE_MAX_BY_TYPE.get(be_type, BE_MAX_DEFAULT)
    over = values[values > cap]
    assert over.empty, (
        f'{be_type}: {len(over)} de {len(values)} break-even por encima de {cap} '
        f'(maximo {over.max():.2f}). Un BE > 1 es legitimo, pero un valor tan alto '
        f'apunta a un denominador degenerado.'
    )


def test_sb_by_count_nunca_es_negativo(sb_by_count):
    values = sb_by_count['breakeven_pct'].dropna()
    negatives = values[values < BE_MIN]
    assert negatives.empty, (
        f'sb_breakeven_by_count: {len(negatives)} break-even negativos '
        f'(minimo {negatives.min():.4f})'
    )


def test_ibb_delta_re_es_un_delta_no_una_re_absoluta(re_impact):
    """
    run_value en las filas de evento de re24_values.parquet es la RE ABSOLUTA
    posterior al evento, no el cambio de RE. Publicarla tal cual como delta_re
    inflaba la cifra hasta 50x y forzaba el signo a ser siempre positivo.

    Un delta real de una base por bolas intencional tiene dos firmas: magnitud
    por debajo de una carrera, y ambos signos (a veces la IBB sale cara, a veces
    le sale bien al equipo defensivo).
    """
    ibb = re_impact[re_impact['type'] == 'ibb']
    if ibb.empty:
        pytest.skip('sin filas ibb')

    deltas = ibb['delta_re'].astype('float64').dropna()

    worst = deltas.abs().max()
    assert worst <= 1.0, (
        f'ibb: |delta_re| llega a {worst:.3f}, por encima de una carrera. '
        f'Sintoma de estar publicando la RE absoluta en vez de '
        f'run_value - RE(estado inicial).'
    )

    assert (deltas < 0).any() and (deltas > 0).any(), (
        'ibb: delta_re tiene un solo signo. Un delta real alterna; una RE '
        'absoluta es siempre positiva.'
    )


def test_ibb_respeta_la_muestra_minima(re_impact):
    ibb = re_impact[re_impact['type'] == 'ibb']
    if ibb.empty:
        pytest.skip('sin filas ibb')

    smallest = int(ibb['n'].min())
    assert smallest >= MIN_IBB_N, (
        f'ibb: hay celdas con n={smallest}, por debajo de MIN_IBB_N={MIN_IBB_N}. '
        f'El promedio de un punado de eventos es ruido puro.'
    )


def test_re_impact_delta_re_tiene_magnitud_plausible(re_impact):
    """Todo delta_re es un cambio de RE por evento; nada deberia acercarse a 2 carreras."""
    deltas = re_impact['delta_re'].astype('float64').dropna()
    worst_idx = deltas.abs().idxmax()
    worst = deltas.loc[worst_idx]
    assert abs(worst) <= 2.0, (
        f'delta_re llega a {worst:.3f} en la fila '
        f'{re_impact.loc[worst_idx, ["type", "season", "scenario", "outs"]].to_dict()}'
    )


def test_win_expectancy_es_una_probabilidad(win_expectancy):
    """
    WE es una probabilidad de ganar: tiene que vivir en [0, 1].

    No habia ningun invariante sobre este parquet pese a que alimenta las tablas
    de win expectancy y leverage index. Rangos reales medidos: WE en
    [0.000002, 0.999977], que es exactamente lo esperable -- ni certeza absoluta
    ni imposibilidad absoluta en ningun estado observado.
    """
    we = win_expectancy['WE'].astype('float64')
    fuera = we[(we < 0.0) | (we > 1.0)]
    assert fuera.empty, (
        f'WE fuera de [0,1] en {len(fuera)} filas '
        f'(min {we.min():.6f}, max {we.max():.6f})'
    )


def test_win_expectancy_li_y_wpa_en_rango_plausible(win_expectancy):
    """
    LI (leverage index) es un cociente de varianzas: nunca negativo.
    WPA es un cambio de probabilidad: acotado a [-1, 1] por construccion.
    """
    li = win_expectancy['LI'].astype('float64')
    assert (li >= 0).all(), f'LI negativo en {int((li < 0).sum())} filas (min {li.min():.4f})'

    wpa = win_expectancy['WPA'].astype('float64')
    fuera = wpa[(wpa < -1.0) | (wpa > 1.0)]
    assert fuera.empty, (
        f'WPA fuera de [-1,1] en {len(fuera)} filas '
        f'(min {wpa.min():.4f}, max {wpa.max():.4f})'
    )

    n = win_expectancy['n'].astype('int64')
    assert (n > 0).all(), f'celdas con n <= 0: {int((n <= 0).sum())}'


def test_datos_derivados_no_estan_obsoletos():
    """
    Las tablas que produce `03` deben corresponder a la matriz RE publicada.

    `create_break_even_points.py` lee `re24_values.parquet` y
    `re288_values.parquet` (que produce `01`) y de ahi calcula
    `breakeven_points`, `re_impact` y `sb_breakeven_by_count`. Ejecutar `01` sin
    ejecutar `03` despues deja esas tres calculadas sobre una matriz distinta de
    la que se sirve, sin que nada lo delate.

    Ya paso: al refrescar `01` durante la revision de agosto de 2026,
    `re24_values` cambio en +476 filas y `re24_transitions` en -4,144, y hubo que
    reejecutar `03` a mano.

    `03` deja constancia de con que version de las entradas calculo sus salidas
    en `derived_from.json`. Aqui se recalculan esos hashes sobre los ficheros
    actuales y se comparan.
    """
    manifest_path = DATA_DIR / 'derived_from.json'
    if not manifest_path.exists():
        pytest.fail('sin derived_from.json. Sin el no se puede comprobar que las tablas derivadas correspondan a la matriz publicada, que es justo lo que este test existe para vigilar.')

    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    recorded = manifest.get('inputs', {})
    assert recorded, 'derived_from.json no registra ninguna entrada'

    stale = []
    for name, recorded_hash in recorded.items():
        path = DATA_DIR / name
        if not path.exists():
            stale.append(f'{name} (ya no existe)')
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != recorded_hash:
            stale.append(name)

    assert not stale, (
        f'los datos derivados estan obsoletos: cambiaron {", ".join(stale)} '
        f'desde que se calcularon {", ".join(manifest.get("outputs", []))} '
        f'(el {manifest.get("generated_utc", "?")}). '
        f'Ejecuta: python utils/create_break_even_points.py'
    )


APP_JS = Path(__file__).resolve().parent.parent / 'app' / 'static' / 'js' / 'app.js'

# Tipos de break-even cuyos escenarios el frontend tiene escritos a mano en
# BREAKEVEN_SCENARIOS. swing-take y swing_take_zone no entran: sus escenarios se
# construyen dinamicamente a partir del conteo y la zona.
BREAKEVEN_TYPES_WITH_FIXED_SCENARIOS = ['stealing', 'running', 'tagging', 'bunt-for-hit']


def _js_breakeven_scenarios() -> dict:
    """
    Extrae BREAKEVEN_SCENARIOS de app.js como {tipo: [claves]}.

    Se parsea el JS en vez de duplicar las listas aqui, para que el test no
    pueda quedarse obsoleto respecto a lo que la app usa de verdad.
    """
    import re

    source = APP_JS.read_text(encoding='utf-8')
    block = re.search(r'const BREAKEVEN_SCENARIOS = \{(.*?)\n\};', source, re.S)
    assert block, 'no se encontro BREAKEVEN_SCENARIOS en app.js'

    out = {}
    for match in re.finditer(r"'?([\w-]+)'?:\s*\[(.*?)\]", block.group(1), re.S):
        out[match.group(1)] = re.findall(r"key:\s*'([^']+)'", match.group(2))

    # Si el parseo falla en silencio el test pasaria sin comprobar nada.
    assert set(BREAKEVEN_TYPES_WITH_FIXED_SCENARIOS).issubset(out), (
        f'el parseo de BREAKEVEN_SCENARIOS no encontro todos los tipos: {sorted(out)}'
    )
    return out


def _js_runners_label_aliases() -> dict:
    """RUNNERS_LABEL_ALIASES de app.js: etiqueta corta del parquet -> etiqueta larga de la UI."""
    import re

    source = APP_JS.read_text(encoding='utf-8')
    block = re.search(r'const RUNNERS_LABEL_ALIASES = \{(.*?)\};', source, re.S)
    assert block, 'no se encontro RUNNERS_LABEL_ALIASES en app.js'
    pairs = re.findall(r"'([^']+)':\s*'([^']+)'", block.group(1))
    assert pairs, 'el parseo de RUNNERS_LABEL_ALIASES no encontro ninguna pareja'
    return dict(pairs)


@pytest.mark.parametrize('be_type', BREAKEVEN_TYPES_WITH_FIXED_SCENARIOS)
def test_escenarios_publicados_casan_con_los_que_la_ui_espera(breakeven, be_type):
    """
    Los valores de `scenario` deben coincidir con las claves de app.js.

    `BREAKEVEN_SCENARIOS` en app.js lista los escenarios de cada familia a mano y
    los usa como clave para construir las filas de la tabla. Si el parquet
    publica un escenario que no esta en esa lista, la fila **no se muestra**; si
    la lista tiene uno que el dato no trae, sale una fila vacia.

    Este test existe porque al pasar `tagging` a datos empiricos los escenarios
    cambiaron de 3 a 6 con nombres nuevos, y la tabla salio completamente vacia
    en la UI. El test de contrato de esquema no lo detecto porque comprueba
    columnas, no valores.
    """
    if not APP_JS.exists():
        pytest.fail('app.js no encontrado; sin el no se puede comprobar el contrato entre los datos y lo que el frontend declara.')

    expected = set(_js_breakeven_scenarios()[be_type])
    published = set(breakeven.loc[breakeven['type'] == be_type, 'scenario'].dropna().unique())
    if not published:
        pytest.skip(f'sin filas publicadas para {be_type}')

    # bunt-for-hit es el unico tipo donde app.js NO casa directamente por
    # scenario: traduce antes con RUNNERS_LABEL_ALIASES, porque el parquet
    # publica las etiquetas cortas de RUNNERS_LABELS ('Empty', '1st') y la UI
    # muestra las largas ('Bases empty', 'Runner on 1B'). Hay que aplicar la
    # misma traduccion o el test da un falso positivo.
    if be_type == 'bunt-for-hit':
        aliases = _js_runners_label_aliases()
        published = {aliases.get(s, s) for s in published}

    # Solo se comprueba una direccion. Que el parquet publique un escenario que
    # app.js no conoce es grave: la fila desaparece sin aviso. Al reves no lo es
    # -- el render descarta las filas sin ningun dato, y de hecho ocurre a
    # proposito en bunt-for-hit, donde app.js lista los ocho estados base y solo
    # cuatro alcanzan el minimo de toques.
    invisibles = published - expected
    assert not invisibles, (
        f"{be_type}: estos escenarios se publican pero app.js no los conoce, "
        f"asi que no se veran en la tabla: {sorted(invisibles)}. "
        f"Anadelos a BREAKEVEN_SCENARIOS en app/static/js/app.js."
    )


def test_escenarios_de_sac_bunt_casan_con_la_ui(re_impact):
    """
    Los `scenario` de sac_bunt son códigos de corredores ('100', '010', '110') y
    app.js los referencia por el campo `runners` de SAC_BUNT_RE_SCENARIOS.
    """
    import re

    if not APP_JS.exists():
        pytest.fail('app.js no encontrado; sin el no se puede comprobar el contrato entre los datos y lo que el frontend declara.')

    source = APP_JS.read_text(encoding='utf-8')
    block = re.search(r'const SAC_BUNT_RE_SCENARIOS = \[(.*?)\];', source, re.S)
    assert block, 'no se encontro SAC_BUNT_RE_SCENARIOS en app.js'
    expected = set(re.findall(r"runners:\s*'([^']+)'", block.group(1)))
    assert expected, 'el parseo de SAC_BUNT_RE_SCENARIOS no encontro ningun runners'

    published = set(re_impact.loc[re_impact['type'] == 'sac_bunt', 'scenario'].dropna().unique())
    if not published:
        pytest.skip('sin filas sac_bunt')

    assert published == expected, (
        f'sac_bunt: publicados {sorted(published)} vs esperados por app.js {sorted(expected)}'
    )


def _on_demand_datasets() -> list:
    """
    Datasets que la app carga bajo demanda, leidos de su propio registro.

    Se derivan de app.main.data_store en vez de escribirlos aqui para que la
    lista no se quede obsoleta cuando se anada uno nuevo. Los que se cargan al
    arrancar (event types, seasons, last_updated, innings) no pasan por
    ensure_data_loaded y se excluyen.
    """
    from app.main import data_store

    startup_loaded = {"re24_event_types", "seasons", "last_updated", "innings"}
    return sorted(k for k in data_store if k not in startup_loaded)


@pytest.mark.parametrize('dataset', _on_demand_datasets())
def test_contrato_de_esquema_entre_parquet_y_app(dataset, monkeypatch):
    """
    Cada parquet debe tener las columnas que la app declara como obligatorias.

    Este test existe porque re288_frequencies.parquet se estuvo sirviendo con la
    columna 'frequency' mientras app/main.py exigia 'pa_final_count_share': ese
    endpoint devolvia error 500 y nada lo detectaba, porque los datos publicados
    se habian quedado atras respecto del codigo que los consume.

    No se replican aqui las listas de columnas: se invoca el cargador real de la
    app, que ya valida el esquema. Asi el contrato no puede divergir del que la
    aplicacion aplica de verdad.
    """
    if not DATA_DIR.exists():
        pytest.fail('sin datos generados; los parquets estan versionados y deberian estar aqui.')

    from app import main as app_main
    from app.main import data_store, ensure_data_loaded

    # app.main.DATA_DIR se fija al importar, desde app/config.py. Sin este
    # monkeypatch el test leeria siempre app/data e ignoraria RE_DATA_DIR, con
    # lo que no serviria para validar un lote en staging ni para comprobar que
    # detecta un lote defectuoso.
    monkeypatch.setattr(app_main, 'DATA_DIR', DATA_DIR)

    # Forzar la carga aunque otro test ya la haya hecho.
    data_store[dataset] = None

    try:
        ensure_data_loaded(dataset)
    except Exception as exc:  # HTTPException incluida
        detail = getattr(exc, 'detail', str(exc))
        pytest.fail(
            f"el dataset '{dataset}' no cumple el contrato que app/main.py exige: {detail}"
        )

    assert data_store[dataset] is not None, f"'{dataset}' quedo sin cargar"


def test_run_value_de_eventos_sigue_siendo_re_absoluta(re24_values):
    """
    Ancla el supuesto del que depende el calculo de IBB.

    Si algun dia el pipeline aguas arriba pasa a guardar deltas en lugar de RE
    absoluta, este test falla y avisa de que hay que quitar la resta en
    calculate_ibb_impact — en vez de que el cambio pase inadvertido.

    Comprobacion: un jonron desde bases limpias y 0 outs debe valer
    ~1 carrera + RE(0 outs, bases limpias).
    """
    season = int(re24_values['season'].max())
    same_season = re24_values[re24_values['season'] == season]

    base_state = same_season[
        (same_season['event_cd'] == -1)
        & (same_season['outs'] == 0)
        & (same_season['runners'] == '000')
    ]
    home_run = same_season[
        (same_season['event_cd'] == 23)
        & (same_season['outs'] == 0)
        & (same_season['runners'] == '000')
    ]
    if base_state.empty or home_run.empty:
        pytest.skip(f'faltan filas de referencia para {season}')

    re_base = float(base_state['run_value'].iloc[0])
    re_hr = float(home_run['run_value'].iloc[0])

    assert re_hr == pytest.approx(1.0 + re_base, abs=0.15), (
        f'{season}: un jonron desde 0 outs/000 vale {re_hr:.4f}, y se esperaba '
        f'~{1.0 + re_base:.4f} (1 carrera + RE base de {re_base:.4f}). '
        f'Si run_value paso a ser un delta, calculate_ibb_impact ya no debe restar '
        f'el baseline.'
    )


def _catalogo_de_extraccion() -> list:
    import extract_re_data

    return [(d.key, d.output, d.dtypes) for d in extract_re_data.DATASETS
            if d.output.endswith('.parquet')]


@pytest.mark.parametrize('key,output,dtypes',
                         _catalogo_de_extraccion(),
                         ids=[k for k, _, _ in _catalogo_de_extraccion()])
def test_ninguna_columna_se_publica_sin_tipo_declarado(key, output, dtypes):
    """
    Todo lo que sale en un parquet debe tener tipo en el catalogo de extraccion.

    Sin declarar, la columna se publica con lo que devuelva el driver ODBC: asi
    se colaron 're24_values.prob' en float64 y 'win_expectancy.n' en int64, que
    ademas de ocupar el doble no pasaban por la comprobacion posterior de tipos
    -- solo se verifica lo declarado. El script ya lo avisaba por log, pero un
    aviso en un pipeline de siete minutos no lo lee nadie.
    """
    path = DATA_DIR / output
    if not path.exists():
        pytest.fail(f'sin {output} generado; esta versionado y deberia estar aqui.')

    columnas = pd.read_parquet(path).columns
    sin_declarar = [c for c in columnas if c not in dtypes]

    assert not sin_declarar, (
        f"{key}: {output} publica {sin_declarar} sin tipo en el catalogo. "
        f"Anadelas a Dataset.dtypes en utils/extract_re_data.py."
    )


# Analisis con un confundidor conocido que el numero no refleja. No es ruido
# muestral: mas temporadas no lo corrigen, asi que el marcador de fiabilidad no
# sirve y hace falta una nota metodologica junto a la tabla.
#
# El valor dice como llega la clave al pie de tabla: 'literal' si el renderizador
# la escribe a mano, 'selectedType' si va por la variable de la tabla general
# (ahi la clave ES el tipo seleccionado, asi que basta con pasar la variable).
ANALISIS_CON_SESGO_DECLARADO = {
    'swing-take': 'literal',        # rejilla por conteo, renderizador propio
    'swing-take-zone': 'literal',
    'sb_by_count': 'literal',
    'bunt-for-hit': 'selectedType',  # tabla general de break-even
}


def _argumentos_de_llamada(args: str) -> list:
    """
    Parte la lista de argumentos de una llamada JS por las comas de primer nivel.

    Un split(',') normal no vale: el segundo argumento suele ser otra llamada con
    sus propias comas — `setBreakevenFooterText(el, getBreakevenFooterText('x', n))`
    tiene tres comas y solo dos argumentos. Buscar el literal en cualquier sitio
    de la llamada tampoco: 'sb_by_count' aparece dentro del segundo argumento, asi
    que el test pasaria aunque nadie pase el tercero.
    """
    partes, actual, profundidad, comilla = [], '', 0, None
    for ch in args:
        if comilla:
            # Dentro de una cadena no cuentan ni parentesis ni comas.
            if ch == comilla:
                comilla = None
            actual += ch
            continue
        if ch in '\'"`':
            comilla = ch
            actual += ch
            continue
        if ch in '([{':
            profundidad += 1
        elif ch in ')]}':
            profundidad -= 1
        if ch == ',' and profundidad == 0:
            partes.append(actual.strip())
            actual = ''
        else:
            actual += ch
    if actual.strip():
        partes.append(actual.strip())
    return partes


def _js_method_notes() -> dict:
    """METHOD_NOTES de app.js como {clave: texto concatenado}."""
    import re

    source = APP_JS.read_text(encoding='utf-8')
    block = re.search(r'const METHOD_NOTES = \{(.*?)\n\};', source, re.S)
    assert block, 'no se encontro METHOD_NOTES en app.js'

    # Cada entrada va desde su clave hasta la siguiente clave o el cierre.
    cuerpo = block.group(1)
    claves = list(re.finditer(r"^\s{4}'?([\w-]+)'?:\s", cuerpo, re.M))
    notas = {}
    for i, m in enumerate(claves):
        fin = claves[i + 1].start() if i + 1 < len(claves) else len(cuerpo)
        notas[m.group(1)] = ''.join(re.findall(r"'([^']*)'", cuerpo[m.end():fin]))
    assert notas, 'el parseo de METHOD_NOTES no encontro ninguna entrada'
    return notas


@pytest.mark.parametrize('analisis,via', sorted(ANALISIS_CON_SESGO_DECLARADO.items()))
def test_los_analisis_sesgados_llevan_nota_metodologica(analisis, via):
    """
    Cada analisis con sesgo declarado debe tener texto en METHOD_NOTES y llegar
    a la UI: el pie se renderiza con `setBreakevenFooterText(el, texto, clave)`,
    y sin ese tercer argumento la nota existe en el fichero pero no se muestra.
    """
    import re

    source = APP_JS.read_text(encoding='utf-8')
    notas = _js_method_notes()

    assert analisis in notas, f"'{analisis}' no tiene nota metodologica en METHOD_NOTES"
    assert len(notas[analisis]) > 60, (
        f"la nota de '{analisis}' tiene {len(notas[analisis])} caracteres; "
        f"parece vacia o un marcador de posicion"
    )

    terceros = [_argumentos_de_llamada(args)[2:3]
                for args in re.findall(r'setBreakevenFooterText\(([^;]*?)\);', source, re.S)]
    terceros = [a[0] for a in terceros if a]

    esperado = f"'{analisis}'" if via == 'literal' else 'selectedType'
    assert esperado in terceros, (
        f"ninguna llamada a setBreakevenFooterText pasa {esperado} como TERCER argumento, "
        f"asi que la nota de '{analisis}' no se renderiza. Terceros vistos: {terceros}"
    )


def test_las_tablas_de_diferencial_nombran_al_equipo_en_la_cabecera():
    """
    Win Expectancy, WPA y Leverage Index mezclan dos equipos: el +/- de las
    columnas es la ventaja del equipo que BATEA (el del selector Away/Home) y el
    valor de la celda es siempre del equipo de casa. Comprobado contra los datos:
    con Away bateando, 1a entrada, bases limpias, 2025, la fila va de 52.79% en
    'Tie' a 15.75% en '+4' — el '+4' es del away, por eso el numero BAJA.

    Ese contexto vive en la cabecera de grupo que produce buildRunDiffHeaderHTML.
    Si una de las tres vuelve a montar la cabecera a mano, el signo se queda sin
    dueno y la tabla vuelve a ser ambigua.
    """
    import re

    source = APP_JS.read_text(encoding='utf-8')

    assert "team's lead" in source, 'buildRunDiffHeaderHTML ya no etiqueta el grupo de columnas'
    assert source.count('buildRunDiffHeaderHTML(data.headers, teamName)') == 3, (
        'se esperaban las tres tablas de diferencial (Win Expectancy, WPA, '
        'Leverage Index) usando la cabecera de dos filas'
    )

    # La frase que la cabecera sustituye. Explicaba que '+' es ir ganando sin
    # decir de quien, que era justo lo que no se entendia.
    assert not re.search(r'Positive \(\+\) numbers in the headers', source), (
        'volvio la segunda linea de subtitulo que la cabecera de grupo reemplaza'
    )


def test_el_leverage_medio_ponderado_sigue_valiendo_uno(win_expectancy):
    """
    El subtitulo de Leverage Index afirma que un turno promedio vale 1.0.

    Es cierto SOLO ponderando por cuantas veces ocurre cada estado. Sin ponderar,
    la media de las celdas publicadas es 1.57 y el 58.9% pasan de 1.0, porque los
    estados frecuentes son los aburridos: por eso la frase parecia falsa mirando
    la tabla. Este test fija la parte que si es cierta; si la normalizacion de
    aguas arriba cambia, el subtitulo deja de valer y hay que reescribirlo.
    """
    df = win_expectancy.copy()
    df['LI'] = df['LI'].astype('float64')

    global_avg = float((df['LI'] * df['n']).sum() / df['n'].sum())
    assert 0.97 <= global_avg <= 1.04, (
        f'el leverage medio ponderado es {global_avg:.4f}; el subtitulo dice que un '
        f'turno promedio vale 1.0'
    )

    por_temporada = df.groupby('SEASON').apply(
        lambda d: (d['LI'] * d['n']).sum() / d['n'].sum(), include_groups=False)
    dentro = ((por_temporada >= 0.98) & (por_temporada <= 1.02)).mean()
    assert dentro >= 0.85, (
        f'solo el {dentro:.1%} de las temporadas tiene leverage medio en [0.98, 1.02] '
        f'(rango {por_temporada.min():.4f}-{por_temporada.max():.4f})'
    )


def test_el_leverage_medio_se_calcula_y_no_se_afirma():
    """
    El subtitulo decia 'Average leverage is 1.0' como constante. Ahora se calcula
    sobre la seleccion real, porque el corte por equipo no es neutro: el local
    batea ultimo, asi que sus turnos deciden mas (2025: away 0.995, home 1.038).
    """
    # Sin las lineas de comentario: el porque del cambio esta documentado en el
    # propio app.js y menciona la frase vieja, que no debe contar como reaparicion.
    source = APP_JS.read_text(encoding='utf-8')
    codigo = '\n'.join(l for l in source.splitlines() if not l.lstrip().startswith('//'))

    assert 'Average leverage is 1.0' not in codigo, (
        'volvio el 1.0 escrito a mano en el subtitulo de Leverage Index'
    )
    # Con '= ' delante: sin el, la definicion `function computeWeightedLeverage(data)`
    # ya casa y el test pasa aunque nadie la llame.
    assert '= computeWeightedLeverage(data)' in codigo, (
        'el subtitulo de Leverage Index ya no calcula la media ponderada'
    )


# Devuelven la serie historica completa e ignoran el rango de temporadas por
# diseño: son series por temporada, no agregados sobre un rango.
TABLAS_DE_SERIE_HISTORICA = ['WOBA_COEFFS', 'FIP_CONST', 'PYTHAG_EXPONENTS', 'RUNS_PER_WIN']


@pytest.mark.parametrize('matrix_type', TABLAS_DE_SERIE_HISTORICA)
def test_las_tablas_de_serie_historica_ocultan_el_selector_de_temporadas(matrix_type):
    """
    Si el selector estuviera activo, elegir 2025/2025 devolveria 125 temporadas y
    la tabla pareceria rota. app.js ya oculta ambos contenedores para estos
    cuatro tipos; esto lo fija.

    Comprobado en el navegador con RE24 de control: RE24 los muestra, los cuatro
    de aqui los ocultan. Se anoto en el TODO como pendiente por error.
    """
    import re

    source = APP_JS.read_text(encoding='utf-8')

    rama = re.search(
        r'\}\s*else if \(([^)]*WOBA_COEFFS[^)]*)\)\s*\{(.*?)\n    \} else if',
        source, re.S)
    assert rama, 'no se encontro la rama que oculta los selectores de temporada'

    assert f"'{matrix_type}'" in rama.group(1), (
        f"{matrix_type} ya no esta en la rama que oculta los selectores de temporada"
    )
    cuerpo = rama.group(2)
    for contenedor in ('fromSeasonContainer', 'toSeasonContainer'):
        assert f"{contenedor}.classList.add('d-none')" in cuerpo, (
            f'{contenedor} ya no se oculta para las tablas de serie historica'
        )


# Las tres tablas que salen de la matriz RE288 comparten suelo de temporada.
RE288_DATASETS = ['re288_values.parquet', 're288_frequencies.parquet',
                  're288_transitions.parquet', 'sb_breakeven_by_count.parquet']


@pytest.mark.parametrize('fichero', RE288_DATASETS)
def test_ninguna_tabla_re288_baja_del_suelo_de_temporada(fichero):
    """
    El suelo es 1989, que es donde empieza dbo.r288Values.

    Estaba puesto en 1988 en la consulta de frecuencias y en la constante de la
    UI, y 1988 no corresponde a ninguna fuente: r24Events_PitchCount llega hasta
    1910 y r288Values arranca en 1989. El efecto era concreto y estaba publicado:
    re288_frequencies traia una temporada de 1988 -- 288 filas, exactamente los
    288 estados -- sin contrapartida en re288_values ni re288_transitions, y la
    UI dejaba seleccionarla.
    """
    from app.config import RE288_MIN_SEASON

    ruta = DATA_DIR / fichero
    if not ruta.exists():
        pytest.fail(f'sin {fichero} generado; esta versionado y deberia estar aqui.')

    df = pd.read_parquet(ruta, columns=['season'])
    minima = int(df['season'].min())

    assert minima >= RE288_MIN_SEASON, (
        f'{fichero} empieza en {minima}, por debajo del suelo {RE288_MIN_SEASON}. '
        f'Filas afectadas: {int((df["season"] < RE288_MIN_SEASON).sum()):,}'
    )


def test_el_temporizador_del_fetch_vive_dentro_del_try():
    """
    El setTimeout se creaba ANTES de validaciones que hacen return (rango de
    temporadas invertido, RE288 antes de 1989), y el clearTimeout esta en el
    finally del try. Cada validacion fallida dejaba un timer vivo 45s que al
    disparar ponia abortadoPorTimeout = true -- variable GLOBAL --, asi que la
    siguiente peticion legitima mostraba "se agoto el tiempo" sin haberse agotado.

    Se comprueba estructuralmente: el setTimeout tiene que estar despues del
    `try {`, que es lo unico que garantiza que el finally lo limpie.
    """
    fuente = APP_JS.read_text(encoding='utf-8')

    decl = fuente.index('let temporizador = null;')
    inicio_try = fuente.index('    try {', decl)
    arranque = fuente.index('temporizador = setTimeout(', decl)

    assert arranque > inicio_try, (
        'el temporizador se arranca antes del try: un return temprano lo deja vivo'
    )
    assert 'if (temporizador !== null) clearTimeout(temporizador);' in fuente, (
        'el finally debe tolerar que el temporizador nunca se arrancara'
    )


# ---------------------------------------------------------------------------
# Paginacion: los botones tienen que FUNCIONAR
# ---------------------------------------------------------------------------
#
# Se rompio entera -- las 11 tablas paginadas a la vez -- al pasar la CSP a nonce.
# Los botones llevaban onclick="..." en linea, y los nonces cubren <script> pero
# NO los manejadores en atributo. El navegador dejo de ejecutarlos EN SILENCIO:
# ni error de consola visible, ni test rojo. Se detecto usando la web.
#
# Estos tests son la red para que no se repita.

def _js() -> str:
    return APP_JS.read_text(encoding='utf-8')


def test_ningun_manejador_de_evento_en_linea():
    """
    Con CSP de nonce sin 'unsafe-inline', cualquier on*="..." en atributo es
    codigo muerto. No es estilo: es la diferencia entre que el boton funcione o no.
    """
    import re

    fuente = _js()
    # Se ignoran las lineas de comentario, que si mencionan onclick al explicar esto.
    codigo = '\n'.join(l for l in fuente.splitlines() if not l.strip().startswith('//'))
    encontrados = re.findall(r'\bon(?:click|change|input|submit|load|error)\s*=\s*["\'`]', codigo)

    assert not encontrados, (
        f'{len(encontrados)} manejadores de evento en linea: la CSP con nonce los '
        f'bloquea y el control queda muerto sin avisar. Usa data-accion + '
        f'registrarAccion().'
    )


def test_todas_las_paginaciones_estan_registradas():
    """
    Cada tabla paginada declara su `changeHandlerName`, y ese nombre tiene que
    estar en el mapa de acciones delegadas. Si se anade una tabla nueva y se
    olvida registrarla, sus flechas no hacen nada -- en silencio.
    """
    import re

    fuente = _js()
    declarados = set(re.findall(r"changeHandlerName: '([A-Za-z]+)'", fuente))
    registrados = set(re.findall(r"registrarAccion\('([A-Za-z]+)'", fuente))

    assert declarados, 'no se encontro ningun changeHandlerName'
    sin_registrar = declarados - registrados
    assert not sin_registrar, (
        f'paginaciones sin registrar, sus botones no harian nada: {sorted(sin_registrar)}'
    )


def test_toda_funcion_de_paginacion_definida_esta_registrada():
    """
    Complemento del anterior por el otro lado: una `changeXPage` definida y no
    registrada es una tabla cuya paginacion no funciona.
    """
    import re

    fuente = _js()
    definidas = set(re.findall(r'function (change[A-Za-z]*Page)\s*\(', fuente))
    registrados = set(re.findall(r"registrarAccion\('([A-Za-z]+)'", fuente))

    assert definidas, 'no se encontro ninguna funcion changeXPage'
    assert definidas <= registrados, (
        f'definidas pero sin registrar: {sorted(definidas - registrados)}'
    )


def test_los_botones_de_paginacion_llevan_los_datos_que_el_despachador_lee():
    """
    El renderizador compartido de las cuatro flechas debe emitir data-accion y
    data-pagina. Sin ellos el despachador no sabe a quien llamar.
    """
    fuente = _js()

    assert 'data-accion="${changeHandlerName}"' in fuente, (
        'el renderizador de paginacion no emite data-accion'
    )
    assert fuente.count('data-pagina=') >= 4, (
        'faltan botones con data-pagina (primera, anterior, siguiente, ultima)'
    )
    assert "closest('[data-accion]')" in fuente, 'no hay despachador delegado'


def test_el_despachador_no_invoca_funciones_arbitrarias():
    """
    El mapa es explicito a proposito: con window[nombre], un data-accion inyectado
    en el DOM podria invocar cualquier funcion global.
    """
    fuente = _js()

    assert 'ACCIONES_DELEGADAS[el.dataset.accion]' in fuente
    assert 'window[el.dataset.accion]' not in fuente


# ---------------------------------------------------------------------------
# Botones de descarga: que no se queden mudos en silencio
# ---------------------------------------------------------------------------
#
# Hoy funcionan, y sobrevivieron al cambio de CSP porque usan addEventListener y
# no onclick en linea. Pero el modo de fallo que preocupa es el mismo que tumbo la
# paginacion: el control sigue ahi, se puede pulsar, y no pasa nada. Sin error de
# consola y sin test rojo.
#
# Estos tests cubren las tres formas de quedarse mudo:
#   1. el id que busca el JS no existe en la plantilla -> listener nunca atado
#   2. se pasa a handleDownload un tipo que currentTableData nunca guarda
#   3. alguien vuelve a poner onclick en linea (lo cubre el test general)

def _ids_de_descarga_en_js() -> set:
    import re
    return set(re.findall(r"getElementById\('([a-z0-9-]*download[a-z0-9-]*)'\)", _js()))


def test_todo_boton_de_descarga_que_busca_el_js_existe_en_la_plantilla():
    """
    Si el id no casa, addEventListener nunca llega a atarse: el boton se pinta,
    se pulsa y no hace nada. Es exactamente el sintoma que tuvo la paginacion.
    """
    from fastapi.testclient import TestClient

    from app.main import app as fastapi_app

    # Se comprueba contra el HTML **renderizado**, no contra el fuente de la
    # plantilla: varios de estos ids no aparecen como id="..." porque se pasan a
    # la macro Jinja `table_container` y se generan al renderizar.
    with TestClient(fastapi_app) as client:
        html = client.get('/').text

    buscados = _ids_de_descarga_en_js()
    assert buscados, 'no se encontro ningun boton de descarga en app.js'

    faltan = sorted(i for i in buscados if f'id="{i}"' not in html)
    assert not faltan, (
        f'el JS busca estos ids y la pagina renderizada no los tiene, asi que sus '
        f'listeners nunca se atan y el boton queda mudo: {faltan}'
    )


def test_todo_boton_de_descarga_de_la_plantilla_lo_cablea_el_js():
    """El otro sentido: un boton en el HTML que nadie cablea es decorado."""
    import re

    from fastapi.testclient import TestClient

    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        html = client.get('/').text
    en_html = set(re.findall(r'id="([a-z0-9-]*download[a-z0-9-]*)"', html))
    cableados = _ids_de_descarga_en_js()

    assert en_html, 'la plantilla no declara ningun boton de descarga'
    sin_cablear = sorted(en_html - cableados)
    assert not sin_cablear, f'botones sin listener, no harian nada: {sin_cablear}'


def test_cada_tipo_de_descarga_corresponde_a_un_dataset_que_se_guarda():
    """
    handleDownload lee `currentTableData[tableType]` y, si no hay nada, avisa de
    "No table data available". Un tipo que nunca se guarda con ese nombre da ese
    error siempre, aunque la tabla este pintada delante.
    """
    import re

    fuente = _js()
    pedidos = set(re.findall(r"handleDownload\('([a-z0-9_]+)'\)", fuente))
    guardados = set(re.findall(r"currentTableData\.([a-z0-9_]+)\s*=", fuente))
    guardados |= set(re.findall(r"currentTableData\['([a-z0-9_]+)'\]\s*=", fuente))

    assert pedidos, 'no se encontro ninguna llamada a handleDownload'
    huerfanos = sorted(pedidos - guardados)
    assert not huerfanos, (
        f'se pide descargar tipos que nunca se guardan en currentTableData, asi que '
        f'siempre darian "No table data available": {huerfanos}'
    )


def test_los_botones_de_descarga_se_cablean_con_addeventlistener():
    """
    Se deja escrito por que sobrevivieron al cambio de CSP: usan addEventListener.
    Si alguien los pasa a onclick en linea, la CSP con nonce los mata en silencio.
    """
    import re

    fuente = _js()
    cableados = len(re.findall(r"download[A-Za-z]*Btn\.addEventListener\('click'", fuente))

    assert cableados >= 13, (
        f'solo {cableados} de los 13 botones de descarga usan addEventListener; si alguno paso '
        f'a onclick en linea, la CSP con nonce lo bloquea'
    )


# ---------------------------------------------------------------------------
# Reproducibilidad de dependencias (code_review_v04, CR-04-05)
# ---------------------------------------------------------------------------

def _constraints() -> dict:
    import re

    ruta = Path(__file__).resolve().parent.parent / 'constraints.txt'
    texto = ruta.read_text(encoding='utf-8')
    return dict(re.findall(r'^([A-Za-z0-9_.\-]+)==([^\s#]+)', texto, re.M))


def test_todo_requisito_declarado_esta_fijado_en_constraints():
    """
    Los requirements usan `>=` a proposito -- dicen el MINIMO que el codigo
    necesita. constraints.txt dice lo que se PROBO. Si una dependencia directa no
    aparece ahi, su version queda al azar de cuando se construya la imagen, y una
    regresion de pandas o pyarrow en esta app no da un error: da otro numero.
    """
    import re

    raiz = Path(__file__).resolve().parent.parent
    fijados = {k.lower().replace('_', '-') for k in _constraints()}

    declarados = set()
    for f in ('app/requirements.txt', 'utils/requirements.txt'):
        for linea in (raiz / f).read_text(encoding='utf-8').splitlines():
            m = re.match(r'^([A-Za-z0-9_.\-]+)>=', linea)
            # Las condicionales de plataforma (`; platform_system != "Windows"`)
            # no se instalan en todas partes, asi que no pueden exigirse en un
            # constraints generado desde una sola maquina. uvloop es el caso.
            if m and ';' not in linea:
                declarados.add(m.group(1).lower().replace('_', '-'))

    assert declarados, 'no se leyo ninguna dependencia de los requirements'
    sin_fijar = sorted(declarados - fijados)
    assert not sin_fijar, (
        f'declaradas con >= pero sin version fijada en constraints.txt: {sin_fijar}. '
        f'Regeneralo con las versiones que acaben de pasar los tests.'
    )


def test_los_minimos_declarados_no_superan_lo_que_se_probo():
    """
    Un `>=` mayor que el pin es una contradiccion: pide una version mas nueva de
    la que se valido, y `pip -c` no la dejaria instalar.
    """
    import re

    raiz = Path(__file__).resolve().parent.parent
    fijados = {k.lower().replace('_', '-'): v for k, v in _constraints().items()}

    def piezas(v):
        return [int(x) if x.isdigit() else 0 for x in re.split(r'[.\-+]', v)[:3]]

    conflictos = []
    for f in ('app/requirements.txt', 'utils/requirements.txt'):
        for linea in (raiz / f).read_text(encoding='utf-8').splitlines():
            m = re.match(r'^([A-Za-z0-9_.\-]+)>=([0-9][^\s;#]*)', linea)
            if not m:
                continue
            nombre = m.group(1).lower().replace('_', '-')
            if nombre in fijados and piezas(m.group(2)) > piezas(fijados[nombre]):
                conflictos.append(f'{nombre}: requirements pide >={m.group(2)} '
                                  f'pero constraints fija {fijados[nombre]}')

    assert not conflictos, '; '.join(conflictos)


def test_el_dockerfile_instala_con_constraints():
    """
    Sin el `-c`, el fichero existe pero no lo aplica nadie: la imagen volveria a
    resolver versiones libremente y el pin seria decorativo.
    """
    raiz = Path(__file__).resolve().parent.parent
    dockerfile = (raiz / 'Dockerfile').read_text(encoding='utf-8')

    assert 'constraints.txt' in dockerfile, 'el Dockerfile no copia constraints.txt'
    assert '-c constraints.txt' in dockerfile, (
        'el Dockerfile copia constraints.txt pero no lo pasa a pip con -c'
    )


# ---------------------------------------------------------------------------
# SEO basico (docs/TODO.md 7.2)
# ---------------------------------------------------------------------------

def _portada() -> str:
    from fastapi.testclient import TestClient

    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        return client.get('/').text


def test_la_meta_description_cabe_en_un_resultado_de_busqueda():
    """
    Google corta alrededor de los 160 caracteres. La original tenia **304**: mas
    de la mitad no se mostraba nunca, y lo que quedaba fuera era justo la lista de
    tablas.
    """
    import re

    m = re.search(r'<meta name="description" content="(.*?)"', _portada(), re.S)
    assert m, 'la portada no declara meta description'
    assert 50 <= len(m.group(1)) <= 160, (
        f'la description mide {len(m.group(1))} caracteres; Google corta sobre 160'
    )


def test_canonical_og_url_y_sitemap_apuntan_a_la_misma_url():
    """
    El canonical decia `https://www.sabertables.com` (sin barra) y el sitemap
    `https://www.sabertables.com/` (con barra). Para un buscador son **dos URLs
    distintas**: es una señal contradictoria sobre cual es la buena.
    """
    import re

    from app.config import STATIC_DIR

    html = _portada()
    canonical = re.search(r'rel="canonical" href="([^"]+)"', html).group(1)
    og_url = re.search(r'property="og:url" content="([^"]+)"', html).group(1)
    sitemap = re.search(r'<loc>([^<]+)</loc>',
                        (STATIC_DIR / 'sitemap.xml').read_text(encoding='utf-8')).group(1)

    assert canonical == og_url == sitemap, (
        f'no coinciden -> canonical={canonical!r} og:url={og_url!r} sitemap={sitemap!r}'
    )


def test_la_portada_conserva_sus_senales_basicas():
    """Un solo h1, idioma declarado y datos estructurados."""
    import re

    html = _portada()

    assert len(re.findall(r'<h1[\s>]', html)) == 1, 'debe haber exactamente un h1'
    assert re.search(r'<html[^>]*lang="[a-z]{2}', html), 'falta el idioma en <html>'
    assert 'application/ld+json' in html, 'faltan los datos estructurados JSON-LD'


# ---------------------------------------------------------------------------
# Las dos vistas del mismo numero tienen que cuadrar (seccion 10 del TODO)
# ---------------------------------------------------------------------------

TIPOS_EN_CARRERAS = {
    "steal_runs": "stealing",
    "running_runs": "running",
    "tagging_runs": "tagging",
}


def _impacto():
    return pd.read_parquet(DATA_DIR / "re_impact.parquet")


def _breakeven():
    return pd.read_parquet(DATA_DIR / "breakeven_points.parquet")


def test_los_tres_analisis_en_carreras_estan_publicados():
    """
    Si el pipeline deja de emitirlos, la tabla sale vacia en la web y no falla
    nada. Este test lo convierte en un fallo inmediato.
    """
    tipos = set(_impacto()["type"])

    faltan = set(TIPOS_EN_CARRERAS) - tipos
    assert not faltan, f"faltan en re_impact.parquet: {sorted(faltan)}"


def test_las_carreras_traen_ganancia_y_coste():
    df = _impacto()
    en_carreras = df[df["type"].isin(TIPOS_EN_CARRERAS)]

    assert en_carreras["gain_runs"].notna().all(), "hay filas sin ganancia"
    assert en_carreras["cost_runs"].notna().all(), "hay filas sin coste"


def test_los_otros_analisis_NO_traen_ganancia_ni_coste():
    """
    Son columnas de los tipos *_runs. Si el toque de sacrificio empezara a
    traerlas, significaria que algo se esta mezclando entre familias.
    """
    df = _impacto()
    otros = df[~df["type"].isin(TIPOS_EN_CARRERAS)]

    assert otros["gain_runs"].isna().all()
    assert otros["cost_runs"].isna().all()


@pytest.mark.parametrize("tipo_runs,tipo_be", TIPOS_EN_CARRERAS.items())
def test_el_break_even_se_reconstruye_desde_las_carreras(tipo_runs, tipo_be):
    """
    EL INVARIANTE QUE UNE LAS DOS TABLAS, contra los datos publicados:

        BE = coste / (ganancia + coste)

    El break-even y las carreras salen de los mismos tres numeros. Si dejan de
    cuadrar, el sitio publica dos respuestas incompatibles a la misma pregunta y
    una de las dos esta mal.
    """
    carreras = _impacto()
    carreras = carreras[carreras["type"] == tipo_runs]
    umbral = _breakeven()
    umbral = umbral[umbral["type"] == tipo_be]

    j = carreras.merge(
        umbral[["season", "scenario", "outs", "breakeven_pct"]],
        on=["season", "scenario", "outs"], how="inner",
    )
    assert len(j) > 0, f"{tipo_runs} y {tipo_be} no cruzan por season/scenario/outs"

    j = j[j["breakeven_pct"].notna()]
    reconstruido = j["cost_runs"] / (j["gain_runs"] + j["cost_runs"])
    dif = (reconstruido - j["breakeven_pct"]).abs()

    assert dif.max() < 1e-4, (
        f"{tipo_runs}: la identidad no cuadra, diferencia maxima {dif.max():.6f} "
        f"en {int((dif >= 1e-4).sum())} de {len(j)} celdas"
    )


@pytest.mark.parametrize("tipo", list(TIPOS_EN_CARRERAS))
def test_el_signo_del_neto_coincide_con_superar_el_umbral(tipo):
    """
    Control de coherencia sabermetrica: donde la tasa observada supera el
    break-even, intentarlo tiene que GANAR carreras. Si no, una de las dos
    tablas miente.
    """
    df = _impacto()
    df = df[(df["type"] == tipo) & df["delta_re"].notna()]
    assert len(df) > 0

    umbral_implicito = df["cost_runs"] / (df["gain_runs"] + df["cost_runs"])
    # p implicita despejada del neto: neto = p*g - (1-p)*c  =>  p = (neto+c)/(g+c)
    p = (df["delta_re"] + df["cost_runs"]) / (df["gain_runs"] + df["cost_runs"])

    positivos = df["delta_re"] > 0
    assert (p[positivos] > umbral_implicito[positivos]).all()
    assert (p[~positivos] <= umbral_implicito[~positivos] + 1e-9).all()


# ---------------------------------------------------------------------------
# El porcentaje del toggle de RE Impact
# ---------------------------------------------------------------------------

def test_todos_los_analisis_de_re_impact_traen_delta_re_pct():
    """
    El interruptor de Percentages de RE Impact cambia el campo que se agrega:
    `delta_re_pct` en vez de `delta_re`. Si un tipo sale del pipeline sin ese
    campo, el interruptor NO FALLA: se pulsa, la tabla se repinta y se queda
    igual, o con huecos. Parece que el control esta muerto.

    Paso con steal_runs, running_runs y tagging_runs: los construi sin rellenar el
    porcentaje. sac_bunt, gidp e ibb si lo traian, y por eso ahi funcionaba.

    El porcentaje es `delta_re / baseline_re`: la parte de la expectativa de
    carreras que tenias y que la jugada te quita o te suma.
    """
    import pandas as pd

    df = pd.read_parquet(DATA_DIR / "re_impact.parquet")

    sin_pct = {}
    for tipo, g in df.groupby("type"):
        # Solo cuenta donde el porcentaje es calculable: con baseline cero no lo es.
        # Dos supresiones son DELIBERADAS y no cuentan como falta:
        #   - sin delta_re no hay nada que dividir (el robo donde no cabe ganancia);
        #   - baseline por debajo del umbral, que dispararia el cociente.
        calculables = g[g["baseline_re"].notna() & (g["baseline_re"] != 0)
                        & g["delta_re"].notna()
                        & (g["baseline_re"].abs() >= 0.01)]
        if not len(calculables):
            continue
        faltan = int(calculables["delta_re_pct"].isna().sum())
        if faltan:
            sin_pct[tipo] = f"{faltan} de {len(calculables)}"

    assert not sin_pct, (
        f"analisis sin delta_re_pct: {sin_pct}. "
        f"El interruptor de Percentages no ensenara nada para esos tipos."
    )


def test_delta_re_pct_es_delta_re_entre_el_VALOR_ABSOLUTO_del_baseline():
    """
    UNA SOLA DEFINICION: delta_re / |baseline_re|.

    Convivian dos. Casi todos los tipos dividian entre el baseline CON SIGNO,
    mientras platoon_baseline y tto usaban el valor absoluto
    (v_re_analysis_platoon_impact_final: dividido por baseline_abs_run_value).
    El mismo interruptor de Percentages presentaba las dos como si fueran la misma
    metrica.

    Gana el valor absoluto, y no por gusto: con un baseline NEGATIVO, dividir con
    signo invierte la lectura --una mejora sale como porcentaje negativo-- y por
    eso platoon lo evitaba. Donde el baseline es positivo, que es casi siempre, el
    numero no cambia.
    """
    import numpy as np
    import pandas as pd

    df = pd.read_parquet(DATA_DIR / "re_impact.parquet")
    x = df[df["delta_re_pct"].notna() & df["baseline_re"].notna()
           & (df["baseline_re"].abs() >= 0.01) & df["delta_re"].notna()]
    assert len(x) > 100, f"muy pocas filas para comprobar: {len(x)}"

    esperado = x["delta_re"].astype(float) / x["baseline_re"].astype(float).abs()
    dif = np.abs(esperado - x["delta_re_pct"].astype(float))

    malas = x[dif > 1e-4]
    assert malas.empty, (
        f"{len(malas)} filas donde delta_re_pct no es delta_re/|baseline_re|. "
        f"Tipos: {sorted(malas['type'].unique())}"
    )


def test_las_carreras_de_liga_no_se_filtran_por_evento_de_bateo():
    """
    LAS CARRERAS NO SON UN EVENTO DE BATEO.

    `BAT_EVENT_FL = 'T'` estaba en el WHERE de v_wrc_league_inputs, asi que
    filtraba la consulta ENTERA. Las apariciones al plato si son eventos de bateo,
    pero las carreras no: se anota tambien en un robo de home, un wild pitch, un
    passed ball o un balk.

    Medido en 2025: 21.276 con el filtro contra 21.613 sin el. Faltaban 337, un
    1,6%, y todo el lgR/PA salia bajo. En wRC+ casi se cancela --el error esta
    arriba y abajo de la fraccion-- pero el wRC y el wRAA se lo comen entero: una
    carrera de menos por bateador y temporada, sin forma de detectarlo.

    El filtro tiene que estar EN LA COLUMNA de PA, no en el WHERE.
    """
    import re

    sql = (Path(__file__).resolve().parent.parent / 'utils' / 'sql'
           / '23_v_wrc_league_inputs.sql').read_text(encoding='utf-8')

    # Fuera comentarios: la explicacion del fallo menciona BAT_EVENT_FL a proposito.
    codigo = '\n'.join(l for l in sql.splitlines() if not l.strip().startswith('--'))

    where = codigo.split('FROM dbo.v_retroAllEventsRegAndPost', 1)[1].split(')', 1)[0]
    assert 'BAT_EVENT_FL' not in where.upper(), (
        'BAT_EVENT_FL vuelve a estar en el WHERE: las carreras de robos, wild '
        'pitches, passed balls y balks se pierden y el lgR/PA sale bajo.'
    )

    # Y donde SI tiene que estar: en las columnas de apariciones al plato.
    for col in ('AS den_woba', 'AS pa'):
        tramo = codigo.split(col, 1)[0].rsplit('CASE', 1)[-1]
        assert 'BAT_EVENT_FL' in tramo.upper(), (
            f'la columna {col.strip()} ya no filtra por BAT_EVENT_FL: contaria '
            f'como aparicion al plato cosas que no lo son.'
        )


def test_lgR_por_PA_esta_en_un_rango_creible():
    """
    Una red mas basta que la anterior, pero que no depende de leer SQL: si el
    lgR/PA se sale de la banda historica, algo cambio en la poblacion contada.
    Con el filtro puesto, 2025 daba 0,116 en vez de 0,118 -- dentro de banda, asi
    que ESTE test solo no lo habria cazado. Se deja por lo que si coge: cambios
    gruesos de cobertura.
    """
    import pandas as pd

    df = pd.read_parquet(DATA_DIR / "wrc_league_inputs.parquet")
    fuera = df[(df["lg_r_per_pa"] < 0.08) | (df["lg_r_per_pa"] > 0.16)]

    assert fuera.empty, (
        f"{len(fuera)} temporadas con lgR/PA fuera de 0,08-0,16: "
        f"{fuera[['season', 'lg_r_per_pa']].head(5).to_dict('records')}"
    )


def test_el_pitagorico_filtra_por_marcador_y_no_por_codigo_de_estado():
    """
    UN PARTIDO CUENTA SI SE JUGO, NO SI TIENE EL CODIGO QUE ALGUIEN LISTO.

    v_mlb_team_season_pythag_input filtraba `statusCode IN ('F','FT')` y dejaba
    fuera 176 juegos que SI se disputaron, en 42 temporadas y con 1.333 carreras:
    'FR' (final por lluvia), 'FO', 'FG' y 'RV'. En 2025 eran cinco, tres de ellos
    de los White Sox, cuya temporada salia con 159 juegos en vez de 162.

    Y ya habia pasado antes: se anadio 'FT' para rescatar los empates y nadie
    comprobo si faltaban mas codigos. Faltaban cuatro familias.

    Filtrar por marcador cierra el problema entero y aguanta codigos nuevos. Los
    cancelados siguen fuera --184 partidos sin marcador, incluidos los 120 de 2001
    y los 41 de 2000-- que es lo correcto: nunca se jugaron.
    """
    sql = (Path(__file__).resolve().parent.parent / 'utils' / 'sql'
           / '01_v_mlb_team_season_pythag_input.sql').read_text(encoding='utf-8')
    codigo = '\n'.join(l for l in sql.splitlines() if not l.strip().startswith('--'))

    assert 'statusCode' not in codigo, (
        "el pitagorico vuelve a filtrar por statusCode. Cada codigo nuevo que "
        "aparezca --o que no se recuerde-- se llevara juegos jugados por delante."
    )
    for col in ('teamAwayScore IS NOT NULL', 'teamHomeScore IS NOT NULL'):
        assert col in codigo, f"falta el filtro por marcador: {col}"


def test_el_pitagorico_cuenta_las_temporadas_completas():
    """
    La consecuencia visible: con el filtro viejo, 2025 salia con 2.425 juegos en
    vez de 2.430. Una temporada moderna son 30 equipos x 162 / 2.

    Se comprueba sobre el parquet, que es lo que llega a la aplicacion.
    """
    import pandas as pd

    df = pd.read_parquet(DATA_DIR / "pythag_exponents.parquet")
    assert len(df) > 100, f"pocas temporadas: {len(df)}"

    col_g = next((c for c in df.columns if 'game' in c.lower() or 'juego' in c.lower()), None)
    if col_g is None:
        pytest.skip("el parquet de exponentes no publica el conteo de juegos")

    modernas = df[df["season"].between(2013, 2019)]  # sin pandemia ni acortadas
    flojas = modernas[modernas[col_g] < 2400]
    assert flojas.empty, (
        f"temporadas modernas con menos de 2.400 juegos: "
        f"{flojas[['season', col_g]].to_dict('records')}"
    )


# ---------------------------------------------------------------------------
# Missed Calls (8.9). LOS TESTS VAN ANTES QUE EL CODIGO: hoy estan ROJOS.
#
# PARQUET PROPIO, no dentro de re_impact.parquet. Ahi caben 6 filas por temporada
# --sac_bunt-- y Missed Calls trae 8 estados x 3 outs x 12 conteos x 2 brazos, o
# sea 576 por temporada y ~10.000 en la serie. Ademas de una dimension mas, el
# conteo, que ese esquema no tiene.
#
# Es la leccion de los factores de parque de esta misma sesion: la informacion
# nueva no entra deformando la que ya funciona. Si Missed Calls sale mal, los
# otros seis analisis ni se enteran.
#
# Ver docs/PLAN_MISSED_CALLS.md.
# ---------------------------------------------------------------------------

MISSED_CALLS_2025 = {
    "strike_cantado_bola": 18682,
    "bola_cantada_strike": 12157,
    "total": 30839,
}
PARQUET_MISSED_CALLS = DATA_DIR / "missed_calls.parquet"


def _missed_calls():
    import pandas as pd

    if not PARQUET_MISSED_CALLS.exists():
        raise AssertionError(
            "no existe missed_calls.parquet. Es lo esperado hasta que se construya "
            "el analisis (8.9)."
        )
    return pd.read_parquet(PARQUET_MISSED_CALLS)


def test_missed_calls_tiene_las_columnas_de_su_esquema():
    """
    Parquet propio, esquema propio. La columna que NO cabia en re_impact.parquet
    es `count`, y es el eje entero de la tabla.
    """
    df = _missed_calls()
    faltan = [c for c in ("season", "runners", "outs", "count", "tipo", "n", "delta_re")
              if c not in df.columns]

    assert not faltan, f"faltan columnas: {faltan}. Hay: {sorted(df.columns)}"


def test_missed_calls_cuadra_con_lo_medido_en_2025():
    """
    Los dos brazos tienen que sumar lo contado a mano ANTES de escribir el
    pipeline: 18.682 strikes cantados bola y 12.157 bolas cantadas strike.

    Si el pipeline devuelve otra cosa sin que nadie lo pida, algo cambio aguas
    arriba --el filtro game_type='R', la zona, o la poblacion de tomados-- y hay
    que enterarse aqui y no en la pantalla.
    """
    df = _missed_calls()
    x = df[df["season"] == 2025]
    total = int(x["n"].sum())

    assert total == MISSED_CALLS_2025["total"], (
        f"2025 trae {total:,} cantos equivocados y se midieron "
        f"{MISSED_CALLS_2025['total']:,}."
    )


def test_missed_calls_trae_los_doce_conteos_y_los_ocho_estados():
    """
    Los DOS ejes llevan senal, y el de bases mas que el del conteo: 0,2076
    carreras de rango contra 0,1684. En 2-2 el coste va de 0,082 a 0,502 segun las
    bases. Si falta cualquiera de los dos ejes, la tabla esconde la mitad.
    """
    df = _missed_calls()
    conteos = df["count"].astype(str).nunique()
    bases = df["runners"].astype(str).nunique()

    assert conteos == 12, f"se esperaban 12 conteos y hay {conteos}"
    assert bases == 8, f"se esperaban 8 estados de bases y hay {bases}"


def test_missed_calls_no_mete_la_postemporada():
    """
    En 2025 hay 14.245 lanzamientos de postemporada, un 2%. No es catastrofico,
    pero es OTRA poblacion: en octubre ni los arbitros ni los bateadores son una
    muestra de la temporada. El total de 2025 lo delata si se cuelan.
    """
    df = _missed_calls()
    total = int(df[df["season"] == 2025]["n"].sum())

    assert total <= MISSED_CALLS_2025["total"], (
        f"2025 trae {total:,} fallos, mas que los {MISSED_CALLS_2025['total']:,} "
        f"de temporada regular: se colo la postemporada."
    )
