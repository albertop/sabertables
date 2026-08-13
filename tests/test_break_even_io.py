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
    with pytest.raises(FileNotFoundError):
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
    estado = {"respuestas": {}, "consultadas": [], "consultas": []}

    def falso(engine, query, view_name, por_lanzamiento=False):
        estado["consultadas"].append(view_name)
        estado["consultas"].append(query)
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

    # Y con CONSULTAS distintas, no solo con nombres distintos: mandar la
    # consulta equivocada a la vista con el nombre correcto daria un resultado
    # plausible y calculado sobre otra cosa.
    assert len(set(vistas["consultas"])) == 3


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


# ---------------------------------------------------------------------------
# El orquestador completo
# ---------------------------------------------------------------------------

@pytest.fixture
def pipeline_falso(monkeypatch, tmp_path):
    """
    main() de punta a punta sin base de datos.

    Se sustituyen los cargadores de vistas por listas fijas --su cableado ya se
    prueba arriba, uno por uno-- y se dejan intactos el calculo sobre la matriz
    RE24, la validacion de rango y las tres escrituras, que es lo que se quiere
    ver funcionando junto.
    """
    import seasons as _s

    # La temporada del TECHO: el resumen final de main() muestra una muestra de
    # seasons.MAX_SEASON, asi que con 2024 esa parte no encontraria nada.
    anio = _s.MAX_SEASON
    matriz = pd.DataFrame([
        {"season": anio, "outs": o, "runners": r, "run_value": rv, "n": 5000,
         "event_cd": cd}
        for o in (0, 1, 2)
        for r, rv in (("000", 0.48), ("100", 0.85), ("010", 1.05), ("001", 1.35),
                      ("110", 1.45), ("101", 1.75), ("011", 1.95), ("111", 2.25))
        for cd in (-1, 15)
    ])
    matriz.to_parquet(tmp_path / "re24_values.parquet", index=False)

    # El valor tiene que CRECER al avanzar el corredor. Con el mismo numero en
    # todos los estados, robar con exito no gana nada, la guarda de monotonia
    # anula todas las celdas y el parquet sale sin un solo break-even.
    VALOR = {"000": 0.48, "100": 0.85, "010": 1.05, "001": 1.35,
             "110": 1.45, "101": 1.75, "011": 1.95, "111": 2.25}
    pd.DataFrame([
        {"season": anio, "outs": o, "runners": r, "count": c,
         "run_value": VALOR[r] * (1 - 0.25 * o), "n": 5000}
        for o in (0, 1, 2, 3) for r in VALOR for c in ("0-0", "3-2")
    ]).to_parquet(tmp_path / "re288_values.parquet", index=False)

    def be_falso(*a, **k):
        return [{"season": anio, "type": "stealing", "scenario": "esc",
                 "label": "Esc", "outs": 0, "breakeven_pct": 0.7, "n": 1000}]

    def impacto_falso(*a, **k):
        # Un tipo distinto por cargador, para que el resumen final encuentre
        # muestra de ph_position y ph_pitcher ademas de la de IBB.
        return [
            {"season": anio, "type": t, "scenario": "010",
             "label": "Runner on 2B", "outs": 1, "delta_re": 0.5,
             "baseline_re": 0.7, "delta_re_pct": 0.7, "n": 1000}
            for t in ("ibb", "ph_position", "ph_pitcher")
        ]

    for nombre in ("load_advance_break_even_rows_from_db",
                   "load_bunt_break_even_rows_from_db",
                   "load_swing_take_break_even_rows_from_db",
                   "load_swing_take_zone_break_even_rows_from_db"):
        monkeypatch.setattr(be, nombre, be_falso)

    # Estos DEVUELVEN TUPLA (break-even, carreras) y hacen una sola consulta.
    # Si se parchean los cargadores viejos, main() llama igualmente a estos y se
    # conecta a SQL Server de verdad: la suite paso de 15 segundos a casi cinco
    # minutos antes de darnos cuenta.
    for nombre in ("load_stretch_rows_from_db", "load_tag_up_rows_from_db",
                   "load_advance_rows_from_db"):
        monkeypatch.setattr(be, nombre, lambda *a, **k: (be_falso(), []))

    monkeypatch.setattr(be, "load_steal_impact_rows_from_db", lambda *a, **k: [])
    monkeypatch.setattr(be, "load_gidp_rows_from_db", lambda *a, **k: [])
    monkeypatch.setattr(be, "load_missed_calls_rows_from_db", lambda *a, **k: [])

    for nombre in ("load_ph_impact_rows_from_db",
                   "load_platoon_baseline_impact_rows_from_db",
                   "load_tto_impact_rows_from_db"):
        monkeypatch.setattr(be, nombre, impacto_falso)

    return tmp_path


def test_el_pipeline_escribe_los_tres_parquets(pipeline_falso):
    be.main(data_dir=pipeline_falso)

    for nombre in ("breakeven_points.parquet", "re_impact.parquet",
                   "sb_breakeven_by_count.parquet"):
        assert (pipeline_falso / nombre).exists(), f"falta {nombre}"


def test_el_pipeline_deja_el_manifiesto_de_procedencia(pipeline_falso):
    """
    Es lo que permite que la siguiente extraccion avise de que estas tablas
    quedaron obsoletas. Si no se escribiera, ese aviso no saltaria nunca.
    """
    be.main(data_dir=pipeline_falso)

    manifiesto = json.loads(
        (pipeline_falso / "derived_from.json").read_text(encoding="utf-8")
    )
    assert manifiesto["inputs"]
    assert "breakeven_points.parquet" in manifiesto["outputs"]


def test_el_robo_se_calcula_desde_la_matriz_y_no_de_una_vista(pipeline_falso):
    """
    Los break-even de robo salen de la matriz RE24, no de SQL. Es la unica
    familia asi, y por eso sigue funcionando aunque la base no responda.
    """
    be.main(data_dir=pipeline_falso)

    df = pd.read_parquet(pipeline_falso / "breakeven_points.parquet")
    assert "stealing" in set(df["type"])


def test_el_impacto_del_toque_de_sacrificio_sale_de_la_matriz(pipeline_falso):
    be.main(data_dir=pipeline_falso)

    df = pd.read_parquet(pipeline_falso / "re_impact.parquet")
    assert "sac_bunt" in set(df["type"])


def test_el_cli_lanza_el_pipeline_y_cierra_la_conexion(monkeypatch):
    llamado, cerrado = [], []
    monkeypatch.setattr(be, "main", lambda: llamado.append(1))
    monkeypatch.setattr(be.db, "dispose_engine", lambda: cerrado.append(1))

    assert be._cli([]) == 0
    assert llamado == [1]
    assert cerrado == [1]


def test_la_conexion_se_cierra_aunque_el_pipeline_falle(monkeypatch):
    """
    El engine se comparte entre las diecisiete vistas. Si un fallo lo dejara
    abierto, la conexion quedaria colgada hasta que el proceso muera.
    """
    cerrado = []
    monkeypatch.setattr(be, "main", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(be.db, "dispose_engine", lambda: cerrado.append(1))

    with pytest.raises(RuntimeError):
        be._cli([])

    assert cerrado == [1]


# ---------------------------------------------------------------------------
# Las vistas tienen que traer lo que dicen
#
# Cada calculo comprueba sus columnas y aborta con los nombres que faltan. Sin
# eso, una vista que cambie produce un KeyError a mitad del calculo o, peor,
# un resultado con una columna a NaN que nadie mira.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("funcion,texto", [
    ("calculate_running_breakeven", "Stretch view"),
    ("calculate_advance_breakeven", "Advance view"),
    ("calculate_tagging_breakeven", "Tag-up view"),
    ("calculate_swing_take_breakeven", "Swing/take aggregate"),
])
def test_una_vista_incompleta_aborta_diciendo_que_falta(funcion, texto):
    incompleto = pd.DataFrame({"season": [2024]})

    with pytest.raises(ValueError, match=texto):
        getattr(be, funcion)(incompleto)


def test_el_toque_comprueba_las_tres_vistas_por_separado():
    completo = pd.DataFrame({"season": [2024], "batter_id": ["x"], "effect": [0.0]})
    pesos = pd.DataFrame({"season": [2024], "batter_id": ["x"],
                          "scenario": ["surprise_bunt"], "outs": [1],
                          "n_bunts": [10], "lg_rv": [0.0]})
    incompleto = pd.DataFrame({"season": [2024]})

    with pytest.raises(ValueError, match="batter-effect"):
        be.build_bunt_swing_away(incompleto, pesos)

    with pytest.raises(ValueError, match="weights"):
        be.build_bunt_swing_away(completo, incompleto)


def test_sin_bateadores_con_muestra_el_baseline_sale_vacio_pero_con_columnas():
    """
    Devolver un DataFrame sin columnas haria fallar el merge de despues con un
    KeyError que no dice de donde viene.
    """
    vacio = pd.DataFrame(columns=["season", "batter_id", "effect"])
    pesos = pd.DataFrame(columns=["season", "batter_id", "scenario", "outs",
                                  "n_bunts", "lg_rv"])

    salida = be.build_bunt_swing_away(vacio, pesos)

    assert len(salida) == 0
    assert "swing_away" in salida.columns


def test_el_toque_tambien_comprueba_su_vista_principal():
    incompleto = pd.DataFrame({"season": [2024]})
    efecto = pd.DataFrame({"season": [2024], "batter_id": ["x"], "effect": [0.0]})
    pesos = pd.DataFrame({"season": [2024], "batter_id": ["x"],
                          "scenario": ["surprise_bunt"], "outs": [1],
                          "n_bunts": [10], "lg_rv": [0.0]})

    with pytest.raises(ValueError, match="Bunt view"):
        be.calculate_bunt_breakeven(incompleto, efecto, pesos)


def test_la_zona_comprueba_su_consulta_de_muestras(vistas):
    """
    La zona hace DOS consultas: los umbrales y los tamanos de muestra. La
    validacion de la segunda vive en el cargador, no en el calculo, porque es
    ahi donde se sabe de que consulta vino.
    """
    vistas["respuestas"] = {
        "agregado por zona": pd.DataFrame({
            "season": [2024], "zone": [5], "handedness_split": ["RHP vs RHB"],
            **{f"c_{b}_{st}": [0.5] for b in range(4) for st in range(3)},
        }),
        "n by season": pd.DataFrame({"season": [2024]}),
    }

    with pytest.raises(ValueError, match="zone n query"):
        be.load_swing_take_zone_break_even_rows_from_db()


# ---------------------------------------------------------------------------
# Celdas incompletas
# ---------------------------------------------------------------------------

def test_si_falta_un_desenlace_entero_la_columna_se_crea_vacia():
    """
    Con solo dos de los tres cubos, el pivote no genera la columna del tercero.
    Sin crearla, el calculo revienta con un KeyError en vez de descartar la
    celda por falta de datos.
    """
    solo_dos = pd.DataFrame([
        {"season": 2024, "outs": 1, "direction": "double", "bucket": b,
         "n": 100, "mean_rv": rv, "sd_rv": 0.2}
        for b, rv in (("stop", 0.26), ("success", 0.43))
    ])

    assert be.calculate_running_breakeven(solo_dos) == []


def test_el_toque_tambien_sobrevive_a_un_cubo_ausente():
    bunt = pd.DataFrame([
        {"season": 2024, "scenario": "surprise_bunt", "outs": 1, "bucket": b,
         "n": 100, "mean_rv": rv, "sd_rv": 0.2}
        for b, rv in (("stop", 0.26), ("success", 0.43))
    ])
    efecto = pd.DataFrame({"season": [2024], "batter_id": ["x"], "effect": [0.0]})
    pesos = pd.DataFrame({"season": [2024], "batter_id": ["x"],
                          "scenario": ["surprise_bunt"], "outs": [1],
                          "n_bunts": [10], "lg_rv": [0.0]})

    assert be.calculate_bunt_breakeven(bunt, efecto, pesos) == []


# ---------------------------------------------------------------------------
# Columnas opcionales al escribir
# ---------------------------------------------------------------------------

def test_el_impacto_sin_etiqueta_usa_el_escenario(tmp_path):
    filas = _filas_impacto()
    for f in filas:
        del f["label"]

    be.save_re_impact(filas, tmp_path)

    df = pd.read_parquet(tmp_path / "re_impact.parquet")
    assert (df["label"] == df["scenario"]).all()


def test_el_impacto_sin_baseline_deja_la_columna_a_nulo(tmp_path):
    """
    No todos los analisis tienen baseline. La columna tiene que existir igual:
    la app lee el parquet con un esquema fijo.
    """
    filas = _filas_impacto()
    for f in filas:
        del f["baseline_re"]

    be.save_re_impact(filas, tmp_path)

    df = pd.read_parquet(tmp_path / "re_impact.parquet")
    assert "baseline_re" in df.columns
    assert df["baseline_re"].isna().all()


def test_el_robo_por_conteo_sin_etiqueta_usa_el_escenario(tmp_path):
    filas = _filas_por_conteo()
    for f in filas:
        del f["label"]

    be.save_sb_breakeven_by_count(filas, tmp_path)

    df = pd.read_parquet(tmp_path / "sb_breakeven_by_count.parquet")
    assert (df["label"] == df["scenario"]).all()


# ---------------------------------------------------------------------------
# Cosas sueltas
# ---------------------------------------------------------------------------

def test_el_break_even_usa_la_conexion_compartida(monkeypatch):
    centinela = object()
    monkeypatch.setattr(be.db, "get_engine", lambda: centinela)

    assert be.connect_to_database() is centinela


def test_con_timeout_configurado_se_manda_el_LOCK_TIMEOUT(monkeypatch):
    """
    Sirve para que una vista bloqueada falle antes en vez de dejar el pipeline
    esperando. Solo se manda si esta configurado.
    """
    registro = []
    monkeypatch.setattr(be, "PH_QUERY_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(be.pd, "read_sql_query",
                        lambda q, c: pd.DataFrame({"season": [2024]}))

    be._read_sql_view(_EngineFalso(None, registro), "SELECT 1", "v")

    assert any("LOCK_TIMEOUT 30000" in s for s in registro)


def test_sin_timeout_configurado_no_se_manda_nada(monkeypatch):
    registro = []
    monkeypatch.setattr(be, "PH_QUERY_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(be.pd, "read_sql_query",
                        lambda q, c: pd.DataFrame({"season": [2024]}))

    be._read_sql_view(_EngineFalso(None, registro), "SELECT 1", "v")

    assert registro == []


# ---------------------------------------------------------------------------
# Celdas que se descartan por falta de datos
# ---------------------------------------------------------------------------

def _re(mapa):
    return {(2024, o, r): v for (o, r), v in mapa.items()}


def test_una_celda_sin_los_tres_estados_de_re_se_salta():
    """
    Si falta la RE de partida, la del robo o la del atrapado, no hay formula.
    Saltar la celda es correcto; publicarla con un hueco no.
    """
    filas = be.calculate_sb_breakeven(_re({(0, "100"): 0.9}), {}, [2024])

    assert all(f["scenario"] != "Runner on 1B steals 2B" or pd.isna(f["breakeven_pct"])
               for f in filas) or filas == []


def test_un_denominador_cero_no_produce_un_infinito():
    """
    Con RE_sb == RE_cs la division seria infinito, y un break-even infinito se
    pinta en la tabla como un numero enorme que parece un dato.
    """
    lookup = _re({(0, "100"): 0.9, (0, "010"): 1.2, (1, "000"): 1.2})

    filas = be.calculate_sb_breakeven(lookup, {}, [2024])
    escenario = [f for f in filas if f["scenario"] == "Runner on 1B steals 2B"]

    assert all(pd.isna(f["breakeven_pct"]) for f in escenario) or escenario == []


def test_por_conteo_tambien_se_saltan_las_celdas_incompletas():
    re288 = {(2024, 0, "100", "0-0"): 0.9}

    filas = be.calculate_sb_breakeven_by_count(re288, {}, [2024])

    assert all(pd.isna(f["breakeven_pct"]) for f in filas) or filas == []


def test_por_conteo_un_denominador_cero_tampoco_pasa():
    re288 = {(2024, 0, "100", "0-0"): 0.9,
             (2024, 0, "010", "0-0"): 1.2,
             (2024, 1, "000", "0-0"): 1.2}

    filas = be.calculate_sb_breakeven_by_count(re288, dict.fromkeys(re288, 5000), [2024])

    assert all(pd.isna(f["breakeven_pct"]) for f in filas) or filas == []


def test_el_elevado_al_jardin_distingue_anotar_de_llegar_a_tercera():
    """
    Son dos jugadas distintas y la etiqueta es lo unico que las separa en la
    tabla: el corredor de 3B que anota y el de 2B que avanza.
    """
    def celda(direccion, runners):
        # El cubo de quedarse se llama `stay` aqui y `stop` en las vistas de
        # estirar el batazo: no quedarse quieto en tercera no es lo mismo que
        # frenar en primera.
        return pd.DataFrame([
            {"season": 2024, "outs": 1, "direction": direccion, "runners": runners,
             "bucket": b, "n": n, "mean_rv": rv, "sd_rv": 0.2}
            for b, (n, rv) in {"stay": (500, 0.26), "success": (300, 0.43),
                               "fail": (40, -0.15)}.items()
        ])

    anota = be.calculate_tagging_breakeven(celda("home", "001"))
    avanza = be.calculate_tagging_breakeven(celda("third", "010"))

    assert "score" in anota[0]["scenario"]
    assert "3B" in avanza[0]["scenario"]
    assert anota[0]["scenario"] != avanza[0]["scenario"]


def test_las_celdas_de_ibb_sin_re_de_partida_se_descartan_y_se_cuentan(caplog):
    """
    Sin la RE del estado inicial no hay delta que calcular. Se cuentan y se
    registran: si un dia se descartaran muchas, el log lo diria.
    """
    ibb = pd.DataFrame([
        {"season": 2024, "outs": 1, "runners": "010", "n": 500, "run_value": 1.2},
    ])

    filas = be.calculate_ibb_impact(ibb, [2024], re_lookup={})

    assert filas == []


def test_una_vuelta_al_orden_desconocida_se_deja_como_viene():
    """
    Las conocidas se normalizan a TTO-1..TTO-3 y la 4+ se pliega sobre la 3. Un
    valor raro se conserva tal cual en vez de inventarse un cubo: asi aparece en
    la tabla y se ve que hay algo que revisar.
    """
    df = pd.DataFrame({
        "season": [2024, 2024], "runners": ["000", "000"],
        "handedness_split": ["RHP vs RHB", "RHP vs RHB"],
        "tto_pass": ["4+", "raro"], "delta_run_value": [0.03, 0.04],
        "n": [4000, 10],
    })

    salida = be._normalize_tto_impact_view_df(df)

    valores = set(salida["tto_pass"])
    assert "TTO-3" in valores, "la cuarta vuelta se pliega sobre la tercera"
    assert "raro" in valores


def test_avanzar_una_base_tambien_descarta_las_celdas_incompletas():
    """
    Misma forma que estirar el batazo: sin los tres desenlaces no hay
    break-even, y la columna que falta hay que crearla o el pivote revienta.
    """
    solo_dos = pd.DataFrame([
        {"season": 2024, "outs": 1, "direction": "first_to_third", "bucket": b,
         "n": 100, "mean_rv": rv, "sd_rv": 0.2}
        for b, rv in (("stop", 0.26), ("success", 0.43))
    ])

    assert be.calculate_advance_breakeven(solo_dos) == []


def test_el_platoon_no_depende_ya_de_la_columna_de_porcentaje_del_sql(caplog):
    """
    Antes, si `delta_run_value_scaled` llegaba nulo, la columna de porcentaje
    salia vacia y el log lo avisaba. Esa dependencia desaparecio: platoon calcula
    el porcentaje como todos, delta / |baseline|.

    Aqui NO hay baseline --ni columna ni re_lookup-- asi que el porcentaje sigue
    saliendo nulo, pero por el motivo correcto: no hay entre que dividir. Lo que
    ya no puede pasar es que dependa de una columna del SQL.
    """
    df = pd.DataFrame({
        "season": [2024], "outs": [1], "runners": ["000"],
        "handedness_split": ["RHP vs RHB"], "delta_run_value": [0.02],
        "delta_run_value_scaled": [0.999], "n": [5000],
    })

    with caplog.at_level("INFO"):
        out = be._normalize_platoon_impact_view_df(df, re_lookup={})

    assert pd.isna(out["delta_re_pct"].iloc[0]), (
        f"sin baseline el porcentaje tiene que salir nulo, y salio "
        f"{out['delta_re_pct'].iloc[0]!r}: sigue viniendo de delta_run_value_scaled"
    )


def test_el_impacto_sin_ninguna_columna_opcional_se_escribe_igual(tmp_path):
    """
    Los seis analisis comparten un solo parquet y cada uno trae unas columnas.
    El esquema publicado tiene que ser SIEMPRE el mismo, o la app --que lee con
    un esquema fijo-- se queda sin ese endpoint.
    """
    minimas = [{
        "season": 2024, "type": "sac_bunt", "scenario": "100",
        "outs": 0, "delta_re": -0.2, "n": 4000,
    }]

    be.save_re_impact(minimas, tmp_path)

    df = pd.read_parquet(tmp_path / "re_impact.parquet")
    for col in ("label", "baseline_re", "delta_re_pct", "platoon",
                "handedness_split", "tto_pass"):
        assert col in df.columns, f"falta {col}"


@pytest.fixture
def pipeline_de_otra_temporada(monkeypatch, tmp_path):
    """
    Un pipeline cuya matriz es de una temporada ANTERIOR al techo, y sin filas
    de base intencional.

    Sirve para el resumen final, que muestra una muestra de seasons.MAX_SEASON:
    aqui ningun analisis tiene datos de esa temporada.
    """
    import seasons as _s

    anio = _s.MIN_SEASON
    VALOR = {"000": 0.48, "100": 0.85, "010": 1.05, "001": 1.35,
             "110": 1.45, "101": 1.75, "011": 1.95, "111": 2.25}

    # Solo event_cd -1: sin filas de IBB (que serian event_cd 15).
    pd.DataFrame([
        {"season": anio, "outs": o, "runners": r,
         "run_value": VALOR[r] * (1 - 0.25 * o), "n": 5000, "event_cd": -1}
        for o in (0, 1, 2, 3) for r in VALOR
    ]).to_parquet(tmp_path / "re24_values.parquet", index=False)

    pd.DataFrame([
        {"season": anio, "outs": o, "runners": r, "count": c,
         "run_value": VALOR[r] * (1 - 0.25 * o), "n": 5000}
        for o in (0, 1, 2, 3) for r in VALOR for c in ("0-0", "3-2")
    ]).to_parquet(tmp_path / "re288_values.parquet", index=False)

    vacio = []
    for nombre in ("load_advance_break_even_rows_from_db",
                   "load_bunt_break_even_rows_from_db",
                   "load_swing_take_break_even_rows_from_db",
                   "load_swing_take_zone_break_even_rows_from_db",
                   "load_ph_impact_rows_from_db",
                   "load_platoon_baseline_impact_rows_from_db",
                   "load_tto_impact_rows_from_db",
                   "load_steal_impact_rows_from_db",
                   "load_gidp_rows_from_db",
                   "load_missed_calls_rows_from_db"):
        monkeypatch.setattr(be, nombre, lambda *a, **k: list(vacio))

    for nombre in ("load_stretch_rows_from_db", "load_tag_up_rows_from_db",
                   "load_advance_rows_from_db"):
        monkeypatch.setattr(be, nombre, lambda *a, **k: ([], []))

    return tmp_path


def test_sin_datos_de_la_ultima_temporada_el_resumen_lo_dice(pipeline_de_otra_temporada, caplog):
    """
    El resumen del final imprime una muestra de la ultima temporada. Cuando un
    analisis no la tiene --porque su fuente termina antes, como ph_pitcher con
    el DH universal-- tiene que decirlo en vez de imprimir una tabla vacia, que
    se lee como si el calculo hubiera fallado.
    """
    with caplog.at_level("INFO"):
        be.main(data_dir=pipeline_de_otra_temporada)

    assert "no IBB data" in caplog.text
    assert "no sac bunt data" in caplog.text


def test_el_pipeline_se_completa_aunque_falten_analisis(pipeline_de_otra_temporada):
    """Control: que falte la muestra no impide publicar los parquets."""
    be.main(data_dir=pipeline_de_otra_temporada)

    assert (pipeline_de_otra_temporada / "re_impact.parquet").exists()


def test_el_pipeline_acota_swing_take_con_el_parametro(pipeline_falso, monkeypatch):
    """
    El tope sale de seasons.MAX_SEASON y no del maximo del parquet de re24.
    Derivarlo del dato hacia que el rango publicado cambiara solo en cuanto
    alguien cargara una temporada nueva, sin tocar una linea de codigo.
    """
    import seasons as _s

    recibido = {}

    def espia(season_cap):
        recibido["cap"] = season_cap
        return []

    monkeypatch.setattr(be, "load_swing_take_break_even_rows_from_db", espia)

    be.main(data_dir=pipeline_falso)

    assert recibido["cap"] == _s.MAX_SEASON


# ---------------------------------------------------------------------------
# El dominio de los estados de corredores (CR-06-03)
# ---------------------------------------------------------------------------

def test_los_ocho_estados_canonicos_se_aceptan():
    for estado in ("000", "100", "010", "001", "110", "101", "011", "111"):
        assert be._normalizar_runners(pd.Series([estado]), "prueba").iloc[0] == estado


def test_un_estado_corto_se_rellena_con_ceros():
    """
    Si SQL devuelve el estado como numero, `1` es en realidad `001`: corredor en
    TERCERA. Sin el relleno se buscaria una clave que no existe.
    """
    assert be._normalizar_runners(pd.Series(["1"]), "prueba").iloc[0] == "001"


def test_un_estado_que_no_existe_aborta_la_generacion():
    """
    Los estados de corredores son la LLAVE de RE24 y RE288. Uno mal normalizado
    no da error: contamina el lookup, y con el los escenarios de tag-up,
    swing/take, platoon y vueltas al orden. El resultado sale plausible y es de
    otra situacion de juego.

    Solo hay ocho estados posibles. Cualquier otra cosa es un dato roto.
    """
    with pytest.raises(ValueError, match="222"):
        be._normalizar_runners(pd.Series(["100", "222"]), "v_prueba")


def test_el_error_dice_de_que_vista_vino_el_estado_malo():
    with pytest.raises(ValueError, match="v_re_analysis_tag_up"):
        be._normalizar_runners(pd.Series(["999"]), "v_re_analysis_tag_up")


def test_el_tag_up_valida_sus_estados():
    """La validacion tiene que estar en las cuatro rutas, no solo en una."""
    df = pd.DataFrame([
        {"season": 2024, "outs": 1, "direction": "home", "runners": "777",
         "bucket": b, "n": 100, "mean_rv": rv, "sd_rv": 0.2}
        for b, rv in (("stay", 0.26), ("success", 0.43), ("fail", -0.15))
    ])

    with pytest.raises(ValueError, match="777"):
        be.calculate_tagging_breakeven(df)


# ---------------------------------------------------------------------------
# Impacto del robo en carreras (seccion 10 del TODO)
# ---------------------------------------------------------------------------

def _intentos(exitos=780, fallos=220):
    """Lo que devuelve v_re_analysis_sb_attempts."""
    return pd.DataFrame([{
        "season": 2024, "outs": 0, "runners": "100",
        "scenario": "Runner on 1B steals 2B",
        "n_success": exitos, "n_fail": fallos,
        "n_attempts": exitos + fallos,
    }])


def _re_robo():
    """RE24 coherente: robar sube la RE, ser atrapado la baja."""
    return {(2024, 0, "100"): 0.85, (2024, 0, "010"): 1.05, (2024, 1, "000"): 0.28}


def test_el_robo_en_carreras_usa_la_tasa_observada(vistas):
    """
    Es lo que aporta la vista nueva: sin ella solo se podian publicar ganancia y
    coste, no el valor esperado de intentarlo.
    """
    vistas["respuestas"] = {"sb_attempts": _intentos()}

    filas = be.load_steal_impact_rows_from_db(_re_robo(), [2024])
    fila = next(f for f in filas if f["outs"] == 0)

    p = 780 / 1000
    ganancia = 1.05 - 0.85
    coste = 0.85 - 0.28
    assert fila["delta_re"] == pytest.approx(p * ganancia - (1 - p) * coste)


def test_las_filas_del_robo_van_al_esquema_de_re_impact(vistas):
    """
    Comparten parquet con el toque de sacrificio y la base intencional, asi que
    tienen que traer las mismas columnas o el endpoint se rompe.
    """
    vistas["respuestas"] = {"sb_attempts": _intentos()}

    fila = be.load_steal_impact_rows_from_db(_re_robo(), [2024])[0]

    assert {"season", "type", "scenario", "label", "outs", "delta_re", "n"} <= set(fila)
    assert fila["type"] == "steal_runs"


def test_el_n_publicado_son_los_INTENTOS_y_no_los_fallos(vistas):
    """
    Los break-even publican n_fail, porque es el termino que domina su varianza.
    Una tabla en carreras quiere los intentos totales: son denominadores
    distintos y copiarlos daria marcadores de fiabilidad que describen otra cosa.
    """
    vistas["respuestas"] = {"sb_attempts": _intentos(exitos=780, fallos=220)}

    fila = be.load_steal_impact_rows_from_db(_re_robo(), [2024])[0]

    assert fila["n"] == 1000, "los intentos, no los 220 fallos"


def test_un_robo_que_baja_la_re_no_publica_impacto(vistas):
    """
    Misma guarda que en el break-even: robar con exito no puede bajar la RE.
    Cuando la matriz dice lo contrario, la celda esta mal estimada y publicar
    una cifra en carreras seria darle credibilidad.
    """
    vistas["respuestas"] = {"sb_attempts": _intentos()}
    incoherente = {(2024, 0, "100"): 1.20, (2024, 0, "010"): 0.90, (2024, 1, "000"): 0.28}

    filas = be.load_steal_impact_rows_from_db(incoherente, [2024])

    assert all(f["delta_re"] is None or pd.isna(f["delta_re"]) for f in filas)


def test_sin_intentos_registrados_no_se_publica_la_celda(vistas):
    """Una celda sin un solo intento no dice nada sobre la decision."""
    vistas["respuestas"] = {"sb_attempts": pd.DataFrame(columns=[
        "season", "outs", "runners", "scenario", "n_success", "n_fail", "n_attempts"])}

    assert be.load_steal_impact_rows_from_db(_re_robo(), [2024]) == []


def test_con_dos_outs_el_atrapado_vale_cero_por_definicion(vistas):
    """
    El tercer out acaba la entrada: la RE posterior es CERO, no un valor que
    buscar en la matriz con outs=3, que no existe.

    Sin esa regla la celda desaparece en silencio --el lookup falla y se salta--
    y justo con dos outs es cuando mas se roba: 1.163 intentos en 2025 frente a
    788 con cero outs.
    """
    vistas["respuestas"] = {"sb_attempts": pd.DataFrame([{
        "season": 2024, "outs": 2, "runners": "100",
        "scenario": "Runner on 1B steals 2B",
        "n_success": 800, "n_fail": 200, "n_attempts": 1000,
    }])}
    # A proposito SIN entrada para (2024, 3, "000"): con la regla no hace falta.
    solo_dos_outs = {(2024, 2, "100"): 0.22, (2024, 2, "010"): 0.32}

    filas = be.load_steal_impact_rows_from_db(solo_dos_outs, [2024])

    assert filas, "la celda de dos outs deberia publicarse"
    fila = filas[0]
    assert fila["cost_runs"] == pytest.approx(0.22), "el coste es la RE entera"
    assert fila["delta_re"] == pytest.approx(0.8 * (0.32 - 0.22) - 0.2 * 0.22)


def test_la_vista_de_intentos_tiene_que_traer_sus_columnas(vistas):
    """Si la vista cambia, mejor abortar que calcular con lo que quede."""
    vistas["respuestas"] = {"sb_attempts": pd.DataFrame({"season": [2024]})}

    with pytest.raises(ValueError, match="SB attempts view"):
        be.load_steal_impact_rows_from_db(_re_robo(), [2024])


def test_una_celda_sin_intentos_registrados_se_salta(vistas):
    """
    La vista solo trae celdas con al menos un intento, pero el bucle recorre los
    cuatro escenarios por tres estados de out: la mayoria no estara.
    """
    vistas["respuestas"] = {"sb_attempts": _intentos()}

    filas = be.load_steal_impact_rows_from_db(_re_robo(), [2024])

    assert len(filas) == 1, "solo la celda que la vista trajo"


def test_una_celda_sin_re_en_la_matriz_se_salta(vistas):
    """Sin los tres estados de RE no hay carreras que calcular."""
    vistas["respuestas"] = {"sb_attempts": _intentos()}
    incompleta = {(2024, 0, "100"): 0.85}

    assert be.load_steal_impact_rows_from_db(incompleta, [2024]) == []


def test_una_celda_con_cero_intentos_no_divide_por_cero(vistas):
    """
    La vista trae `HAVING n_attempts > 0`, asi que esto no deberia llegar. La
    guarda esta porque la alternativa es un ZeroDivisionError al calcular la
    tasa, y el pipeline entero se caeria por una fila rara de una vista que
    alguien toque manana.
    """
    vistas["respuestas"] = {"sb_attempts": pd.DataFrame([{
        "season": 2024, "outs": 0, "runners": "100",
        "scenario": "Runner on 1B steals 2B",
        "n_success": 0, "n_fail": 0, "n_attempts": 0,
    }])}

    assert be.load_steal_impact_rows_from_db(_re_robo(), [2024]) == []


def test_el_pipeline_no_abre_ninguna_conexion(pipeline_falso, monkeypatch):
    """
    Ningun test debe tocar SQL Server. Cuando main() empezo a llamar a
    load_stretch_rows_from_db --nuevo, devuelve tupla-- las fixtures seguian
    parcheando el cargador viejo, asi que la suite se conectaba de verdad y paso
    de 15 segundos a casi cinco minutos. Tardamos en verlo porque los tests
    seguian en verde.

    Este test lo convierte en un fallo inmediato en vez de una lentitud rara.
    """
    def prohibido():
        raise AssertionError("un test intento conectar a SQL Server")

    monkeypatch.setattr(be, "connect_to_database", prohibido)

    be.main(data_dir=pipeline_falso)


def test_estirar_el_batazo_consulta_UNA_vez_y_da_las_dos_familias(vistas):
    """
    La vista es lenta. Un cargador por familia la consultaria dos veces para
    sacar de los mismos tres cubos el umbral y las carreras.
    """
    vistas["respuestas"] = {"stretch": _estirar()}

    umbral, carreras = be.load_stretch_rows_from_db()

    assert len([v for v in vistas["consultadas"] if "stretch" in v]) == 1
    assert umbral and carreras
    assert umbral[0]["type"] == be.BREAKEVEN_TYPE_RUNNING
    assert carreras[0]["type"] == "running_runs"


def test_el_tag_up_tambien_consulta_una_sola_vez(vistas):
    # El cubo de quedarse se llama `stay` en tag-up, no `stop`.
    tag = pd.DataFrame([
        {"season": 2024, "outs": 1, "direction": "home", "runners": "001",
         "bucket": b, "n": n, "mean_rv": rv, "sd_rv": 0.2}
        for b, (n, rv) in {"stay": (500, 0.26), "success": (300, 0.43),
                           "fail": (40, -0.15)}.items()
    ])
    vistas["respuestas"] = {"tag_up": tag}

    umbral, carreras = be.load_tag_up_rows_from_db()

    assert len([v for v in vistas["consultadas"] if "tag_up" in v]) == 1
    assert umbral and carreras
    assert carreras[0]["type"] == "tagging_runs"


def test_el_doble_robo_reparte_el_atrapado_entre_los_dos_corredores(vistas):
    """
    El atrapado del doble robo es una MEZCLA: 0.863 el corredor de delante,
    0.137 el de atras, medido sobre r24Transitions. No es un solo estado.

    Usar uno solo hacia que esta tabla y la de break-even dejaran de cuadrar. Lo
    detecto el test de la identidad contra los datos publicados: 232 celdas
    descuadradas de 348, todas de doble robo, y solo con 0 y 1 out --con 2 el
    atrapado vale cero por definicion y no hay mezcla que hacer.
    """
    vistas["respuestas"] = {"sb_attempts": pd.DataFrame([{
        "season": 2024, "outs": 0, "runners": "110", "scenario": "Double steal",
        "n_success": 80, "n_fail": 20, "n_attempts": 100,
    }])}
    lookup = {
        (2024, 0, "110"): 1.45, (2024, 0, "011"): 1.95,
        (2024, 1, "010"): 0.65,   # atrapan al de delante
        (2024, 1, "001"): 0.95,   # atrapan al de atras
    }

    fila = be.load_steal_impact_rows_from_db(lookup, [2024])[0]

    w = be.DOUBLE_STEAL_LEAD_CAUGHT_WEIGHT
    re_cs = w * 0.65 + (1 - w) * 0.95
    assert fila["cost_runs"] == pytest.approx(1.45 - re_cs)
    assert fila["cost_runs"] != pytest.approx(1.45 - 0.65), "eso seria solo el de delante"
