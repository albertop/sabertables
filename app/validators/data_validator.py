"""
Data validation functions for Run Expectancy Tables.
"""
import re

import pandas as pd
from loguru import logger


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
    required_columns: list[str]
) -> None:
    """
    Validate that DataFrame has required columns and proper dtypes.

    Args:
        df: DataFrame to validate
        data_name: Name of dataset for error messages
        required_columns: List of required column names

    Raises:
        ValueError: If validation fails
    """
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
