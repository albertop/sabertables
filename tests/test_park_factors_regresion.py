"""
La red que faltaba alrededor de los factores de parque.

POR QUE EXISTE ESTE FICHERO
===========================

Se anadio `HOME_TEAM_ID` a `dbo.retroParkFactors` para poder calcular wRC+. La
tabla paso de 7.524 a 7.737 filas y eso habria roto la tabla de Park Factors de la
aplicacion en el siguiente pipeline. No se rompio de milagro: el parquet en disco
no se habia regenerado todavia.

Y NO HABRIA BASTADO UN TEST DE DUPLICADOS. La aplicacion YA repite parques a
proposito: cruza con `ballparks`, que tiene una fila por equipo, asi que un
estadio compartido sale con una fila por cada equipo que lo uso. Buscar claves
repetidas habria dado verde.

Lo que cambio fue el NUMERO DE FILAS. Fenway en 1914 pasaba de dos filas a cuatro
--producto cartesiano-- y dos de ellas emparejaban a los Red Sox con el factor de
los Braves. Contar filas por temporada es lo unico que lo ve.

Ver docs/PLAN_PARK_FACTORS_POR_EQUIPO.md.
"""
import json
import pathlib

import pandas as pd
import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
PARQUET = RAIZ / "app" / "data" / "parkFactors.parquet"
INSTANTANEA = pathlib.Path(__file__).parent / "instantanea_park_factors.json"

# Los estadios compartidos por dos equipos en la misma temporada. Son el caso que
# revienta, y todos son historicos: Fenway y el Polo Grounds en 1914, Dodger
# Stadium mientras los Angelinos fueron inquilinos, y Shea con los Yankees dentro
# durante la reforma de su estadio.
TEMPORADAS_CON_ESTADIO_COMPARTIDO = [1914, 1964, 1974]


@pytest.fixture(scope="module")
def park_factors() -> pd.DataFrame:
    if not PARQUET.exists():
        pytest.skip("no hay parkFactors.parquet")
    return pd.read_parquet(PARQUET)


@pytest.fixture(scope="module")
def instantanea() -> dict:
    return json.loads(INSTANTANEA.read_text(encoding="utf-8"))


def test_el_numero_de_filas_por_temporada_no_cambia(park_factors, instantanea):
    """
    EL TEST QUE HABRIA CAZADO LA REGRESION.

    Una fila de mas por temporada significa que algo aguas arriba duplico el
    parque, y en la pantalla eso sale como un estadio repetido con numeros que no
    le corresponden. Nada falla, nada avisa: la tabla simplemente miente.
    """
    actual = {
        f"{int(s)}|{int(w)}": int(n)
        for (s, w), n in park_factors.groupby(["SEASON", "SEASONS"]).size().items()
    }
    esperado = instantanea["filas_por_temporada_y_ventana"]

    sobran = {k: (esperado.get(k), v) for k, v in actual.items() if esperado.get(k) != v}
    faltan = [k for k in esperado if k not in actual]

    assert not sobran, (
        "cambio el numero de filas en temporada|ventana "
        f"(esperado, actual): {dict(list(sobran.items())[:8])}"
    )
    assert not faltan, f"desaparecieron combinaciones temporada|ventana: {faltan[:8]}"


def test_el_total_de_filas_no_cambia(park_factors, instantanea):
    assert len(park_factors) == instantanea["filas_totales"]


def test_no_hay_claves_repetidas(park_factors):
    """
    Por si mismo NO detecta la regresion --la app repite parques a proposito en la
    presentacion-- pero en el parquet la clave si tiene que ser unica: dos filas
    con el mismo parque, temporada y ventana son dos factores distintos para la
    misma cosa, y cual gana depende del orden.
    """
    dup = park_factors.duplicated(subset=["PARK_ID", "SEASON", "SEASONS"], keep=False)

    assert not dup.any(), (
        f"{int(dup.sum())} filas con (PARK_ID, SEASON, SEASONS) repetida:\n"
        f"{park_factors[dup].head(6).to_string(index=False)}"
    )


@pytest.mark.parametrize("temporada", TEMPORADAS_CON_ESTADIO_COMPARTIDO)
def test_los_estadios_compartidos_no_se_multiplican_en_la_tabla(park_factors, temporada):
    """
    Comprueba la TABLA QUE VE EL USUARIO, no el parquet.

    Es donde aparece el producto cartesiano: el parquet puede tener una fila por
    parque y aun asi la pantalla sacar cuatro, porque el cruce con `ballparks`
    multiplica. Un parque compartido debe dar UNA fila por equipo, ni una mas.
    """
    from app.services.table_generator import generate_park_factors_table

    ballparks = pd.read_parquet(RAIZ / "app" / "data" / "ballparks.parquet")
    tabla = generate_park_factors_table(
        {"park_factors": park_factors, "ballparks": ballparks}, temporada, 1
    )
    filas = tabla.get("rows", [])
    assert filas, f"{temporada}: la tabla salio vacia"

    por_parque_y_equipo = {}
    for f in filas:
        clave = (str(f.get("park_id")), str(f.get("team_name")))
        por_parque_y_equipo[clave] = por_parque_y_equipo.get(clave, 0) + 1

    repetidos = {k: n for k, n in por_parque_y_equipo.items() if n > 1}
    assert not repetidos, (
        f"{temporada}: el mismo parque y equipo sale mas de una vez, que es el "
        f"producto cartesiano al cruzar con ballparks: {repetidos}"
    )


# ---------------------------------------------------------------------------
# El parquet NUEVO, el que lleva el equipo (paso 3 del plan)
# ---------------------------------------------------------------------------

PARQUET_POR_EQUIPO = RAIZ / "app" / "data" / "parkFactorsByTeam.parquet"


@pytest.fixture(scope="module")
def por_equipo() -> pd.DataFrame:
    if not PARQUET_POR_EQUIPO.exists():
        pytest.skip("no hay parkFactorsByTeam.parquet")
    return pd.read_parquet(PARQUET_POR_EQUIPO)


def test_el_parquet_por_equipo_tiene_clave_unica(por_equipo):
    """Con el equipo dentro, la clave vuelve a ser unica. Sin el no lo era, y por
    eso la tabla vieja perdia una de las dos filas de un estadio compartido."""
    dup = por_equipo.duplicated(
        subset=["PARK_ID", "SEASON", "HOME_TEAM_ID", "SEASONS"], keep=False
    )

    assert not dup.any(), f"{int(dup.sum())} filas con la clave repetida"


def test_los_valores_son_LOS_MISMOS_que_en_el_parquet_de_siempre(por_equipo, park_factors):
    """
    LA GARANTIA DE QUE NO HAY DOS VERDADES.

    El factor es del PARQUE, no del inquilino: los dos equipos de un estadio
    compartido reciben el mismo numero. Si estos dos parquets divergieran,
    tendriamos dos factores distintos para el mismo estadio y la misma temporada,
    y cual gana dependeria de por donde entre el dato.
    """
    m = por_equipo.merge(park_factors, on=["PARK_ID", "SEASON", "SEASONS"],
                         suffixes=("_eq", "_pf"))
    assert len(m) == len(por_equipo), "hay filas por equipo sin su pareja en el parquet de siempre"

    cols = ["PF_R", "PF_1B", "PF_2B", "PF_3B", "PF_HR", "PF_BB", "PF_SO"]
    import numpy as np
    dif = np.abs(m[[c + "_eq" for c in cols]].values.astype(float)
                 - m[[c + "_pf" for c in cols]].values.astype(float))

    assert float(dif.max()) < 1e-5, f"los dos parquets no dicen lo mismo: max {dif.max():.2e}"


def test_la_ventana_efectiva_nunca_supera_la_pedida(por_equipo):
    """
    `SEASONS_EFF > SEASONS` seria imposible y significaria que el calculo de la
    ventana esta mal. Que sea MENOR es normal y esperado en los bordes de la
    serie: eso es lo que avisa de que la fila cambiara con datos nuevos.
    """
    malas = por_equipo[por_equipo["SEASONS_EFF"] > por_equipo["SEASONS"]]

    assert malas.empty, f"{len(malas)} filas con ventana efectiva mayor que la pedida"


def test_los_estadios_compartidos_estan_todos(por_equipo):
    """71 parques-temporada con dos equipos. Es la razon de ser de este parquet:
    en el de siempre solo cabia uno de los dos."""
    de_un_ano = por_equipo[por_equipo["SEASONS"] == 1]
    compartidos = de_un_ano.groupby(["PARK_ID", "SEASON"])["HOME_TEAM_ID"].nunique()

    assert int((compartidos > 1).sum()) == 71, (
        f"se esperaban 71 parques-temporada compartidos, hay {int((compartidos > 1).sum())}"
    )
