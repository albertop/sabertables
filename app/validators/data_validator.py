"""
Data validation functions for Run Expectancy Tables.
"""
import re

import pandas as pd


def sanitize_error_message(error_msg: str, is_production: bool = False) -> str:
    """
    Sanitize error messages to avoid exposing internal paths in production.

    Args:
        error_msg: Original error message
        is_production: Whether running in production mode

    Returns:
        Sanitized error message
    """
    if not is_production:
        return error_msg

    # Remove file paths (Windows and Unix style)
    sanitized = re.sub(r'[A-Za-z]:\\[^\s]+', '[PATH]', error_msg)
    sanitized = re.sub(r'/[^\s]+\.(py|parquet|json|jsonl)', '[FILE]', sanitized)
    sanitized = re.sub(r'\\[^\s]+\.(py|parquet|json|jsonl)', '[FILE]', sanitized)

    # Remove line numbers and function names from stack traces
    sanitized = re.sub(r'line \d+', 'line [REDACTED]', sanitized)
    sanitized = re.sub(r'File "[^"]+"', 'File "[REDACTED]"', sanitized)

    # Remove specific column names and technical details
    sanitized = re.sub(r'column[s]?: [^,.\s]+', 'columns: [REDACTED]', sanitized)

    return sanitized


# validate_season_range vivia aqui y no la llamaba nadie: app/main.py define y
# usa la suya. Tener dos copias hizo justo lo que se teme -- la regla del suelo
# de temporada de RE288 quedo duplicada, y las DOS con el mismo
# `== "RE288"` incompleto que dejaba pasar RE288_FREQ y RE288_TRANS.
# Se elimina la copia muerta; la viva esta en app/main.py y la lista de tipos
# en app/config.py (RE288_MATRIX_TYPES).


def validate_dataframe_schema(
    df: pd.DataFrame,
    data_name: str,
    required_columns: list[str],
    allow_empty: bool = False,
) -> None:
    """
    Comprueba que el DataFrame trae lo que la aplicacion necesita.

    Args:
        df: el DataFrame a validar
        data_name: nombre del dataset, para el mensaje de error
        required_columns: columnas que tienen que estar y sin nulos
        allow_empty: si cero filas es legitimo para este dataset

    Raises:
        ValueError: si falta una columna, hay nulos, o esta vacio sin permiso

    POR QUE `allow_empty` POR DEFECTO EN FALSO. Un parquet con el esquema
    correcto y CERO filas pasaba esta validacion. El fallo aparecia despues,
    lejos de la causa: tabla en blanco en la web, un 400 generico, o una
    excepcion en el pivote. Y la promocion del staging no lo caza, porque
    rechaza ficheros de cero BYTES y un parquet con esquema pesa mas que cero.
    """
    if df.empty and not allow_empty:
        raise ValueError(
            f"{data_name}: el dataset viene sin filas. Un esquema correcto y cero "
            f"filas publica una tabla en blanco sin dar ningun error; si eso es "
            f"legitimo aqui, pasa allow_empty=True."
        )

    missing_columns = set(required_columns) - set(df.columns)
    if missing_columns:
        raise ValueError(f"{data_name}: Missing required columns: {missing_columns}")

    # Validate no NaN in required columns
    null_counts = df[required_columns].isnull().sum()
    columns_with_nulls = null_counts[null_counts > 0]
    if not columns_with_nulls.empty:
        raise ValueError(f"{data_name}: Found NaN values in columns: {columns_with_nulls.to_dict()}")


# check_for_duplicates() y validate_weighted_mean_inputs() vivian aqui y no las
# llamaba nadie: cero usos fuera de este modulo. Mismo caso que
# validate_season_range. Se borran en vez de escribirles tests que solo servirian
# para subir un porcentaje.
