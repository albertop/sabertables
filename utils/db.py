"""
Conexion compartida a SQL Server para los scripts de utils/.

Antes habia dos implementaciones incompatibles: 01 y 02 probaban
['localhost', '(local)', '.', 'tcp:localhost,1433'] mientras que 03 probaba
['localhost\\SQLEXPRESS', 'localhost', 'DESKTOP-JPFCQI5\\SQLEXPRESS',
'192.168.1.102\\SQLEXPRESS'], con el nombre de una maquina concreta y una IP
privada escritos en el codigo. Ninguna leia el .env que ya existe en el
proyecto, asi que cambiar de servidor obligaba a editar dos ficheros.

Configuracion (todo opcional; los valores por defecto cubren una instancia
local). Se lee del entorno o del .env en la raiz del proyecto:

    DGL_SQL_SERVERS    Lista separada por comas de servidores a probar en orden.
                       Ej: DGL_SQL_SERVERS=localhost\\SQLEXPRESS,192.168.1.102
    DGL_SQL_DATABASE   Base de datos (por defecto: DGL)
    DGL_SQL_DRIVER     Driver ODBC (por defecto: ODBC Driver 18 for SQL Server)
    DGL_SQL_TIMEOUT    Timeout de conexion en segundos (por defecto: 5)
    DGL_SQL_USER       Usuario. Si se omite se usa autenticacion de Windows.
    DGL_SQL_PASSWORD   Contrasena, solo si se define DGL_SQL_USER.

El engine se cachea: create_break_even_points.py consultaba seis vistas y
abria una conexion nueva para cada una, con hasta cuatro intentos cada vez.
"""
import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:  # python-dotenv es opcional: sin el se usan las variables del entorno.
    from dotenv import load_dotenv

    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:  # pragma: no cover
    pass


# Instancia local por defecto, con y sin nombre de instancia. Cualquier otro
# servidor se configura con DGL_SQL_SERVERS en vez de tocar el codigo.
DEFAULT_SERVERS = [
    "localhost",
    "localhost\\SQLEXPRESS",
    "(local)",
    ".",
    "tcp:localhost,1433",
]

_engine: Engine | None = None
_connected_server: str | None = None


def _configured_servers() -> list[str]:
    raw = os.getenv("DGL_SQL_SERVERS", "").strip()
    if not raw:
        return list(DEFAULT_SERVERS)
    return [s.strip() for s in raw.split(",") if s.strip()]


def _build_url(server: str) -> URL:
    """
    Construye la URL usando odbc_connect, es decir pasando la cadena ODBC
    literal.

    NO usar URL.create(query={"server": ...}): SQLAlchemy genera el 'Server='
    de la cadena ODBC a partir del host de la URL, que en esa forma va vacio, y
    despues anade el query param como un segundo 'server=' duplicado:

        DRIVER={...};Server=;Database=DGL;...;server=loQueSea;...

    ODBC se queda con la PRIMERA aparicion, o sea con 'Server=' vacio, que
    significa "instancia local por defecto". El resultado es que el nombre de
    servidor se ignora en silencio y cualquier valor -- incluso uno inexistente --
    parece conectar correctamente. Ese era el comportamiento de los tres scripts
    originales: sus listas de servidores no tenian ningun efecto.
    """
    driver = os.getenv("DGL_SQL_DRIVER", "ODBC Driver 18 for SQL Server")
    database = os.getenv("DGL_SQL_DATABASE", "DGL")

    # TrustServerCertificate: configurable, no fijo.
    #
    # Estaba escrito a "yes" siempre, o sea que el cliente aceptaba CUALQUIER
    # certificado sin validarlo. Contra localhost --que es como corre hoy, y con
    # autenticacion de Windows, sin contrasena en la cadena-- eso no es una
    # amenaza real. El problema es que si algun dia DGL_SQL_SERVER apunta a un
    # host remoto, la validacion TLS deja de hacerse SIN QUE NADIE SE ENTERE:
    # no hay aviso, no hay error, la conexion simplemente funciona.
    #
    # Por defecto sigue en "yes" para no romper la instalacion actual --el SQL
    # Server local usa un certificado autofirmado--, pero ahora se puede apagar
    # con DGL_SQL_TRUST_CERT=no, y el modo efectivo se registra en el log.
    confiar = os.getenv("DGL_SQL_TRUST_CERT", "yes").strip().lower() not in {"no", "false", "0"}

    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={server}",
        f"DATABASE={database}",
        f"TrustServerCertificate={'yes' if confiar else 'no'}",
    ]

    user = os.getenv("DGL_SQL_USER", "").strip()
    if user:
        # Autenticacion SQL. La contrasena va en la cadena ODBC y nunca se imprime.
        parts.append(f"UID={user}")
        parts.append(f"PWD={os.getenv('DGL_SQL_PASSWORD', '')}")
    else:
        parts.append("Trusted_Connection=yes")

    return URL.create("mssql+pyodbc", query={"odbc_connect": ";".join(parts)})


def get_engine(verbose: bool = True):
    """
    Devuelve un engine de SQLAlchemy, reutilizando el ya abierto si lo hay.

    Prueba los servidores configurados en orden y se queda con el primero que
    responda. Lanza la ultima excepcion si ninguno responde.
    """
    global _engine, _connected_server
    if _engine is not None:
        return _engine

    servers = _configured_servers()
    timeout = int(os.getenv("DGL_SQL_TIMEOUT", "5"))

    if verbose:
        print("Conectando a SQL Server...")

    last_error: Exception | None = None
    for idx, server in enumerate(servers, 1):
        try:
            if verbose:
                # El modo TLS se dice en voz alta. Un "sin validar" que no se
                # ve es justo como esto llego a estar fijo sin que nadie lo
                # notara durante anios.
                modo_tls = ("certificado sin validar"
                            if os.getenv("DGL_SQL_TRUST_CERT", "yes").strip().lower()
                            not in {"no", "false", "0"} else "TLS validado")
                print(f"  Intento {idx}/{len(servers)} ({server}, {modo_tls})...", end=" ")

            engine = create_engine(_build_url(server), connect_args={"timeout": timeout})
            with engine.connect() as conn:
                conn.execute(text("SELECT 1")).fetchone()

            if verbose:
                print("OK")
                print(f"Conectado a {server} (base {os.getenv('DGL_SQL_DATABASE', 'DGL')})")

            _engine, _connected_server = engine, server
            return _engine

        except Exception as exc:
            if verbose:
                print("fallo")
            last_error = exc
            continue

    print(f"\nNo se pudo conectar a ninguno de: {', '.join(servers)}")
    print("Configura DGL_SQL_SERVERS en el .env si tu instancia no es local.")
    print(f"Ultimo error: {last_error}")
    if last_error is None:
        # Pasa si la lista de servidores queda vacia: el bucle no llega a
        # ejecutarse y last_error sigue a None. `raise None` da un TypeError
        # criptico en vez de decir lo que ocurre.
        raise RuntimeError(
            "No hay ningun servidor configurado. Revisa DGL_SQL_SERVERS en el .env."
        )
    raise last_error


def connected_server() -> str | None:
    """Nombre del servidor al que se conecto, o None si aun no hay conexion."""
    return _connected_server


def dispose_engine() -> None:
    """Cierra el engine cacheado. Idempotente."""
    global _engine, _connected_server
    if _engine is not None:
        _engine.dispose()
        _engine = None
        _connected_server = None
