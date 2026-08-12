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
