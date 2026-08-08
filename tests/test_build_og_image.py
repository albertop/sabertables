"""
Generacion de la imagen de tarjeta social.

Se construyen logos sinteticos --un circulo de color sobre un fondo plano-- en
vez de usar el logo real, para que los tests digan por que fallan y no dependan
de un fichero que puede cambiar.

Por que se prueba algo tan visual: esta imagen NO se pinta en ninguna pagina,
solo la bajan los rastreadores de Facebook, X, LinkedIn y Slack. Si sale mal,
nadie lo ve hasta que alguien comparte un enlace.
"""
import build_og_image as og
import pytest
from PIL import Image, ImageDraw


def _logo(tmp_path, lado=1024, fondo=(31, 35, 47), circulo=(40, 110, 200), radio=400,
          centro=None, nombre="logo.png"):
    """Un logo de mentira: circulo centrado sobre fondo plano, como el real."""
    im = Image.new("RGB", (lado, lado), fondo)
    cx, cy = centro or (lado // 2, lado // 2)
    ImageDraw.Draw(im).ellipse((cx - radio, cy - radio, cx + radio, cy + radio), fill=circulo)
    ruta = tmp_path / nombre
    im.save(ruta)
    return ruta


# ---------------------------------------------------------------------------
# Localizar la chapa
# ---------------------------------------------------------------------------

def test_se_detecta_el_circulo_y_no_se_da_por_supuesto(tmp_path):
    """
    El radio se mide en vez de escribirse a mano para que sobreviva a un logo
    rehecho con otro encuadre.
    """
    im = Image.open(_logo(tmp_path, lado=1000, radio=300)).convert("RGB")

    cx, cy, radio = og._borde_del_circulo(im)

    assert cx == pytest.approx(500, abs=3)
    assert cy == 500
    assert radio == pytest.approx(300, abs=3)


def test_se_detecta_aunque_el_circulo_este_descentrado(tmp_path):
    """El logo real no esta perfectamente centrado: su centro cae en x=519."""
    im = Image.open(_logo(tmp_path, lado=1000, radio=250, centro=(540, 500))).convert("RGB")

    cx, _, radio = og._borde_del_circulo(im)

    assert cx == pytest.approx(540, abs=3)
    assert radio == pytest.approx(250, abs=3)


# ---------------------------------------------------------------------------
# El lienzo
# ---------------------------------------------------------------------------

def test_la_salida_es_1200x630(tmp_path):
    """
    Es la proporcion 2:1 que documentan Facebook y X. Por debajo de 600x315, X
    degrada la tarjeta a la variante pequenia.
    """
    salida = og.construir(_logo(tmp_path), tmp_path / "og.png")

    assert Image.open(salida).size == (1200, 630)


def test_el_logo_no_toca_los_bordes(tmp_path):
    """
    Sin margen, algunos clientes que redondean las esquinas recortan el borde
    del logo.
    """
    im = Image.open(og.construir(_logo(tmp_path), tmp_path / "og.png")).convert("RGB")
    fondo = im.getpixel((5, 5))

    for punto in ((600, 2), (600, 627), (2, 315), (1197, 315)):
        assert im.getpixel(punto) == fondo, f"el logo llega hasta {punto}"


def test_el_fondo_es_uniforme_y_sin_costura(tmp_path):
    """
    EL FALLO QUE MOTIVO LA MASCARA. Pegando el cuadrado entero quedaba un
    recuadro visible: el logo tiene vinieteado, asi que su fondo junto al
    circulo no es el mismo que el de la esquina, y sobre azul marino plano esos
    dos puntos de diferencia se ven. Recortando el circulo, todo lo de fuera es
    un unico color.
    """
    # Vinieteado a proposito: esquina mas clara que el centro.
    lado = 800
    im = Image.new("RGB", (lado, lado), (40, 44, 56))
    ImageDraw.Draw(im).rectangle((100, 100, 700, 700), fill=(29, 33, 45))
    ImageDraw.Draw(im).ellipse((150, 150, 650, 650), fill=(40, 110, 200))
    origen = tmp_path / "vineteado.png"
    im.save(origen)

    salida = Image.open(og.construir(origen, tmp_path / "og.png")).convert("RGB")

    esquinas = {salida.getpixel(p) for p in ((3, 3), (1196, 3), (3, 626), (1196, 626))}
    borde_superior = {salida.getpixel((x, 3)) for x in range(100, 1100, 50)}

    assert len(esquinas) == 1, f"las esquinas no son del mismo color: {esquinas}"
    assert borde_superior == esquinas, "hay una costura en el borde superior"


def test_el_color_de_fondo_sale_del_propio_logo(tmp_path):
    """
    Si el logo se rehace con otra paleta, el lienzo la sigue sin tocar el
    script.
    """
    salida = og.construir(_logo(tmp_path, fondo=(90, 10, 10)), tmp_path / "og.png")

    assert Image.open(salida).convert("RGB").getpixel((5, 5)) == (90, 10, 10)


def test_el_circulo_sigue_ahi_en_el_centro(tmp_path):
    """Control: no vale un lienzo perfectamente uniforme y vacio."""
    im = Image.open(og.construir(_logo(tmp_path), tmp_path / "og.png")).convert("RGB")

    assert im.getpixel((600, 315)) != im.getpixel((5, 5))


def test_crea_el_directorio_de_salida_si_no_existe(tmp_path):
    destino = tmp_path / "nueva" / "carpeta" / "og.png"

    og.construir(_logo(tmp_path), destino)

    assert destino.exists()


# ---------------------------------------------------------------------------
# La linea de comandos
# ---------------------------------------------------------------------------

def test_el_cli_escribe_y_dice_donde(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(og, "RAIZ", tmp_path)
    destino = tmp_path / "salida.png"

    assert og._cli(["--logo", str(_logo(tmp_path)), "--salida", str(destino)]) == 0

    salida = capsys.readouterr().out
    assert "1200x630" in salida
    assert "KB" in salida
    assert destino.exists()
