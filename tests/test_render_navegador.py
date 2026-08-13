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


# ---------------------------------------------------------------------------
# wRC Components (8.8): la tabla que da al investigador con que calcular
# wRC, wRC+ y wRAA. ESTE TEST SE ESCRIBE ANTES QUE EL CABLEADO.
# ---------------------------------------------------------------------------

def _generar_matriz(pagina, tipo: str) -> dict:
    """
    Elige un tipo de matriz del selector principal, pulsa Generate y devuelve lo
    que quedo pintado.

    NO busca un contenedor por identificador a proposito. El cableado toca ocho
    sitios de app.js y uno puede quedarse sin hacer: si el test conociera el id de
    antemano, podria dar verde mirando una tabla que el usuario no ve. Busca LA
    TABLA VISIBLE, que es lo que hay en la pantalla.
    """
    return pagina.evaluate(
        """async (tipo) => {
            const ms = document.querySelector('select[name="matrix_type"], #matrix_type');
            if (!Array.from(ms.options).some(o => o.value === tipo)) {
                return {error: 'el tipo no esta en el selector', opciones: ms.options.length};
            }
            ms.value = tipo;
            ms.dispatchEvent(new Event('change', {bubbles: true}));
            await new Promise(r => setTimeout(r, 500));

            Array.from(document.querySelectorAll('button'))
                 .filter(b => /generate/i.test(b.textContent))[0].click();
            await new Promise(r => setTimeout(r, 3500));

            const tabla = Array.from(document.querySelectorAll('table'))
                .find(t => t.offsetParent !== null && t.querySelectorAll('tbody tr').length);
            if (!tabla) return {error: 'ninguna tabla visible con filas'};

            const filas = Array.from(tabla.querySelectorAll('tbody tr'));
            return {
                filas: filas.length,
                celdasConValor: filas.reduce((acc, tr) => acc + Array.from(tr.cells)
                    .filter(c => /[0-9]/.test(c.textContent)).length, 0),
                cabeceras: (tabla.querySelector('thead')?.textContent || '')
                           .replace(/\s+/g, ' ').trim(),
                primera: filas[0].textContent.replace(/\s+/g, ' ').trim().slice(0, 120)
            };
        }""",
        tipo,
    )


def test_wrc_components_pinta_la_tabla(pagina):
    """
    EL TEST VA ANTES QUE EL CABLEADO, y hoy tiene que estar ROJO.

    Son trece sitios entre main.py, la plantilla y app.js. Ocho de ellos en
    app.js, dispersos. Esa es exactamente la forma que dejo las tres tablas de RE
    Impact en blanco, una por ronda, sin un solo error en consola y con la API
    devolviendo 200.
    """
    r = _generar_matriz(pagina, "WRC_COMPONENTS")

    assert "error" not in r, f"wRC Components no se pinto: {r}"
    assert r["filas"] > 0, "la tabla se pinto SIN FILAS"
    assert r["celdasConValor"] > 0, f"filas sin un solo numero: {r['primera']!r}"


def test_wrc_components_trae_las_constantes_que_hacen_falta(pagina):
    """
    Que pinte filas no basta: la tabla existe para que un investigador calcule
    wRC, wRC+ y wRAA, y para eso necesita CUATRO constantes. Si falta una, la
    tabla se ve perfecta y no sirve para nada.
    """
    r = _generar_matriz(pagina, "WRC_COMPONENTS")
    assert "error" not in r, f"wRC Components no se pinto: {r}"

    texto = r["cabeceras"].lower()
    faltan = [q for q, patrones in {
        "lgwOBA":            ("woba",),
        "escala wOBA":       ("scale", "escala"),
        "R/PA de liga":      ("r/pa", "runs"),
        "sin lanzadores":    ("pitcher", "lanzador", "non-p", "wrc"),
    }.items() if not any(p in texto for p in patrones)]

    assert not faltan, f"la cabecera no ofrece {faltan}. Cabecera: {r['cabeceras']!r}"


def test_wrc_components_no_ensena_el_codigo_interno(pagina):
    r = _generar_matriz(pagina, "WRC_COMPONENTS")
    assert "error" not in r, f"wRC Components no se pinto: {r}"

    assert "WRC_COMPONENTS" not in r["cabeceras"], (
        f"la cabecera enseña el codigo crudo: {r['cabeceras']!r}"
    )


@pytest.mark.parametrize("formato", ["csv", "json"])
def test_wrc_components_baja_un_fichero(pagina, formato):
    """
    SE PULSA EL BOTON. Comprobar que el tipo existe en los mapas no prueba que
    baje nada: el generador de CSV y el de JSON son OTRA cadena, y si no tiene su
    rama el fichero sale vacio y el navegador ni dispara la descarga -- sin error
    en consola y sin excepcion. Ya paso con los tres analisis en carreras.
    """
    import json as _json

    r = _generar_matriz(pagina, "WRC_COMPONENTS")
    assert "error" not in r, f"no se pinto: {r}"

    pagina.select_option("#wrc-components-format-select", formato)
    boton = pagina.locator("#download-wrc-components-btn")
    assert boton.count() == 1, "no existe #download-wrc-components-btn"

    try:
        with pagina.expect_download(timeout=15000) as info:
            boton.click()
    except PlaywrightTimeout:
        banner = pagina.evaluate(
            "() => Array.from(document.querySelectorAll('.error-message, #error-message, [class*=error]'))"
            ".map(e => e.textContent.trim()).filter(Boolean).join(' | ')"
        )
        raise AssertionError(
            f"{formato}: se pulso el boton y NO bajo nada. Banner: {banner or '(ninguno)'}"
        ) from None

    ruta = info.value.path()
    contenido = pathlib.Path(ruta).read_text(encoding="utf-8", errors="replace")
    assert contenido.strip(), f"{formato}: el fichero vino VACIO"
    assert info.value.suggested_filename.endswith(f".{formato}")

    if formato == "json":
        _json.loads(contenido)
    else:
        assert contenido.count("\n") >= 100, "un CSV sin las 116 temporadas"


# ---------------------------------------------------------------------------
# wRC Components: que se PAREZCA a las demas tablas (PLAN_WRC_COMPONENTS_UI.md)
#
# Tres de estos comprueban el EFECTO, no que el control exista. Es la leccion del
# boton de descarga: estaba puesto y no bajaba nada.
# ---------------------------------------------------------------------------

def _pintar_wrc(pagina):
    """Deja wRC Components en pantalla. Devuelve el numero de filas visibles."""
    r = _generar_matriz(pagina, "WRC_COMPONENTS")
    assert "error" not in r, f"wRC Components no se pinto: {r}"
    return r


def test_wrc_las_cabeceras_llevan_el_fondo_del_sitio(pagina):
    """
    Sin `bg-primary` la cabecera se lee como una fila mas. Todas las demas tablas
    lo llevan; esta se quedo sin el porque me invente la presentacion.
    """
    _pintar_wrc(pagina)
    sin_fondo = pagina.evaluate(
        """() => Array.from(document.querySelectorAll('#wrc-components-table-head th'))
                .filter(th => !th.className.includes('bg-primary'))
                .map(th => th.textContent.trim())"""
    )

    assert not sin_fondo, f"cabeceras sin bg-primary: {sin_fondo}"


def test_wrc_se_puede_ordenar_y_las_filas_CAMBIAN(pagina):
    """
    No basta con que aparezca el icono de ordenar. Se pulsa una cabecera y la
    primera fila tiene que ser OTRA.
    """
    _pintar_wrc(pagina)
    r = pagina.evaluate(
        """async () => {
            const th = document.querySelectorAll('#wrc-components-table-head th');
            if (!th.length) return {error: 'sin cabeceras'};
            const conAccion = Array.from(th).filter(x => x.dataset.accion && x.dataset.clave);
            if (!conAccion.length) return {error: 'ninguna cabecera es ordenable'};
            const primera = () => document.querySelector('#wrc-components-table-body tr')
                                          ?.textContent.replace(/\s+/g, ' ').trim();
            const antes = primera();
            conAccion[0].click();
            await new Promise(r => setTimeout(r, 600));
            return {antes, despues: primera(), ordenables: conAccion.length};
        }"""
    )

    assert "error" not in r, r["error"]
    assert r["antes"] != r["despues"], (
        f"se pulso la cabecera y la tabla no se reordeno: {r['antes']!r}"
    )


def test_wrc_tiene_heatmap_y_arranca_encendido(pagina):
    _pintar_wrc(pagina)
    r = pagina.evaluate(
        """() => {
            const t = document.querySelector('#wrc-components-table-container [id*="heatmap"]');
            return t ? {existe: true, encendido: t.checked} : {existe: false};
        }"""
    )

    assert r["existe"], "no hay interruptor de heatmap"
    assert r["encendido"], "el heatmap existe pero arranca APAGADO"


def test_wrc_el_heatmap_PINTA_y_al_apagarlo_desaparece(pagina):
    """
    Que el interruptor exista no prueba que haga nada. Con el encendido tiene que
    haber celdas con color; al apagarlo, ninguna.
    """
    _pintar_wrc(pagina)
    r = pagina.evaluate(
        """async () => {
            const t = document.querySelector('#wrc-components-table-container [id*="heatmap"]');
            if (!t) return {error: 'no hay interruptor'};
            // La pildora, no el <td>: es el idioma del sitio. Contar fondos de
            // celda daria verde con una presentacion que no se parece a ninguna
            // otra tabla.
            const conColor = () => Array.from(
                document.querySelectorAll('#wrc-components-table-body td span'))
                .filter(sp => sp.style.backgroundColor && sp.style.backgroundColor !== 'transparent').length;
            if (!t.checked) { t.click(); await new Promise(r => setTimeout(r, 500)); }
            const encendido = conColor();
            t.click(); await new Promise(r => setTimeout(r, 500));
            const apagado = conColor();
            t.click(); await new Promise(r => setTimeout(r, 300));
            return {encendido, apagado};
        }"""
    )

    assert "error" not in r, r["error"]
    assert r["encendido"] > 0, "el heatmap esta encendido y no pinta una sola celda"
    assert r["apagado"] == 0, f"se apago el heatmap y quedan {r['apagado']} celdas con color"


def test_wrc_pagina_y_no_vuelca_las_116_temporadas(pagina):
    """
    116 filas de golpe son 5.060 px de alto. Las demas tablas pintan 12 y paginan.
    Y pasar de pagina tiene que cambiar la primera fila, no solo el numerito.
    """
    filas = _pintar_wrc(pagina)["filas"]
    assert filas <= 20, f"se pintaron {filas} filas de golpe; las demas paginan a 12"

    r = pagina.evaluate(
        """async () => {
            const cont = document.getElementById('wrc-components-table-container');
            // Por data-accion, no por texto: el sitio usa flechas (▶, ⏭),
            // asi que buscar "next" no encuentra nada aunque la paginacion exista.
            const botones = Array.from(cont.querySelectorAll('[data-accion*="Page"]'));
            if (!botones.length) return {error: 'no hay control de paginacion'};
            const siguiente = botones.find(b => Number(b.dataset.pagina) === 2 && !b.disabled);
            if (!siguiente) return {error: 'no hay boton para la pagina 2'};
            const primera = () => document.querySelector('#wrc-components-table-body tr')
                                          ?.textContent.replace(/\s+/g, ' ').trim();
            const antes = primera();
            siguiente.click();
            await new Promise(r => setTimeout(r, 600));
            return {antes, despues: primera()};
        }"""
    )

    assert "error" not in r, r["error"]
    assert r["antes"] != r["despues"], "se paso de pagina y siguen las mismas filas"


# ---------------------------------------------------------------------------
# EL TEST QUE FALTABA EN TODO EL SITIO
#
# Todos los demas generan UNA tabla y comprueban ESA. Ninguno cambia de tipo. Por
# eso nadie vio que al anadir wRC Components su contenedor se quedara visible
# encima de las otras: cada tabla se pintaba bien, no habia ni un error de
# JavaScript, y desde fuera parecia que el boton Generate habia dejado de
# funcionar en el sitio entero.
#
# Un contenedor que no se esconde no rompe nada por dentro. Solo tapa.
# ---------------------------------------------------------------------------

TIPOS_DE_MATRIZ = ["RE24", "RE288", "LINEAR_WEIGHTS", "WOBA_COEFFS", "FIP_CONST",
                   "PYTHAG_EXPONENTS", "RUNS_PER_WIN", "WRC_COMPONENTS",
                   "WIN_EXPECTANCY", "LEVERAGE_INDEX"]


@pytest.mark.parametrize("tipo", TIPOS_DE_MATRIZ)
def test_solo_UNA_tabla_queda_visible_al_cambiar_de_tipo(pagina, tipo):
    """
    Se pasa por wRC Components y luego se elige otro tipo, que es el orden en el
    que aparecio el fallo. Al terminar tiene que quedar UN solo contenedor
    visible: el que se pidio.
    """
    _generar_matriz(pagina, "WRC_COMPONENTS")
    r = _generar_matriz(pagina, tipo)
    assert "error" not in r, f"{tipo}: no se pinto: {r}"

    visibles = pagina.evaluate(
        # Dos tablas no usan el sufijo `-table-container`: win-expectancy-container
        # y leverage-index-container. Buscar solo ese sufijo daba CERO visibles y
        # el test fallaba por su cuenta, no por el sitio.
        """() => Array.from(document.querySelectorAll('[id$="-container"]'))
                .filter(c => /table|expectancy|leverage|index/.test(c.id))
                .filter(c => !c.classList.contains('d-none') && c.offsetParent !== null)
                .map(c => c.id)"""
    )

    assert len(visibles) == 1, (
        f"{tipo}: quedaron {len(visibles)} contenedores visibles a la vez: {visibles}. "
        f"Uno tapa al otro y parece que Generate no funciona."
    )


@pytest.mark.parametrize("tipo", TIPOS_DE_MATRIZ)
def test_cada_tipo_pinta_filas_despues_de_pasar_por_otro(pagina, tipo):
    """
    Que la tabla se pinte VINIENDO DE OTRA. Generarla en solitario no prueba que
    el cambio de tipo la deje en buen estado.
    """
    _generar_matriz(pagina, "WRC_COMPONENTS")
    r = _generar_matriz(pagina, tipo)

    assert "error" not in r, f"{tipo}: {r}"
    assert r["filas"] > 0, f"{tipo}: sin filas tras venir de wRC Components"


def test_el_csv_de_wrc_trae_los_decimales_COMPLETOS(pagina):
    """
    El fichero se descarga para CALCULAR con el, no para mirarlo.

    En pantalla los valores van recortados para caber --lgwOBA a cinco decimales--
    pero si el CSV lleva ese recorte, quien lo use arrastra el error a su wRAA y a
    su wRC+ sin enterarse. Un lgwOBA redondeado a 0,31515 en vez de
    0,3151500225067139 mueve el wRC+ de cualquier bateador.

    Se compara CONTRA LO QUE SIRVIO LA API, no contra un numero escrito a mano:
    la celda del CSV tiene que ser el mismo float, exacto.
    """
    r = _generar_matriz(pagina, "WRC_COMPONENTS")
    assert "error" not in r, f"no se pinto: {r}"

    crudo = pagina.evaluate(
        """() => {
            const d = currentTableData.wrc_components;
            const f = (d.table_data || []).find(x => Number(x.season) === 2025);
            return f ? {lgwOBA: f.League_Avg_OBP, escala: f.WOBA_Scale,
                        rpa: f.lg_r_per_pa, cuota: f.lg_share_pa_pitchers} : null;
        }"""
    )
    assert crudo, "no hay fila de 2025 en los datos servidos"

    pagina.select_option("#wrc-components-format-select", "csv")
    with pagina.expect_download(timeout=15000) as info:
        pagina.locator("#download-wrc-components-btn").click()
    contenido = pathlib.Path(info.value.path()).read_text(encoding="utf-8")

    lineas = [ln for ln in contenido.splitlines() if ln.strip()]
    cabecera = [c.strip().strip('"') for c in lineas[0].split(",")]
    fila = next((ln.split(",") for ln in lineas[1:] if ln.split(",")[0] == "2025"), None)
    assert fila, "el CSV no trae la temporada 2025"
    celdas = dict(zip(cabecera, fila, strict=False))

    esperado = {
        "lgwOBA": crudo["lgwOBA"],
        "wOBA Scale": crudo["escala"],
        "lgR/PA": crudo["rpa"],
        "Pitcher PA %": crudo["cuota"],
    }
    for etiqueta, valor in esperado.items():
        assert etiqueta in celdas, f"falta la columna {etiqueta!r} en el CSV"
        assert float(celdas[etiqueta]) == float(valor), (
            f"{etiqueta}: el CSV trae {celdas[etiqueta]} y la API sirvio {valor}. "
            f"Se perdio precision por el camino."
        )

    # Y que no sea el valor de PANTALLA: cinco decimales no bastan para el lgwOBA.
    assert len(celdas["lgwOBA"].split(".")[-1]) > 5, (
        f"el CSV trae el lgwOBA recortado a pantalla: {celdas['lgwOBA']}"
    )


def test_el_json_de_wrc_trae_los_decimales_COMPLETOS(pagina):
    """
    Lo mismo que el CSV, pero el JSON se genera en OTRA funcion --convertToJSON--
    y ya nos paso con RE Impact que una cadena estuviera bien y la otra no.
    Probar solo una de las dos deja la mitad sin vigilar.
    """
    import json as _json

    r = _generar_matriz(pagina, "WRC_COMPONENTS")
    assert "error" not in r, f"no se pinto: {r}"

    crudo = pagina.evaluate(
        """() => {
            const d = currentTableData.wrc_components;
            const f = (d.table_data || []).find(x => Number(x.season) === 2025);
            return f ? {lgwOBA: f.League_Avg_OBP, escala: f.WOBA_Scale,
                        rpa: f.lg_r_per_pa, cuota: f.lg_share_pa_pitchers} : null;
        }"""
    )
    assert crudo, "no hay fila de 2025 en los datos servidos"

    pagina.select_option("#wrc-components-format-select", "json")
    with pagina.expect_download(timeout=15000) as info:
        pagina.locator("#download-wrc-components-btn").click()
    datos = _json.loads(pathlib.Path(info.value.path()).read_text(encoding="utf-8"))

    filas = datos.get("data") or []
    assert filas, "el JSON no trae datos"
    fila = next((f for f in filas if int(f.get("Season")) == 2025), None)
    assert fila, "el JSON no trae la temporada 2025"

    for etiqueta, valor in {
        "lgwOBA": crudo["lgwOBA"],
        "wOBA Scale": crudo["escala"],
        "lgR/PA": crudo["rpa"],
        "Pitcher PA %": crudo["cuota"],
    }.items():
        assert etiqueta in fila, f"falta la clave {etiqueta!r} en el JSON"
        assert float(fila[etiqueta]) == float(valor), (
            f"{etiqueta}: el JSON trae {fila[etiqueta]} y la API sirvio {valor}"
        )

    assert len(repr(float(fila["lgwOBA"])).split(".")[-1]) > 5, (
        f"el JSON trae el lgwOBA recortado a pantalla: {fila['lgwOBA']}"
    )


# ---------------------------------------------------------------------------
# UN SOLO ESTILO DE HEATMAP EN TODO EL SITIO
#
# Al anadir wRC Components pinte el fondo del <td> entero. El resto del sitio
# pinta una PILDORA dentro de la celda --padding, borde redondeado, ancho minimo--
# y la diferencia se ve a simple vista: la celda queda a medias entre los bordes.
#
# El test de "el heatmap pinta" daba verde igualmente, porque pintaba. Lo que no
# comprobaba nadie es que pintara COMO LAS DEMAS.
# ---------------------------------------------------------------------------

TIPOS_CON_HEATMAP = ["RE24", "RE288", "LINEAR_WEIGHTS", "WOBA_COEFFS",
                     "PYTHAG_EXPONENTS", "RUNS_PER_WIN", "WRC_COMPONENTS"]


def _firmas_de_heatmap(pagina, tipo):
    """Recoge la forma de los elementos coloreados: que etiqueta y que estilo."""
    r = _generar_matriz(pagina, tipo)
    if "error" in r:
        return None
    return pagina.evaluate(
        """() => {
            const cont = Array.from(document.querySelectorAll('[id$="-container"]'))
                .find(c => !c.classList.contains('d-none') && c.offsetParent !== null
                        && c.querySelector('tbody tr'));
            if (!cont) return [];
            const pintados = Array.from(cont.querySelectorAll('tbody td, tbody td span'))
                .filter(e => e.style.backgroundColor && e.style.backgroundColor !== 'transparent');
            return [...new Set(pintados.map(e => [
                e.tagName,
                e.style.padding || '(sin padding)',
                e.style.borderRadius || '(sin radio)',
                e.style.display || '(sin display)',
                e.style.minWidth || '(sin ancho minimo)'
            ].join(' | ')))];
        }"""
    )


def test_el_heatmap_se_pinta_IGUAL_en_todas_las_tablas(pagina):
    """
    Recorre las tablas con heatmap y exige UNA sola forma de celda coloreada.

    No compara contra un estilo escrito aqui a mano: compara las tablas entre si.
    Si manana se cambia el estilo del sitio, este test no estorba; solo se queja
    cuando una tabla se sale del resto, que es lo que hay que impedir.
    """
    por_tabla = {}
    for tipo in TIPOS_CON_HEATMAP:
        firmas = _firmas_de_heatmap(pagina, tipo)
        if firmas:
            por_tabla[tipo] = firmas

    assert len(por_tabla) >= 3, f"solo se pudieron medir {list(por_tabla)}"

    todas = {f for firmas in por_tabla.values() for f in firmas}

    assert len(todas) == 1, (
        "el heatmap se pinta de mas de una forma en el sitio:\n  "
        + "\n  ".join(f"{t}: {sorted(f)}" for t, f in sorted(por_tabla.items()))
    )


@pytest.mark.parametrize("tipo", ["WOBA_COEFFS", "RE24", "FIP_CONST"])
def test_al_CAMBIAR_el_desplegable_sin_generar_wrc_desaparece(pagina, tipo):
    """
    EL CASO QUE MIS TESTS NO COGIAN.

    Los otros generan wRC Components y luego generan otra tabla: ahi si se
    escondia, porque el escondite estaba en la ruta de Generate. Pero el usuario
    solo DESPLIEGA el selector, y en ese momento la tabla vieja se queda en
    pantalla con los datos de otra cosa.

    Aqui NO se pulsa Generate a proposito.
    """
    _generar_matriz(pagina, "WRC_COMPONENTS")

    visibles = pagina.evaluate(
        """async (tipo) => {
            const ms = document.querySelector('#matrix_type');
            ms.value = tipo;
            ms.dispatchEvent(new Event('change', {bubbles: true}));
            await new Promise(r => setTimeout(r, 700));
            return Array.from(document.querySelectorAll('[id$="-container"]'))
                .filter(c => /table|expectancy|leverage|index/.test(c.id))
                .filter(c => !c.classList.contains('d-none') && c.offsetParent !== null)
                .map(c => c.id);
        }""",
        tipo,
    )

    assert "wrc-components-table-container" not in visibles, (
        f"se eligio {tipo} en el desplegable y wRC Components sigue en pantalla. "
        f"Visibles: {visibles}"
    )


# ---------------------------------------------------------------------------
# Missed Calls (8.9). ANTES DEL CODIGO: hoy en rojo.
# ---------------------------------------------------------------------------

def test_missed_calls_pinta_la_rejilla_de_24_por_12(pagina):
    """
    La forma es la de RE288, y no por copiarla: los DOS ejes llevan senal, y el
    de bases mas que el del conteo --0,2076 carreras de rango contra 0,1684--.
    En 2-2 el coste va de 0,082 a 0,502 segun las bases, seis veces.

    Colapsar cualquiera de los dos ejes esconderia la mitad de lo que la tabla
    tiene que contar, asi que el test exige los dos: 24 filas y 12 columnas.
    """
    r = pagina.evaluate(
        """async () => {
            const ms = document.querySelector('#matrix_type');
            if (!Array.from(ms.options).some(o => o.value === 'RE_IMPACT')) {
                return {error: 'no hay RE_IMPACT'};
            }
            ms.value = 'RE_IMPACT'; ms.dispatchEvent(new Event('change', {bubbles: true}));
            await new Promise(r => setTimeout(r, 400));

            const sel = document.querySelector('#re_impact_type');
            if (!Array.from(sel.options).some(o => o.value === 'missed_calls')) {
                return {error: 'missed_calls no esta en el desplegable'};
            }
            sel.value = 'missed_calls'; sel.dispatchEvent(new Event('change', {bubbles: true}));
            await new Promise(r => setTimeout(r, 400));

            Array.from(document.querySelectorAll('button'))
                 .filter(b => /generate/i.test(b.textContent))[0].click();
            await new Promise(r => setTimeout(r, 3500));

            const cuerpo = document.querySelector('#re-impact-table-body');
            const filas = Array.from(cuerpo ? cuerpo.querySelectorAll('tr') : []);
            if (!filas.length) return {error: 'la tabla se pinto SIN FILAS'};

            return {
                filas: filas.length,
                // Las columnas de conteo: se cuentan sobre una fila del medio,
                // porque la primera de cada grupo lleva el rombo con rowspan.
                celdas: filas[1] ? filas[1].cells.length : 0,
                rombos: cuerpo.querySelectorAll('svg').length,
                conNumero: filas.reduce((a, tr) => a + Array.from(tr.cells)
                    .filter(c => /[0-9]/.test(c.textContent)).length, 0)
            };
        }"""
    )

    assert "error" not in r, f"missed_calls no se pinto: {r['error']}"
    assert r["conNumero"] > 0, "filas sin un solo numero"
    assert r["filas"] == 24, (
        f"se pintaron {r['filas']} filas y son 8 estados de bases x 3 outs = 24. "
        f"Si son 3, se colapso el eje de bases, que es el que MAS pesa."
    )
    assert r["rombos"] == 8, (
        f"{r['rombos']} rombos; va uno por estado de bases, con rowspan de 3"
    )
    assert r["celdas"] >= 12, (
        f"solo {r['celdas']} celdas en una fila del medio: faltan conteos"
    )
