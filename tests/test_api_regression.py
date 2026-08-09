import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _post_generate_table(client: TestClient, payload: dict):
    response = client.post('/api/generate-table', json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_re24_schema_and_rows():
    with TestClient(app) as client:
        data = _post_generate_table(
            client,
            {
                'matrix_type': 'RE24',
                'from_season': 2024,
                'to_season': 2024,
            },
        )

    assert data['matrix_type'] == 'RE24'
    assert data['headers'] == ['Runners', '0 Outs', '1 Out', '2 Outs']
    assert len(data['rows']) == 8

    expected_row_keys = {
        'runners',
        'label',
        'out_0',
        'out_1',
        'out_2',
        'out_0_raw',
        'out_1_raw',
        'out_2_raw',
        'out_0_n',
        'out_1_n',
        'out_2_n',
    }
    assert expected_row_keys.issubset(set(data['rows'][0].keys()))


def test_re288_transitions_probability_columns_align_with_rows():
    with TestClient(app) as client:
        data = _post_generate_table(
            client,
            {
                'matrix_type': 'RE288_TRANS',
                'from_season': 2024,
                'to_season': 2024,
                'transition_type': 'B',
                'transition_mode': 2,
            },
        )

    assert data['matrix_type'] == 'RE288_TRANS'
    assert data['transition_mode'] == 2
    assert data['total_pitches'] is not None
    assert data['total_pitches'] > 0
    assert len(data['count_columns']) > 0
    assert len(data['rows']) > 0

    row = data['rows'][0]
    assert {'runners', 'label', 'outs'}.issubset(row.keys())

    for count_col in data['count_columns']:
        assert count_col in row
        assert f'{count_col}_raw' in row


def test_r24_frequencies_single_season():
    """
    R24_FREQUENCIES devolvia 500 con KeyError 'runs_diff'.

    generate_r24_frequencies_table tenia pegada una linea de bucketing de
    diferencial de carreras que pertenece a las tablas de win probability y
    leverage index. re24_frequencies no tiene esa columna, asi que el endpoint
    fallaba en cualquier rango de temporadas.
    """
    with TestClient(app) as client:
        data = _post_generate_table(
            client,
            {
                'matrix_type': 'R24_FREQUENCIES',
                'from_season': 2025,
                'to_season': 2025,
            },
        )

    assert data['matrix_type'] == 'R24_FREQUENCIES'
    assert len(data['rows']) == 8, 'se esperan los 8 estados de corredores'

    row = data['rows'][0]
    assert {'runners', 'label'}.issubset(row.keys())


def test_todos_los_matrix_types_responden():
    """
    Barrido de humo sobre cada tipo de tabla.

    El fallo de R24_FREQUENCIES vivio sin detectarse porque solo seis de los
    dieciocho tipos tenian test. Este recorre los que no necesitan parametros
    extra; los otros nueve van en
    test_los_matrix_types_con_parametros_responden, que es donde estaba el hueco
    de verdad -- entre los dos se cubren los dieciocho.
    """
    simple = [
        'RE24', 'RE24_PCT', 'RE288', 'RE288_FREQ', 'R24_FREQUENCIES',
        'WOBA_COEFFS', 'FIP_CONST', 'PYTHAG_EXPONENTS', 'RUNS_PER_WIN',
    ]
    with TestClient(app) as client:
        for matrix_type in simple:
            response = client.post(
                '/api/generate-table',
                json={'matrix_type': matrix_type, 'from_season': 2024, 'to_season': 2024},
            )
            assert response.status_code == 200, f'{matrix_type} devolvio {response.status_code}: {response.text}'


def test_linear_weights_includes_reliability_count_fields():
    with TestClient(app) as client:
        data = _post_generate_table(
            client,
            {
                'matrix_type': 'LINEAR_WEIGHTS',
                'from_season': 2024,
                'to_season': 2024,
                'outs': -1,
            },
        )

    assert data['matrix_type'] == 'LINEAR_WEIGHTS'
    assert data['total_events'] is not None
    assert data['total_events'] > 0
    assert len(data['rows']) > 0

    row = data['rows'][0]
    expected_n_keys = {
        'AVG_n',
        '000_n',
        '100_n',
        '010_n',
        '001_n',
        '110_n',
        '101_n',
        '011_n',
        '111_n',
    }
    assert expected_n_keys.issubset(row.keys())


def test_win_expectancy_requires_team_parameter():
    with TestClient(app) as client:
        response = client.post(
            '/api/generate-table',
            json={
                'matrix_type': 'WIN_EXPECTANCY',
                'from_season': 2024,
                'to_season': 2024,
            },
        )

    assert response.status_code == 400
    assert 'team' in response.text.lower()


def test_win_expectancy_headers_match_row_keys():
    with TestClient(app) as client:
        data = _post_generate_table(
            client,
            {
                'matrix_type': 'WIN_EXPECTANCY',
                'from_season': 2024,
                'to_season': 2024,
                'team': 0,
            },
        )

    assert len(data['rows']) > 0
    row = data['rows'][0]

    required_base_keys = {'inn_ct', 'outs_ct', 'runners'}
    assert required_base_keys.issubset(row.keys())

    # Dynamic differential headers should map directly to row keys.
    for header in data['headers'][3:]:
        assert header in row


def test_leverage_index_includes_total_events_and_cell_counts():
    with TestClient(app) as client:
        data = _post_generate_table(
            client,
            {
                'matrix_type': 'LEVERAGE_INDEX',
                'from_season': 2024,
                'to_season': 2024,
                'team': 0,
            },
        )

    assert data['matrix_type'] == 'LEVERAGE_INDEX'
    assert data['total_events'] is not None
    assert data['total_events'] > 0
    assert len(data['rows']) > 0

    row = data['rows'][0]
    for header in data['headers'][3:]:
        assert header in row
        assert f'{header}_n' in row


def test_re_impact_tto_payload_shape():
    with TestClient(app) as client:
        data = _post_generate_table(
            client,
            {
                'matrix_type': 'RE_IMPACT',
                'analysis_type': 'tto',
            },
        )

    assert data['matrix_type'] == 'RE_IMPACT'
    assert data['analysis_type'] == 'tto'
    assert len(data['table_data']) > 0

    row = data['table_data'][0]
    expected_keys = {
        'season',
        'scenario',
        'label',
        'delta_re',
        'delta_re_pct',
        'n',
        'handedness_split',
        'tto_pass',
    }
    assert expected_keys.issubset(row.keys())


# ---------------------------------------------------------------------------
# Hallazgos de docs/reviews/code_review_v02.md
# ---------------------------------------------------------------------------

def test_el_limite_de_tamano_de_cuerpo_existe_de_verdad():
    """
    Antes se pasaba max_request_size=1_000_000 a FastAPI() con el comentario
    "prevent DoS attacks". Ese parametro NO existe en FastAPI: caia en **extra y
    acababa en app.extra, un diccionario que va al esquema OpenAPI y no corta
    nada. La proteccion se leia en el codigo y no estaba en ninguna parte.
    """
    from app.main import MAX_REQUEST_BODY_BYTES

    with TestClient(app) as client:
        grande = 'x' * (MAX_REQUEST_BODY_BYTES + 1024)
        r = client.post('/api/generate-table',
                        content=grande.encode(),
                        headers={'Content-Type': 'application/json'})
        assert r.status_code == 413, f'un cuerpo de {len(grande):,} bytes devolvio {r.status_code}'

        # Y una peticion normal sigue pasando.
        ok = client.post('/api/generate-table',
                         json={'matrix_type': 'RE24', 'from_season': 2024, 'to_season': 2024})
        assert ok.status_code == 200


@pytest.mark.parametrize('matrix_type', ['RE288', 'RE288_FREQ', 'RE288_TRANS'])
def test_el_suelo_de_temporada_de_re288_cubre_los_tres_tipos(matrix_type):
    """
    Los tres salen de r288Values. El frontend los bloqueaba a la vez antes de
    1989, pero el backend solo miraba `matrix_type == "RE288"`, asi que una
    llamada directa a la API con RE288_FREQ y from_season=1950 se saltaba una
    regla que la UI considera obligatoria.
    """
    from app.config import RE288_MIN_SEASON

    payload = {'matrix_type': matrix_type,
               'from_season': RE288_MIN_SEASON - 30,
               'to_season': RE288_MIN_SEASON - 30}
    if matrix_type == 'RE288_TRANS':
        payload.update({'transition_type': 'B', 'transition_mode': 1})

    with TestClient(app) as client:
        r = client.post('/api/generate-table', json=payload)

    assert r.status_code == 400, (
        f'{matrix_type} con temporada {payload["from_season"]} devolvio '
        f'{r.status_code}; deberia rechazarse por debajo de {RE288_MIN_SEASON}'
    )
    # Y por LA RAZON correcta: sin esto, un 400 de "no hay datos para ese rango"
    # satisface el test igual y la regla puede seguir sin aplicarse. Paso: la
    # primera version de este test daba verde con el `== "RE288"` incompleto.
    detalle = r.json().get('detail', '')
    assert str(RE288_MIN_SEASON) in str(detalle), (
        f'{matrix_type} se rechazo, pero por otra causa: {detalle!r}'
    )


def test_solo_hay_un_validador_de_rango_de_temporadas():
    """
    Habia dos: el de app/main.py (vivo) y otro en app/validators/data_validator.py
    que no llamaba nadie. La consecuencia no fue teorica -- la regla de RE288
    quedo duplicada y las DOS copias tenian el mismo `== "RE288"` incompleto.
    """
    import app.validators.data_validator as dv

    assert not hasattr(dv, 'validate_season_range'), (
        'volvio el validador duplicado en data_validator.py'
    )


@pytest.fixture
def app_en_produccion(monkeypatch):
    """
    Recarga app.main con ENVIRONMENT=production y LO DEVUELVE A SU SITIO al salir.

    monkeypatch restaura la variable de entorno, no el modulo: sin esta vuelta
    atras, app.main se queda en modo produccion para todos los tests que vengan
    despues. Se descubrio al activar API_REQUIRE_ORIGIN, que solo exige Origin en
    produccion: 30 tests empezaron a devolver 403 por contaminacion entre tests,
    no por el cambio.
    """
    import importlib

    import app.main

    monkeypatch.setenv('ENVIRONMENT', 'production')
    yield importlib.reload(app.main)
    monkeypatch.undo()
    importlib.reload(app.main)


# ---------------------------------------------------------------------------
# Open redirect en la aplicacion de HTTPS (docs/TODO.md 5.6)
# ---------------------------------------------------------------------------

def test_un_host_ajeno_ni_siquiera_llega_al_redirect(app_en_produccion):
    """
    Con TrustedHostMiddleware delante, un Host ajeno se rechaza con 400 ANTES de
    que nada lo use. Es la primera linea; la segunda es que el redirect no lo
    refleje aunque llegue (el test siguiente).
    """
    with TestClient(app_en_produccion.app) as client:
        r = client.get('/', headers={'host': 'evil.test', 'x-forwarded-proto': 'http'},
                       follow_redirects=False)

    assert r.status_code == 400, f'devolvio {r.status_code}, deberia rechazar el Host'


@pytest.mark.parametrize('cabecera', ['x-forwarded-host'])
def test_el_redirect_https_no_refleja_un_host_ajeno(cabecera, app_en_produccion):
    """
    El destino del 308 se construia con la cabecera que mandara el cliente, y
    x-forwarded-host iba PRIMERO -- una cabecera que nadie filtra, porque
    ProxyHeadersMiddleware solo gestiona X-Forwarded-For y X-Forwarded-Proto.

    Con "X-Forwarded-Host: evil.com" la respuesta era un 308 PERMANENTE a
    https://evil.com/, preservando metodo y query. Al ser permanente, navegador
    y caches se lo quedan.

    TrustedHostMiddleware NO cubre esto: solo mira `Host`, no `X-Forwarded-Host`.
    Por eso hacen falta las dos defensas y no una.
    """
    m = app_en_produccion

    with TestClient(m.app) as client:
        r = client.get('/', headers={cabecera: 'evil.com', 'x-forwarded-proto': 'http'},
                       follow_redirects=False)

    assert r.status_code == 308
    destino = r.headers['location']
    assert 'evil.com' not in destino, f'el redirect refleja el host del atacante: {destino}'
    assert destino.startswith(f'https://{m.CANONICAL_HOST}'), destino


def test_el_redirect_https_si_respeta_un_host_legitimo(app_en_produccion):
    """Control del anterior: un host de la lista si se conserva."""
    m = app_en_produccion
    legitimo = 'sabertables.com'
    assert legitimo in m.ALLOWED_REDIRECT_HOSTS

    with TestClient(m.app) as client:
        r = client.get('/', headers={'x-forwarded-host': legitimo, 'x-forwarded-proto': 'http'},
                       follow_redirects=False)

    assert r.status_code == 308
    assert r.headers['location'].startswith(f'https://{legitimo}'), r.headers['location']


# ---------------------------------------------------------------------------
# Carga perezosa bajo concurrencia (docs/TODO.md 6.4)
# ---------------------------------------------------------------------------

def test_un_dataset_frio_se_lee_una_sola_vez_aunque_lo_pidan_a_la_vez(monkeypatch):
    """
    ensure_data_loaded comprobaba "esta cargado?" y leia el parquet sin cerrojo.
    FastAPI corre los endpoints sincronos en un pool de hilos, asi que dos
    peticiones al mismo dataset frio entraban a la vez y lo leian las dos:
    doble lectura de disco y doble pico de memoria en el arranque en frio, con
    los datasets mas pesados (WE, WPA, LI).

    Se cuenta cuantas veces se llega a leer de verdad, con ocho hilos saliendo a
    la vez desde una barrera.
    """
    import threading

    import app.main as m

    lecturas = []
    original = m._load_dataset

    def contando(nombre):
        lecturas.append(nombre)
        # Ensancha la ventana de carrera: sin cerrojo, los otros hilos entran aqui.
        time.sleep(0.05)
        return original(nombre)

    monkeypatch.setattr(m, '_load_dataset', contando)
    m.data_store['re24'] = None          # forzar frio

    barrera = threading.Barrier(8)

    def pedir():
        barrera.wait()
        m.ensure_data_loaded('re24')

    hilos = [threading.Thread(target=pedir) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert lecturas == ['re24'], (
        f'el parquet se leyo {len(lecturas)} veces con 8 peticiones simultaneas; '
        f'deberia leerse una'
    )
    assert m.data_store['re24'] is not None


# Los nueve tipos que exigen parametros ademas del rango de temporadas. Estaban
# fuera de todo barrido de humo: un cambio en su validacion o en su payload podia
# romper la tabla entera sin que ningun test se enterara.
#
# El payload es el MINIMO valido de cada uno, no el tipico: si el contrato gana un
# parametro obligatorio, este test se entera antes que el usuario.
MATRIX_TYPES_CON_PARAMETROS = {
    'RE288_TRANS':           {'transition_type': 'B', 'transition_mode': 1},
    'LINEAR_WEIGHTS':        {'outs': -1},
    'WIN_EXPECTANCY':        {'team': 0},
    'WIN_PROBABILITY_ADDED': {'team': 0},
    'LEVERAGE_INDEX':        {'team': 0},
    'PARK_FACTORS':          {'average': 1},
    # RE24_TRANSITIONS exige tambien event_cd; 23 es el jonron.
    'RE24_TRANSITIONS':      {'re24_transitions_outs': 0, 're24_transitions_value_type': 1, 'event_cd': 23},
    'BREAKEVEN_POINTS':      {},
    'RE_IMPACT':             {'analysis_type': 'sac_bunt'},
}


@pytest.mark.parametrize('matrix_type,extra', sorted(MATRIX_TYPES_CON_PARAMETROS.items()))
def test_los_matrix_types_con_parametros_responden(matrix_type, extra):
    """Barrido de humo sobre los tipos que el otro test deja fuera."""
    payload = {'matrix_type': matrix_type, 'from_season': 2024, 'to_season': 2024, **extra}

    with TestClient(app) as client:
        response = client.post('/api/generate-table', json=payload)

    assert response.status_code == 200, (
        f'{matrix_type} devolvio {response.status_code}: {response.text[:300]}'
    )
    cuerpo = response.json()
    assert isinstance(cuerpo, dict) and cuerpo, f'{matrix_type} devolvio un cuerpo vacio'


def test_el_barrido_de_humo_cubre_los_dieciocho_matrix_types():
    """
    Los dos barridos juntos deben cubrir el patron de TableRequest.

    Sin esto, anadir un tipo nuevo al patron lo deja sin cobertura en silencio,
    que es justo como R24_FREQUENCIES se rompio sin que nadie lo viera.
    """
    import re

    fuente = (Path(__file__).resolve().parent.parent / 'app' / 'main.py').read_text(encoding='utf-8')
    m = re.search(r'matrix_type: str = Field\(\.\.\., pattern="\^\(([^)]*)\)\$"', fuente)
    assert m, 'no se encontro el patron de matrix_type en app/main.py'
    declarados = set(m.group(1).split('|'))

    simples = {
        'RE24', 'RE24_PCT', 'RE288', 'RE288_FREQ', 'R24_FREQUENCIES',
        'WOBA_COEFFS', 'FIP_CONST', 'PYTHAG_EXPONENTS', 'RUNS_PER_WIN',
    }
    cubiertos = simples | set(MATRIX_TYPES_CON_PARAMETROS)

    assert declarados == cubiertos, (
        f'sin barrido de humo: {sorted(declarados - cubiertos)}; '
        f'en el barrido pero ya no en el patron: {sorted(cubiertos - declarados)}'
    )


# ---------------------------------------------------------------------------
# Filtrado por tipo en BREAKEVEN_POINTS (docs/TODO.md 6.6)
# ---------------------------------------------------------------------------

BREAKEVEN_TIPOS = ['stealing', 'running', 'tagging', 'bunt-for-hit',
                   'swing-take', 'swing-take-zone', 'sb_by_count']


@pytest.mark.parametrize('tipo', BREAKEVEN_TIPOS)
def test_breakeven_manda_solo_el_tipo_pedido(tipo):
    """
    Antes llegaban los seis tipos juntos mas la tabla por conteo: 12,309 filas y
    5,328, o sea 2.7 MB crudos. Eligiendo "Stolen Bases" (1,392 filas) se
    descartaba el 90%, y no se ahorraba en cache porque cambiar de tipo llama a
    hideTableContainers() y obliga a pedir otra vez.

    Las TEMPORADAS siguen viniendo todas a proposito: la agregacion por rango la
    hace el frontend y es lo que hace instantaneo el selector.
    """
    from app.config import BREAKEVEN_TYPE_TO_DATA_TYPE

    with TestClient(app) as client:
        r = client.post('/api/generate-table',
                        json={'matrix_type': 'BREAKEVEN_POINTS', 'from_season': 2025,
                              'to_season': 2025, 'breakeven_type': tipo})

    assert r.status_code == 200, r.text
    d = r.json()

    esperado = BREAKEVEN_TYPE_TO_DATA_TYPE[tipo]
    tipos_recibidos = {fila['type'] for fila in d['table_data']}

    if esperado is None:                      # sb_by_count vive en el otro bloque
        assert d['table_data'] == []
        assert d['by_count_data'], 'sb_by_count llego sin datos por conteo'
    else:
        assert tipos_recibidos == {esperado}, f'llegaron tipos de mas: {tipos_recibidos}'
        assert d['by_count_data'] == [], (
            'llego by_count_data sin haberlo pedido: son 878 KB que nadie usa'
        )

    # Las listas de temporadas describen lo disponible y alimentan los
    # desplegables, asi que no deben encogerse con el filtro.
    assert len(d['seasons']) > 100, f"solo {len(d['seasons'])} temporadas"
    assert len(d['seasons_by_count']) > 30, f"solo {len(d['seasons_by_count'])} por conteo"


def test_breakeven_sin_tipo_sigue_mandando_todo():
    """Compatibilidad: un cliente viejo que no manda breakeven_type no se rompe."""
    with TestClient(app) as client:
        r = client.post('/api/generate-table',
                        json={'matrix_type': 'BREAKEVEN_POINTS',
                              'from_season': 2025, 'to_season': 2025})

    d = r.json()
    assert len({fila['type'] for fila in d['table_data']}) == 6
    assert d['by_count_data']


# ---------------------------------------------------------------------------
# CSP con nonce en script-src (docs/TODO.md 5.4)
# ---------------------------------------------------------------------------

def test_la_csp_usa_nonce_y_no_unsafe_inline_en_scripts(app_en_produccion):
    """
    Con 'unsafe-inline' el navegador ejecuta cualquier <script> inyectado y la
    CSP no sirve de nada frente a un XSS. Con nonce solo corre los que llevan el
    valor de esa peticion, que el atacante no puede adivinar.
    """
    import re as _re
    m = app_en_produccion

    with TestClient(m.app) as client:
        r = client.get('/')

    csp = r.headers['Content-Security-Policy']
    script_src = _re.search(r'script-src ([^;]*)', csp).group(1)

    assert "'unsafe-inline'" not in script_src, f'script-src sigue abierto: {script_src}'
    nonce = _re.search(r"'nonce-([^']+)'", script_src)
    assert nonce, f'script-src no lleva nonce: {script_src}'

    # Y el nonce de la cabecera tiene que ser el que llevan los <script> del HTML,
    # o la pagina se queda sin ejecutar su propio codigo.
    assert f'nonce="{nonce.group(1)}"' in r.text, 'el HTML no lleva el nonce de la cabecera'


def test_el_nonce_cambia_en_cada_peticion(app_en_produccion):
    """Un nonce fijo es adivinable, y entonces no protege de nada."""
    import re as _re
    m = app_en_produccion

    with TestClient(m.app) as client:
        vistos = {
            _re.search(r"'nonce-([^']+)'", client.get('/').headers['Content-Security-Policy']).group(1)
            for _ in range(5)
        }

    assert len(vistos) == 5, f'el nonce se repite entre peticiones: {vistos}'


def test_style_src_sigue_permitiendo_inline_a_proposito(app_en_produccion):
    """
    Decision medida, no olvido. Los nonces NO cubren atributos style="...", y el
    frontend genera 86 desde JavaScript con valores calculados (heatmap, badges
    de fiabilidad). Cerrarlo obligaria a reescribir el renderizado a clases CSS,
    y un style= inyectado no ejecuta codigo.

    Si algun dia se refactoriza, este test es el recordatorio de por que estaba.
    """
    import re as _re
    m = app_en_produccion

    with TestClient(m.app) as client:
        csp = client.get('/').headers['Content-Security-Policy']

    assert "'unsafe-inline'" in _re.search(r'style-src ([^;]*)', csp).group(1)


def test_la_plantilla_no_lleva_su_propia_csp():
    """
    Habia un <meta> CSP ademas de la cabecera. Dos politicas se aplican LAS DOS y
    gana la mas restrictiva: el meta permitia "https:" entero en script-src, asi
    que no relajaba nada pero hacia creer que la politica era mucho mas laxa.
    """
    from app.config import TEMPLATES_DIR

    html = (TEMPLATES_DIR / 'index.html').read_text(encoding='utf-8')
    assert 'http-equiv="Content-Security-Policy"' not in html


# ---------------------------------------------------------------------------
# verify_origin en la API de tablas (docs/TODO.md 5.5)
# ---------------------------------------------------------------------------

@pytest.fixture
def app_con_origen_exigido(monkeypatch):
    """Enciende API_REQUIRE_ORIGIN y devuelve el modulo a su sitio al salir."""
    import importlib

    import app.main

    monkeypatch.setenv('API_REQUIRE_ORIGIN', 'true')
    monkeypatch.setenv('ENVIRONMENT', 'development')   # sin el redirect HTTPS de por medio
    yield importlib.reload(app.main)
    monkeypatch.undo()
    importlib.reload(app.main)


TABLA = {'matrix_type': 'RE24', 'from_season': 2024, 'to_season': 2024}


def test_la_api_de_tablas_rechaza_sin_origen(app_con_origen_exigido):
    """
    /api/generate-table es el endpoint que hace el trabajo; solo
    /api/download-stats estaba protegido. Ahora una llamada sin Origin ni Referer
    conocidos se rechaza.
    """
    with TestClient(app_con_origen_exigido.app) as client:
        r = client.post('/api/generate-table', json=TABLA)

    assert r.status_code == 403, f'devolvio {r.status_code}, deberia rechazar'


@pytest.mark.parametrize('cabecera', ['origin', 'referer'])
def test_la_api_de_tablas_acepta_un_origen_conocido(cabecera, app_con_origen_exigido):
    """Control: con la cabecera que pone el navegador, pasa."""
    permitido = app_con_origen_exigido.ALLOWED_ORIGINS[0]
    valor = permitido if cabecera == 'origin' else f'{permitido}/'

    with TestClient(app_con_origen_exigido.app) as client:
        r = client.post('/api/generate-table', json=TABLA, headers={cabecera: valor})

    assert r.status_code == 200, f'{cabecera}={valor} devolvio {r.status_code}: {r.text[:200]}'


def test_la_puerta_se_puede_apagar_para_desarrollo(monkeypatch):
    """
    Sin esta salida, curl y Postman dejan de funcionar contra la instancia local
    -- y los 151 tests tambien, que fue justo lo que paso al activarla.
    """
    import importlib

    import app.main

    monkeypatch.setenv('API_REQUIRE_ORIGIN', 'false')
    m = importlib.reload(app.main)
    try:
        with TestClient(m.app) as client:
            r = client.post('/api/generate-table', json=TABLA)
        assert r.status_code == 200, r.text
    finally:
        monkeypatch.undo()
        importlib.reload(app.main)


def test_healthz_no_exige_origen(app_con_origen_exigido):
    """
    El healthcheck del contenedor no manda Origin. Si la puerta lo alcanzara,
    Docker marcaria el contenedor como no sano.
    """
    with TestClient(app_con_origen_exigido.app) as client:
        assert client.get('/healthz').status_code == 200


# ---------------------------------------------------------------------------
# /healthz como sonda de verdad (para Uptime Kuma y el HEALTHCHECK de Docker)
# ---------------------------------------------------------------------------

def test_healthz_responde_ok_con_los_datos_en_su_sitio():
    with TestClient(app) as client:
        r = client.get('/healthz')

    assert r.status_code == 200
    d = r.json()
    assert d['status'] == 'ok'
    # Sin cache: con una CDN delante, un 200 guardado taparia una caida real.
    assert 'no-store' in r.headers.get('cache-control', '')


def _sondear(data_dir, monkeypatch):
    """
    Llama a healthz() directamente, sin levantar la app.

    Con TestClient no vale: el lifespan carga los datos esenciales desde
    DATA_DIR, asi que apuntarlo a un directorio roto revienta el ARRANQUE y nunca
    se llega a medir la sonda, que es lo que se quiere probar.
    """
    import asyncio

    from fastapi import Response

    import app.main as m

    monkeypatch.setattr(m, 'DATA_DIR', data_dir)
    respuesta = Response()
    cuerpo = asyncio.run(m.healthz(respuesta))
    return respuesta, cuerpo


def test_healthz_avisa_cuando_faltan_los_datos(tmp_path, monkeypatch):
    """
    Antes devolvia 200 SIEMPRE mientras el proceso viviera. Si faltaba un parquet,
    todas las tablas daban 500 y el monitor seguia en verde.
    """
    respuesta, cuerpo = _sondear(tmp_path, monkeypatch)     # directorio vacio

    assert respuesta.status_code == 503, f'devolvio {respuesta.status_code} sin datos'
    assert cuerpo['status'] == 'unhealthy'
    assert cuerpo['problems'], 'no dice que falta'


def test_healthz_detecta_un_parquet_corrupto(tmp_path, monkeypatch):
    """
    Un parquet truncado EXISTE y pesa: comprobar solo la presencia no lo detecta.
    Por eso el mas grande se abre de verdad.
    """
    import shutil

    import app.main as m

    for nombre in m.HEALTH_REQUIRED_FILES:
        shutil.copy(m.DATA_DIR / nombre, tmp_path / nombre)
    (tmp_path / 're24_values.parquet').write_bytes(b'PAR1' + bytes(500))

    respuesta, cuerpo = _sondear(tmp_path, monkeypatch)

    assert respuesta.status_code == 503, 'un parquet corrupto paso como sano'
    assert any('no se puede leer' in p for p in cuerpo['problems'])


# ---------------------------------------------------------------------------
# verify_origin endurecido (docs/reviews/code_review_v04.md, CR-04-02)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('referer_falso', [
    'https://sabertables.com.loquesea.invalid/tablas',   # el dominio real como PREFIJO
    'https://sabertables.com.evil.test/',
    'https://sabertables.comevil.test/',
])
def test_un_referer_con_el_dominio_de_prefijo_no_cuela(referer_falso, app_con_origen_exigido):
    """
    Antes era referer.startswith(allowed). Basta registrar un dominio que tenga
    el tuyo de prefijo -- "sabertables.com.loquesea.invalid" empieza por
    "https://sabertables.com" -- para saltarse la comprobacion.
    """
    with TestClient(app_con_origen_exigido.app) as client:
        r = client.post('/api/generate-table', json=TABLA, headers={'referer': referer_falso})

    assert r.status_code == 403, f'{referer_falso} paso la comprobacion'


def test_un_origin_no_permitido_no_lo_rescata_el_referer(app_con_origen_exigido):
    """
    Antes bastaba con que UNA de las dos cabeceras casara. Un Origin invalido
    quedaba rescatado por el Referer, o sea que la cabecera mas fiable de las dos
    quedaba subordinada a la mas debil.
    """
    m = app_con_origen_exigido
    legitimo = m.ALLOWED_ORIGINS[0]

    with TestClient(m.app) as client:
        r = client.post('/api/generate-table', json=TABLA,
                        headers={'origin': 'https://evil.test', 'referer': f'{legitimo}/'})

    assert r.status_code == 403, 'un Origin no permitido paso porque el Referer casaba'


def test_el_referer_sigue_valiendo_cuando_no_hay_origin(app_con_origen_exigido):
    """Control: sin Origin, un Referer legitimo sigue bastando."""
    m = app_con_origen_exigido
    legitimo = m.ALLOWED_ORIGINS[0]

    with TestClient(m.app) as client:
        r = client.post('/api/generate-table', json=TABLA,
                        headers={'referer': f'{legitimo}/alguna/ruta?x=1'})

    assert r.status_code == 200, r.text


def test_un_host_ajeno_se_rechaza(app_en_produccion):
    """
    TrustedHostMiddleware se importaba y nunca se registraba, asi que la cabecera
    Host -- que la pone el cliente -- no se validaba. Es la que alimentaba el open
    redirect del 5.6; esto protege al codigo futuro que vuelva a fiarse de ella.
    """
    with TestClient(app_en_produccion.app) as client:
        r = client.get('/', headers={'host': 'evil.test'}, follow_redirects=False)

    assert r.status_code == 400, f'un Host ajeno devolvio {r.status_code}'


def test_localhost_se_acepta_para_que_no_muera_el_healthcheck(app_en_produccion):
    """
    El HEALTHCHECK del contenedor llama a http://localhost:8000/healthz/, o sea
    que manda Host: localhost. Sin el en la lista, Docker marcaria el contenedor
    como no sano y el despliegue no arrancaria.
    """
    assert 'localhost' in app_en_produccion.ALLOWED_HOSTS

    with TestClient(app_en_produccion.app) as client:
        r = client.get('/healthz', headers={'host': 'localhost:8000'})

    assert r.status_code == 200, f'localhost:8000 devolvio {r.status_code}'


@pytest.mark.parametrize('average,esperado', [(1, 200), (3, 200), (5, 200), (7, 422), (0, 422)])
def test_park_factors_declara_sus_ventanas_validas(average, esperado):
    """
    `average` era `int | None`, asi que un 7 llegaba al generador y fallaba como
    "no data" -- un error de datos donde correspondia uno de validacion, invisible
    en el esquema OpenAPI. Ahora es Literal[1, 3, 5] y lo rechaza Pydantic con 422.
    """
    with TestClient(app) as client:
        r = client.post('/api/generate-table',
                        json={'matrix_type': 'PARK_FACTORS', 'from_season': 2024,
                              'to_season': 2024, 'average': average})

    assert r.status_code == esperado, f'average={average} devolvio {r.status_code}: {r.text[:200]}'


# ---------------------------------------------------------------------------
# SEO (7.2)
#
# Todo esto son cosas que fallan en SILENCIO: la pagina sigue funcionando, nadie
# ve un error, y el problema solo aparece cuando alguien comparte un enlace o
# cuando un buscador pasa. Justo el tipo de cosa que hay que atar con un test.
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """
    Como el `with TestClient(app)` que usan los tests de arriba, pero reutilizable.
    El `with` importa: dispara el lifespan, sin el cual los datasets no se cargan.
    """
    with TestClient(app) as c:
        yield c


def _head(client):
    return client.get("/").text


def test_las_dimensiones_declaradas_del_og_image_son_las_reales(client):
    """
    Facebook y LinkedIn se creen lo declarado para reservar el hueco ANTES de
    bajar la imagen. Aqui decia 1200x630 apuntando a un PNG de 1024x1024, o sea
    que la tarjeta se montaba con el encuadre equivocado.
    """
    import re
    import struct
    from pathlib import Path

    html = _head(client)
    src = re.search(r'property="og:image" content="([^"]+)"', html).group(1)
    ancho = int(re.search(r'property="og:image:width" content="(\d+)"', html).group(1))
    alto = int(re.search(r'property="og:image:height" content="(\d+)"', html).group(1))

    fichero = Path(__file__).parent.parent / "app" / "static" / src.split("/static/", 1)[1]
    assert fichero.exists(), f"og:image apunta a un fichero que no existe: {src}"

    datos = fichero.read_bytes()
    w, h = struct.unpack(">II", datos[16:24])  # cabecera IHDR del PNG
    assert (ancho, alto) == (w, h), f"declarado {ancho}x{alto}, real {w}x{h}"


def test_la_tarjeta_grande_necesita_una_imagen_apaisada(client):
    """
    `summary_large_image` recorta al centro en 2:1. Con una imagen cuadrada eso
    se come la parte de arriba y la de abajo -- en este logo, justo donde va el
    texto "SABERTABLES.COM".
    """
    import re

    html = _head(client)
    if 'name="twitter:card" content="summary_large_image"' not in html:
        pytest.skip("la tarjeta no es la grande; el recorte 2:1 no aplica")

    ancho = int(re.search(r'property="og:image:width" content="(\d+)"', html).group(1))
    alto = int(re.search(r'property="og:image:height" content="(\d+)"', html).group(1))

    assert ancho / alto == pytest.approx(2.0, abs=0.15), (
        f"imagen {ancho}x{alto}: proporcion {ancho / alto:.2f}, se recortara"
    )
    assert ancho >= 600 and alto >= 315, "por debajo de 600x315 X degrada la tarjeta"


def test_og_y_twitter_apuntan_a_la_misma_imagen(client):
    """Divergir aquí da una tarjeta distinta en cada red sin razón."""
    import re

    html = _head(client)
    og = re.search(r'property="og:image" content="([^"]+)"', html).group(1)
    tw = re.search(r'name="twitter:image" content="([^"]+)"', html).group(1)

    assert og == tw


def test_los_datos_estructurados_son_json_valido(client):
    """
    Un JSON-LD mal formado lo descarta el buscador entero, sin avisar. Y aquí se
    escribe a mano dentro de la plantilla.
    """
    import json
    import re

    bloques = re.findall(r'(?s)application/ld\+json[^>]*>(.*?)</script>', _head(client))

    assert bloques, "no hay datos estructurados"
    for b in bloques:
        json.loads(b)  # revienta el test si no es JSON


def test_hay_un_bloque_Dataset_para_dataset_search(client):
    """
    Lo que se publica aquí son datos, y Google los indexa por un canal aparte
    (Dataset Search) que solo mira el tipo Dataset. Un WebApplication describe la
    herramienta, no el conjunto de datos.
    """
    import json
    import re

    tipos = {
        json.loads(b)["@type"]
        for b in re.findall(r'(?s)application/ld\+json[^>]*>(.*?)</script>', _head(client))
    }

    assert "Dataset" in tipos, f"solo hay {tipos}"


def test_robots_lo_sirve_el_fichero_y_no_una_copia_en_el_codigo(client):
    """
    Había dos fuentes: app/static/robots.txt y una cadena escrita a mano en la
    ruta. Editar el fichero no cambiaba nada, y nadie lo habría notado hasta que
    las dos dijeran cosas distintas -- un Disallow puesto en el fichero que el
    sitio nunca llegaría a anunciar.
    """
    from pathlib import Path

    def normalizado(t):
        # El fichero esta en CRLF y read_text lo convierte a LF; comparar en
        # crudo solo probaria el fin de linea, que a un rastreador le da igual.
        return [l.strip() for l in t.splitlines() if l.strip()]

    servido = client.get("/robots.txt").text
    fichero = (Path(__file__).parent.parent / "app" / "static" / "robots.txt").read_text()

    assert normalizado(servido) == normalizado(fichero)


def test_el_sitemap_que_anuncia_robots_existe_y_responde(client):
    """
    Si la URL del Sitemap no responde, el buscador se queda sin el índice y no
    hay ningún error visible en ninguna parte.
    """
    import re

    robots = client.get("/robots.txt").text
    ruta = "/" + re.search(r"Sitemap:\s*\S+//[^/]+/(\S+)", robots).group(1)

    r = client.get(ruta)

    assert r.status_code == 200
    assert "<urlset" in r.text


def test_no_hay_favicons_duplicados(client):
    """Había dos <link rel="icon"> al mismo favicon.ico, uno de ellos sobrante."""
    import re

    hrefs = re.findall(r'<link rel="icon"[^>]*href="([^"]+)"', _head(client))

    assert len(hrefs) == len(set(hrefs)), f"repetidos: {hrefs}"


def test_la_portada_sirve_texto_indexable_sin_javascript(client):
    """
    Es una SPA: las tablas las pinta el JS. Lo que un buscador ve sin ejecutar
    nada es solo el HTML del servidor, y ahí tiene que haber contenido de verdad
    -- hoy hay ~6.000 caracteres. Si un refactor lo vaciara, la portada dejaría
    de posicionar sin que nada fallara.
    """
    import html as html_mod
    import re

    d = _head(client)
    cuerpo = d[d.find("<body"):]
    cuerpo = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", " ", cuerpo)
    texto = re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"(?s)<[^>]+>", " ", cuerpo))).strip()

    assert len(texto) > 3000, f"solo {len(texto)} caracteres indexables"


def test_la_portada_tiene_un_solo_h1(client):
    """Varios h1 diluyen cuál es el tema de la página."""
    import re

    assert len(re.findall(r"<h1[ >]", _head(client))) == 1


def test_los_workers_por_defecto_son_dos():
    """
    Los workers NO comparten memoria: cada uno carga su propia copia de los
    parquets. Medido: 427 MB con 4 tras pedir tres tablas pesadas, 226 MB con 2.
    Y no se gana concurrencia -- worker_class es UvicornWorker, asincrono.

    El Dockerfile y el compose ya ponian WORKERS=2, pero el defecto del codigo
    seguia en 4, asi que un arranque sin esas variables se llevaba cuatro copias
    sin que nada lo dijera. Se comprueban los DOS sitios: gunicorn.conf.py es el
    que manda en produccion, main.py el que vale al arrancar con uvicorn.
    """
    import os
    import re
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent
    previo = os.environ.pop("WORKERS", None)
    try:
        for fichero, patron in (
            ("app/gunicorn.conf.py", r'workers\s*=\s*int\(os\.getenv\("WORKERS",\s*"(\d+)"\)\)'),
            ("app/main.py", r'WORKERS\s*=\s*int\(os\.getenv\("WORKERS",\s*"(\d+)"\)\)'),
        ):
            texto = (raiz / fichero).read_text(encoding="utf-8")
            m = re.search(patron, texto)
            assert m, f"no se encontro el defecto de WORKERS en {fichero}"
            assert m.group(1) == "2", f"{fichero} tiene el defecto en {m.group(1)}, no en 2"
    finally:
        if previo is not None:
            os.environ["WORKERS"] = previo


def test_el_valor_por_defecto_de_workers_es_2():
    """
    Los workers NO comparten memoria: cada uno carga su copia de los parquets.
    Medido: 427 MB con 4 tras pedir tres tablas pesadas, 226 MB con 2. Y no se
    pierde concurrencia, porque worker_class es UvicornWorker, asincrono --
    cuatro workers no dan cuatro veces la capacidad, dan cuatro copias.

    El compose lo pone a 2, pero un arranque SIN compose se llevaba el defecto
    del codigo. El defecto tiene que ser el valor bueno, no el que hay que
    corregir desde fuera.
    """
    import os
    import re
    from pathlib import Path

    fuente = (Path(__file__).parent.parent / "app" / "main.py").read_text(encoding="utf-8")
    m = re.search(r'WORKERS = int\(os\.getenv\("WORKERS", "(\d+)"\)\)', fuente)

    assert m, "no se encuentra la declaracion de WORKERS"
    assert m.group(1) == "2", f"el defecto es {m.group(1)}, deberia ser 2"
    assert os.getenv("WORKERS") is None or True  # el entorno sigue mandando


# La atribucion a Retrosheet es ademas el keyword del monitor de Uptime Kuma que
# vigila la portada. Se eligio por ser lo mas estable de la pagina --es
# obligatoria mientras el sitio use sus datos-- y por estar al 97% del HTML, con
# lo que su presencia prueba que la pagina se renderizo ENTERA.
MONITOR_KEYWORD = "copyrighted by Retrosheet"


def test_el_keyword_del_monitor_sigue_en_la_portada(client):
    """
    Si este texto desaparece o se parte, el monitor de Uptime Kuma se pone rojo
    con el sitio perfectamente sano, y nadie sabra por que. Cambiarlo obliga a
    cambiarlo tambien en Kuma: eso es lo que este test recuerda.
    """
    assert MONITOR_KEYWORD in client.get("/").text


def test_el_keyword_del_monitor_esta_al_final_de_la_pagina(client):
    """
    Un keyword al principio no prueba nada: si el HTML se cortara a medias,
    seguiria casando. Tiene que estar en el ultimo tramo para que encontrarlo
    signifique que la pagina llego entera.
    """
    html = client.get("/").text
    posicion = html.find(MONITOR_KEYWORD) / len(html)

    assert posicion > 0.80, f"esta al {posicion:.0%} de la pagina; deberia estar al final"


def test_el_keyword_del_monitor_no_depende_de_javascript(client):
    """
    Uptime Kuma NO ejecuta JavaScript: busca en el HTML tal como llega. Si el
    texto pasara a inyectarse desde el cliente, el monitor dejaria de verlo.
    """
    import re

    html = client.get("/").text
    sin_scripts = re.sub(r"(?s)<script.*?</script>", " ", html)

    assert MONITOR_KEYWORD in sin_scripts


def test_la_sonda_de_salud_responde_lo_que_espera_el_monitor(client):
    """
    El monitor principal busca la cadena exacta `"status":"ok"`, sin espacios.
    Si la serializacion cambiara y metiera un espacio tras los dos puntos, el
    monitor se pondria rojo sin que nada estuviera roto.
    """
    r = client.get("/healthz")

    assert r.status_code == 200
    assert '"status":"ok"' in r.text.replace(", ", ",")
    assert "no-store" in r.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# El host canonico (7.3)
#
# nginx redirige www -> apex con 301. Si la aplicacion declara www como canonico,
# esta senalando una URL que rebota: senal contradictoria para los buscadores y
# una redireccion de mas en cada tarjeta social. Uno de los dos tiene que ceder,
# y cedio la aplicacion porque nginx ya habia decidido.
# ---------------------------------------------------------------------------

HOST_CANONICO = "https://sabertables.com"


def test_el_canonical_no_apunta_a_un_host_que_redirige(client):
    """
    En produccion, https://www.sabertables.com/ devuelve 301 hacia el apex.
    Declarar www como canonico manda a Google a una URL que rebota.
    """
    import re

    html = client.get("/").text
    canonical = re.search(r'<link rel="canonical" href="([^"]+)"', html).group(1)

    assert canonical.startswith(HOST_CANONICO + "/"), canonical
    assert "//www." not in canonical


def test_todas_las_urls_absolutas_usan_el_mismo_host(client):
    """
    canonical, og:url, twitter:url, las imagenes y los datos estructurados tienen
    que apuntar al mismo sitio. Mezclarlos reparte las senales entre dos hosts
    que el buscador ve como paginas distintas.
    """
    import re

    html = client.get("/").text
    urls = re.findall(r'https://(?:www\.)?sabertables\.com[^"\'\s]*', html)

    con_www = sorted({u for u in urls if u.startswith("https://www.")})

    assert urls, "no hay ninguna URL absoluta; el test no esta mirando nada"
    assert not con_www, f"siguen apuntando a www: {con_www}"


def test_el_sitemap_lista_el_host_canonico(client):
    """
    Un sitemap que lista una URL que redirige es una contradiccion mas.

    Se miran SOLO los <loc>. El documento declara namespaces que apuntan a
    http://www.sitemaps.org/..., que son estandar y no tienen nada que ver con
    el host del sitio: buscar "//www." en todo el fichero da un falso positivo.
    """
    import re

    xml = client.get("/sitemap.xml").text
    locs = re.findall(r"<loc>([^<]+)</loc>", xml)

    assert locs, "el sitemap no lista ninguna URL"
    for url in locs:
        assert url.startswith(HOST_CANONICO), url


def test_robots_anuncia_el_sitemap_en_el_host_canonico(client):
    assert "//www." not in client.get("/robots.txt").text


def test_www_sigue_aceptandose_como_Host(client):
    """
    Que no sea el canonico NO significa que se rechace. nginx redirige antes de
    llegar aqui, pero si algun dia deja de hacerlo --o alguien llega por otra
    via-- la aplicacion no debe responder 400 a un dominio que es suyo.
    """
    from app.main import ALLOWED_HOSTS, DEFAULT_ALLOWED_HOSTS

    # El DEFECTO del codigo, que es lo que se lleva un despliegue sin la
    # variable de entorno. Mirar solo ALLOWED_HOSTS no vale: conftest.py la fija,
    # asi que el test estaria validando su propia fixture y no el codigo -- de
    # hecho asi paso muda la primera mutacion que le lance.
    # Partida por comas y NO con `in` sobre la cadena: "sabertables.com" es
    # subcadena de "www.sabertables.com", asi que un defecto que solo tuviera www
    # pasaria el test igual. Tambien se colo asi en la segunda mutacion.
    por_defecto = {h.strip() for h in DEFAULT_ALLOWED_HOSTS.split(",")}

    assert "www.sabertables.com" in por_defecto
    assert "sabertables.com" in por_defecto

    # Y el valor efectivo, que es el que de verdad decide si entra la peticion.
    assert "www.sabertables.com" in ALLOWED_HOSTS
    assert "sabertables.com" in ALLOWED_HOSTS


# ---------------------------------------------------------------------------
# Nombre accesible de las tablas (CR-06-05)
# ---------------------------------------------------------------------------

def test_todas_las_tablas_tienen_nombre_accesible(client):
    """
    El titulo se pinta FUERA del <table>, asi que un lector de pantalla anuncia
    "tabla" sin mas. La pagina tiene catorce, todas parecidas y varias que
    aparecen y desaparecen segun lo que elijas: sin nombre no hay forma de saber
    en cual estas.

    `aria-labelledby` apuntando al titulo lo resuelve sin cambiar nada visual.
    """
    import re

    html = _head(client)
    tablas = re.findall(r"<table[^>]*>", html)

    sin_nombre = [t for t in tablas
                  if "aria-labelledby" not in t and "aria-label" not in t]

    assert tablas, "no hay tablas; el test no esta mirando nada"
    assert not sin_nombre, f"{len(sin_nombre)} tablas sin nombre accesible"


def test_el_nombre_accesible_apunta_a_un_elemento_que_existe(client):
    """
    Un aria-labelledby que apunta a un id inexistente es peor que no ponerlo:
    el lector no anuncia nada y parece que si esta resuelto.
    """
    import re

    html = _head(client)
    referidos = re.findall(r'<table[^>]*aria-labelledby="([^"]+)"', html)
    ids = set(re.findall(r'id="([^"]+)"', html))

    assert referidos
    for destino in referidos:
        for parte in destino.split():
            assert parte in ids, f"aria-labelledby apunta a un id que no existe: {parte}"
