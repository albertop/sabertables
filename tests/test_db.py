"""
Construccion de la conexion a SQL Server.

No abre ninguna conexion: comprueba la cadena que se construye, que es donde
estuvo el fallo que hizo que el nombre de servidor se ignorara en silencio
durante meses.
"""

import db
import pytest


@pytest.fixture(autouse=True)
def _entorno_limpio(monkeypatch):
    for k in ("DGL_SQL_SERVERS", "DGL_SQL_SERVER", "DGL_SQL_DRIVER", "DGL_SQL_DATABASE",
              "DGL_SQL_TRUST_CERT", "DGL_SQL_USER", "DGL_SQL_PASSWORD", "DGL_SQL_TIMEOUT"):
        monkeypatch.delenv(k, raising=False)


def _odbc(url):
    """Saca la cadena ODBC literal de la URL de SQLAlchemy."""
    # unquote_plus y no unquote: en la query de una URL el espacio va como '+',
    # asi que un driver con espacios --"ODBC Driver 18 for SQL Server"-- sale
    # como "ODBC+Driver+18..." y la comparacion falla por donde no es.
    from urllib.parse import unquote_plus
    return unquote_plus(str(url).split("odbc_connect=", 1)[1])


# ---------------------------------------------------------------------------
# La lista de servidores
# ---------------------------------------------------------------------------

def test_sin_configurar_se_usa_la_lista_por_defecto():
    assert db._configured_servers() == list(db.DEFAULT_SERVERS)


def test_se_pueden_dar_varios_servidores_separados_por_coma(monkeypatch):
    """Se prueban en orden: el primero que responda gana."""
    monkeypatch.setenv("DGL_SQL_SERVERS", "uno,dos,tres")

    assert db._configured_servers() == ["uno", "dos", "tres"]


def test_los_espacios_y_los_huecos_no_generan_servidores_fantasma(monkeypatch):
    """Una coma de mas produciria un intento contra la cadena vacia."""
    monkeypatch.setenv("DGL_SQL_SERVERS", " uno , , dos ,")

    assert db._configured_servers() == ["uno", "dos"]


def test_una_lista_en_blanco_cae_a_la_de_por_defecto(monkeypatch):
    monkeypatch.setenv("DGL_SQL_SERVERS", "   ")

    assert db._configured_servers() == list(db.DEFAULT_SERVERS)


# ---------------------------------------------------------------------------
# La cadena ODBC
# ---------------------------------------------------------------------------

def test_el_servidor_pedido_llega_a_la_cadena_una_sola_vez():
    """
    EL FALLO HISTORICO. Con URL.create(query={"server": ...}) la cadena salia
    con dos 'Server=': el que genera SQLAlchemy desde el host de la URL --vacio--
    y el del query param. ODBC se queda con el PRIMERO, o sea con el vacio, que
    significa "instancia local por defecto". Resultado: el nombre de servidor se
    ignoraba y hasta un host inexistente parecia conectar.
    """
    odbc = _odbc(db._build_url("miservidor"))

    assert odbc.upper().count("SERVER=") == 1, odbc
    assert "SERVER=miservidor" in odbc


def test_la_base_y_el_driver_salen_del_entorno(monkeypatch):
    monkeypatch.setenv("DGL_SQL_DATABASE", "OTRA")
    monkeypatch.setenv("DGL_SQL_DRIVER", "Driver Raro 99")

    odbc = _odbc(db._build_url("s"))

    assert "DATABASE=OTRA" in odbc
    assert "DRIVER={Driver Raro 99}" in odbc


def test_por_defecto_se_confia_en_el_certificado():
    """
    El SQL Server local usa un certificado autofirmado, asi que el defecto tiene
    que seguir funcionando sin configurar nada.
    """
    assert "TrustServerCertificate=yes" in _odbc(db._build_url("s"))


@pytest.mark.parametrize("valor", ["no", "NO", "false", "0"])
def test_la_validacion_del_certificado_se_puede_exigir(monkeypatch, valor):
    """
    Importa el dia que el servidor deje de ser localhost: con "yes" fijo, la
    validacion TLS deja de hacerse SIN QUE NADIE SE ENTERE -- no hay aviso ni
    error, la conexion simplemente funciona.
    """
    monkeypatch.setenv("DGL_SQL_TRUST_CERT", valor)

    assert "TrustServerCertificate=no" in _odbc(db._build_url("s"))


def test_sin_usuario_se_usa_autenticacion_de_windows():
    odbc = _odbc(db._build_url("s"))

    assert "Trusted_Connection=yes" in odbc
    assert "UID=" not in odbc
    assert "PWD=" not in odbc


def test_con_usuario_se_manda_usuario_y_contrasena(monkeypatch):
    monkeypatch.setenv("DGL_SQL_USER", "sa")
    monkeypatch.setenv("DGL_SQL_PASSWORD", "secreta")

    odbc = _odbc(db._build_url("s"))

    assert "UID=sa" in odbc
    assert "PWD=secreta" in odbc
    assert "Trusted_Connection" not in odbc, "no se mezclan los dos modos"


def test_un_usuario_solo_de_espacios_no_activa_el_modo_usuario(monkeypatch):
    """Un DGL_SQL_USER= vacio en el .env no debe romper la conexion integrada."""
    monkeypatch.setenv("DGL_SQL_USER", "   ")

    assert "Trusted_Connection=yes" in _odbc(db._build_url("s"))


# ---------------------------------------------------------------------------
# La apertura del engine
#
# Se sustituye create_engine: estos tests no abren ninguna conexion. Lo que se
# comprueba es la LOGICA de reintento, que es la que decide si el pipeline
# arranca o se queda mirando.
# ---------------------------------------------------------------------------

class _EngineFalso:
    def __init__(self, funciona=True):
        self.funciona = funciona
        self.dispuesto = False

    def connect(self):
        engine = self

        class _Conexion:
            def __enter__(self_):
                if not engine.funciona:
                    raise OSError("servidor no responde")
                return self_

            def __exit__(self_, *a):
                return False

            def execute(self_, *a, **k):
                class _R:
                    def fetchone(self_inner):
                        return (1,)
                return _R()

        return _Conexion()

    def dispose(self):
        self.dispuesto = True


@pytest.fixture(autouse=True)
def _engine_limpio():
    """El engine es global y se cachea; sin esto un test contamina al siguiente."""
    db._engine = None
    db._connected_server = None
    yield
    db._engine = None
    db._connected_server = None


def test_se_conecta_al_primer_servidor_que_responde(monkeypatch):
    intentados = []

    def falso(url, **k):
        server = _odbc(url).split("SERVER=")[1].split(";")[0]
        intentados.append(server)
        return _EngineFalso(funciona=(server == "bueno"))

    monkeypatch.setenv("DGL_SQL_SERVERS", "malo1,malo2,bueno,nosellega")
    monkeypatch.setattr(db, "create_engine", falso)

    db.get_engine(verbose=False)

    assert intentados == ["malo1", "malo2", "bueno"], "no debe seguir tras el primero bueno"
    assert db.connected_server() == "bueno"


def test_el_engine_se_reutiliza_y_no_se_reconecta(monkeypatch):
    """
    Sin la cache, cada script del pipeline abriria su propia conexion contra la
    misma base en la misma ejecucion.
    """
    llamadas = []
    monkeypatch.setattr(db, "create_engine", lambda url, **k: (llamadas.append(1), _EngineFalso())[1])

    primero = db.get_engine(verbose=False)
    segundo = db.get_engine(verbose=False)

    assert primero is segundo
    assert len(llamadas) == 1


def test_si_ninguno_responde_se_lanza_el_ultimo_error(monkeypatch):
    """
    Y se lanza el error de verdad, no uno generico: el mensaje del driver es lo
    unico que dice si es la red, el certificado o las credenciales.
    """
    monkeypatch.setenv("DGL_SQL_SERVERS", "a,b")
    monkeypatch.setattr(db, "create_engine", lambda url, **k: _EngineFalso(funciona=False))

    with pytest.raises(OSError, match="servidor no responde"):
        db.get_engine(verbose=False)


def test_una_lista_vacia_de_servidores_da_un_error_que_se_entiende(monkeypatch):
    """
    El bucle no llega a ejecutarse y last_error sigue a None. Sin la guarda,
    `raise None` da un TypeError criptico en vez de decir lo que pasa.
    """
    monkeypatch.setattr(db, "_configured_servers", list)

    with pytest.raises(RuntimeError, match="DGL_SQL_SERVERS"):
        db.get_engine(verbose=False)


def test_el_modo_verboso_dice_si_el_certificado_se_valida(monkeypatch, capsys):
    """
    Un "sin validar" que no se ve es justo como esto llego a estar fijo durante
    anos sin que nadie lo notara.
    """
    monkeypatch.setattr(db, "create_engine", lambda url, **k: _EngineFalso())

    monkeypatch.setenv("DGL_SQL_TRUST_CERT", "yes")
    db.get_engine(verbose=True)
    assert "certificado sin validar" in capsys.readouterr().out

    db._engine = None
    monkeypatch.setenv("DGL_SQL_TRUST_CERT", "no")
    db.get_engine(verbose=True)
    assert "TLS validado" in capsys.readouterr().out


def test_al_fallar_del_todo_se_explica_como_configurarlo(monkeypatch, capsys):
    monkeypatch.setenv("DGL_SQL_SERVERS", "unico")
    monkeypatch.setattr(db, "create_engine", lambda url, **k: _EngineFalso(funciona=False))

    with pytest.raises(OSError):
        db.get_engine(verbose=True)

    salida = capsys.readouterr().out
    assert "No se pudo conectar" in salida
    assert "DGL_SQL_SERVERS" in salida


def test_cerrar_el_engine_lo_libera_y_permite_reconectar(monkeypatch):
    creados = []
    monkeypatch.setattr(db, "create_engine", lambda url, **k: (creados.append(_EngineFalso()), creados[-1])[1])

    db.get_engine(verbose=False)
    db.dispose_engine()

    assert creados[0].dispuesto, "no se cerro el pool"
    assert db.connected_server() is None

    db.get_engine(verbose=False)
    assert len(creados) == 2, "tras cerrarlo deberia poder reconectar"


def test_cerrar_sin_haber_abierto_no_falla():
    db.dispose_engine()
