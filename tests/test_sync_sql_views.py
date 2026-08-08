"""
Sincronizacion del DDL de las vistas entre el repositorio y SQL Server.

Con una conexion falsa: lo que se prueba es la comparacion, que es lo que decide
si el pipeline arranca contra unas vistas que no son las del repositorio.
"""
import logging

import cli
import pytest
import sync_sql_views as sv


@pytest.fixture(autouse=True)
def _log():
    cli.setup_logging(quiet=True)


@pytest.fixture
def registro(caplog):
    """
    Captura lo que escribe el logger del pipeline.

    caplog engancha en el logger raiz, y el del pipeline lleva propagate=False
    --para no duplicar la salida en consola--, asi que por defecto no ve nada y
    cualquier asercion sobre el log pasaria en vacio.
    """
    log = cli.get_logger()
    previo = log.propagate
    log.propagate = True
    with caplog.at_level(logging.INFO, logger=log.name):
        yield caplog
    log.propagate = previo


class _ConexionFalsa:
    """
    Imita lo justo de una conexion de SQLAlchemy: devolver la definicion viva de
    una vista y aceptar DDL.
    """

    def __init__(self, definiciones=None):
        self.definiciones = definiciones or {}
        self.ejecutado = []
        self.commits = 0

    def execute(self, _stmt, params=None):
        return _Fila(self.definiciones.get((params or {}).get("n")))

    def exec_driver_sql(self, sql):
        self.ejecutado.append(sql)

    def commit(self):
        self.commits += 1


class _Fila:
    def __init__(self, valor):
        self.valor = valor

    def fetchone(self):
        return None if self.valor is None else (self.valor,)


@pytest.fixture
def sql_dir(tmp_path, monkeypatch):
    """Un directorio de vistas de mentira, para no depender de las 17 reales."""
    monkeypatch.setattr(sv, "SQL_DIR", tmp_path)
    return tmp_path


def _vista(directorio, prefijo, nombre, cuerpo="SELECT 1 AS x"):
    f = directorio / f"{prefijo}_{nombre}.sql"
    f.write_text(f"CREATE OR ALTER VIEW [dbo].[{nombre}]\nAS\n{cuerpo}\n", encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# Los ayudantes
# ---------------------------------------------------------------------------

def test_el_nombre_de_la_vista_sale_del_fichero_sin_el_prefijo(tmp_path):
    ruta = tmp_path / "07_v_re_analysis_platoon_impact_final.sql"

    assert sv._view_name(ruta) == "v_re_analysis_platoon_impact_final"


def test_los_ficheros_se_leen_en_orden_de_dependencias(sql_dir):
    """
    El prefijo numerico ES el orden de creacion: una vista que se apoya en otra
    tiene que aplicarse despues. Sin orden, apply falla en la primera.
    """
    for p in ("14", "02", "09"):
        _vista(sql_dir, p, f"v_{p}")

    assert [f.name[:2] for f in sv._view_files()] == ["02", "09", "14"]


def test_un_directorio_sin_vistas_para_el_script(sql_dir):
    """Seguir con cero vistas daria un "todo correcto" que no significa nada."""
    with pytest.raises(SystemExit, match="No hay ficheros"):
        sv._view_files()


def test_la_comparacion_ignora_create_or_alter():
    """
    El repositorio escribe CREATE OR ALTER VIEW; la base guarda lo que se le
    envio. Sin neutralizarlo, las 17 vistas saldrian con deriva siempre.
    """
    a = "CREATE OR ALTER VIEW [dbo].[v] AS SELECT 1"
    b = "CREATE VIEW [dbo].[v] AS SELECT 1"

    assert sv._normalize(a) == sv._normalize(b)


def test_la_comparacion_ignora_los_finales_de_linea_y_los_bordes():
    """Windows contra Linux no es deriva."""
    a = "  CREATE VIEW [dbo].[v]\r\nAS SELECT 1  \n"
    b = "CREATE VIEW [dbo].[v]\nAS SELECT 1"

    assert sv._normalize(a) == sv._normalize(b)


def test_la_comparacion_si_ve_un_cambio_real():
    a = "CREATE VIEW [dbo].[v] AS SELECT 1"
    b = "CREATE VIEW [dbo].[v] AS SELECT 2"

    assert sv._normalize(a) != sv._normalize(b)


# ---------------------------------------------------------------------------
# --check
# ---------------------------------------------------------------------------

def test_check_con_todo_igual_devuelve_cero(sql_dir):
    f = _vista(sql_dir, "01", "v_uno")
    conn = _ConexionFalsa({"v_uno": f.read_text(encoding="utf-8")})

    assert sv.check(conn) == 0


def test_check_detecta_una_vista_que_no_existe_en_la_base(sql_dir):
    _vista(sql_dir, "01", "v_uno")

    assert sv.check(_ConexionFalsa({})) == 1


def test_check_detecta_deriva(sql_dir):
    """
    Es el caso que importa: el fichero dice una cosa y la base hace otra, asi
    que el pipeline calcula sobre una vista que nadie ha revisado.
    """
    _vista(sql_dir, "01", "v_uno", cuerpo="SELECT 1 AS x")
    conn = _ConexionFalsa({"v_uno": "CREATE VIEW [dbo].[v_uno] AS SELECT 999 AS x"})

    assert sv.check(conn) == 1


def test_check_no_toca_la_base(sql_dir):
    _vista(sql_dir, "01", "v_uno")
    conn = _ConexionFalsa({})

    sv.check(conn)

    assert conn.ejecutado == []
    assert conn.commits == 0


# ---------------------------------------------------------------------------
# --apply
# ---------------------------------------------------------------------------

def test_apply_ejecuta_las_vistas_en_orden_y_confirma(sql_dir):
    _vista(sql_dir, "02", "v_dos")
    _vista(sql_dir, "01", "v_uno")
    conn = _ConexionFalsa({})

    assert sv.apply(conn) == 0
    assert [s.split("[dbo].[")[1].split("]")[0] for s in conn.ejecutado] == ["v_uno", "v_dos"]
    assert conn.commits == 1


def test_apply_distingue_crear_de_actualizar(sql_dir, registro):
    """El log tiene que decir cual se creo y cual ya estaba."""
    _vista(sql_dir, "01", "v_nueva")
    _vista(sql_dir, "02", "v_vieja")
    conn = _ConexionFalsa({"v_vieja": "CREATE VIEW [dbo].[v_vieja] AS SELECT 1"})

    sv.apply(conn)

    assert "creada" in registro.text
    assert "actualizada" in registro.text


def test_apply_usa_exec_driver_sql_y_no_text(sql_dir):
    """
    text() interpreta los dos puntos seguidos de un nombre como parametro de
    vinculo, y una definicion SQL puede llevarlos en un literal o en un formato
    de hora. Aqui se ejecuta DDL arbitrario, asi que eso no debe importar.
    """
    _vista(sql_dir, "01", "v_uno", cuerpo="SELECT 'hora_12:30' AS h")
    conn = _ConexionFalsa({})

    sv.apply(conn)

    assert "12:30" in conn.ejecutado[0], "los dos puntos deben llegar intactos"


# ---------------------------------------------------------------------------
# --dump
# ---------------------------------------------------------------------------

def test_dump_reescribe_el_fichero_desde_la_base(sql_dir):
    f = _vista(sql_dir, "01", "v_uno", cuerpo="SELECT 1")
    conn = _ConexionFalsa({"v_uno": "CREATE VIEW [dbo].[v_uno]\nAS\nSELECT 42"})

    assert sv.dump(conn) == 0

    escrito = f.read_text(encoding="utf-8")
    assert "SELECT 42" in escrito
    assert escrito.startswith("CREATE OR ALTER VIEW"), "el repositorio usa OR ALTER"
    assert escrito.endswith("\n")
    assert "\r" not in escrito


def test_dump_omite_lo_que_no_esta_en_la_base(sql_dir):
    f = _vista(sql_dir, "01", "v_uno", cuerpo="SELECT original")

    sv.dump(_ConexionFalsa({}))

    assert "SELECT original" in f.read_text(encoding="utf-8"), "no debe vaciar el fichero"


def test_dump_no_toca_una_definicion_con_varios_create_view(sql_dir):
    """
    Con dos sentencias no se sabe cual es la buena. Reescribir a ciegas
    destruiria el fichero del repositorio, que es la unica copia del DDL.
    """
    f = _vista(sql_dir, "01", "v_uno", cuerpo="SELECT original")
    doble = "CREATE VIEW [dbo].[a] AS SELECT 1\nGO\nCREATE VIEW [dbo].[b] AS SELECT 2"

    sv.dump(_ConexionFalsa({"v_uno": doble}))

    assert "SELECT original" in f.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# La linea de comandos
# ---------------------------------------------------------------------------

@pytest.fixture
def sin_base(monkeypatch):
    """Sustituye la conexion real; devuelve la conexion falsa que se uso."""
    conn = _ConexionFalsa({})

    class _Engine:
        def connect(self):
            class _Ctx:
                def __enter__(self_):
                    return conn

                def __exit__(self_, *a):
                    return False

            return _Ctx()

    monkeypatch.setattr(sv.db, "get_engine", lambda *a, **k: _Engine())
    monkeypatch.setattr(sv.db, "dispose_engine", lambda: None)
    return conn


def test_sin_argumentos_comprueba_y_no_modifica(sql_dir, sin_base):
    _vista(sql_dir, "01", "v_uno")

    sv.main([])

    assert sin_base.ejecutado == [], "el modo por defecto NO debe tocar la base"


def test_apply_por_linea_de_comandos(sql_dir, sin_base):
    _vista(sql_dir, "01", "v_uno")

    assert sv.main(["--apply"]) == 0
    assert len(sin_base.ejecutado) == 1


def test_dump_por_linea_de_comandos(sql_dir, sin_base):
    _vista(sql_dir, "01", "v_uno")

    assert sv.main(["--dump"]) == 0


def test_los_tres_modos_son_excluyentes(sql_dir, sin_base):
    """Pedir --apply y --dump a la vez es ambiguo; mejor error que elegir uno."""
    with pytest.raises(SystemExit):
        sv.main(["--apply", "--dump"])


def test_la_conexion_se_cierra_aunque_falle(sql_dir, monkeypatch):
    cerrado = []

    class _Engine:
        def connect(self):
            class _Ctx:
                def __enter__(self_):
                    raise RuntimeError("boom")

                def __exit__(self_, *a):
                    return False

            return _Ctx()

    monkeypatch.setattr(sv.db, "get_engine", lambda *a, **k: _Engine())
    monkeypatch.setattr(sv.db, "dispose_engine", lambda: cerrado.append(1))

    with pytest.raises(RuntimeError):
        sv.main(["--check"])

    assert cerrado, "el pool quedaria abierto tras un fallo"
