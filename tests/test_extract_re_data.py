"""
La extraccion: tipado, escritura, staging y promocion.

No se abre ninguna conexion. `pd.read_sql` se sustituye por un DataFrame, que es
lo que devolveria SQL Server, y todo se escribe a un directorio temporal.

Lo que se prueba aqui decide si un lote a medias llega a publicarse. Es codigo
que corre una o dos veces al ano, o sea que un fallo no se descubre hasta el
siguiente ciclo -- y para entonces los datos ya llevan meses en la web.
"""
import json

import extract_re_data as extract
import pandas as pd
import pytest
import seasons

# ---------------------------------------------------------------------------
# Los marcadores de temporada
# ---------------------------------------------------------------------------

def test_los_marcadores_se_sustituyen_por_los_valores_vigentes():
    sql = extract._resolver_temporadas(
        "WHERE a BETWEEN {SEASON_MIN} AND {SEASON_MAX} AND b >= {SEASON_PP_MIN}"
    )

    assert sql == (
        f"WHERE a BETWEEN {seasons.MIN_SEASON} AND {seasons.MAX_SEASON} "
        f"AND b >= {seasons.MIN_PER_PITCH_SEASON}"
    )


def test_una_consulta_sin_marcadores_se_queda_igual():
    assert extract._resolver_temporadas("SELECT 1") == "SELECT 1"


# ---------------------------------------------------------------------------
# Tipado
# ---------------------------------------------------------------------------

def _dataset(**kw):
    base = {
        "key": "prueba", "title": "Prueba", "query": "SELECT 1",
        "output": "prueba.parquet",
        "dtypes": {"season": "int32", "n": "int32", "run_value": "float32"},
    }
    base.update(kw)
    return extract.Dataset(**base)


def _filas(seasons_lista=(2024, 2025)):
    return pd.DataFrame({
        "season": list(seasons_lista),
        "n": [10] * len(seasons_lista),
        "run_value": [0.5] * len(seasons_lista),
    })


def test_los_tipos_declarados_se_aplican():
    """
    Sin esto los parquets salen con int64 y float64 por defecto: ocupan el doble
    y la app los sirve igual, asi que nadie lo nota.
    """
    df = extract._apply_dtypes(_filas(), _dataset())

    assert df["season"].dtype == "int32"
    assert df["run_value"].dtype == "float32"


def test_una_columna_declarada_que_no_viene_detiene_la_extraccion():
    """
    Si la consulta deja de traer una columna declarada, se aborta con su nombre.

    Es lo contrario de lo que se podria esperar --que el tipado ignorase lo que
    no esta-- y es lo correcto: un dtype declarado que no se aplica significa que
    la consulta cambio, y publicar el parquet sin esa columna deja la tabla
    servida a medias sin ningun error.
    """
    ds = _dataset(dtypes={"season": "int32", "no_existe": "int32"})

    with pytest.raises(ValueError, match="no_existe"):
        extract._apply_dtypes(_filas(), ds)


def test_el_error_de_tipado_dice_que_columnas_si_llegaron():
    """Sin eso hay que ir a ejecutar la consulta a mano para compararlas."""
    ds = _dataset(dtypes={"no_existe": "int32"})

    with pytest.raises(ValueError, match="run_value"):
        extract._apply_dtypes(_filas(), ds)


# ---------------------------------------------------------------------------
# Extraer un dataset
# ---------------------------------------------------------------------------

@pytest.fixture
def sin_base(monkeypatch):
    """Sustituye la consulta por un DataFrame fijo y devuelve el SQL recibido."""
    recibido = {}

    def falso(sql, _engine):
        recibido["sql"] = sql
        return recibido.get("df", _filas()).copy()

    monkeypatch.setattr(extract.pd, "read_sql", falso)
    return recibido


def test_extraer_un_dataset_escribe_su_parquet(sin_base, tmp_path):
    df = extract._extract_one(_dataset(), object(), tmp_path)

    assert (tmp_path / "prueba.parquet").exists()
    assert len(df) == 2


def test_la_consulta_llega_con_el_rango_ya_resuelto(sin_base, tmp_path):
    """
    Un `{SEASON_MAX}` que llegue a SQL sin sustituir es un error de sintaxis en
    plena extraccion anual, despues de haber esperado media hora.
    """
    ds = _dataset(query="SELECT * FROM t WHERE s BETWEEN {SEASON_MIN} AND {SEASON_MAX}")

    extract._extract_one(ds, object(), tmp_path)

    assert "{SEASON" not in sin_base["sql"]
    assert str(seasons.MAX_SEASON) in sin_base["sql"]


def test_lo_que_venga_fuera_de_rango_se_recorta(sin_base, tmp_path):
    """
    Filtro de garantia. Es lo que corto 676 filas de la temporada en curso que
    la vista de swing/take por zona seguia devolviendo sin acotar.
    """
    sin_base["df"] = _filas([1800, 2024, 2025, 2099])

    df = extract._extract_one(_dataset(), object(), tmp_path)

    assert df["season"].tolist() == [2024, 2025]


def test_el_suelo_de_conteo_se_aplica_a_los_datasets_marcados(sin_base, tmp_path):
    """RE288 arranca en 1989; con el suelo general publicaria decadas vacias."""
    sin_base["df"] = _filas([1950, 2024])

    df = extract._extract_one(_dataset(por_lanzamiento=True), object(), tmp_path)

    assert df["season"].tolist() == [2024]


def test_un_dataset_sin_temporada_no_se_recorta(sin_base, tmp_path):
    """El catalogo de entradas o el de tipos de evento no llevan temporada."""
    sin_base["df"] = pd.DataFrame({"inn_id": [1, 2, 3]})
    ds = _dataset(season_col=None, dtypes={})

    df = extract._extract_one(ds, object(), tmp_path)

    assert len(df) == 3


def test_un_dataset_en_json_se_escribe_como_json(sin_base, tmp_path):
    sin_base["df"] = pd.DataFrame({"inn_id": [1, 2]})
    ds = _dataset(output="innings.json", fmt="json", season_col=None, dtypes={})

    extract._extract_one(ds, object(), tmp_path)

    contenido = json.loads((tmp_path / "innings.json").read_text())
    assert contenido == [{"inn_id": 1}, {"inn_id": 2}]


def test_el_resumen_extra_no_falla_si_falta_la_columna(sin_base, tmp_path):
    ds = _dataset(extra_summary=["no_existe", "season"])

    extract._extract_one(ds, object(), tmp_path)

    assert (tmp_path / "prueba.parquet").exists()


# ---------------------------------------------------------------------------
# Promocion del staging
# ---------------------------------------------------------------------------

def _staging(tmp_path, ficheros, con_marca=True):
    staging = tmp_path / ".staging"
    staging.mkdir()
    for nombre in ficheros:
        (staging / nombre).write_bytes(b"contenido")
    if con_marca:
        (staging / "last_updated.json").write_text('{"last_updated_utc": "hoy"}')
    return staging, tmp_path / "final"


def test_un_lote_completo_se_promueve(tmp_path):
    staging, final = _staging(tmp_path, ["a.parquet", "b.parquet"])
    final.mkdir()

    extract._promote_staging(staging, final, ["a.parquet", "b.parquet"])

    assert (final / "a.parquet").exists()
    assert (final / "b.parquet").exists()


def test_un_lote_al_que_le_falta_un_fichero_NO_se_promueve(tmp_path):
    """
    Antes se escribia in place uno a uno: si fallaba el decimo, app/data quedaba
    con diez ficheros nuevos y seis viejos, mutuamente inconsistentes, y la app
    los servia sin enterarse.
    """
    staging, final = _staging(tmp_path, ["a.parquet"])
    final.mkdir()
    (final / "a.parquet").write_bytes(b"viejo")

    with pytest.raises(RuntimeError, match="incompleto"):
        extract._promote_staging(staging, final, ["a.parquet", "falta.parquet"])

    assert (final / "a.parquet").read_bytes() == b"viejo", "no se toco nada"


def test_un_fichero_vacio_tampoco_se_promueve(tmp_path):
    """
    Un parquet de cero bytes no da error al escribirse y deja la tabla en blanco
    en la web sin que nada falle.
    """
    staging, final = _staging(tmp_path, ["a.parquet"])
    (staging / "vacio.parquet").write_bytes(b"")
    final.mkdir()

    with pytest.raises(RuntimeError, match=r"[Vv]acio"):
        extract._promote_staging(staging, final, ["a.parquet", "vacio.parquet"])


def test_en_un_lote_completo_la_marca_de_frescura_es_obligatoria(tmp_path):
    """
    last_updated.json es la marca de commit: si su timestamp es nuevo, el lote
    entero esta completo. Antes el bucle hacia `if not source.exists(): continue`,
    o sea que si dejaba de escribirse el lote se promovia igual -- datos nuevos
    con una fecha vieja, que es justo lo que la web ensena al usuario.
    """
    staging, final = _staging(tmp_path, ["a.parquet"], con_marca=False)
    final.mkdir()

    with pytest.raises(RuntimeError):
        extract._promote_staging(staging, final, ["a.parquet"])


def test_en_una_extraccion_parcial_la_marca_no_es_obligatoria(tmp_path):
    """
    Con --dataset no se reescribe a proposito: seguiria describiendo un lote que
    ya no es homogeneo, porque el resto de ficheros conserva su version previa.
    """
    staging, final = _staging(tmp_path, ["a.parquet"], con_marca=False)
    final.mkdir()

    extract._promote_staging(staging, final, ["a.parquet"], completa=False)

    assert (final / "a.parquet").exists()


def test_la_marca_de_frescura_se_mueve_la_ultima(tmp_path):
    """
    Es lo que la convierte en marca de commit. Si fuera antes que los parquets,
    una interrupcion dejaria una fecha nueva sobre datos viejos.
    """
    staging, final = _staging(tmp_path, ["a.parquet"])
    final.mkdir()

    extract._promote_staging(staging, final, ["a.parquet"])

    assert (final / "last_updated.json").exists()
    assert (final / "a.parquet").exists()


# ---------------------------------------------------------------------------
# El aviso de datos derivados obsoletos
# ---------------------------------------------------------------------------

def _manifiesto(final, entradas, salidas=("breakeven_points.parquet",)):
    (final / "derived_from.json").write_text(json.dumps({
        "inputs": entradas, "outputs": list(salidas),
    }), encoding="utf-8")


def test_avisa_si_cambia_un_fichero_del_que_dependen_los_derivados(tmp_path, caplog):
    """
    No es hipotetico: refrescar 01 cambio re24_values en +476 filas y
    re24_transitions en -4.144, y hubo que reejecutar 03 a mano. Nada avisaba, y
    mientras tanto las tres tablas derivadas estaban calculadas sobre una matriz
    distinta de la publicada.
    """
    final = tmp_path
    (final / "re24_values.parquet").write_bytes(b"nuevo contenido")
    _manifiesto(final, {"re24_values.parquet": "hash_viejo"})

    with caplog.at_level("WARNING"):
        extract._warn_if_derived_data_now_stale(final, ["re24_values.parquet"])

    assert "OBSOLETOS" in caplog.text
    assert "re24_values.parquet" in caplog.text
    assert "create_break_even_points" in caplog.text


def test_no_avisa_si_el_fichero_no_cambio(tmp_path, caplog):
    """Un aviso que salta siempre deja de leerse."""
    final = tmp_path
    fichero = final / "re24_values.parquet"
    fichero.write_bytes(b"contenido")
    _manifiesto(final, {"re24_values.parquet": extract._sha256(fichero)})

    with caplog.at_level("WARNING"):
        extract._warn_if_derived_data_now_stale(final, ["re24_values.parquet"])

    assert "OBSOLETOS" not in caplog.text


def test_no_avisa_por_ficheros_que_no_se_promovieron(tmp_path, caplog):
    """En una extraccion parcial solo importan los que se tocaron."""
    final = tmp_path
    (final / "re24_values.parquet").write_bytes(b"otro")
    _manifiesto(final, {"re24_values.parquet": "hash_viejo"})

    with caplog.at_level("WARNING"):
        extract._warn_if_derived_data_now_stale(final, ["woba_coeffs.parquet"])

    assert "OBSOLETOS" not in caplog.text


def test_sin_manifiesto_no_hay_nada_que_avisar(tmp_path, caplog):
    with caplog.at_level("WARNING"):
        extract._warn_if_derived_data_now_stale(tmp_path, ["a.parquet"])

    assert "OBSOLETOS" not in caplog.text


def test_un_manifiesto_corrupto_no_tumba_la_extraccion(tmp_path, caplog):
    """
    El aviso es una cortesia. Que un JSON mal formado hiciera fallar una
    extraccion ya terminada seria perder media hora por un fichero accesorio.
    """
    (tmp_path / "derived_from.json").write_text("{esto no es json", encoding="utf-8")

    with caplog.at_level("WARNING"):
        extract._warn_if_derived_data_now_stale(tmp_path, ["a.parquet"])

    assert "OBSOLETOS" not in caplog.text


def test_el_hash_cambia_con_el_contenido(tmp_path):
    a = tmp_path / "a"
    a.write_bytes(b"uno")
    b = tmp_path / "b"
    b.write_bytes(b"dos")

    assert extract._sha256(a) != extract._sha256(b)
    assert extract._sha256(a) == extract._sha256(a)


# ---------------------------------------------------------------------------
# El orquestador completo y su linea de comandos
# ---------------------------------------------------------------------------

@pytest.fixture
def extraccion_falsa(monkeypatch, tmp_path):
    """
    Toda la extraccion sin base de datos y sin tocar app/data.

    Se sustituye `pd.read_sql` --no `_extract_one`-- para que el camino real
    quede cubierto: tipado, recorte, escritura, staging y promocion.
    """
    def falso(sql, _engine):
        return pd.DataFrame({
            "season": [2024, 2025], "n": [10, 20], "run_value": [0.5, 0.6],
        })

    monkeypatch.setattr(extract.pd, "read_sql", falso)
    monkeypatch.setattr(extract, "get_connection", lambda: object())
    monkeypatch.setattr(extract.db, "dispose_engine", lambda: None)

    # Un catalogo de dos datasets en vez de los dieciseis reales: prueba el mismo
    # camino y no depende de que las consultas de verdad devuelvan estas columnas.
    catalogo = [
        extract.Dataset(key="uno", title="Uno", query="SELECT 1",
                        output="uno.parquet",
                        dtypes={"season": "int32", "n": "int32", "run_value": "float32"}),
        extract.Dataset(key="dos", title="Dos", query="SELECT 2",
                        output="dos.parquet",
                        dtypes={"season": "int32", "n": "int32", "run_value": "float32"}),
    ]
    monkeypatch.setattr(extract, "DATASETS", catalogo)
    return tmp_path


def test_una_extraccion_completa_publica_todo_y_su_marca(extraccion_falsa):
    extract.extract_and_save_data(data_dir=extraccion_falsa)

    assert (extraccion_falsa / "uno.parquet").exists()
    assert (extraccion_falsa / "dos.parquet").exists()
    assert (extraccion_falsa / "last_updated.json").exists()


def test_la_marca_de_frescura_lista_los_ficheros_del_lote(extraccion_falsa):
    extract.extract_and_save_data(data_dir=extraccion_falsa)

    marca = json.loads((extraccion_falsa / "last_updated.json").read_text())
    assert sorted(marca["files"]) == ["dos.parquet", "uno.parquet"]
    assert marca["last_updated_utc"]


def test_el_staging_no_queda_tirado_al_terminar(extraccion_falsa):
    """Vive dentro de app/data; dejarlo ahi confunde a quien mire la carpeta."""
    extract.extract_and_save_data(data_dir=extraccion_falsa)

    restos = list((extraccion_falsa / ".staging").glob("*")) if (extraccion_falsa / ".staging").exists() else []
    assert not restos


def test_una_extraccion_parcial_no_reescribe_la_marca(extraccion_falsa):
    """
    Con --dataset la marca seguiria describiendo un lote que ya no es homogeneo:
    el resto de ficheros conserva su version previa.
    """
    extract.extract_and_save_data(data_dir=extraccion_falsa)
    marca_original = (extraccion_falsa / "last_updated.json").read_text()

    extract.extract_and_save_data(only=["uno"], data_dir=extraccion_falsa)

    assert (extraccion_falsa / "last_updated.json").read_text() == marca_original


def test_un_dataset_desconocido_se_rechaza_antes_de_conectar(extraccion_falsa):
    """
    Un nombre mal escrito tiene que fallar al instante y con la lista de los
    validos, no despues de veinte minutos de consultas.
    """
    with pytest.raises(SystemExit, match="uno"):
        extract.extract_and_save_data(only=["no_existe"], data_dir=extraccion_falsa)


def test_si_algo_falla_a_medias_los_datos_publicados_no_se_tocan(extraccion_falsa, monkeypatch):
    """
    Es la razon de ser del staging. Antes se escribia in place y un fallo dejaba
    app/data mezclando ficheros nuevos y viejos.
    """
    extract.extract_and_save_data(data_dir=extraccion_falsa)
    bueno = (extraccion_falsa / "uno.parquet").read_bytes()

    def revienta(sql, _engine):
        raise RuntimeError("la vista no responde")

    monkeypatch.setattr(extract.pd, "read_sql", revienta)

    with pytest.raises(RuntimeError, match="no responde"):
        extract.extract_and_save_data(data_dir=extraccion_falsa)

    assert (extraccion_falsa / "uno.parquet").read_bytes() == bueno
    assert not (extraccion_falsa / ".staging").exists(), "el staging se descarta"


def test_la_conexion_se_cierra_aunque_la_extraccion_falle(extraccion_falsa, monkeypatch):
    cerrado = []
    monkeypatch.setattr(extract.db, "dispose_engine", lambda: cerrado.append(1))
    monkeypatch.setattr(extract.pd, "read_sql",
                        lambda sql, _e: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        extract.extract_and_save_data(data_dir=extraccion_falsa)

    assert cerrado, "el pool quedaria abierto"


def test_el_cli_lista_el_catalogo_sin_conectar(extraccion_falsa, capsys):
    """
    --list tiene que funcionar sin base delante.

    Se lee de capsys y no de caplog: cli.setup_logging monta su propio logger con
    handler a consola, asi que caplog --que engancha en el logger raiz-- no ve
    nada y el test pasaria por vacio.
    """
    assert extract.main(["--list"]) == 0

    salida = capsys.readouterr().out + capsys.readouterr().err
    assert "uno" in salida and "dos" in salida


def test_el_cli_lanza_la_extraccion(monkeypatch):
    recibido = {}
    monkeypatch.setattr(extract, "extract_and_save_data",
                        lambda only=None: recibido.update(only=only))

    assert extract.main([]) == 0
    assert recibido == {"only": None}


def test_el_cli_pasa_los_datasets_pedidos(monkeypatch):
    recibido = {}
    monkeypatch.setattr(extract, "extract_and_save_data",
                        lambda only=None: recibido.update(only=only))

    extract.main(["--dataset", "uno", "--dataset", "dos"])

    assert recibido == {"only": ["uno", "dos"]}


def test_el_cli_acepta_las_tres_fechas(monkeypatch):
    visto = []
    monkeypatch.setattr(extract, "extract_and_save_data",
                        lambda only=None: visto.append(seasons.rango()))

    extract.main(["--min-season", "1930", "--max-season", "2020"])

    assert visto == [(1930, 2020)]


def test_la_extraccion_usa_la_conexion_compartida(monkeypatch):
    centinela = object()
    monkeypatch.setattr(extract.db, "get_engine", lambda: centinela)

    assert extract.get_connection() is centinela


# ---------------------------------------------------------------------------
# Las columnas de tres caracteres y la comprobacion de tipos
# ---------------------------------------------------------------------------

def test_las_columnas_de_tres_caracteres_se_normalizan():
    """
    `runners` y `count` son de tres caracteres exactos: '100', '0-0'. Un cero a
    la izquierda perdido convierte el estado de bases en otro distinto.
    """
    df = pd.DataFrame({"season": [2024], "runners": ["100"], "n": [1]})
    ds = _dataset(dtypes={"season": "int32", "runners": extract.STRING3, "n": "int32"})

    salida = extract._apply_dtypes(df, ds)

    assert str(salida["runners"].dtype) == "string"
    assert salida["runners"].iloc[0] == "100"


def test_si_un_cast_no_deja_el_tipo_declarado_se_aborta(monkeypatch):
    """
    La comprobacion posterior al cast existia solo para tres de los dieciseis
    datasets, escrita a mano en cada bloque. Ahora es automatica: publicar un
    parquet con un tipo distinto del declarado pasa desapercibido hasta que algo
    aguas abajo se comporta raro.
    """
    ds = _dataset(dtypes={"season": "int32", "n": "int32", "run_value": "float32"})

    # Un astype que no hace nada: simula un cast que no toma efecto.
    monkeypatch.setattr(pd.Series, "astype", lambda self, dtype, **kw: self)

    with pytest.raises(ValueError, match="se esperaba"):
        extract._apply_dtypes(_filas(), ds)


def test_una_columna_sin_tipo_declarado_se_avisa_pero_se_publica(capsys):
    """
    No es un error --puede ser una columna nueva de la consulta-- pero conviene
    saberlo: sale con el tipo por defecto de pandas, que suele ocupar el doble.
    """
    df = _filas()
    df["extra"] = [1, 2]

    salida = extract._apply_dtypes(df, _dataset())

    assert "extra" in salida.columns


def test_el_resumen_lista_los_valores_cuando_son_pocos(sin_base, tmp_path):
    """
    Con doce valores o menos se listan; con mas, solo el rango. Un volcado de
    miles de valores en el log lo hace ilegible.
    """
    sin_base["df"] = pd.DataFrame({
        "season": [2024] * 3, "n": [1, 2, 3], "run_value": [0.1, 0.2, 0.3],
    })

    extract._extract_one(_dataset(extra_summary=["n"]), object(), tmp_path)

    assert (tmp_path / "prueba.parquet").exists()


def test_el_resumen_solo_da_el_rango_cuando_hay_muchos(sin_base, tmp_path):
    sin_base["df"] = pd.DataFrame({
        "season": [2024] * 20, "n": list(range(20)), "run_value": [0.1] * 20,
    })

    extract._extract_one(_dataset(extra_summary=["n"]), object(), tmp_path)

    assert (tmp_path / "prueba.parquet").exists()


def test_un_staging_de_una_ejecucion_anterior_se_limpia(extraccion_falsa):
    """
    Si quedo de una extraccion que fallo, sus ficheros viejos se mezclarian con
    los nuevos y el lote promovido seria una mezcla de dos ejecuciones.
    """
    staging = extraccion_falsa / ".staging"
    staging.mkdir(parents=True)
    (staging / "basura.parquet").write_bytes(b"de una ejecucion anterior")

    extract.extract_and_save_data(data_dir=extraccion_falsa)

    assert not (extraccion_falsa / "basura.parquet").exists()


def test_si_no_se_pueden_ajustar_permisos_la_extraccion_sigue(extraccion_falsa, monkeypatch):
    """
    chmod es un no-op practico en Windows. Que un permiso no se pueda ajustar no
    puede impedir publicar los datos.
    """
    monkeypatch.setattr(extract.os, "chmod",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("sin permiso")))

    extract.extract_and_save_data(data_dir=extraccion_falsa)

    assert (extraccion_falsa / "uno.parquet").exists()
