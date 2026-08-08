"""
Logging y argumentos comunes a los scripts de utils/.

Antes todo salia por print(), sin marca de tiempo y sin forma de bajar el
volumen. En una extraccion que tarda minutos eso significa no poder saber
cuanto tardo cada paso, ni guardar un registro sin redirigir la consola a mano.

Se usa el logging de la biblioteca estandar y no loguru: estos scripts se
ejecutan fuera del contenedor y conviene que arrastren las menos dependencias
posibles.
"""
import argparse
import logging
import sys
from pathlib import Path

LOGGER_NAME = "re_pipeline"


def setup_logging(verbose: bool = False, quiet: bool = False, log_file=None) -> logging.Logger:
    """
    Configura el logger raiz del pipeline.

    verbose -> DEBUG, quiet -> WARNING, por defecto INFO.
    Si se pasa log_file se escribe ademas a disco, siempre en UTF-8 (la consola
    de Windows usa cp1252 y los simbolos de estado reventaban al redirigir).
    """
    level = logging.DEBUG if verbose else (logging.WARNING if quiet else logging.INFO)

    handlers = []

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
    handlers.append(stream)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, mode="w", encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s")
        )
        handlers.append(file_handler)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.handlers.clear()
    for handler in handlers:
        handler.setLevel(level)
        logger.addHandler(handler)
    logger.propagate = False

    return logger


def get_logger() -> logging.Logger:
    """Logger del pipeline. Si nadie lo configuro aun, se comporta como INFO a stdout."""
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        return setup_logging()
    return logger


def base_parser(description: str) -> argparse.ArgumentParser:
    """Parser con las opciones que comparten todos los scripts."""
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="salida DEBUG")
    parser.add_argument("-q", "--quiet", action="store_true", help="solo avisos y errores")
    parser.add_argument("--log-file", metavar="RUTA", help="ademas de la consola, escribir a este fichero")

    # Las tres fechas que acotan toda la extraccion. Van en el parser COMPARTIDO
    # para que ningun script pueda quedarse fuera del rango por olvido: si manana
    # se anade un script nuevo con base_parser(), ya las tiene.
    # Valores por defecto y explicacion en utils/seasons.py.
    # Los valores por defecto que se muestran salen de seasons.py y no escritos
    # aqui: una ayuda que miente sobre el valor vigente es peor que no tenerla.
    import seasons

    grupo = parser.add_argument_group("rango de temporadas")
    grupo.add_argument("--min-season", type=int, metavar="ANIO",
                       help=f"suelo general (ahora {seasons.MIN_SEASON})")
    grupo.add_argument("--min-per-pitch-season", type=int, metavar="ANIO",
                       help="suelo de lo que necesita detalle de lanzamiento "
                            f"(ahora {seasons.MIN_PER_PITCH_SEASON})")
    grupo.add_argument("--max-season", type=int, metavar="ANIO",
                       help=f"techo, comun a todo (ahora {seasons.MAX_SEASON})")
    return parser


def aplicar_temporadas(args) -> None:
    """
    Traslada --min-season / --min-per-pitch-season / --max-season al modulo
    seasons, y valida el rango resultante.

    Se llama al principio de cada script, ANTES de tocar la base. Un rango
    invertido no da error por si solo: da parquets vacios, y un parquet vacio
    deja la tabla en blanco en la web sin que nada falle.
    """
    import seasons

    for atributo, constante in (("min_season", "MIN_SEASON"),
                                ("min_per_pitch_season", "MIN_PER_PITCH_SEASON"),
                                ("max_season", "MAX_SEASON")):
        valor = getattr(args, atributo, None)
        if valor is not None:
            setattr(seasons, constante, int(valor))

    seasons.validar()
    get_logger().info(
        f"Rango de temporadas: {seasons.MIN_SEASON}-{seasons.MAX_SEASON} "
        f"(desde {seasons.MIN_PER_PITCH_SEASON} para lo que necesita conteo)"
    )


def section(logger: logging.Logger, title: str) -> None:
    """Cabecera de seccion, para que el registro siga siendo legible de un vistazo."""
    logger.info("")
    logger.info("=" * 60)
    logger.info(title)
    logger.info("=" * 60)
