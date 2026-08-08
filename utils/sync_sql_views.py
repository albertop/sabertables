"""
Crea o actualiza en SQL Server las vistas de las que depende el pipeline.

Antes de este script, trece de las catorce vistas existian unicamente dentro de
la instancia de SQL Server: no habia forma de reconstruir el pipeline en otra
maquina, y la logica sabermetrica que vive dentro de ellas -- que cuenta como
intento de toque por hit, como se agrupan los pases de TTO, que define un
enfrentamiento en platoon -- no era revisable ni tenia historial.

Las definiciones estan en utils/sql/, con prefijo numerico que marca el ORDEN DE
CREACION: hay vistas que se apoyan en otras, por ejemplo

    v_re_analysis_tto_impact_final_same_platoon_no_outs
        -> v_re_analysis_tto_agg_same_platoon_no_outs
            -> v_re_analysis_tto_base_same_platoon_no_outs
                -> v_retroAllEventsRegAndPost

Ese orden se resolvio con sys.sql_expression_dependencies, no a ojo.

Uso:

    python utils/sync_sql_views.py --check     # compara ficheros vs base, no modifica nada
    python utils/sync_sql_views.py --apply     # crea o actualiza las vistas
    python utils/sync_sql_views.py --dump      # regenera los ficheros desde la base

--check es el modo por defecto a proposito: aplicar DDL modifica la base de
datos y no deberia ocurrir por accidente.
"""
import re
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli
import db

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

log = cli.get_logger()

SQL_DIR = Path(__file__).resolve().parent / "sql"

# No anclado al inicio: alguna definicion lleva un comentario delante.
# \s+ cubre 'CREATE   VIEW' con espacios multiples, que es como lo guarda
# SQL Server en al menos una de las vistas.
CREATE_RE = re.compile(r"CREATE\s+(?:OR\s+ALTER\s+)?VIEW\b", re.IGNORECASE)


def _view_files() -> list:
    """Ficheros .sql en orden de creacion (el prefijo numerico lo garantiza)."""
    files = sorted(SQL_DIR.glob("*.sql"))
    if not files:
        raise SystemExit(f"No hay ficheros .sql en {SQL_DIR}")
    return files


def _view_name(path: Path) -> str:
    """'07_v_re_analysis_platoon_impact_final.sql' -> 'v_re_analysis_platoon_impact_final'."""
    return path.stem.split("_", 1)[1]


def _normalize(sql: str) -> str:
    """
    Normaliza para comparar fichero contra base.

    La base guarda la definicion tal cual se envio, asi que hay que neutralizar
    lo unico que este script cambia a proposito (CREATE VIEW -> CREATE OR ALTER
    VIEW) mas los finales de linea y el espacio en blanco de los extremos.
    """
    sql = CREATE_RE.sub("CREATE VIEW", sql, count=1)
    sql = sql.replace("\r\n", "\n").strip()
    return sql


def _live_definition(conn, name: str):
    row = conn.execute(
        text("SELECT OBJECT_DEFINITION(OBJECT_ID('dbo.' + :n))"), {"n": name}
    ).fetchone()
    return row[0] if row else None


def check(conn) -> int:
    """Compara cada fichero con la definicion viva. No modifica nada."""
    cli.section(log, "COMPROBANDO VISTAS (sin modificar la base)")

    missing, drifted, equal = [], [], []
    for path in _view_files():
        name = _view_name(path)
        live = _live_definition(conn, name)
        if live is None:
            missing.append(name)
            log.warning(f"  AUSENTE  {name}  (no existe en la base)")
            continue
        if _normalize(live) == _normalize(path.read_text(encoding="utf-8")):
            equal.append(name)
            log.info(f"  igual    {name}")
        else:
            drifted.append(name)
            log.warning(f"  DERIVA   {name}  (el fichero y la base difieren)")

    log.info("")
    log.info(f"  iguales: {len(equal)}   con deriva: {len(drifted)}   ausentes: {len(missing)}")

    if drifted:
        log.warning("")
        log.warning("  Hay deriva. Si la version buena es la de la base, ejecuta --dump;")
        log.warning("  si la buena es la del repositorio, ejecuta --apply.")
    return 1 if (drifted or missing) else 0


def apply(conn) -> int:
    """Crea o actualiza las vistas, en orden de dependencias."""
    cli.section(log, "APLICANDO VISTAS")

    for path in _view_files():
        name = _view_name(path)
        sql = path.read_text(encoding="utf-8")
        existed = _live_definition(conn, name) is not None
        # exec_driver_sql y no text(): text() interpreta ':nombre' como parametro
        # de vinculo, y una definicion SQL puede contener dos puntos en un
        # literal o en un formato de hora. Hoy ninguna de las catorce los tiene,
        # pero aqui se ejecuta DDL arbitrario y conviene que eso no importe.
        conn.exec_driver_sql(sql)
        log.info(f"  {'actualizada' if existed else 'creada     '}  {name}")

    conn.commit()
    log.info("")
    log.info(f"  {len(_view_files())} vistas aplicadas")
    return 0


def dump(conn) -> int:
    """Regenera los ficheros desde la base, conservando el orden ya establecido."""
    cli.section(log, "VOLCANDO VISTAS DESDE LA BASE")

    for path in _view_files():
        name = _view_name(path)
        live = _live_definition(conn, name)
        if live is None:
            log.warning(f"  AUSENTE  {name}, se omite")
            continue

        hits = CREATE_RE.findall(live)
        if len(hits) != 1:
            log.error(f"  {name}: se esperaba 1 sentencia CREATE VIEW, hay {len(hits)}; se omite")
            continue

        body = CREATE_RE.sub("CREATE OR ALTER VIEW", live, count=1)
        path.write_text(body.replace("\r\n", "\n").rstrip() + "\n", encoding="utf-8")
        log.info(f"  {path.name}")
    return 0


def main(argv=None):
    parser = cli.base_parser(
        "Crea o actualiza las vistas de SQL Server de las que depende el pipeline."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="comparar ficheros contra la base sin modificar nada (por defecto)")
    mode.add_argument("--apply", action="store_true",
                      help="crear o actualizar las vistas en la base")
    mode.add_argument("--dump", action="store_true",
                      help="regenerar los ficheros .sql desde la base")
    args = parser.parse_args(argv)

    cli.setup_logging(verbose=args.verbose, quiet=args.quiet, log_file=args.log_file)

    engine = db.get_engine()
    try:
        with engine.connect() as conn:
            if args.apply:
                return apply(conn)
            if args.dump:
                return dump(conn)
            return check(conn)
    finally:
        db.dispose_engine()


if __name__ == "__main__":
    raise SystemExit(main())
