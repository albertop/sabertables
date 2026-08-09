"""
Que las tablas SE PINTEN. En un navegador de verdad.

POR QUE EXISTE ESTE FICHERO
===========================

Se anadieron tres analisis a RE Impact y las tres tablas salian EN BLANCO. La
suite entera estaba en verde:

- la API devolvia 200,
- el parquet tenia sus 1.392 filas,
- el tipo estaba en el modelo, en el filtro, en el selector, en el mapa de
  descargas y en el de titulos.

Y no se pintaba ni una fila. `renderReImpactPage` tiene SEIS cadenas
`if (analysisType === ...)` --clave del pivote, pivote a columnas, cabeceras,
cuerpo, CSV y JSON-- y al anadir un tipo se parchean las que uno recuerda. Cada
arreglo destapaba la siguiente, una por ronda, con el usuario mirando la pantalla.

Se intento cubrirlo con tests estaticos que buscaban cada tipo en cada mapa del
JS. No sirvio: hay que acertar QUE estructura mirar, y siempre falta una.

Lo unico que detecta "la tabla sale vacia" es pintar la tabla. Eso es esto.

Se marcan con `@pytest.mark.navegador` para poder saltarlos con
`-m "not navegador"` cuando se quiere la suite rapida.
"""
import socket
import threading
import time

import pytest
import uvicorn

pytest.importorskip("playwright", reason="hace falta playwright para el render")
from playwright.sync_api import sync_playwright  # noqa: E402

pytestmark = pytest.mark.navegador

# Los que se piden desde el desplegable de RE Impact.
ANALISIS = ["steal_runs", "running_runs", "tagging_runs", "sac_bunt", "ibb",
            "ph_position", "ph_pitcher", "platoon_baseline", "tto"]


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def sitio():
    """
    Levanta la aplicacion real en un hilo y devuelve su URL.

    En modo desarrollo: con ENVIRONMENT=production forzaria el redirect a HTTPS
    y el navegador no llegaria nunca a la pagina.
    """
    import os

    os.environ["ENVIRONMENT"] = "development"
    os.environ["API_REQUIRE_ORIGIN"] = "false"

    import app.main as m

    puerto = _puerto_libre()
    config = uvicorn.Config(m.app, host="127.0.0.1", port=puerto, log_level="error")
    servidor = uvicorn.Server(config)
    hilo = threading.Thread(target=servidor.run, daemon=True)
    hilo.start()

    for _ in range(100):
        if servidor.started:
            break
        time.sleep(0.1)
    else:
        pytest.fail("el servidor no arranco")

    yield f"http://127.0.0.1:{puerto}"

    servidor.should_exit = True
    hilo.join(timeout=10)


@pytest.fixture(scope="module")
def pagina(sitio):
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pag = navegador.new_page()
        errores = []
        pag.on("pageerror", lambda e: errores.append(str(e)))
        # Fuera todo lo que no sea del sitio.
        #
        # La pagina pide bootstrap, Google Analytics y el boton de Buy Me A
        # Coffee a CDN externos. En un navegador sin salida esas peticiones se
        # quedan colgadas y `networkidle` no llega nunca: el test moria en un
        # timeout de 30 s sin haber probado nada. Bloqueandolas el test es
        # hermetico --no depende de que internet funcione-- y arranca en un
        # segundo.
        pag.route("**/*", lambda ruta: (
            ruta.abort() if "127.0.0.1" not in ruta.request.url else ruta.continue_()
        ))
        pag.goto(sitio, wait_until="domcontentloaded")
        pag.wait_for_selector("#re_impact_type", timeout=15000)
        pag.errores_js = errores
        yield pag
        navegador.close()


def _generar_re_impact(pagina, analisis: str) -> dict:
    """Elige el analisis, pulsa Generate y devuelve lo que quedo en la tabla."""
    return pagina.evaluate(
        """async (analisis) => {
            const ms = document.querySelector('select[name="matrix_type"], #matrix_type');
            ms.value = 'RE_IMPACT';
            ms.dispatchEvent(new Event('change', {bubbles: true}));
            await new Promise(r => setTimeout(r, 400));

            const sel = document.querySelector('#re_impact_type');
            sel.value = analisis;
            sel.dispatchEvent(new Event('change', {bubbles: true}));
            await new Promise(r => setTimeout(r, 400));

            Array.from(document.querySelectorAll('button'))
                 .filter(b => /generate/i.test(b.textContent))[0].click();
            await new Promise(r => setTimeout(r, 3500));

            const cuerpo = document.querySelector('#re-impact-table-body');
            const cabecera = document.querySelector('#re-impact-table-head');
            const titulo = document.querySelector('#re-impact-table-title');
            const filas = Array.from(cuerpo ? cuerpo.querySelectorAll('tr') : []);
            return {
                filas: filas.length,
                celdasConValor: filas.reduce((acc, tr) => acc + Array.from(tr.cells)
                    .filter(c => /[0-9]/.test(c.textContent)).length, 0),
                cabeceras: cabecera ? cabecera.textContent.replace(/\\s+/g, ' ').trim() : '',
                titulo: titulo ? titulo.textContent.replace(/\\s+/g, ' ').trim() : '',
                primera: filas[0] ? filas[0].textContent.replace(/\\s+/g, ' ').trim() : ''
            };
        }""",
        analisis,
    )


@pytest.mark.parametrize("analisis", ANALISIS)
def test_la_tabla_de_re_impact_pinta_filas(pagina, analisis):
    """
    EL TEST QUE FALTABA. Una tabla vacia no da error en ninguna capa: la API
    responde 200, el titulo sale bien y el usuario ve un hueco.
    """
    r = _generar_re_impact(pagina, analisis)

    assert r["filas"] > 0, f"{analisis}: la tabla se pinto SIN FILAS. Titulo: {r['titulo']!r}"


@pytest.mark.parametrize("analisis", ANALISIS)
def test_las_celdas_traen_numeros_y_no_solo_etiquetas(pagina, analisis):
    """
    Una tabla con filas pero con todas las celdas vacias es el mismo fallo un
    paso mas adelante: el pivote encontro los escenarios pero no los valores.
    """
    r = _generar_re_impact(pagina, analisis)

    assert r["celdasConValor"] > 0, f"{analisis}: filas sin un solo numero: {r['primera']!r}"


@pytest.mark.parametrize("analisis", ANALISIS)
def test_el_titulo_no_ensena_el_codigo_interno(pagina, analisis):
    """
    El titulo sale de un mapa con `|| analysisType` de respaldo, asi que un tipo
    sin entrada no falla: pinta `steal_runs` donde deberia decir Stolen Bases.
    """
    r = _generar_re_impact(pagina, analisis)

    assert analisis not in r["titulo"], f"titulo con el codigo crudo: {r['titulo']!r}"


@pytest.mark.parametrize("analisis", ANALISIS)
def test_la_tabla_tiene_cabeceras(pagina, analisis):
    r = _generar_re_impact(pagina, analisis)

    assert r["cabeceras"].strip(), f"{analisis}: la tabla se pinto sin cabeceras"


def test_ningun_error_de_javascript_en_toda_la_sesion(pagina):
    """
    Un `undefined is not a function` a mitad del render deja la tabla a medias y
    solo se ve abriendo la consola del navegador.
    """
    for analisis in ANALISIS:
        _generar_re_impact(pagina, analisis)

    assert not pagina.errores_js, f"errores de JavaScript: {pagina.errores_js[:3]}"


# ---------------------------------------------------------------------------
# Marcadores de fiabilidad
# ---------------------------------------------------------------------------

def _marcadores(pagina) -> dict:
    return pagina.evaluate(
        """() => {
            const marcas = Array.from(document.querySelectorAll('.reliability-marker'));
            if (!marcas.length) return {total: 0};
            const punto = marcas[0].querySelector('span');
            const cs = getComputedStyle(punto);
            const celda = marcas[0].closest('td, th');
            return {
                total: marcas.length,
                color: cs.backgroundColor,
                ancho: punto.getBoundingClientRect().width,
                alto: punto.getBoundingClientRect().height,
                redondeado: cs.borderRadius,
                colorDelTexto: celda ? getComputedStyle(celda).color : null,
                textoCrudo: marcas.map(m => m.textContent).join('')
            };
        }"""
    )


def test_los_marcadores_de_fiabilidad_se_ven(pagina):
    """
    Se dibujan con CSS y no con los caracteres U+25CF / U+2022.

    Con caracteres, la forma la decide la tipografia: si no tiene el glifo, el
    navegador sustituye por otra fuente y salen ROMBOS BLANCOS en vez de puntos.
    Eso es lo que se veia, y ningun test lo detectaba.
    """
    _generar_re_impact(pagina, "steal_runs")
    m = _marcadores(pagina)

    assert m["total"] > 0, "no se pinto ningun marcador de fiabilidad"
    assert m["ancho"] > 0 and m["alto"] > 0, "el marcador ocupa cero: no se ve"


def test_los_marcadores_son_naranjas_y_no_del_color_del_texto(pagina):
    """
    Heredaban `currentColor`, o sea el blanco del texto de la celda. Un marcador
    del mismo color que el numero al que avisa no avisa de nada.
    """
    _generar_re_impact(pagina, "steal_runs")
    m = _marcadores(pagina)

    assert m["color"] != m["colorDelTexto"], (
        f"el marcador tiene el mismo color que el texto: {m['color']}"
    )
    rgb = [int(x) for x in m["color"].replace("rgb(", "").replace(")", "").split(",")[:3]]
    assert rgb[0] > rgb[2] and rgb[0] > 150, f"no parece naranja: {m['color']}"


def test_el_marcador_no_depende_de_ningun_caracter(pagina):
    """
    Si vuelve a escribirse con texto, la tipografia decide como se ve y el fallo
    reaparece en cuanto alguien cambie la fuente.
    """
    _generar_re_impact(pagina, "steal_runs")
    m = _marcadores(pagina)

    assert not m["textoCrudo"].strip(), f"el marcador lleva texto: {m['textoCrudo']!r}"


def test_el_marcador_es_redondo(pagina):
    """Un cuadrado naranja se lee como otra cosa; el sitio usa puntos."""
    _generar_re_impact(pagina, "steal_runs")
    m = _marcadores(pagina)

    assert "50%" in m["redondeado"] or "px" in m["redondeado"], m["redondeado"]


# ---------------------------------------------------------------------------
# El rombo de bases
# ---------------------------------------------------------------------------

# Los que muestran estado de bases. `running_runs` NO esta: estirar el batazo no
# tiene estado de bases, porque el que corre es el bateador.
CON_ROMBO = ["steal_runs", "tagging_runs", "sac_bunt", "ibb"]


def _rombos(pagina) -> dict:
    return pagina.evaluate(
        """() => {
            const svgs = document.querySelectorAll('#re-impact-table-body svg');
            const rellenos = Array.from(document.querySelectorAll('#re-impact-table-body svg *'))
                .map(e => e.getAttribute('fill'))
                .filter(f => f && f !== 'none' && !/#fff|white|transparent/i.test(f));
            return { svgs: svgs.length, iluminadas: rellenos.length,
                     colores: [...new Set(rellenos)] };
        }"""
    )


@pytest.mark.parametrize("analisis", CON_ROMBO)
def test_el_rombo_de_bases_se_dibuja(pagina, analisis):
    """
    La tabla dibuja un diagrama del campo con las bases ocupadas. La funcion que
    lo pinta recibe `scenario` esperando "100", "010"... Si se le pasa el texto
    descriptivo ("Double steal"), no reconoce nada y sale el ROMBO VACIO: sin
    error, con la tabla llena de numeros correctos y las bases todas apagadas.
    """
    _generar_re_impact(pagina, analisis)
    r = _rombos(pagina)

    assert r["svgs"] > 0, f"{analisis}: ninguna fila dibuja el rombo de bases"


@pytest.mark.parametrize("analisis", CON_ROMBO)
def test_el_rombo_ilumina_bases_ocupadas(pagina, analisis):
    """
    Un rombo dibujado pero con todas las bases apagadas es el mismo fallo un
    paso mas adelante: el estado de bases no se reconocio.
    """
    _generar_re_impact(pagina, analisis)
    r = _rombos(pagina)

    assert r["iluminadas"] > 0, (
        f"{analisis}: {r['svgs']} rombos y NINGUNA base encendida. "
        f"El `scenario` no es un estado de bases."
    )


@pytest.mark.parametrize("analisis", CON_ROMBO)
def test_las_bases_usan_el_mismo_naranja_que_el_resto_del_sitio(pagina, analisis):
    """
    El color sale del propio sitio, no se elige aparte: si un analisis pinta sus
    bases de otro color, la tabla se lee como si dijera algo distinto.
    """
    _generar_re_impact(pagina, "sac_bunt")
    referencia = set(_rombos(pagina)["colores"])

    _generar_re_impact(pagina, analisis)
    colores = set(_rombos(pagina)["colores"])

    assert colores <= referencia, f"{analisis} usa colores propios: {colores - referencia}"
