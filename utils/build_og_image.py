"""
Genera la imagen de tarjeta social (og:image) a partir del logo.

POR QUE EXISTE. `logo.png` es cuadrado (1024x1024) y la tarjeta esta declarada
como `summary_large_image`, que recorta al centro en 2:1. Sobre este logo eso
corta por arriba y por abajo justo donde va el texto "SABERTABLES.COM", o sea
que cada enlace compartido perdia el nombre del sitio. Y no se veia venir,
porque la imagen no se pinta en ninguna pagina: solo la bajan los rastreadores
de Facebook, X, LinkedIn, WhatsApp y Slack.

La salida es 1200x630 (2:1, lo que piden todas), con el logo centrado sobre el
mismo fondo azul marino, asi que no hay nada que recortar.

Se deja como script y no como algo hecho a mano para que la imagen se pueda
rehacer si el logo cambia:

    python utils/build_og_image.py

Pillow SOLO hace falta aqui, para regenerar. No entra en la imagen de
produccion: el PNG resultante se sirve como fichero estatico.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw

LANCZOS = Image.Resampling.LANCZOS

RAIZ = Path(__file__).resolve().parent.parent
LOGO = RAIZ / "app" / "static" / "images" / "logo.png"
SALIDA = RAIZ / "app" / "static" / "images" / "og-image.png"

# 1200x630 es lo que documentan Facebook y X. Por debajo de 600x315 X degrada la
# tarjeta a la variante pequenia.
ANCHO, ALTO = 1200, 630

# Margen arriba y abajo. Sin el, el logo toca el borde y en algunos clientes que
# redondean las esquinas queda cortado.
MARGEN = 45


def _borde_del_circulo(logo: Image.Image) -> tuple[int, int, int]:
    """
    Localiza la chapa circular midiendo donde deja de haber fondo en la fila
    central. Se detecta en vez de escribirse a mano para que sobreviva a un
    logo rehecho con otro encuadre.
    """
    ancho, alto = logo.size
    fila = alto // 2

    def rgb(x: int, y: int) -> tuple[int, int, int]:
        return cast(tuple[int, int, int], logo.getpixel((x, y)))

    fondo = rgb(4, 4)

    def lejos(x: int) -> bool:
        return sum((a - b) ** 2 for a, b in zip(rgb(x, fila), fondo, strict=True)) ** 0.5 > 18

    izq = next(x for x in range(ancho) if lejos(x))
    der = next(x for x in range(ancho - 1, -1, -1) if lejos(x))
    centro_x = (izq + der) // 2
    radio = (der - izq) // 2
    return centro_x, fila, radio


def construir(logo_path: Path = LOGO, salida: Path = SALIDA) -> Path:
    logo = Image.open(logo_path).convert("RGB")
    cx, cy, radio = _borde_del_circulo(logo)

    # Solo se pega el CIRCULO, no el cuadrado entero.
    #
    # Pegar el cuadrado dejaba una costura visible: el logo tiene un vinieteado
    # suave, asi que su fondo junto al circulo (29,33,45) no es exactamente el
    # de la esquina (31,35,47), y sobre azul marino plano esos dos puntos de
    # diferencia se ven como un recuadro. Recortando el circulo, todo lo demas
    # es un unico color y no hay borde que ver.
    fondo = cast(tuple[int, int, int], logo.getpixel((max(0, cx - radio - 40), cy)))

    # Un poco mas que el radio medido, para no comerse el antialiasing del borde
    # del propio logo.
    r = radio + 12

    # Mascara a 4x y reducida: el borde sale suave en vez de escalonado.
    ss = 4
    mascara = Image.new("L", ((2 * r) * ss, (2 * r) * ss), 0)
    ImageDraw.Draw(mascara).ellipse((0, 0, (2 * r) * ss - 1, (2 * r) * ss - 1), fill=255)
    mascara = mascara.resize((2 * r, 2 * r), LANCZOS)

    recorte = logo.crop((cx - r, cy - r, cx + r, cy + r))

    lado = ALTO - 2 * MARGEN
    recorte = recorte.resize((lado, lado), LANCZOS)
    mascara = mascara.resize((lado, lado), LANCZOS)

    lienzo = Image.new("RGB", (ANCHO, ALTO), fondo)
    lienzo.paste(recorte, ((ANCHO - lado) // 2, (ALTO - lado) // 2), mascara)

    salida.parent.mkdir(parents=True, exist_ok=True)
    lienzo.save(salida, "PNG", optimize=True)
    return salida


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--logo", type=Path, default=LOGO)
    p.add_argument("--salida", type=Path, default=SALIDA)
    args = p.parse_args(argv)

    destino = construir(args.logo, args.salida)
    kb = destino.stat().st_size / 1024
    print(f"{destino.relative_to(RAIZ)}: {ANCHO}x{ALTO}, {kb:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
