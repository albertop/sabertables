"""
Ejecuta el pipeline completo en orden.

Los scripts se llamaban 00_, 01_, 02_ y 03_ y el prefijo numerico codificaba el
orden. Eso los hacia imposibles de importar -- un nombre de modulo no puede
empezar por digito -- y por tanto imposibles de testear unitariamente: los tests
solo podian validar los parquets ya publicados, no las funciones que los
calculan. Al renombrarlos, el orden pasa a vivir aqui.

    python utils/run_all.py              # todo
    python utils/run_all.py --skip-views # sin tocar las vistas SQL

El orden importa:

    sync_sql_views             las 17 vistas de las que dependen 02 y 03
    extract_re_data            los 16 parquets base
    calculate_pythag_exponents lee SQL directamente, no depende de extract
    create_break_even_points   lee re24_values y re288_values de extract

sync_sql_views va en modo --check por defecto: aplicar DDL modifica la base y no
deberia ocurrir por accidente. Con --apply-views se aplica de verdad.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):  # pragma: no cover
        pass


def main(argv=None):
    parser = cli.base_parser("Ejecuta el pipeline de run expectancy en orden.")
    parser.add_argument("--skip-views", action="store_true",
                        help="no comprobar las vistas SQL")
    parser.add_argument("--apply-views", action="store_true",
                        help="aplicar el DDL de las vistas en vez de solo comprobarlo")
    args = parser.parse_args(argv)

    log = cli.setup_logging(verbose=args.verbose, quiet=args.quiet, log_file=args.log_file)

    # OJO: run_all llama a las funciones directamente (extract_and_save_data,
    # run, main), no a los _cli de cada script, asi que el enganche que ellos
    # tienen NO se dispara por esta via. Tiene que estar tambien aqui.
    # Funciona porque seasons es un modulo: los tres scripts ven el mismo objeto,
    # asi que fijarlo una vez vale para los tres.
    cli.aplicar_temporadas(args)

    import calculate_pythag_exponents
    import create_break_even_points
    import extract_re_data
    import sync_sql_views

    steps = []
    if not args.skip_views:
        mode = ["--apply"] if args.apply_views else ["--check"]
        steps.append(("vistas SQL", lambda: sync_sql_views.main(mode)))
    steps.append(("extraccion de datos", extract_re_data.extract_and_save_data))
    steps.append(("exponentes pitagoricos", calculate_pythag_exponents.run))
    steps.append(("break-even e impacto RE", create_break_even_points.main))

    started = time.monotonic()
    for name, fn in steps:
        cli.section(log, f"PASO: {name}")
        step_started = time.monotonic()
        result = fn()
        if isinstance(result, int) and result != 0:
            log.error(f"'{name}' devolvio codigo {result}; se aborta el pipeline")
            return result
        log.info(f"  {name}: {time.monotonic() - step_started:.1f}s")

    cli.section(log, "PIPELINE COMPLETO")
    log.info(f"  Tiempo total: {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
