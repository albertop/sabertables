"""
La frontera de create_break_even_points: lectura de parquets, escritura de los
tres publicados, y las funciones que consultan las vistas SQL.

No se abre ninguna conexion. `_read_sql_view` es el punto unico por el que pasan
TODAS las vistas, asi que se sustituye ahi: cada test le da el DataFrame que
devolveria la vista.

El calculo sabermetrico en si vive en tests/test_calculations.py; aqui esta lo
que lo rodea, que es lo que decide si el resultado llega bien al parquet.
"""
import json

import create_break_even_points as be
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Lectura de las matrices
# ---------------------------------------------------------------------------

def _matriz_re24(tmp_path, seasons=(2024,)):
    df = pd.DataFrame([
        {"season": s, "outs": o, "runners": r, "run_value": 0.5, "n": 100, "event_cd": -1}
        for s in seasons for o in (0, 1, 2) for r in ("000", "100", "010")
    ])
    df.to_parquet(tmp_path / "re24_values.parquet", index=False)
    return df


def test_la_matriz_re24_se_lee_del_parquet(tmp_path):
    _matriz_re24(tmp_path)

    df = be.load_re24_matrix(tmp_path)

    assert len(df) == 9
    assert {"season", "outs", "runners", "run_value"} <= set(df.columns)


def test_si_falta_la_matriz_re24_se_dice_que_hay_que_ejecutar_01(tmp_path):
    """
    Este script depende de la salida del anterior. Sin ese aviso, el error es un
    FileNotFoundError con una ruta y hay que deducir el resto.
    """
    with pytest.raises(Exception) as e:
        be.load_re24_matrix(tmp_path)

    assert "re24_values" in str(e.value)


def test_la_matriz_re288_se_lee_del_parquet(tmp_path):
    pd.DataFrame([
        {"season": 2024, "outs": 0, "runners": "100", "count": "0-0",
         "run_value": 0.9, "n": 500},
    ]).to_parquet(tmp_path / "re288_values.parquet", index=False)

    df = be.load_re288_matrix(tmp_path)

    assert len(df) == 1


def test_si_falta_la_matriz_re288_tambien_se_avisa(tmp_path):
    with pytest.raises(Exception) as e:
        be.load_re288_matrix(tmp_path)

    assert "re288_values" in str(e.value)


def test_los_valores_de_ibb_salen_de_la_matriz_re24(tmp_path):
    """
    No hay parquet propio de IBB: se filtran de re24_values por event_cd == 15,
    que es el codigo de la base intencional en Retrosheet.
    """
    pd.DataFrame([
        {"season": 2024, "outs": 1, "runners": "010", "n": 100, "run_value": 1.2, "event_cd": 15},
        {"season": 2024, "outs": 1, "runners": "010", "n": 900, "run_value": 0.6, "event_cd": 23},
    ]).to_parquet(tmp_path / "re24_values.parquet", index=False)

    df = be.load_ibb_real_values(tmp_path)

    assert len(df) == 1, "solo las filas de event_cd 15"
    assert df.iloc[0]["run_value"] == pytest.approx(1.2)


def test_sin_la_matriz_re24_no_hay_valores_de_ibb(tmp_path):
    with pytest.raises(Exception):
        be.load_ibb_real_values(tmp_path)


# ---------------------------------------------------------------------------
# Escritura de break-even
# ---------------------------------------------------------------------------

def _filas_be(n=3, tipo="stealing", pct=0.7):
    return [
        {"season": 2024, "type": tipo, "scenario": f"esc{i}", "label": f"Esc {i}",
         "outs": i % 3, "breakeven_pct": pct, "n": 1000}
        for i in range(n)
    ]


def test_los_break_even_se_escriben_con_sus_tipos(tmp_path):
    """
    Los tipos importan: el parquet se sirve tal cual a la web, y un float64
    donde se declaro float32 duplica el tamano de la descarga.
    """
    be.save_breakeven_points(_filas_be(), tmp_path)

    df = pd.read_parquet(tmp_path / "breakeven_points.parquet")
    assert df["season"].dtype == "int32"
    assert df["breakeven_pct"].dtype == "float32"
    assert df["outs"].dtype == "uint8"
    assert str(df["type"].dtype) == "string"


def test_sin_etiqueta_se_usa_el_escenario(tmp_path):
    """
    `label` es lo que ve el usuario. Si una familia no lo trae, mejor repetir el
    escenario que dejar la columna vacia en la tabla.
    """
    filas = _filas_be()
    for f in filas:
        del f["label"]

    be.save_breakeven_points(filas, tmp_path)

    df = pd.read_parquet(tmp_path / "breakeven_points.parquet")
    assert (df["label"] == df["scenario"]).all()


def test_un_break_even_negativo_impide_escribir_el_parquet(tmp_path):
    """
    Invariante duro: diria que la jugada conviene con probabilidad de exito
    negativa. Es un signo invertido en el denominador, no ruido.
    """
    filas = _filas_be()
    filas[0]["breakeven_pct"] = -0.2

    with pytest.raises(ValueError, match="negativos"):
        be.save_breakeven_points(filas, tmp_path)

    assert not (tmp_path / "breakeven_points.parquet").exists()


# ---------------------------------------------------------------------------
# Escritura de impacto en RE
# ---------------------------------------------------------------------------

def _filas_impacto(tipo="sac_bunt"):
    return [
        {"season": 2024, "type": tipo, "scenario": "100", "label": "Runner on 1B",
         "outs": o, "delta_re": -0.2, "baseline_re": 0.85,
         "delta_re_pct": -0.235, "n": 4000}
        for o in (0, 1)
    ]


def test_el_impacto_en_re_conserva_el_baseline(tmp_path):
    """
    baseline_re se calculaba y luego se descartaba al escribir, pese a que el
    modulo lo lista como columna de salida. Es el denominador de delta_re_pct:
    sin el, ese porcentaje no se puede verificar, y en las filas de IBB es lo que
    dice si un delta de +0.02 es mucho o poco.
    """
    be.save_re_impact(_filas_impacto(), tmp_path)

    df = pd.read_parquet(tmp_path / "re_impact.parquet")
    assert "baseline_re" in df.columns
    assert df["baseline_re"].notna().all()


def test_las_columnas_opcionales_existen_aunque_el_tipo_no_las_use(tmp_path):
    """
    platoon, handedness_split y tto_pass solo aplican a algunos analisis. La
    columna tiene que estar igualmente: la app lee el parquet con un esquema
    fijo y una columna ausente rompe el endpoint.
    """
    be.save_re_impact(_filas_impacto(), tmp_path)

    df = pd.read_parquet(tmp_path / "re_impact.parquet")
    assert {"platoon", "handedness_split", "tto_pass"} <= set(df.columns)


def test_el_impacto_en_re_se_escribe_con_sus_tipos(tmp_path):
    be.save_re_impact(_filas_impacto(), tmp_path)

    df = pd.read_parquet(tmp_path / "re_impact.parquet")
    assert df["season"].dtype == "int16"
    assert str(df["type"].dtype) == "string"


# ---------------------------------------------------------------------------
# Escritura del robo por conteo
# ---------------------------------------------------------------------------

def _filas_por_conteo(pct=0.72):
    return [
        {"season": 2024, "type": "sb_by_count", "scenario": "Runner on 1B steals 2B",
         "label": "Runner on 1B steals 2B", "outs": o, "count": c,
         "breakeven_pct": pct, "n": 2000}
        for o in (0, 1, 2) for c in ("0-0", "3-2")
    ]


def test_el_robo_por_conteo_se_escribe_con_su_columna_de_conteo(tmp_path):
    """
    `count` es lo que distingue esta tabla de la de robo normal. Sin ella, seis
    filas por escenario se leen como duplicados.
    """
    be.save_sb_breakeven_by_count(_filas_por_conteo(), tmp_path)

    df = pd.read_parquet(tmp_path / "sb_breakeven_by_count.parquet")
    assert "count" in df.columns
    assert df["count"].nunique() == 2


def test_el_robo_por_conteo_tambien_valida_el_rango(tmp_path):
    filas = _filas_por_conteo()
    filas[0]["breakeven_pct"] = -0.5

    with pytest.raises(ValueError, match="negativos"):
        be.save_sb_breakeven_by_count(filas, tmp_path)


# ---------------------------------------------------------------------------
# El manifiesto de procedencia
# ---------------------------------------------------------------------------

def test_el_manifiesto_registra_de_que_ficheros_salen_los_derivados(tmp_path):
    """
    Es lo que permite que la siguiente extraccion avise de que estas tres tablas
    quedaron obsoletas. Sin el, refrescar 01 y no ejecutar 03 deja las derivadas
    calculadas sobre una matriz distinta de la publicada, y nada lo dice.
    """
    # Entradas Y salidas: el manifiesto solo lista los ficheros que existen, asi
    # que sin las salidas escritas la lista sale vacia y el aviso no diria de
    # que depende cada cosa.
    for nombre in ("re24_values.parquet", "re288_values.parquet",
                   "breakeven_points.parquet", "re_impact.parquet"):
        pd.DataFrame({"a": [1]}).to_parquet(tmp_path / nombre, index=False)

    be.write_derived_manifest(tmp_path)

    manifiesto = json.loads((tmp_path / "derived_from.json").read_text(encoding="utf-8"))
    assert "re24_values.parquet" in manifiesto["inputs"]
    assert "breakeven_points.parquet" in manifiesto["outputs"]


def test_el_manifiesto_no_lista_ficheros_que_no_estan(tmp_path):
    """
    Registrar una salida que no se escribio haria que el aviso de la siguiente
    extraccion hablara de una tabla inexistente.
    """
    pd.DataFrame({"a": [1]}).to_parquet(tmp_path / "re24_values.parquet", index=False)

    be.write_derived_manifest(tmp_path)

    manifiesto = json.loads((tmp_path / "derived_from.json").read_text(encoding="utf-8"))
    assert "re288_values.parquet" not in manifiesto["inputs"]
    assert manifiesto["outputs"] == []


def test_el_manifiesto_guarda_el_hash_y_no_la_fecha(tmp_path):
    """
    Con la fecha, tocar un fichero sin cambiarlo daria un falso aviso. Con el
    hash solo avisa si el contenido cambio de verdad.
    """
    ruta = tmp_path / "re24_values.parquet"
    pd.DataFrame({"a": [1]}).to_parquet(ruta, index=False)
    pd.DataFrame({"a": [1]}).to_parquet(tmp_path / "re288_values.parquet", index=False)

    be.write_derived_manifest(tmp_path)

    manifiesto = json.loads((tmp_path / "derived_from.json").read_text(encoding="utf-8"))
    assert manifiesto["inputs"]["re24_values.parquet"] == be._sha256(ruta)


def test_el_hash_distingue_contenidos(tmp_path):
    a = tmp_path / "a"
    a.write_bytes(b"uno")
    b = tmp_path / "b"
    b.write_bytes(b"dos")

    assert be._sha256(a) != be._sha256(b)
    assert be._sha256(a) == be._sha256(a)


# ---------------------------------------------------------------------------
# La lectura de vistas: punto unico donde se acota la temporada
# ---------------------------------------------------------------------------

class _EngineFalso:
    """Devuelve siempre el mismo DataFrame; no abre nada."""

    def __init__(self, df, registro=None):
        self._df = df
        self._registro = registro if registro is not None else []

    def connect(self):
        engine = self

        class _Conexion:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def exec_driver_sql(self_inner, sql):
                engine._registro.append(sql)

        return _Conexion()


@pytest.fixture
def vista(monkeypatch):
    """Sustituye pd.read_sql_query por un DataFrame configurable."""
    estado = {"df": pd.DataFrame({"season": [2024], "n": [10]})}
    monkeypatch.setattr(be.pd, "read_sql_query", lambda q, c: estado["df"].copy())
    return estado


def test_una_vista_se_lee_y_se_devuelve(vista):
    df = be._read_sql_view(_EngineFalso(None), "SELECT 1", "v_prueba")

    assert len(df) == 1


def test_lo_que_la_vista_devuelva_fuera_de_rango_se_recorta(vista):
    """
    Es lo que corto las 676 filas de la temporada en curso que
    v_re_analysis_swing_take_zone seguia devolviendo sin acotar. La vista no
    cambio; el recorte esta aqui.
    """
    vista["df"] = pd.DataFrame({"season": [1800, 2024, 2099], "n": [1, 2, 3]})

    df = be._read_sql_view(_EngineFalso(None), "SELECT 1", "v_prueba")

    assert df["season"].tolist() == [2024]


def test_una_vista_de_conteo_usa_el_suelo_de_1989(vista):
    vista["df"] = pd.DataFrame({"season": [1950, 2024], "n": [1, 2]})

    df = be._read_sql_view(_EngineFalso(None), "SELECT 1", "v", por_lanzamiento=True)

    assert df["season"].tolist() == [2024]


def test_una_vista_sin_columna_de_temporada_pasa_intacta(vista):
    vista["df"] = pd.DataFrame({"otra": [1, 2, 3]})

    df = be._read_sql_view(_EngineFalso(None), "SELECT 1", "v")

    assert len(df) == 3


def test_si_la_consulta_falla_se_dice_QUE_vista_fue(monkeypatch):
    """
    Diecisiete vistas: sin el nombre en el error, hay que ir a mirar el log para
    saber cual reviento.
    """
    def revienta(q, c):
        raise RuntimeError("la base no responde")

    monkeypatch.setattr(be.pd, "read_sql_query", revienta)

    with pytest.raises(RuntimeError, match="v_re_analysis_bunt"):
        be._read_sql_view(_EngineFalso(None), "SELECT 1", "v_re_analysis_bunt")


def test_una_interrupcion_sugiere_el_timeout(monkeypatch):
    """
    Una vista lenta se interrumpe con Ctrl-C, y ahi conviene decir que existe
    PH_QUERY_TIMEOUT_SECONDS en vez de dejar un KeyboardInterrupt pelado.
    """
    def interrumpe(q, c):
        raise KeyboardInterrupt

    monkeypatch.setattr(be.pd, "read_sql_query", interrumpe)

    with pytest.raises(RuntimeError, match="PH_QUERY_TIMEOUT_SECONDS"):
        be._read_sql_view(_EngineFalso(None), "SELECT 1", "v_lenta")


# ---------------------------------------------------------------------------
# Los cargadores: una funcion por vista
#
# Se sustituye _read_sql_view, que es el punto por el que pasan todas, y se le
# devuelve a cada una lo que su vista daria. Lo que se comprueba es el
# cableado --que consulta las vistas que dice y devuelve filas del tipo
# correcto--, no el calculo, que ya esta en test_calculations.py.
# ---------------------------------------------------------------------------

@pytest.fixture
def vistas(monkeypatch):
    """
    Intercepta _read_sql_view. `respuestas` mapea un trozo del nombre de la
    vista al DataFrame que devolvera; `consultadas` recoge lo que se pidio.
    """
    estado = {"respuestas": {}, "consultadas": []}

    def falso(engine, query, view_name, por_lanzamiento=False):
        estado["consultadas"].append(view_name)
        # Coincidencia EXACTA primero y subcadena despues.
        #
        # Con subcadena a secas, "v_re_analysis_bunt" tambien casa con
        # "v_re_analysis_bunt_batter_effect" y las tres vistas del toque
        # reciben el mismo DataFrame. Ordenar por longitud tampoco arregla eso,
        # porque la clave generica es mas larga que la especifica.
        if view_name in estado["respuestas"]:
            return estado["respuestas"][view_name].copy()
        for clave in sorted(estado["respuestas"], key=len, reverse=True):
            if clave in view_name:
                return estado["respuestas"][clave].copy()
        return pd.DataFrame()

    monkeypatch.setattr(be, "_read_sql_view", falso)
    monkeypatch.setattr(be, "connect_to_database", lambda: object())
    return estado


def _estirar(direccion="double"):
    return pd.DataFrame([
        {"season": 2024, "outs": 1, "direction": direccion, "bucket": b,
         "n": n, "mean_rv": rv, "sd_rv": 0.2}
        for b, (n, rv) in {"stop": (500, 0.26), "success": (300, 0.43),
                           "fail": (40, -0.15)}.items()
    ])


def test_estirar_el_batazo_consulta_su_vista_y_devuelve_filas(vistas):
    vistas["respuestas"] = {"stretch": _estirar()}

    filas = be.load_stretch_break_even_rows_from_db()

    assert any("stretch" in v for v in vistas["consultadas"])
    assert filas and filas[0]["type"] == be.BREAKEVEN_TYPE_RUNNING


def test_avanzar_una_base_consulta_su_vista(vistas):
    vistas["respuestas"] = {"advance": _estirar("first_to_third")}

    filas = be.load_advance_break_even_rows_from_db()

    assert any("advance" in v for v in vistas["consultadas"])
    assert filas


def _tres_cubos(**extra):
    """Las columnas comunes a las vistas de tres desenlaces."""
    return pd.DataFrame([
        {"season": 2024, "outs": 1, "bucket": b, "n": n, "mean_rv": rv,
         "sd_rv": 0.2, **extra}
        for b, (n, rv) in {"stop": (500, 0.26), "success": (300, 0.43),
                           "fail": (40, -0.15)}.items()
    ])


def test_el_elevado_atrapado_consulta_su_vista(vistas):
    vistas["respuestas"] = {"tag_up": _tres_cubos(direction="second_to_third",
                                                  runners="010")}

    be.load_tag_up_break_even_rows_from_db()

    assert any("tag_up" in v for v in vistas["consultadas"])


def test_el_toque_consulta_sus_TRES_vistas(vistas):
    """
    Se partio en tres porque la consulta combinada no terminaba nunca. Si alguna
    dejara de consultarse, el calculo saldria sin su efecto de bateador o sin
    sus pesos y nadie lo notaria en el numero.
    """
    vistas["respuestas"] = {
        "dbo.v_re_analysis_bunt": _tres_cubos(scenario="surprise_bunt"),
        "dbo.v_re_analysis_bunt_batter_effect":
            pd.DataFrame({"season": [2024], "batter_id": ["x"], "effect": [0.0]}),
        "dbo.v_re_analysis_bunt_weights":
            pd.DataFrame({"season": [2024], "batter_id": ["x"],
                          "scenario": ["surprise_bunt"], "outs": [1],
                          "n_bunts": [10], "lg_rv": [0.0]}),
    }

    be.load_bunt_break_even_rows_from_db()

    consultadas = " ".join(vistas["consultadas"])
    assert "v_re_analysis_bunt" in consultadas
    assert "batter_effect" in consultadas
    assert "weights" in consultadas


def test_swing_take_consulta_con_el_tope_de_temporada(vistas):
    """
    El tope sale del parametro y no del maximo del parquet: derivarlo del dato
    hacia que el rango publicado cambiara solo al cargar una temporada nueva.
    """
    vistas["respuestas"] = {"swing_take": pd.DataFrame([
        {"season": 2024, "balls": 0, "strikes": 0, "outs": 1, "runners": "000",
         "pitch_outcome": o, "ev": v, "sd": 0.2, "n": 1000}
        for o, v in (("take_ball", 0.05), ("take_strike", -0.04),
                     ("swing_miss", -0.06), ("swing_contact", 0.03),
                     ("swing_foul", -0.01))
    ])}

    be.load_swing_take_break_even_rows_from_db(season_cap=2025)

    assert any("2025" in v for v in vistas["consultadas"])


def test_swing_take_por_zona_consulta_dos_veces(vistas):
    """
    Una consulta para los umbrales y otra para los tamanos de muestra. Sin la
    segunda, las celdas saldrian sin marcador de fiabilidad.
    """
    vistas["respuestas"] = {
        "n by season": pd.DataFrame({
            # zone es numerica: son las zonas 1..14 de Statcast.
            "season": [2024], "zone": [5], "handedness_split": ["RHP vs RHB"],
            "count": ["0-0"], "n_total": [5000],
        }),
        "agregado por zona": pd.DataFrame({
            "season": [2024], "zone": [5], "handedness_split": ["RHP vs RHB"],
            # Una columna por conteo: la vista devuelve el umbral de cada uno.
            **{f"c_{b}_{s}": [0.5] for b in range(4) for s in range(3)},
        }),
    }

    be.load_swing_take_zone_break_even_rows_from_db()

    zona = [v for v in vistas["consultadas"] if "swing_take_zone" in v]
    assert len(zona) == 2


def test_el_bateador_emergente_consulta_posicion_y_lanzadores(vistas, tmp_path):
    """
    Son dos vistas distintas: los jugadores de posicion y los lanzadores, que
    ademas terminan en 2021 por el DH universal.
    """
    columnas = pd.DataFrame({
        "season": [2024], "outs": [1], "runners": ["000"],
        "handness": ["R"], "position_bucket": ["RHP vs RHB"],
        "handedness_bucket": ["RHP vs RHB"],
        "n": [100], "avg_run_value": [0.1], "n_universe": [1000],
        "avg_run_value_universe": [0.05], "delta_run_value": [0.05],
    })
    vistas["respuestas"] = {"ph_position_players": columnas, "ph_pitchers": columnas}

    be.load_ph_impact_rows_from_db(tmp_path, re_lookup={})

    consultadas = " ".join(vistas["consultadas"])
    assert "ph_position_players" in consultadas
    assert "ph_pitchers" in consultadas


def test_el_platoon_consulta_su_vista(vistas):
    # runners hace falta para el respaldo cuando la vista no trae
    # baseline_avg_run_value: sin uno de los dos no hay contra que comparar.
    vistas["respuestas"] = {"platoon": pd.DataFrame({
        "season": [2024], "outs": [1], "runners": ["000"],
        "handedness_split": ["RHP vs RHB"],
        "delta_run_value": [0.02], "n": [5000],
    })}

    be.load_platoon_baseline_impact_rows_from_db(re_lookup={})

    assert any("platoon" in v for v in vistas["consultadas"])


def test_las_vueltas_al_orden_consultan_su_vista(vistas):
    vistas["respuestas"] = {"tto": pd.DataFrame({
        "season": [2024], "runners": ["000"], "handedness_split": ["RHP vs RHB"],
        "tto_pass": [2], "delta_run_value": [0.03], "n": [4000],
    })}

    be.load_tto_impact_rows_from_db()

    assert any("tto" in v for v in vistas["consultadas"])
