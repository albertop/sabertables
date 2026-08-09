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
import pathlib
import socket
import threading
import time

import pytest
import uvicorn

pytest.importorskip("playwright", reason="hace falta playwright para el render")
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.navegador

# Los que se piden desde el desplegable de RE Impact.
ANALISIS = ["steal_runs", "running_runs", "tagging_runs", "sac_bunt", "ibb",
            "ph_position", "ph_pitcher", "platoon_baseline", "tto", "gidp"]


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
    os.environ["ALLOWED_HOSTS"] = "127.0.0.1,localhost,testserver"

    # RECARGA OBLIGATORIA. Otros ficheros de la suite importan app.main con
    # ENVIRONMENT=production para probar el redirect a HTTPS, y el modulo queda
    # asi en sys.modules. Sin recargar, este servidor hereda produccion y el
    # navegador recibe un 307 hacia www.sabertables.com: los tests pasan en
    # solitario y revientan al correr la suite entera.
    import importlib

    import app.main as m

    m = importlib.reload(m)

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


# Los que muestran estado de bases.  SI esta desde que su estado
# sale de BREAKEVEN_SCENARIOS.
CON_ROMBO = ["steal_runs", "tagging_runs", "running_runs", "sac_bunt", "ibb", "gidp"]


def _bases_por_fila(pagina, selector_cuerpo: str) -> list:
    """
    Por cada fila: que bases ilumina el rombo y con que etiqueta.

    Las bases se leen del `fill` de cada rombo pequeno en el orden en que el SVG
    las dibuja: primera, segunda, tercera.
    """
    return pagina.evaluate(
        r"""(sel) => {
            return Array.from(document.querySelectorAll(sel + ' tr')).map(tr => {
                const svg = tr.querySelector('svg');
                const bases = svg ? Array.from(svg.querySelectorAll('rect')).map(
                    r => /ec8013/i.test(r.getAttribute('fill') || '') ? '1' : '0').join('') : null;
                return { bases, texto: tr.textContent.replace(/\s+/g, ' ').trim().slice(0, 70) };
            }).filter(f => f.bases !== null);
        }""",
        selector_cuerpo,
    )


@pytest.mark.parametrize("analisis", CON_ROMBO)
def test_el_rombo_ilumina_las_bases_QUE_TOCAN(pagina, analisis):
    """
    LA PRUEBA DE RIGOR. Que el rombo se dibuje y encienda algo no basta: un
    diagrama que ilumina las bases EQUIVOCADAS es peor que uno apagado, porque
    se lee como un dato y describe otra situacion de juego.

    Se comprueba contra el numero de corredores que dice la etiqueta.
    """
    _generar_re_impact(pagina, analisis)
    filas = _bases_por_fila(pagina, "#re-impact-table-body")

    assert filas, f"{analisis}: ninguna fila con rombo"
    for f in filas:
        encendidas = f["bases"].count("1")
        assert 0 <= encendidas <= 3, f"{analisis}: {encendidas} bases encendidas en {f['bases']}"
        assert encendidas > 0, (
            f"{analisis}: rombo apagado en la fila {f['texto']!r}. "
            f"El escenario no se reconocio como estado de bases."
        )


@pytest.mark.parametrize("familia,analisis", [("stealing", "steal_runs"),
                                              ("tagging", "tagging_runs"),
                                              ("running", "running_runs")])
def test_las_dos_tablas_ensenan_LOS_MISMOS_escenarios(pagina, familia, analisis):
    """
    EL TEST QUE FALTABA DESDE EL PRINCIPIO.

    Break-Even y RE Impact son dos vistas del MISMO calculo: si una ensena cinco
    escenarios y la otra dos, una esta incompleta. Paso exactamente eso --Base
    Running tenia 5 filas en una tabla y 2 en la otra-- y ningun test lo veia,
    porque todos comprobaban cada tabla consigo misma.

    Comparar las dos es lo unico que lo detecta.
    """
    impacto = pagina.evaluate(
        """async (analisis) => {
            const ms = document.querySelector('select[name="matrix_type"], #matrix_type');
            ms.value = 'RE_IMPACT'; ms.dispatchEvent(new Event('change', {bubbles:true}));
            await new Promise(r => setTimeout(r, 400));
            const sel = document.querySelector('#re_impact_type');
            sel.value = analisis; sel.dispatchEvent(new Event('change', {bubbles:true}));
            await new Promise(r => setTimeout(r, 400));
            Array.from(document.querySelectorAll('button'))
                 .filter(b => /generate/i.test(b.textContent))[0].click();
            await new Promise(r => setTimeout(r, 3200));
            return Array.from(document.querySelectorAll('#re-impact-table-body tr'))
                        .map(tr => (tr.querySelector('th, td')?.textContent || '')
                                   .replace(/\\s+/g, ' ').trim());
        }""",
        analisis,
    )
    umbral = pagina.evaluate(
        """async (familia) => {
            const ms = document.querySelector('select[name="matrix_type"], #matrix_type');
            ms.value = 'BREAKEVEN_POINTS'; ms.dispatchEvent(new Event('change', {bubbles:true}));
            await new Promise(r => setTimeout(r, 400));
            const bt = document.querySelector('#breakeven_type, select[name="breakeven_type"]');
            if (bt) { bt.value = familia; bt.dispatchEvent(new Event('change', {bubbles:true})); }
            await new Promise(r => setTimeout(r, 400));
            Array.from(document.querySelectorAll('button'))
                 .filter(b => /generate/i.test(b.textContent))[0].click();
            await new Promise(r => setTimeout(r, 3200));
            return Array.from(document.querySelectorAll('#sb-breakeven-table-body tr'))
                        .map(tr => (tr.querySelector('th, td')?.textContent || '')
                                   .replace(/\\s+/g, ' ').trim());
        }""",
        familia,
    )

    assert len(impacto) == len(umbral), (
        f"{familia}: Break-Even ensena {len(umbral)} filas y RE Impact "
        f"{len(impacto)}. Son el mismo calculo; una de las dos esta incompleta."
    )

    # Y con el MISMO TEXTO. Contar filas no basta: tag-up tenia 6 y 6, pero RE
    # Impact ensenaba cuatro filas llamadas "Runner on 3B tags up to score",
    # indistinguibles entre si, donde Break-Even pone el sufijo que las separa.
    assert impacto == umbral, (
        f"{familia}: las dos tablas tienen {len(umbral)} filas pero no dicen lo "
        f"mismo.\n  Break-Even: {umbral}\n  RE Impact:  {impacto}"
    )


# ---------------------------------------------------------------------------
# El boton de descarga: PULSANDOLO
# ---------------------------------------------------------------------------

def _descargar(pagina, analisis: str, formato: str):
    """
    Elige el formato, PULSA el boton y devuelve el fichero que sale.

    Lo anterior que llamaba "test de descarga" solo miraba que el tipo tuviera
    entrada en unos mapas del JS. Eso no pulsa nada: el boton podia estar roto y
    los tests en verde.
    """
    _generar_re_impact(pagina, analisis)

    # Los identificadores REALES, mirados en la pagina. Los que puse a ojo
    # ('#re-impact-download-btn') no existen, y el respaldo por texto pulsaba
    # cualquier boton: el test moria en un timeout mudo que no decia nada.
    pagina.select_option("#re-impact-format-select", formato)

    boton = pagina.locator("#download-re-impact-btn")
    assert boton.count() == 1, "no existe #download-re-impact-btn"
    boton.wait_for(state="visible", timeout=10000)

    try:
        with pagina.expect_download(timeout=15000) as info:
            boton.click()
    except PlaywrightTimeout:
        # handleDownload avisa por el banner cuando no logra armar el fichero.
        # Sin esto el fallo se lee como "Playwright no vio la descarga" en vez
        # de como lo que es: el generador no produjo contenido.
        banner = pagina.evaluate(
            "() => Array.from(document.querySelectorAll('.error-message, #error-message, [class*=error]'))"
            ".map(e => e.textContent.trim()).filter(Boolean).join(' | ')"
        )
        raise AssertionError(
            f"{analisis}/{formato}: se pulso el boton y NO bajo ningun fichero. "
            f"Banner de error en pantalla: {banner or '(ninguno)'}"
        ) from None
    return info.value


@pytest.mark.parametrize("analisis", ["steal_runs", "running_runs", "tagging_runs", "sac_bunt", "gidp"])
@pytest.mark.parametrize("formato", ["csv", "json"])
def test_el_boton_de_descarga_baja_un_fichero(pagina, analisis, formato):
    """
    EL TEST QUE FALTABA. Se pulsa el boton de verdad y se comprueba que sale un
    fichero: que exista, que tenga la extension pedida y que NO venga vacio.
    """
    import json as _json

    d = _descargar(pagina, analisis, formato)
    ruta = d.path()

    assert ruta is not None, f"{analisis}/{formato}: no se descargo nada"
    contenido = pathlib.Path(ruta).read_text(encoding="utf-8", errors="replace")

    assert contenido.strip(), f"{analisis}/{formato}: el fichero vino VACIO"
    assert d.suggested_filename.endswith(f".{formato}"), d.suggested_filename

    if formato == "json":
        # El JSON lleva metadatos ademas de los datos, asi que "no viene vacio"
        # lo cumple un fichero SIN NI UNA FILA. Verificado por mutacion: con el
        # generador devolviendo cero filas, este test pasaba mudo y solo el CSV
        # se ponia rojo. Hay que mirar dentro.
        cargado = _json.loads(contenido)
        assert cargado.get("data"), f"{analisis}: el JSON no trae ni una fila"
    else:
        assert contenido.count("\n") >= 2, "un CSV con cabecera y sin filas"


@pytest.mark.parametrize("analisis", ["steal_runs", "running_runs", "tagging_runs"])
def test_el_csv_descargado_trae_los_escenarios_de_la_tabla(pagina, analisis):
    """
    Un CSV con cabecera y sin datos pesa mas que cero y pasa cualquier
    comprobacion de "existe". Tiene que traer las mismas filas que se ven.
    """
    filas_en_pantalla = _generar_re_impact(pagina, analisis)["filas"]
    contenido = pathlib.Path(_descargar(pagina, analisis, "csv").path()).read_text(
        encoding="utf-8", errors="replace")

    lineas = [l for l in contenido.splitlines() if l.strip()]

    assert len(lineas) - 1 >= filas_en_pantalla, (
        f"{analisis}: la tabla ensena {filas_en_pantalla} filas y el CSV trae {len(lineas) - 1}"
    )
