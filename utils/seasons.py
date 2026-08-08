"""
Las tres fechas que acotan TODA la extraccion. Un solo sitio.

    MIN_SEASON            1910  suelo general
    MIN_PER_PITCH_SEASON  1989  suelo de lo que necesita datos lanzamiento a lanzamiento
    MAX_SEASON            2025  techo, comun a todo

POR QUE EXISTE ESTE FICHERO. Antes cada consulta decidia su propio rango, y de
tres maneras distintas: unas derivaban el suelo de la base con
`>= (SELECT MIN(season) FROM dbo.retroGamesReg)`, otras llevaban un `>= 1989`
escrito a mano, y las de swing/take sacaban la temporada de `YEAR(game_date)`
**sin ninguna cota**. De ahi salieron dos problemas reales:

- `breakeven_points.parquet` traia 302 filas de 2026, la temporada EN CURSO, solo
  en `swing_take_zone`. Statcast publica en vivo; Retrosheet publica al cerrar el
  anio. Asi que la temporada a medias entraba por una sola puerta y se presentaba
  en la tabla igual que un anio entero, sin ninguna marca.
- El pitagorico llegaba a 1901 mientras el resto empezaba en 1910, sin que
  ningun sitio dijera que eso era a proposito.

Derivar el suelo de la base parecia elegante y era fragil: el rango publicado
cambiaba solo si alguien cargaba temporadas nuevas, sin tocar una linea de
codigo y sin que nadie se enterara.

COMO SE CAMBIA. Por variable de entorno (`.env`) o por argumento de linea de
comandos en los scripts que lo aceptan. Subir el techo cada invierno deberia ser
UN cambio, no una busqueda por el repositorio.

    MAX_SEASON=2026

POR QUE HAY DOS SUELOS. Retrosheet publica los eventos jugada a jugada desde
1910, pero el detalle de cada lanzamiento --el conteo, la zona-- no existe hasta
1989. Todo lo que necesita conteo (RE288, robo por conteo, swing/take) arranca
ahi. Mezclar los dos suelos daria tablas con dos decadas de celdas vacias que
parecen ausencia de juego y son ausencia de dato.
"""
from __future__ import annotations

import os

import pandas as pd

# 1910: primera temporada con eventos jugada a jugada en Retrosheet.
MIN_SEASON = int(os.getenv("MIN_SEASON", "1910"))

# 1989: primera temporada con detalle de lanzamiento (conteo y zona).
MIN_PER_PITCH_SEASON = int(os.getenv("MIN_PER_PITCH_SEASON", "1989"))

# 2025: ultima temporada CERRADA. No subir hasta que la fuente publique el anio
# completo, o volveran a colarse temporadas a medias.
MAX_SEASON = int(os.getenv("MAX_SEASON", "2025"))


class RangoDeTemporadasInvalido(ValueError):
    """Los tres valores no forman un rango con sentido."""


def validar(minimo: int | None = None, per_pitch: int | None = None,
            maximo: int | None = None) -> None:
    """
    Se llama al arrancar cada script. Un rango invertido no da error por si
    solo: da parquets vacios, y un parquet vacio deja la tabla en blanco en la
    web sin que nada falle.
    """
    lo = MIN_SEASON if minimo is None else minimo
    pp = MIN_PER_PITCH_SEASON if per_pitch is None else per_pitch
    hi = MAX_SEASON if maximo is None else maximo

    if not lo <= pp:
        raise RangoDeTemporadasInvalido(
            f"MIN_SEASON ({lo}) no puede ser posterior a MIN_PER_PITCH_SEASON ({pp})"
        )
    if not pp <= hi:
        raise RangoDeTemporadasInvalido(
            f"MIN_PER_PITCH_SEASON ({pp}) no puede ser posterior a MAX_SEASON ({hi}): "
            "no quedaria ni una temporada con datos de lanzamiento"
        )


def rango(por_lanzamiento: bool = False) -> tuple[int, int]:
    """(suelo, techo) para este tipo de dato."""
    return (MIN_PER_PITCH_SEASON if por_lanzamiento else MIN_SEASON), MAX_SEASON


def predicado_sql(columna: str = "season", por_lanzamiento: bool = False) -> str:
    """
    Trozo de SQL con el rango ya puesto, para empotrar en las consultas.

    Los valores se interpolan y no se pasan como parametros a proposito: son
    enteros que salen de `int()`, no hay texto de nadie de por medio, y asi las
    consultas siguen siendo copiables a un cliente de SQL tal cual estan.
    """
    lo, hi = rango(por_lanzamiento)
    return f"{columna} BETWEEN {int(lo)} AND {int(hi)}"


def recortar(
    df: pd.DataFrame,
    columna: str = "season",
    por_lanzamiento: bool = False,
) -> tuple[pd.DataFrame, int]:
    """
    Aplica el rango a un DataFrame ya cargado y devuelve (df, filas_descartadas).

    Se aplica ADEMAS del filtro en SQL, no en su lugar. El de SQL esta por no
    traerse un siglo de filas para tirarlas; este es la garantia. Una vista que
    se rehaga sin la cota, o una consulta nueva que se olvide de ponerla, se
    topan igual con esto.
    """
    if columna not in df.columns:
        return df, 0

    lo, hi = rango(por_lanzamiento)
    dentro = df[columna].between(lo, hi)
    descartadas = int((~dentro).sum())
    return (df if descartadas == 0 else df.loc[dentro].copy()), descartadas
