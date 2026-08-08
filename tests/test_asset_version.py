"""
El cache-buster de los assets debe salir del contenido, no de una constante.

Estaba escrito a mano en la plantilla (`{% set asset_version = '1.0.52' %}`) y
habia que subirlo en cada cambio de app.js o styles.css. Durante la revision de
agosto de 2026 se olvido tres veces seguidas, asi que un navegador con cache no
habria visto ninguno de esos cambios -- y comprobarlo con curl sobre el fichero
servido daba verde igualmente, porque el fichero estaba bien y lo que no ocurria
es que el navegador lo pidiera.
"""
import re

from fastapi.testclient import TestClient

from app import main as app_main
from app.main import app

ASSET_REF = re.compile(r'/static/(?:js/app\.js|css/styles\.css)\?v=([^"\']+)')


def test_la_portada_sirve_la_version_calculada():
    """
    Si alguien vuelve a poner un literal en la plantilla, el ?v= dejaria de
    coincidir con el hash y este test lo veria.
    """
    esperada = app_main.asset_version()

    with TestClient(app) as client:
        html = client.get("/").text

    versiones = set(ASSET_REF.findall(html))
    assert versiones, 'la portada no referencia app.js ni styles.css con ?v='
    assert versiones == {esperada}


def test_la_version_cambia_con_el_contenido_y_vuelve(tmp_path, monkeypatch):
    (tmp_path / 'js').mkdir()
    (tmp_path / 'css').mkdir()
    js = tmp_path / 'js' / 'app.js'
    css = tmp_path / 'css' / 'styles.css'
    js.write_text('const a = 1;')
    css.write_text('body { color: red; }')

    monkeypatch.setattr(app_main, 'STATIC_DIR', tmp_path)
    monkeypatch.setattr(app_main, '_asset_version_cache', {'key': None, 'value': None})

    v1 = app_main.asset_version()

    js.write_text('const a = 2;   // cambio del mismo tamano no, mas largo')
    v2 = app_main.asset_version()
    assert v2 != v1, 'editar app.js no movio el cache-buster'

    css.write_text('body { color: blue; }')
    v3 = app_main.asset_version()
    assert v3 not in (v1, v2), 'editar styles.css no movio el cache-buster'

    # Volver al contenido original devuelve la version original: es un hash del
    # contenido, no un contador ni un timestamp.
    js.write_text('const a = 1;')
    css.write_text('body { color: red; }')
    assert app_main.asset_version() == v1


def test_un_asset_que_falta_no_tumba_la_portada(tmp_path, monkeypatch):
    """Mejor un cache-buster degradado que un 500 en la home."""
    monkeypatch.setattr(app_main, 'STATIC_DIR', tmp_path)  # vacio
    monkeypatch.setattr(app_main, '_asset_version_cache', {'key': None, 'value': None})

    assert app_main.asset_version() == '0'


def test_la_plantilla_no_define_asset_version():
    """
    El literal vivia en index.html. Si vuelve, el valor de la plantilla ganaria
    al del contexto en silencio y el problema reaparece.
    """
    from app.config import TEMPLATES_DIR

    html = (TEMPLATES_DIR / 'index.html').read_text(encoding='utf-8')
    assert not re.search(r'{%\s*set\s+asset_version', html)
