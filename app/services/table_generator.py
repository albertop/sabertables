"""
Table generation logic for Run Expectancy matrices.
"""
from itertools import product

import numpy as np
import pandas as pd
from loguru import logger

from app.config import BASE_STATE_LABELS


def media_ponderada_por_grupo(
    df: pd.DataFrame,
    value_column: str,
    groupby_columns: list[str],
):
    """
    sum(valor * n) / sum(n) por grupo. La division ponderada, en UN solo sitio.

    Habia CUATRO copias de estas cuatro lineas --Win Expectancy, WPA, Leverage
    Index y las transiciones RE24-- identicas salvo en el nombre de la columna.
    Cuatro sitios donde arreglar un fallo exige acordarse de los otros tres, y
    donde un `.mean()` colado en uno solo pasa desapercibido: el promedio simple
    y el ponderado se parecen lo bastante como para no chirriar.

    `calculate_weighted_mean` conserva la suya y NO usa este ayudante: ademas de
    la division arrastra `bucket_sort_order` y `sort_order_count` por la
    agregacion, que deciden el orden de las filas en la tabla. Meterlas aqui
    obligaria a que todos los llamantes supieran de columnas que no les tocan.

    Verificado contra produccion, que corre el codigo anterior: Win Expectancy,
    WPA y Leverage Index de 2020-2025 salen fila por fila identicos.

    SIN anotacion de tipo de retorno a proposito. Declarando `-> pd.DataFrame`,
    mypy estrecha la inferencia aguas abajo y da por inalcanzables las ramas que
    distinguen columnas enteras de columnas de texto en los tres pivotes --las
    que producen las cabeceras -4+, Tie y +4+--. Son alcanzables: las columnas de
    pandas son dinamicas y ahi llevan enteros. Dejarlo sin anotar sale mas barato
    que tres `type: ignore`.

    Devuelve las columnas de agrupacion, el valor ya ponderado y `n` sumado.
    Copia la entrada: sin eso, la columna auxiliar se le queda pegada al
    DataFrame de quien llama, y alguno viene de la cache de datasets.
    """
    trabajo = df.copy()
    trabajo["_ponderado"] = trabajo[value_column] * trabajo["n"]

    agrupado = trabajo.groupby(groupby_columns, observed=False).agg(
        _ponderado=("_ponderado", "sum"),
        n=("n", "sum"),
    ).reset_index()

    agrupado[value_column] = agrupado["_ponderado"] / agrupado["n"]
    return agrupado.drop(columns=["_ponderado"])


def calculate_weighted_mean(
    df: pd.DataFrame,
    from_season: int,
    to_season: int,
    value_column: str,
    groupby_columns: list[str]
) -> pd.DataFrame:
    """
    Calculate weighted mean for single or multiple seasons.

    Args:
        df: DataFrame with data
        from_season: Starting season
        to_season: Ending season
        value_column: Name of the column to calculate weighted mean for
        groupby_columns: List of columns to group by

    Returns:
        DataFrame with either direct values (single season) or weighted means (multiple seasons)
    """
    if from_season == to_season:
        # Single season: use value directly
        result_columns = groupby_columns + [value_column]
        if "bucket_sort_order" in df.columns and "bucket_sort_order" not in groupby_columns:
            result_columns.append("bucket_sort_order")
        if "sort_order_count" in df.columns and "sort_order_count" not in groupby_columns:
            result_columns.append("sort_order_count")
        return df[result_columns].copy()
    else:
        # Multiple seasons: calculate weighted mean
        # Weighted mean: sum(value * n) / sum(n) grouped by specified columns
        #
        # La copia no es decorativa: sin ella esta funcion anade la columna
        # weighted_value al DataFrame de QUIEN LLAMA. Hoy no rompe nada porque
        # los siete llamantes reciben el suyo de filter_and_validate_data, que ya
        # devuelve .copy(), pero basta con que alguien pase un df de data_store
        # directamente para que la columna se quede pegada a la cache.
        df = df.copy()
        df["weighted_value"] = df[value_column] * df["n"]

        agg_dict = {
            "weighted_value": "sum",
            "n": "sum"
        }

        # Add sort order columns to aggregation (take first value since they should be consistent per bucket/count)
        if "bucket_sort_order" in df.columns:
            agg_dict["bucket_sort_order"] = "first"
        if "sort_order_count" in df.columns:
            agg_dict["sort_order_count"] = "first"

        grouped = df.groupby(groupby_columns, observed=False).agg(agg_dict).reset_index()
        grouped[value_column] = grouped["weighted_value"] / grouped["n"]

        # Return only needed columns
        result_columns = groupby_columns + [value_column]
        if "bucket_sort_order" in grouped.columns:
            result_columns.append("bucket_sort_order")
        if "sort_order_count" in grouped.columns:
            result_columns.append("sort_order_count")

        return grouped[result_columns]


def filter_and_validate_data(
    data_store: dict,
    data_key: str,
    from_season: int,
    to_season: int,
    required_columns: list[str],
    data_name: str
) -> pd.DataFrame:
    """
    Filter data by season range and validate.

    Args:
        data_store: Dictionary containing all data
        data_key: Key in data_store dictionary
        from_season: Starting season
        to_season: Ending season
        required_columns: List of required columns for dropna
        data_name: Name of dataset for logging

    Returns:
        Filtered and validated DataFrame

    Raises:
        ValueError: If no data found for season range
    """
    # Filter dataframe by season range (inclusive)
    df = data_store[data_key][
        (data_store[data_key]["season"] >= from_season) &
        (data_store[data_key]["season"] <= to_season)
    ]

    if len(df) == 0:
        raise ValueError(f"No data found for season range {from_season}-{to_season}")

    # Remove rows with NaN values in critical columns
    df = df.dropna(subset=required_columns).copy()

    return df


def generate_re24_table(
    data_store: dict,
    from_season: int,
    to_season: int,
    event_cd: int | None = None
) -> dict:
    """
    Generate RE24 table with weighted mean if multiple seasons.

    Args:
        data_store: Dictionary containing all data
        from_season: Starting season
        to_season: Ending season
        event_cd: Optional event code to filter by

    Returns:
        Dictionary with table data ready for JSON response
    """
    # Filter and validate data
    required_cols = ["runners", "outs", "run_value", "n"]
    if event_cd is not None:
        required_cols.append("event_cd")

    df = filter_and_validate_data(
        data_store,
        "re24",
        from_season,
        to_season,
        required_cols,
        "RE24"
    )

    # Filter by event_cd if provided
    # Note: event_cd=-1 represents "All Events" which is pre-calculated in the database
    # We should filter by it just like any other event_cd value
    if event_cd is not None:
        df = df[df["event_cd"] == event_cd].copy()
        if len(df) == 0:
            raise ValueError(f"No data found for event_cd={event_cd} in season range {from_season}-{to_season}")
        df = df.drop(columns=["event_cd"])
    else:
        # If no event_cd provided, default to "All Events" (event_cd=-1)
        df = df[df["event_cd"] == -1].copy()
        if len(df) == 0:
            raise ValueError(f"No data found for event_cd=-1 (All Events) in season range {from_season}-{to_season}")
        df = df.drop(columns=["event_cd"])

    # Calculate weighted mean for single or multiple seasons
    pivot_df = calculate_weighted_mean(
        df,
        from_season,
        to_season,
        "run_value",
        ["runners", "outs"]
    )

    # Aggregate sample sizes for reliability markers
    pivot_n_df = df.groupby(["runners", "outs"], observed=False)["n"].sum().reset_index()

    # Pivot wider: columns = outs (0, 1, 2), values = run_value
    pivot_table = pivot_df.pivot(index="runners", columns="outs", values="run_value")
    pivot_n_table = pivot_n_df.pivot(index="runners", columns="outs", values="n")

    # Define correct order for base states
    runners_order = ["000", "100", "010", "001", "110", "101", "011", "111"]

    # Reindex to ensure correct order
    pivot_table = pivot_table.reindex(runners_order)
    pivot_n_table = pivot_n_table.reindex(runners_order)

    # Ensure we have all 3 outs columns (0, 1, 2)
    for out in [0, 1, 2]:
        if out not in pivot_table.columns:
            pivot_table[out] = np.nan
        if out not in pivot_n_table.columns:
            pivot_n_table[out] = np.nan

    pivot_table = pivot_table[[0, 1, 2]]
    pivot_n_table = pivot_n_table[[0, 1, 2]]

    # Replace NaN with None for JSON compatibility
    pivot_table = pivot_table.replace({np.nan: None})

    # Get event name if event_cd was provided
    event_name = None
    if event_cd is not None and data_store.get("re24_event_types") is not None:
        event_types_df = data_store["re24_event_types"]
        event_row = event_types_df[event_types_df["event_cd"] == event_cd]
        if not event_row.empty:
            event_name = event_row.iloc[0]["event"]

    # Calculate total events from events_counts dataframe
    total_events = None
    if data_store.get("events_counts") is not None:
        events_df = data_store["events_counts"]
        season_mask = (events_df["season"] >= from_season) & (events_df["season"] <= to_season)
        total_events = int(events_df[season_mask]["r24_n"].sum())

    # Convert to dictionary format for JSON response (vectorized)
    result = {
        "matrix_type": "RE24",
        "from_season": from_season,
        "to_season": to_season,
        "event_cd": event_cd,
        "event_name": event_name,
        "total_events": total_events,
        "headers": ["Runners", "0 Outs", "1 Out", "2 Outs"],
        "rows": [
            {
                "runners": runners,
                "label": BASE_STATE_LABELS.get(runners, runners),
                "out_0": round(float(row[0]), 3) if pd.notna(row[0]) else None,
                "out_1": round(float(row[1]), 3) if pd.notna(row[1]) else None,
                "out_2": round(float(row[2]), 3) if pd.notna(row[2]) else None,
                # Raw values for downloads
                "out_0_raw": float(row[0]) if pd.notna(row[0]) else None,
                "out_1_raw": float(row[1]) if pd.notna(row[1]) else None,
                "out_2_raw": float(row[2]) if pd.notna(row[2]) else None,
                "out_0_n": int(pivot_n_table.loc[runners, 0]) if pd.notna(pivot_n_table.loc[runners, 0]) else None,
                "out_1_n": int(pivot_n_table.loc[runners, 1]) if pd.notna(pivot_n_table.loc[runners, 1]) else None,
                "out_2_n": int(pivot_n_table.loc[runners, 2]) if pd.notna(pivot_n_table.loc[runners, 2]) else None
            }
            for runners, row in zip(pivot_table.index, pivot_table.values, strict=True)
        ]
    }

    return result


def generate_re24_probabilities_table(
    data_store: dict,
    from_season: int,
    to_season: int,
    inning: int | None = None
) -> dict:
    """
    Generate RE24 Probabilities table with weighted mean if multiple seasons.

    Args:
        data_store: Dictionary containing all data
        from_season: Starting season
        to_season: Ending season
        inning: Optional inning to filter by

    Returns:
        Dictionary with table data ready for JSON response
    """
    # Filter and validate data
    required_cols = ["runners", "outs", "bucket", "percentage", "n", "bucket_sort_order"]
    if inning is not None:
        required_cols.append("inning")

    df = filter_and_validate_data(
        data_store,
        "re24_percentages",
        from_season,
        to_season,
        required_cols,
        "RE24 Probabilities"
    )

    # Filter by inning if provided (just filter, don't aggregate)
    if inning is not None:
        df = df[df["inning"] == inning].copy()
        if len(df) == 0:
            raise ValueError(f"No data found for inning={inning} in season range {from_season}-{to_season}")
        df = df.drop(columns=["inning"])
    else:
        # If no inning provided, default to "All Innings" (inning=-1)
        df = df[df["inning"] == -1].copy()
        if len(df) == 0:
            raise ValueError(f"No data found for inning=-1 (All Innings) in season range {from_season}-{to_season}")
        df = df.drop(columns=["inning"])

    # Calculate weighted mean for single or multiple seasons
    pivot_df = calculate_weighted_mean(
        df,
        from_season,
        to_season,
        "percentage",
        ["runners", "outs", "bucket"]
    )

    # Aggregate sample sizes for reliability markers
    pivot_n_df = df.groupby(["runners", "outs", "bucket"], observed=False)["n"].sum().reset_index()

    # Pivot wider: runners and outs as index, bucket columns as values
    pivot_table = pivot_df.pivot_table(
        index=["runners", "outs"],
        columns="bucket",
        values="percentage",
        observed=False
    )
    pivot_n_table = pivot_n_df.pivot_table(
        index=["runners", "outs"],
        columns="bucket",
        values="n",
        observed=False
    )

    # Get bucket columns in correct sort order from source data
    bucket_order_df = pivot_df[["bucket", "bucket_sort_order"]].drop_duplicates().sort_values("bucket_sort_order")
    bucket_columns = bucket_order_df["bucket"].tolist()

    # Reorder pivot table columns by bucket_sort_order
    pivot_table = pivot_table[bucket_columns]
    pivot_n_table = pivot_n_table.reindex(columns=bucket_columns)

    # Define correct order for base states and outs
    runners_order = ["000", "100", "010", "001", "110", "101", "011", "111"]
    outs_order = [0, 1, 2]

    # Create multi-index with proper order
    full_index = pd.MultiIndex.from_tuples(
        list(product(runners_order, outs_order)),
        names=["runners", "outs"]
    )

    # Reindex to ensure correct order
    pivot_table = pivot_table.reindex(full_index)
    pivot_n_table = pivot_n_table.reindex(full_index)

    # Replace NaN with None for JSON compatibility
    pivot_table = pivot_table.replace({np.nan: None})

    # Create bucket labels for headers
    bucket_labels = {
        "no_runs": "No Runs",
        "at_least_one_run": "≥1 Run",
        "one_run": "1 Run",
        "two_runs": "2 Runs",
        "three_runs": "3 Runs",
        "four_runs": "4 Runs",
        "five_runs": "5 Runs",
        "more_than_five_runs": ">5 Runs"
    }

    # Get inning name if inning was provided
    inn_name = None
    inn_id_value = inning if inning is not None else -1  # Default to -1 (All Innings)
    if data_store.get("innings") is not None:
        innings_list = data_store["innings"]
        inning_obj = next((i for i in innings_list if i["inn_id"] == inn_id_value), None)
        if inning_obj:
            inn_name = inning_obj["inn_name"]

    # Calculate total events from events_counts dataframe
    total_events = None
    if data_store.get("events_counts") is not None:
        events_df = data_store["events_counts"]
        season_mask = (events_df["season"] >= from_season) & (events_df["season"] <= to_season)
        total_events = int(events_df[season_mask]["r24_n"].sum())

    # Convert to dictionary format for JSON response (vectorized)
    result = {
        "matrix_type": "RE24_PCT",
        "from_season": from_season,
        "to_season": to_season,
        "inn_id": inn_id_value,
        "inn_name": inn_name,
        "total_events": total_events,
        "headers": ["Runners", "Outs"] + [bucket_labels.get(col, col) for col in bucket_columns],
        "bucket_columns": bucket_columns,
        "rows": []
    }

    # Build rows using vectorized operations
    for (runners, outs), row in zip(pivot_table.index, pivot_table.values, strict=True):
        row_data = {
            "runners": runners,
            "label": BASE_STATE_LABELS.get(runners, runners),
            "outs": outs
        }

        # Add bucket values
        for i, bucket in enumerate(bucket_columns):
            value = row[i]
            row_data[bucket] = round(float(value) * 100, 1) if pd.notna(value) else None
            # Raw values for downloads (keep as 0-1 range, not 0-100)
            row_data[f"{bucket}_raw"] = float(value) if pd.notna(value) else None
            n_value = pivot_n_table.loc[(runners, outs), bucket] if bucket in pivot_n_table.columns else np.nan
            row_data[f"{bucket}_n"] = int(n_value) if pd.notna(n_value) else None

        result["rows"].append(row_data)

    return result


def generate_re288_table(
    data_store: dict,
    from_season: int,
    to_season: int
) -> dict:
    """
    Generate RE288 table with weighted mean if multiple seasons.

    Args:
        data_store: Dictionary containing all data
        from_season: Starting season
        to_season: Ending season

    Returns:
        Dictionary with table data ready for JSON response
    """
    # Filter and validate data
    df = filter_and_validate_data(
        data_store,
        "re288",
        from_season,
        to_season,
        ["runners", "outs", "count", "run_value", "n", "sort_order_count"],
        "RE288"
    )

    # Calculate weighted mean for single or multiple seasons
    pivot_df = calculate_weighted_mean(
        df,
        from_season,
        to_season,
        "run_value",
        ["runners", "outs", "count"]
    )

    # Aggregate sample sizes for reliability markers
    pivot_n_df = df.groupby(["runners", "outs", "count"], observed=False)["n"].sum().reset_index()

    # Pivot wider: runners and outs as index, count columns as values
    pivot_table = pivot_df.pivot_table(
        index=["runners", "outs"],
        columns="count",
        values="run_value",
        observed=False
    )
    pivot_n_table = pivot_n_df.pivot_table(
        index=["runners", "outs"],
        columns="count",
        values="n",
        observed=False
    )

    # Get count columns in correct sort order from source data
    count_order_df = pivot_df[["count", "sort_order_count"]].drop_duplicates().sort_values("sort_order_count")
    count_columns = count_order_df["count"].tolist()

    # Reorder pivot table columns by sort_order_count
    pivot_table = pivot_table[count_columns]
    pivot_n_table = pivot_n_table.reindex(columns=count_columns)

    # Define correct order for base states and outs
    runners_order = ["000", "100", "010", "001", "110", "101", "011", "111"]
    outs_order = [0, 1, 2]

    # Create multi-index with proper order
    full_index = pd.MultiIndex.from_tuples(
        list(product(runners_order, outs_order)),
        names=["runners", "outs"]
    )

    # Reindex to ensure correct order
    pivot_table = pivot_table.reindex(full_index)
    pivot_n_table = pivot_n_table.reindex(full_index)

    # Replace NaN with None for JSON compatibility
    pivot_table = pivot_table.replace({np.nan: None})

    # Calculate total pitches from events_counts dataframe
    total_pitches = None
    if data_store.get("events_counts") is not None:
        events_df = data_store["events_counts"]
        season_mask = (events_df["season"] >= from_season) & (events_df["season"] <= to_season)
        total_pitches = int(events_df[season_mask]["r288_n"].sum())

    # Convert to dictionary format for JSON response (vectorized)
    result = {
        "matrix_type": "RE288",
        "from_season": from_season,
        "to_season": to_season,
        "total_pitches": total_pitches,
        "headers": ["Runners", "Outs"] + count_columns,
        "count_columns": count_columns,
        "rows": []
    }

    # Build rows using vectorized operations
    for (runners, outs), row in zip(pivot_table.index, pivot_table.values, strict=True):
        row_data = {
            "runners": runners,
            "label": BASE_STATE_LABELS.get(runners, runners),
            "outs": outs
        }

        # Add count values
        for i, count in enumerate(count_columns):
            value = row[i]
            row_data[count] = round(float(value), 3) if pd.notna(value) else None
            # Raw values for downloads
            row_data[f"{count}_raw"] = float(value) if pd.notna(value) else None
            n_value = pivot_n_table.loc[(runners, outs), count] if count in pivot_n_table.columns else np.nan
            row_data[f"{count}_n"] = int(n_value) if pd.notna(n_value) else None

        result["rows"].append(row_data)

    return result


def generate_re288_frequencies_table(
    data_store: dict,
    from_season: int,
    to_season: int
) -> dict:
    """
    Generate RE288 Frequencies table with weighted mean if multiple seasons.

    Args:
        data_store: Dictionary containing all data
        from_season: Starting season
        to_season: Ending season

    Returns:
        Dictionary with table data ready for JSON response
    """
    # Filter and validate data
    df = filter_and_validate_data(
        data_store,
        "re288_frequencies",
        from_season,
        to_season,
        ["runners", "outs", "count", "pa_final_count_share", "n", "sort_order_count"],
        "RE288 Frequencies"
    )

    # Calculate weighted mean for single or multiple seasons
    pivot_df = calculate_weighted_mean(
        df,
        from_season,
        to_season,
        "pa_final_count_share",
        ["runners", "outs", "count"]
    )

    # Aggregate sample sizes for reliability markers
    pivot_n_df = df.groupby(["runners", "outs", "count"], observed=False)["n"].sum().reset_index()

    # Pivot wider: runners and outs as index, count columns as values
    pivot_table = pivot_df.pivot_table(
        index=["runners", "outs"],
        columns="count",
        values="pa_final_count_share",
        observed=False
    )
    pivot_n_table = pivot_n_df.pivot_table(
        index=["runners", "outs"],
        columns="count",
        values="n",
        observed=False
    )

    # Get count columns in correct sort order from source data
    count_order_df = pivot_df[["count", "sort_order_count"]].drop_duplicates().sort_values("sort_order_count")
    count_columns = count_order_df["count"].tolist()

    # Reorder pivot table columns by sort_order_count
    pivot_table = pivot_table[count_columns]
    pivot_n_table = pivot_n_table.reindex(columns=count_columns)

    # Define correct order for base states and outs
    runners_order = ["000", "100", "010", "001", "110", "101", "011", "111"]
    outs_order = [0, 1, 2]

    # Create multi-index with proper order
    full_index = pd.MultiIndex.from_tuples(
        list(product(runners_order, outs_order)),
        names=["runners", "outs"]
    )

    # Reindex to ensure correct order
    pivot_table = pivot_table.reindex(full_index)
    pivot_n_table = pivot_n_table.reindex(full_index)

    # Replace NaN with None for JSON compatibility
    pivot_table = pivot_table.replace({np.nan: None})

    # Calculate total pitches from events_counts dataframe
    total_pitches = None
    if data_store.get("events_counts") is not None:
        events_df = data_store["events_counts"]
        season_mask = (events_df["season"] >= from_season) & (events_df["season"] <= to_season)
        total_pitches = int(events_df[season_mask]["r288_n"].sum())

    # Convert to dictionary format for JSON response (vectorized)
    result = {
        "matrix_type": "RE288_FREQ",
        "from_season": from_season,
        "to_season": to_season,
        "total_pitches": total_pitches,
        "headers": ["Runners", "Outs"] + count_columns,
        "count_columns": count_columns,
        "rows": []
    }

    # Build rows using vectorized operations
    for (runners, outs), row in zip(pivot_table.index, pivot_table.values, strict=True):
        row_data = {
            "runners": runners,
            "label": BASE_STATE_LABELS.get(runners, runners),
            "outs": outs
        }

        # Add count values (frequencies are typically shown as percentages)
        for i, count in enumerate(count_columns):
            value = row[i]
            row_data[count] = round(float(value) * 100, 3) if pd.notna(value) else None
            # Raw values for downloads (keep as 0-1 range)
            row_data[f"{count}_raw"] = float(value) if pd.notna(value) else None
            n_value = pivot_n_table.loc[(runners, outs), count] if count in pivot_n_table.columns else np.nan
            row_data[f"{count}_n"] = int(n_value) if pd.notna(n_value) else None

        result["rows"].append(row_data)

    logger.info(f"Generated RE288 Frequencies table with {len(result['rows'])} rows")
    return result


def generate_re288_transitions_table(
    data_store: dict,
    from_season: int,
    to_season: int,
    transition_type: str,
    transition_mode: int = 1
) -> dict:
    """
    Generate RE288 Transitions table with weighted mean if multiple seasons.

    Args:
        data_store: Dictionary containing all data
        from_season: Starting season
        to_season: Ending season
        transition_type: Transition type ('B' for balls, 'S' for strikes, 'X' for ball in play)
        transition_mode: 1 for Run Expectancy (transition_value), 2 for Probability (transition_prob), 3 for Expected Run Expectancy (transition_prob * transition_value)

    Returns:
        Dictionary with table data ready for JSON response
    """
    # Filter and validate data
    df = filter_and_validate_data(
        data_store,
        "re288_transitions",
        from_season,
        to_season,
        ["runners", "outs", "count", "transition_type", "transition_value", "transition_prob", "n", "sort_order_count"],
        "RE288 Transitions"
    )

    # Filter by transition type (B or S)
    df = df[df["transition_type"] == transition_type].copy()

    if df.empty:
        raise ValueError(f"No data found for transition type '{transition_type}' in season range {from_season}-{to_season}")

    # For transition_mode=3, calculate expected run expectancy (probability * run expectancy)
    if transition_mode == 3:
        df['expected_run_expectancy'] = df['transition_prob'] * df['transition_value']

    # Drop transition_type column as it's no longer needed
    df = df.drop(columns=["transition_type"])

    # Choose the value column based on transition_mode
    if transition_mode == 1:
        value_column = "transition_value"
    elif transition_mode == 2:
        value_column = "transition_prob"
    else:  # transition_mode == 3
        value_column = "expected_run_expectancy"

    # Calculate weighted mean for single or multiple seasons
    pivot_df = calculate_weighted_mean(
        df,
        from_season,
        to_season,
        value_column,
        ["runners", "outs", "count"]
    )

    # Aggregate sample sizes for reliability markers
    pivot_n_df = df.groupby(["runners", "outs", "count"], observed=False)["n"].sum().reset_index()

    # Pivot wider: runners and outs as index, count columns as values
    pivot_table = pivot_df.pivot_table(
        index=["runners", "outs"],
        columns="count",
        values=value_column,
        observed=False
    )
    pivot_n_table = pivot_n_df.pivot_table(
        index=["runners", "outs"],
        columns="count",
        values="n",
        observed=False
    )

    # Get count columns in correct sort order from source data
    count_order_df = pivot_df[["count", "sort_order_count"]].drop_duplicates().sort_values("sort_order_count")
    count_columns = count_order_df["count"].tolist()

    # Reorder pivot table columns by sort_order_count
    pivot_table = pivot_table[count_columns]
    pivot_n_table = pivot_n_table.reindex(columns=count_columns)

    # Define correct order for base states and outs
    runners_order = ["000", "100", "010", "001", "110", "101", "011", "111"]
    outs_order = [0, 1, 2]

    # Create multi-index with proper order
    full_index = pd.MultiIndex.from_tuples(
        list(product(runners_order, outs_order)),
        names=["runners", "outs"]
    )

    # Reindex to ensure correct order
    pivot_table = pivot_table.reindex(full_index)
    pivot_n_table = pivot_n_table.reindex(full_index)

    # Replace NaN with None for JSON compatibility
    pivot_table = pivot_table.replace({np.nan: None})

    # Calculate total pitches from events_counts dataframe
    total_pitches = None
    if data_store.get("events_counts") is not None:
        events_df = data_store["events_counts"]
        season_mask = (events_df["season"] >= from_season) & (events_df["season"] <= to_season)
        total_pitches = int(events_df[season_mask]["r288_n"].sum())

    # Convert to dictionary format for JSON response (vectorized)
    result = {
        "matrix_type": "RE288_TRANS",
        "from_season": from_season,
        "to_season": to_season,
        "transition_type": transition_type,
        "transition_mode": transition_mode,
        "total_pitches": total_pitches,
        "headers": ["Runners", "Outs"] + count_columns,
        "count_columns": count_columns,
        "rows": []
    }

    # Build rows using vectorized operations
    for (runners, outs), row in zip(pivot_table.index, pivot_table.values, strict=True):
        row_data = {
            "runners": runners,
            "label": BASE_STATE_LABELS.get(runners, runners),
            "outs": outs
        }

        # Add count values (transition probabilities with 3 decimals)
        for i, count in enumerate(count_columns):
            value = row[i]
            row_data[count] = round(float(value), 3) if pd.notna(value) else None
            # Raw values for downloads
            row_data[f"{count}_raw"] = float(value) if pd.notna(value) else None
            n_value = pivot_n_table.loc[(runners, outs), count] if count in pivot_n_table.columns else np.nan
            row_data[f"{count}_n"] = int(n_value) if pd.notna(n_value) else None

        result["rows"].append(row_data)

    return result


def generate_park_factors_table(
    data_store: dict,
    season: int,
    average: int
) -> dict:
    """
    Generate Park Factors table for a specific season and averaging period.

    Args:
        data_store: Dictionary containing all data
        season: Target season
        average: Averaging period (1, 3, or 5 seasons)

    Returns:
        Dictionary with table data ready for JSON response
    """
    # Get park factors and ballparks data
    park_factors_df = data_store.get("park_factors")
    ballparks_df = data_store.get("ballparks")

    if park_factors_df is None:
        raise ValueError("Park factors data not available")
    if ballparks_df is None:
        raise ValueError("Ballparks data not available")

    # Filter by season and average period
    filtered_df = park_factors_df[
        (park_factors_df["SEASON"] == season) &
        (park_factors_df["SEASONS"] == average)
    ].copy()

    if len(filtered_df) == 0:
        raise ValueError(f"No park factors data available for season {season} with {average}-season average")

    # Join with ballparks to get park names and team names
    # Ballparks has Season (not SEASON), so we need to match on that
    ballparks_season = ballparks_df[ballparks_df["Season"] == season].copy()

    # Merge on PARK_ID
    merged_df = filtered_df.merge(
        ballparks_season[["PARK_ID", "Park_Name", "Team_Name"]],
        on="PARK_ID",
        how="left"
    )

    # Fill missing park/team names
    merged_df["Park_Name"] = merged_df["Park_Name"].fillna("Unknown")
    merged_df["Team_Name"] = merged_df["Team_Name"].fillna("Unknown")

    # Sort by Runs (PF_R) descending
    merged_df = merged_df.sort_values("PF_R", ascending=False)

    # Build result
    result = {
        "matrix_type": "PARK_FACTORS",
        "season": season,
        "average": average,
        "headers": ["Season", "Park", "Team", "Runs", "1B", "2B", "3B", "HR", "BB", "SO"],
        "rows": []
    }

    # Convert to rows - send raw values (frontend will format)
    for _, row in merged_df.iterrows():
        row_data = {
            "season": int(row["SEASON"]),
            "park_id": str(row["PARK_ID"]),
            "park_name": str(row["Park_Name"]),
            "team_name": str(row["Team_Name"]),
            "seasons": int(row["SEASONS"]),
            "pf_r": float(row["PF_R"]) if pd.notna(row["PF_R"]) else None,
            "pf_1b": float(row["PF_1B"]) if pd.notna(row["PF_1B"]) else None,
            "pf_2b": float(row["PF_2B"]) if pd.notna(row["PF_2B"]) else None,
            "pf_3b": float(row["PF_3B"]) if pd.notna(row["PF_3B"]) else None,
            "pf_hr": float(row["PF_HR"]) if pd.notna(row["PF_HR"]) else None,
            "pf_bb": float(row["PF_BB"]) if pd.notna(row["PF_BB"]) else None,
            "pf_so": float(row["PF_SO"]) if pd.notna(row["PF_SO"]) else None
        }
        result["rows"].append(row_data)

    return result


def generate_linear_weights_table(
    data_store: dict,
    from_season: int,
    to_season: int,
    outs: int | None = None
) -> dict:
    """
    Generate Linear Weights table with weighted mean if multiple seasons.
    Groups by RUNNERS and EVENT_CD, calculates weighted mean of LINEAR_WEIGHT using n as weight,
    pivots wider with runners as columns, joins with event types, and orders like RE24.

    Args:
        data_store: Dictionary containing all data
        from_season: Starting season
        to_season: Ending season
        outs: Optional outs filter (-1 for all, 0-2 for specific outs)

    Returns:
        Dictionary with table data ready for JSON response
    """
    # Get the linear_weights dataframe
    df = data_store["linear_weights"].copy()

    # Rename SEASON to season for consistency
    df = df.rename(columns={"SEASON": "season"})

    # Some datasets include legacy rollup rows (EVENT_CD == -1), others only event-level rows.
    # Only restrict to rollup rows when they actually exist; otherwise keep event-level rows.
    if "EVENT_CD" in df.columns and (df["EVENT_CD"] == -1).any():
        df = df[df["EVENT_CD"] == -1].copy()

    # Filter by season range
    df = df[
        (df["season"] >= from_season) &
        (df["season"] <= to_season)
    ]

    # Filter by outs - including -1 for "All Outs" aggregated data
    if outs is not None:
        df = df[df["OUTS"] == outs]

    if len(df) == 0:
        raise ValueError(f"No data found for season range {from_season}-{to_season}")

    # Validate required columns
    required_cols = ["RUNNERS", "OUTS", "EVENT_CD", "LINEAR_WEIGHT", "n"]
    df = df.dropna(subset=required_cols).copy()

    # Calculate weighted mean for single or multiple seasons
    # Group by RUNNERS, OUTS, and EVENT_CD
    pivot_df = calculate_weighted_mean(
        df,
        from_season,
        to_season,
        "LINEAR_WEIGHT",
        ["RUNNERS", "OUTS", "EVENT_CD"]
    )

    # Pivot wider: columns = RUNNERS, values = LINEAR_WEIGHT
    pivot_table = pivot_df.pivot(index=["EVENT_CD", "OUTS"], columns="RUNNERS", values="LINEAR_WEIGHT")

    # Aggregate sample sizes for reliability markers
    pivot_n_df = df.groupby(["RUNNERS", "OUTS", "EVENT_CD"], observed=False)["n"].sum().reset_index()
    pivot_n_table = pivot_n_df.pivot(index=["EVENT_CD", "OUTS"], columns="RUNNERS", values="n")

    # Define correct order for base states (same as RE24) with AVG first
    runners_order = ["AVG", "000", "100", "010", "001", "110", "101", "011", "111"]

    # Reindex to ensure correct column order
    pivot_table = pivot_table.reindex(columns=runners_order)
    pivot_n_table = pivot_n_table.reindex(columns=runners_order)

    # Reset index to make EVENT_CD and OUTS regular columns
    pivot_table = pivot_table.reset_index()
    pivot_n_table = pivot_n_table.reset_index()

    # Rename N columns with _n suffix and merge them into the main payload
    n_rename_map = {col: f"{col}_n" for col in runners_order if col in pivot_n_table.columns}
    pivot_n_table = pivot_n_table.rename(columns=n_rename_map)
    pivot_table = pivot_table.merge(pivot_n_table, on=["EVENT_CD", "OUTS"], how="left")

    # Now pivot_table has columns: EVENT_CD, OUTS, and the runner columns
    # Filter to only include runners that exist
    available_runners = [r for r in runners_order if r in pivot_table.columns]
    available_runner_n = [f"{r}_n" for r in available_runners if f"{r}_n" in pivot_table.columns]

    # Join with event types to get event names
    event_types_df = data_store["re24_event_types"].copy()

    # Merge with event types
    result_df = pivot_table.merge(
        event_types_df[["event_cd", "event"]],
        left_on="EVENT_CD",
        right_on="event_cd",
        how="left"
    )

    # Sort by event_cd and OUTS
    result_df = result_df.sort_values(["event_cd", "OUTS"])

    # Drop EVENT_CD and event_cd columns
    result_df = result_df.drop(columns=["EVENT_CD", "event_cd"])

    # Select and reorder columns: event, OUTS, then runner states
    cols_to_select = ["event", "OUTS"] + available_runners + available_runner_n
    result_df = result_df[cols_to_select]

    # Rename OUTS to outs for JSON output
    result_df = result_df.rename(columns={"OUTS": "outs"})

    # Replace NaN with None for JSON compatibility
    result_df = result_df.replace({np.nan: None})

    # Cast reliability counts to int for clean JSON payloads
    for n_col in available_runner_n:
        result_df[n_col] = result_df[n_col].apply(lambda x: int(x) if pd.notna(x) else None)

    # Total events for footer text.
    # Prefer AVG runner rows when present to avoid double counting aggregate/state rows.
    if (df["RUNNERS"] == "AVG").any():
        total_events = int(df.loc[df["RUNNERS"] == "AVG", "n"].sum())
    else:
        total_events = int(df["n"].sum())

    # Send raw values - frontend will format for display

    # Build result
    result = {
        "matrix_type": "LINEAR_WEIGHTS",
        "from_season": from_season,
        "to_season": to_season,
        "outs": outs if outs is not None else -1,
        "total_events": total_events,
        "headers": ["Event", "Outs"] + ["Average" if r == "AVG" else BASE_STATE_LABELS.get(r, r) for r in runners_order if r in available_runners],
        "rows": result_df.to_dict(orient='records')
    }

    return result


def generate_r24_frequencies_table(
    data_store: dict,
    from_season: int,
    to_season: int
) -> dict:
    """
    Generate R24 Frequencies table with weighted mean if multiple seasons.
    Groups by RUNNERS and OUTS, calculates weighted mean of FREQUENCY using n as weight,
    pivots wider with outs as columns.

    Args:
        data_store: Dictionary containing all data
        from_season: Starting season
        to_season: Ending season

    Returns:
        Dictionary with table data ready for JSON response
    """
    # Get the r24_frequencies dataframe
    df = data_store["r24_frequencies"].copy()

    # Rename SEASON to season for consistency
    df = df.rename(columns={"SEASON": "season"})

    # If event-level frequencies are present, keep only the all-events rollup (-1) for this table.
    if "EVENT_CD" in df.columns:
        df = df[df["EVENT_CD"] == -1].copy()

    # Filter by season range
    df = df[
        (df["season"] >= from_season) &
        (df["season"] <= to_season)
    ]

    if len(df) == 0:
        raise ValueError(f"No data found for season range {from_season}-{to_season}")

    # Validate required columns
    required_cols = ["RUNNERS", "OUTS", "FREQUENCY", "n"]
    df = df.dropna(subset=required_cols).copy()

    # Calculate weighted mean for single or multiple seasons
    # Group by RUNNERS and OUTS
    pivot_df = calculate_weighted_mean(
        df,
        from_season,
        to_season,
        "FREQUENCY",
        ["RUNNERS", "OUTS"]
    )

    # Pivot wider: columns = OUTS, values = FREQUENCY
    pivot_table = pivot_df.pivot(index="RUNNERS", columns="OUTS", values="FREQUENCY")

    # Define correct order for outs columns
    outs_order = [0, 1, 2]

    # Reindex columns to ensure correct order
    pivot_table = pivot_table.reindex(columns=outs_order)

    # Reset index to make RUNNERS a column
    pivot_table = pivot_table.reset_index()

    # Define correct order for base states (same as RE24)
    runners_order = ["000", "100", "010", "001", "110", "101", "011", "111"]

    # Sort by runners in the correct order
    pivot_table["RUNNERS"] = pd.Categorical(pivot_table["RUNNERS"], categories=runners_order, ordered=True)
    pivot_table = pivot_table.sort_values("RUNNERS")

    # Replace NaN with None for JSON compatibility
    pivot_table = pivot_table.replace({np.nan: None})

    # Send raw frequency values (0-1 range) - frontend will convert to percentage for display

    # Add label column for display
    pivot_table['label'] = pivot_table['RUNNERS'].apply(lambda x: BASE_STATE_LABELS.get(x, x))

    # Rename RUNNERS to runners for consistency with RE24
    pivot_table = pivot_table.rename(columns={"RUNNERS": "runners"})

    # Calculate total events from events_counts dataframe (same as RE24 Values)
    total_events = None
    if data_store.get("events_counts") is not None:
        events_df = data_store["events_counts"]
        season_mask = (events_df["season"] >= from_season) & (events_df["season"] <= to_season)
        total_events = int(events_df[season_mask]["r24_n"].sum())

    # Build result
    result = {
        "matrix_type": "R24_FREQUENCIES",
        "from_season": from_season,
        "to_season": to_season,
        "headers": ["Runners", "0 Outs", "1 Out", "2 Outs"],
        "rows": pivot_table.to_dict(orient='records'),
        "total_events": total_events
    }

    return result


def generate_win_expectancy_table(
    data_store: dict,
    from_season: int,
    to_season: int,
    team: int
) -> dict:
    """
    Generate Win Expectancy table with weighted mean if multiple seasons.

    Args:
        data_store: Dictionary containing all data
        from_season: Starting season
        to_season: Ending season
        team: 0=Away, 1=Home

    Returns:
        Dictionary with table data ready for JSON response
    """
    # Get the win_expectancy dataframe
    df = data_store["win_expectancy"]

    # Rename columns to lowercase for consistency
    df = df.rename(columns={"SEASON": "season", "INN_CT": "inn_ct", "BAT_HOME_ID": "bat_home_id",
                             "OUTS_CT": "outs_ct", "RUNNERS": "runners", "RUNS_DIFF": "runs_diff",
                             "WE": "we"})

    # Filter by team (home/away)
    df = df[df["bat_home_id"] == team]

    # Filter by season range
    df = df[
        (df["season"] >= from_season) &
        (df["season"] <= to_season)
    ]

    if len(df) == 0:
        team_name = "Home" if team == 1 else "Away"
        raise ValueError(f"No data found for {team_name} team in season range {from_season}-{to_season}")

    # Total situations processed for footer text.
    total_events = int(df["n"].sum())

    # Mismo bucketing de colas que WPA y LI. Hoy la vista ya entrega el rango
    # recortado -- 0 filas de 450,488 fuera de +-4 -- asi que esto no cambia
    # ningun numero. Esta para que no pueda cambiar en silencio: sin el clip, una
    # vista que empezara a emitir -5 le sacaria a Win Expectancy columnas '-5' y
    # '+5' que WPA y LI no tienen, y las tres tablas dejarian de ser comparables.
    df = df.copy()
    df["runs_diff"] = df["runs_diff"].clip(lower=-4, upper=4).astype(int)

    # Aggregate sample sizes for reliability markers at displayed cell grain.
    n_df = df.groupby(["inn_ct", "outs_ct", "runners", "runs_diff"], observed=False)["n"].sum().reset_index()

    # Media ponderada SIEMPRE, tambien con una sola temporada.
    #
    # Antes la rama de temporada unica tomaba las filas tal cual, lo que era
    # correcto solo mientras cada celda tuviera exactamente una fila (verificado:
    # 0 duplicados por season/team/inn/outs/runners/runs_diff). Con el clip
    # puesto eso deja de estar garantizado: un -5 y un -4 se pliegan sobre el
    # mismo -4, y el pivot de mas abajo usa aggfunc="first", que se quedaria con
    # una de las dos y TIRARIA la otra -- su probabilidad y su n. Agregando aqui,
    # las dos se combinan ponderadas por n como debe ser.
    #
    # Con los datos de hoy es identico a lo anterior: un grupo de una fila da
    # sum(we*n)/sum(n) == we.
    grouped = media_ponderada_por_grupo(
        df, "we", ["inn_ct", "outs_ct", "runners", "runs_diff"])
    result_df = grouped[["inn_ct", "outs_ct", "runners", "runs_diff", "we"]].copy()

    # Define custom sort order for runners using categorical type
    runners_order = ["000", "100", "010", "001", "110", "101", "011", "111"]
    result_df["runners"] = pd.Categorical(result_df["runners"], categories=runners_order, ordered=True)

    # Sort by inning, outs, runners (using categorical order), and runs_diff
    result_df = result_df.sort_values(["inn_ct", "outs_ct", "runners", "runs_diff"])

    # Pivot wider: columns = runs_diff, values = we
    # Create multi-index for rows (inn_ct, outs_ct, runners)
    pivot_table = result_df.pivot_table(
        index=["inn_ct", "outs_ct", "runners"],
        columns="runs_diff",
        values="we",
        aggfunc="first",
        observed=False,  # Explicitly set to silence FutureWarning
        sort=False  # Don't sort, preserve our custom order
    ).reset_index()
    pivot_n_table = n_df.pivot_table(
        index=["inn_ct", "outs_ct", "runners"],
        columns="runs_diff",
        values="n",
        aggfunc="first",
        observed=False,
        sort=False
    ).reset_index()

    # Convert runners back to string for JSON serialization
    pivot_table["runners"] = pivot_table["runners"].astype(str)
    pivot_n_table["runners"] = pivot_n_table["runners"].astype(str)

    # Format runners column display
    runners_format = {
        "000": "---",
        "100": "1--",
        "010": "-2-",
        "001": "--3",
        "110": "12-",
        "101": "1-3",
        "011": "-23",
        "111": "123"
    }
    pivot_table["runners"] = pivot_table["runners"].map(runners_format)
    pivot_n_table["runners"] = pivot_n_table["runners"].map(runners_format)

    # Format runs_diff column labels
    # La anotacion hace falta desde que media_ponderada_por_grupo declara su
    # tipo de retorno: mypy afina la inferencia aguas abajo y, sin ella, da
    # por inalcanzables las ramas de este bucle.
    new_column_labels = {}
    for col in pivot_table.columns:
        if isinstance(col, (int, np.integer)):
            if col < 0:
                new_column_labels[col] = "-4+" if col == -4 else str(col)
            elif col == 0:
                new_column_labels[col] = "Tie"
            else:
                new_column_labels[col] = "+4+" if col == 4 else f"+{col}"

    pivot_table = pivot_table.rename(columns=new_column_labels)
    pivot_n_table = pivot_n_table.rename(columns=new_column_labels)

    # Attach per-cell sample sizes with *_n keys for reliability markers.
    n_merge = pivot_n_table.copy()
    n_merge_columns = {
        col: f"{col}_n"
        for col in n_merge.columns
        if col not in ["inn_ct", "outs_ct", "runners"]
    }
    n_merge = n_merge.rename(columns=n_merge_columns)
    pivot_table = pivot_table.merge(n_merge, on=["inn_ct", "outs_ct", "runners"], how="left")

    # Replace NaN with None for JSON compatibility
    pivot_table = pivot_table.replace({np.nan: None})

    # Keep n fields as ints in JSON payload.
    for col in pivot_table.columns:
        if col.endswith("_n"):
            pivot_table[col] = pivot_table[col].apply(lambda x: int(x) if x is not None else None)

    # Convert WE values to percentage (multiply by 100) but keep full precision
    # Frontend will format for display, but CSV/JSON exports need raw precision
    # Convert to Python float for better JSON serialization precision
    for col in pivot_table.columns:
        if col not in ["inn_ct", "outs_ct", "runners"] and not str(col).endswith("_n"):
            pivot_table[col] = pivot_table[col].apply(
                lambda x: float(x * 100) if x is not None else None
            )

    # Build result
    team_name = "Home" if team == 1 else "Away"

    # Get dynamic column headers (runs_diff labels), sorted: negative, Tie, positive
    runs_diff_cols = [
        col for col in pivot_table.columns
        if col not in ["inn_ct", "outs_ct", "runners"] and not str(col).endswith("_n")
    ]
    runs_diff_cols = sorted(runs_diff_cols, key=lambda c: (0, 0) if c == "Tie" else (1, int(c[1:].replace('+', ''))) if c.startswith("+") else (-1, int(c.replace('+', ''))))
    n_cols = [f"{col}_n" for col in runs_diff_cols if f"{col}_n" in pivot_table.columns]
    pivot_table = pivot_table[["inn_ct", "outs_ct", "runners"] + runs_diff_cols + n_cols]
    headers = ["Inning", "Outs", "Runners"] + runs_diff_cols

    result = {
        "matrix_type": "WIN_EXPECTANCY",
        "from_season": from_season,
        "to_season": to_season,
        "team": team,
        "team_name": team_name,
        "total_events": total_events,
        "headers": headers,
        "rows": pivot_table.to_dict(orient='records')
    }

    return result


def generate_win_probability_added_table(
    data_store: dict,
    from_season: int,
    to_season: int,
    team: int
) -> dict:
    """
    Generate Win Probability Added table with weighted mean if multiple seasons.

    Args:
        data_store: Dictionary containing all data
        from_season: Starting season
        to_season: Ending season
        team: 0=Away, 1=Home

    Returns:
        Dictionary with table data ready for JSON response
    """
    # Get the win_expectancy dataframe
    df = data_store["win_expectancy"]

    # Rename columns to lowercase for consistency
    df = df.rename(columns={"SEASON": "season", "INN_CT": "inn_ct", "BAT_HOME_ID": "bat_home_id",
                             "OUTS_CT": "outs_ct", "RUNNERS": "runners", "RUNS_DIFF": "runs_diff",
                             "WPA": "wpa"})

    # Filter by team (home/away)
    df = df[df["bat_home_id"] == team]

    # Filter by season range
    df = df[
        (df["season"] >= from_season) &
        (df["season"] <= to_season)
    ]

    # Tail-bucket run differential for table display: <=-4 and >=+4.
    df["runs_diff"] = df["runs_diff"].clip(lower=-4, upper=4).astype(int)

    if len(df) == 0:
        team_name = "Home" if team == 1 else "Away"
        raise ValueError(f"No data found for {team_name} team in season range {from_season}-{to_season}")

    # Total situations processed for footer text.
    total_events = int(df["n"].sum())

    # Aggregate sample sizes for reliability markers at displayed cell grain.
    n_df = df.groupby(["inn_ct", "outs_ct", "runners", "runs_diff"], observed=False)["n"].sum().reset_index()

    # Calculate weighted mean if multiple seasons
    if from_season == to_season:
        # Single season: use value directly
        result_df = df[["inn_ct", "outs_ct", "runners", "runs_diff", "wpa"]].copy()
    else:
        # Multiple seasons: calculate weighted mean
        grouped = media_ponderada_por_grupo(
            df, "wpa", ["inn_ct", "outs_ct", "runners", "runs_diff"])
        result_df = grouped[["inn_ct", "outs_ct", "runners", "runs_diff", "wpa"]].copy()

    # Define custom sort order for runners using categorical type
    runners_order = ["000", "100", "010", "001", "110", "101", "011", "111"]
    result_df["runners"] = pd.Categorical(result_df["runners"], categories=runners_order, ordered=True)

    # Sort by inning, outs, runners (using categorical order), and runs_diff
    result_df = result_df.sort_values(["inn_ct", "outs_ct", "runners", "runs_diff"])

    # Pivot wider: columns = runs_diff, values = wpa
    # Create multi-index for rows (inn_ct, outs_ct, runners)
    pivot_table = result_df.pivot_table(
        index=["inn_ct", "outs_ct", "runners"],
        columns="runs_diff",
        values="wpa",
        aggfunc="first",
        observed=False,  # Explicitly set to silence FutureWarning
        sort=False  # Don't sort, preserve our custom order
    ).reset_index()
    pivot_n_table = n_df.pivot_table(
        index=["inn_ct", "outs_ct", "runners"],
        columns="runs_diff",
        values="n",
        aggfunc="first",
        observed=False,
        sort=False
    ).reset_index()

    # Convert runners back to string for JSON serialization
    pivot_table["runners"] = pivot_table["runners"].astype(str)
    pivot_n_table["runners"] = pivot_n_table["runners"].astype(str)

    # Format runners column display
    runners_format = {
        "000": "---",
        "100": "1--",
        "010": "-2-",
        "001": "--3",
        "110": "12-",
        "101": "1-3",
        "011": "-23",
        "111": "123"
    }
    pivot_table["runners"] = pivot_table["runners"].map(runners_format)
    pivot_n_table["runners"] = pivot_n_table["runners"].map(runners_format)

    # Format runs_diff column labels
    new_column_labels = {}
    for col in pivot_table.columns:
        if isinstance(col, (int, np.integer)):
            if col < 0:
                new_column_labels[col] = "-4+" if col == -4 else str(col)
            elif col == 0:
                new_column_labels[col] = "Tie"
            else:
                new_column_labels[col] = "+4+" if col == 4 else f"+{col}"

    pivot_table = pivot_table.rename(columns=new_column_labels)
    pivot_n_table = pivot_n_table.rename(columns=new_column_labels)

    # Attach per-cell sample sizes with *_n keys for reliability markers.
    n_merge = pivot_n_table.copy()
    n_merge_columns = {
        col: f"{col}_n"
        for col in n_merge.columns
        if col not in ["inn_ct", "outs_ct", "runners"]
    }
    n_merge = n_merge.rename(columns=n_merge_columns)
    pivot_table = pivot_table.merge(n_merge, on=["inn_ct", "outs_ct", "runners"], how="left")

    # Replace NaN with None for JSON compatibility
    pivot_table = pivot_table.replace({np.nan: None})

    # Keep n fields as ints in JSON payload.
    for col in pivot_table.columns:
        if col.endswith("_n"):
            pivot_table[col] = pivot_table[col].apply(lambda x: int(x) if x is not None else None)

    # Convert WPA values to percentage (multiply by 100) but keep full precision
    # Frontend will format for display, but CSV/JSON exports need raw precision
    # Convert to Python float for better JSON serialization precision
    for col in pivot_table.columns:
        if col not in ["inn_ct", "outs_ct", "runners"] and not str(col).endswith("_n"):
            pivot_table[col] = pivot_table[col].apply(
                lambda x: float(x * 100) if x is not None else None
            )

    # Build result
    team_name = "Home" if team == 1 else "Away"

    # Get dynamic column headers (runs_diff labels), sorted: negative, Tie, positive.
    # Keep *_n helper fields in rows for reliability markers, but exclude them from visible headers.
    runs_diff_cols = [
        col for col in pivot_table.columns
        if col not in ["inn_ct", "outs_ct", "runners"] and not str(col).endswith("_n")
    ]
    runs_diff_cols = sorted(runs_diff_cols, key=lambda c: (0, 0) if c == "Tie" else (1, int(c[1:].replace('+', ''))) if c.startswith("+") else (-1, int(c.replace('+', ''))))
    n_cols = [f"{col}_n" for col in runs_diff_cols if f"{col}_n" in pivot_table.columns]
    pivot_table = pivot_table[["inn_ct", "outs_ct", "runners"] + runs_diff_cols + n_cols]
    headers = ["Inning", "Outs", "Runners"] + runs_diff_cols

    result = {
        "matrix_type": "WIN_PROBABILITY_ADDED",
        "from_season": from_season,
        "to_season": to_season,
        "team": team,
        "team_name": team_name,
        "total_events": total_events,
        "headers": headers,
        "rows": pivot_table.to_dict(orient='records')
    }

    return result


def generate_leverage_index_table(
    data_store: dict,
    from_season: int,
    to_season: int,
    team: int
) -> dict:
    """
    Generate Leverage Index table with weighted mean if multiple seasons.

    Args:
        data_store: Dictionary containing all data
        from_season: Starting season
        to_season: Ending season
        team: 0=Away, 1=Home

    Returns:
        Dictionary with table data ready for JSON response
    """
    # Get the win_expectancy dataframe (which now contains LI column)
    df = data_store["win_expectancy"]

    # Rename columns to lowercase for consistency
    df = df.rename(columns={"SEASON": "season", "INN_CT": "inn_ct", "BAT_HOME_ID": "bat_home_id",
                             "OUTS_CT": "outs_ct", "RUNNERS": "runners", "RUNS_DIFF": "runs_diff",
                             "LI": "li"})

    # Filter by team (home/away)
    df = df[df["bat_home_id"] == team]

    # Filter by season range
    df = df[
        (df["season"] >= from_season) &
        (df["season"] <= to_season)
    ]

    # Tail-bucket run differential for table display: <=-4 and >=+4.
    df["runs_diff"] = df["runs_diff"].clip(lower=-4, upper=4).astype(int)

    if len(df) == 0:
        team_name = "Home" if team == 1 else "Away"
        raise ValueError(f"No data found for {team_name} team in season range {from_season}-{to_season}")

    # Total situations processed for footer text.
    total_events = int(df["n"].sum())

    # Aggregate sample sizes for reliability markers at displayed cell grain.
    n_df = df.groupby(["inn_ct", "outs_ct", "runners", "runs_diff"], observed=False)["n"].sum().reset_index()

    # Calculate weighted mean if multiple seasons
    if from_season == to_season:
        # Single season: use value directly
        result_df = df[["inn_ct", "outs_ct", "runners", "runs_diff", "li"]].copy()
    else:
        # Multiple seasons: calculate weighted mean
        grouped = media_ponderada_por_grupo(
            df, "li", ["inn_ct", "outs_ct", "runners", "runs_diff"])
        result_df = grouped[["inn_ct", "outs_ct", "runners", "runs_diff", "li"]].copy()

    # Define custom sort order for runners using categorical type
    runners_order = ["000", "100", "010", "001", "110", "101", "011", "111"]
    result_df["runners"] = pd.Categorical(result_df["runners"], categories=runners_order, ordered=True)

    # Sort by inning, outs, runners (using categorical order), and runs_diff
    result_df = result_df.sort_values(["inn_ct", "outs_ct", "runners", "runs_diff"])

    # Pivot wider: columns = runs_diff, values = li
    # Create multi-index for rows (inn_ct, outs_ct, runners)
    pivot_table = result_df.pivot_table(
        index=["inn_ct", "outs_ct", "runners"],
        columns="runs_diff",
        values="li",
        aggfunc="first",
        observed=False,  # Explicitly set to silence FutureWarning
        sort=False  # Don't sort, preserve our custom order
    ).reset_index()
    pivot_n_table = n_df.pivot_table(
        index=["inn_ct", "outs_ct", "runners"],
        columns="runs_diff",
        values="n",
        aggfunc="first",
        observed=False,
        sort=False
    ).reset_index()

    # Convert runners back to string for JSON serialization
    pivot_table["runners"] = pivot_table["runners"].astype(str)
    pivot_n_table["runners"] = pivot_n_table["runners"].astype(str)

    # Format runners column display
    runners_format = {
        "000": "---",
        "100": "1--",
        "010": "-2-",
        "001": "--3",
        "110": "12-",
        "101": "1-3",
        "011": "-23",
        "111": "123"
    }
    pivot_table["runners"] = pivot_table["runners"].map(runners_format)
    pivot_n_table["runners"] = pivot_n_table["runners"].map(runners_format)

    # Format runs_diff column labels
    new_column_labels = {}
    for col in pivot_table.columns:
        if isinstance(col, (int, np.integer)):
            if col < 0:
                new_column_labels[col] = "-4+" if col == -4 else str(col)
            elif col == 0:
                new_column_labels[col] = "Tie"
            else:
                new_column_labels[col] = "+4+" if col == 4 else f"+{col}"

    pivot_table = pivot_table.rename(columns=new_column_labels)
    pivot_n_table = pivot_n_table.rename(columns=new_column_labels)

    # Attach per-cell sample sizes with *_n keys for reliability markers.
    n_merge = pivot_n_table.copy()
    n_merge_columns = {
        col: f"{col}_n"
        for col in n_merge.columns
        if col not in ["inn_ct", "outs_ct", "runners"]
    }
    n_merge = n_merge.rename(columns=n_merge_columns)
    pivot_table = pivot_table.merge(n_merge, on=["inn_ct", "outs_ct", "runners"], how="left")

    # Replace NaN with None for JSON compatibility
    pivot_table = pivot_table.replace({np.nan: None})

    # Keep n fields as ints in JSON payload.
    for col in pivot_table.columns:
        if col.endswith("_n"):
            pivot_table[col] = pivot_table[col].apply(lambda x: int(x) if x is not None else None)

    # LI values stay as-is (not converted to percentage like WE)
    # Send raw values - frontend will format

    # Build result
    team_name = "Home" if team == 1 else "Away"

    # Get dynamic column headers (runs_diff labels), sorted: negative, Tie, positive.
    # Exclude *_n helper fields from visible headers but keep them in row payload.
    runs_diff_cols = [
        col for col in pivot_table.columns
        if col not in ["inn_ct", "outs_ct", "runners"] and not str(col).endswith("_n")
    ]
    runs_diff_cols = sorted(runs_diff_cols, key=lambda c: (0, 0) if c == "Tie" else (1, int(c[1:].replace('+', ''))) if c.startswith("+") else (-1, int(c.replace('+', ''))))
    n_cols = [f"{col}_n" for col in runs_diff_cols if f"{col}_n" in pivot_table.columns]
    pivot_table = pivot_table[["inn_ct", "outs_ct", "runners"] + runs_diff_cols + n_cols]
    headers = ["Inning", "Outs", "Runners"] + runs_diff_cols

    result = {
        "matrix_type": "LEVERAGE_INDEX",
        "from_season": from_season,
        "to_season": to_season,
        "team": team,
        "team_name": team_name,
        "total_events": total_events,
        "headers": headers,
        "rows": pivot_table.to_dict(orient='records')
    }

    return result


def generate_re24_transitions_table(
    data_store: dict,
    from_season: int,
    to_season: int,
    from_outs: int,
    event_cd: int,
    value_type: int
) -> dict:
    """
    Generate RE24 Transitions table showing state transitions.

    Args:
        data_store: Dictionary containing all data
        from_season: Starting season
        to_season: Ending season
        from_outs: Outs filter (0, 1, or 2)
        event_cd: Event code filter (-1 for All Events)
        value_type: 1 for ΔRun Value (avg_delta_run_value), 2 for Probability (avg_prob), 3 for Expected Run Value (avg_prob * avg_delta_run_value)

    Returns:
        Dictionary with table data ready for JSON response
    """
    # Get the re24_transitions dataframe
    df = data_store["re24_transitions"].copy()

    # Filter by season range
    df = df[
        (df["season"] >= from_season) &
        (df["season"] <= to_season)
    ]

    # Filter by from_outs
    df = df[df["from_outs"] == from_outs]

    # Filter by event_cd (including -1 for All Events which is pre-calculated)
    df = df[df["event_cd"] == event_cd]

    if len(df) == 0:
        raise ValueError(f"No data found for from_outs={from_outs}, event_cd={event_cd} in season range {from_season}-{to_season}")

    # For value_type=3, calculate expected run value (probability * run value)
    if value_type == 3:
        df['expected_run_value'] = df['avg_prob'] * df['avg_delta_run_value']

    # Drop columns we no longer need
    df = df.drop(columns=['season', 'event_cd'])

    # Transform runners using format dict
    runners_format = {
        "000": "---",
        "100": "1--",
        "010": "-2-",
        "001": "--3",
        "110": "12-",
        "101": "1-3",
        "011": "-23",
        "111": "123",
        "END": "END",
        "OFF": "OFF"
    }

    df['from_runners'] = df['from_runners'].map(runners_format)
    df['to_runners'] = df['to_runners'].map(runners_format)

    # Create to_state column
    def create_to_state(row):
        if row['to_runners'] in {'END', 'OFF'}:
            return row['to_runners']
        return f"{row['to_runners']} {row['to_outs']}"

    df['to_state'] = df.apply(create_to_state, axis=1)

    # Drop to_outs and to_runners
    df = df.drop(columns=['to_outs', 'to_runners', 'from_outs'])

    # Choose value column based on value_type
    if value_type == 1:
        value_column = 'avg_delta_run_value'
    elif value_type == 2:
        value_column = 'avg_prob'
    else:  # value_type == 3
        value_column = 'expected_run_value'

    # Aggregate sample sizes for reliability markers
    pivot_n_df = df.groupby(['from_runners', 'to_state'], observed=False).agg({
        'n': 'sum'
    }).reset_index()

    # Calculate weighted mean for single or multiple seasons
    if from_season == to_season:
        pivot_df = df[['from_runners', 'to_state', value_column]].copy()
    else:
        # Multiple seasons: calculate weighted mean
        grouped = media_ponderada_por_grupo(df, value_column, ['from_runners', 'to_state'])
        pivot_df = grouped[['from_runners', 'to_state', value_column]].copy()

    # Pivot wider
    pivot_table = pivot_df.pivot_table(
        index='from_runners',
        columns='to_state',
        values=value_column,
        aggfunc='first'
    )
    pivot_n_table = pivot_n_df.pivot_table(
        index='from_runners',
        columns='to_state',
        values='n',
        aggfunc='first'
    )

    # Define column order (all possible to_states)
    state_order = []
    for runners in ["---", "1--", "-2-", "--3", "12-", "1-3", "-23", "123"]:
        for outs_val in [0, 1, 2]:
            state_order.append(f"{runners} {outs_val}")
    state_order.append("END")
    state_order.append("OFF")

    # Reindex columns to include only existing ones in correct order
    existing_cols = [col for col in state_order if col in pivot_table.columns]
    pivot_table = pivot_table.reindex(columns=existing_cols)
    pivot_n_table = pivot_n_table.reindex(columns=existing_cols)

    # Define row order for from_runners
    runners_order = ["---", "1--", "-2-", "--3", "12-", "1-3", "-23", "123"]
    pivot_table = pivot_table.reindex(runners_order)
    pivot_n_table = pivot_n_table.reindex(runners_order)

    # Replace NaN with None for JSON compatibility
    pivot_table = pivot_table.replace({np.nan: None})

    # Remove columns that are entirely NULL
    cols_to_keep = []
    for col in pivot_table.columns:
        if pivot_table[col].notna().any():
            cols_to_keep.append(col)

    pivot_table = pivot_table[cols_to_keep]
    pivot_n_table = pivot_n_table[cols_to_keep]
    existing_cols = cols_to_keep

    # Pandas pivot may convert spaces to underscores in column names, convert them back
    pivot_table.columns = [col.replace('_', ' ') for col in pivot_table.columns]
    pivot_n_table.columns = [col.replace('_', ' ') for col in pivot_n_table.columns]
    existing_cols = list(pivot_table.columns)

    # Prepare display headers without altering raw column keys (preserves CSV/JSON)
    header_display_map = {
        "END": "Inn/Game<br>End",
        "OFF": "Walk<br>Off",
    }
    display_cols = [header_display_map.get(col, col) for col in existing_cols]

    # Get event name if event_cd was provided
    event_name = None
    if data_store.get("re24_event_types") is not None:
        event_types_df = data_store["re24_event_types"]
        event_row = event_types_df[event_types_df["event_cd"] == event_cd]
        if not event_row.empty:
            event_name = event_row.iloc[0]["event"]

    # Build result
    type_names = {1: "Run Value", 2: "Probability", 3: "Expected Run Value"}
    result = {
        "matrix_type": "RE24_TRANSITIONS",
        "from_season": from_season,
        "to_season": to_season,
        "event_type": event_name if event_name else "All Events",
        "type": type_names.get(value_type, "Run Value"),
        "outs": from_outs,
        "from_outs": from_outs,
        "event_cd": event_cd,
        "event_name": event_name,
        "value_type": value_type,
        "value_type_name": type_names.get(value_type, "Run Value"),
        "headers": ["From State"] + existing_cols,
        "headers_display": ["From State"] + display_cols,
        "to_state_columns": existing_cols,
        "rows": []
    }

    # Build rows - store raw values only
    for from_runners, row in zip(pivot_table.index, pivot_table.values, strict=True):
        row_data = {
            "from_runners": from_runners
        }

        # Add to_state values as raw (unformatted)
        for i, to_state in enumerate(existing_cols):
            value = row[i]
            if pd.notna(value):
                # Store raw value
                row_data[to_state] = float(value)
                # For probability values, they're already 0-1 range
                # For run expectancy, they're already the actual values
                if value_type == 2:  # Probability
                    # Store formatted display (2 decimals for percentage)
                    row_data[f"{to_state}_display"] = f"{float(value) * 100:.2f}"
                elif value_type == 1:  # RunValue
                    # Store formatted display (3 decimals for run expectancy)
                    row_data[f"{to_state}_display"] = f"{float(value):.3f}"
                else:  # value_type == 3 (Expected Run Value)
                    # Store formatted display (3 decimals for expected run value)
                    row_data[f"{to_state}_display"] = f"{float(value):.3f}"
            else:
                row_data[to_state] = None
                row_data[f"{to_state}_display"] = None

            n_value = pivot_n_table.loc[from_runners, to_state] if to_state in pivot_n_table.columns else np.nan
            row_data[f"{to_state}_n"] = int(n_value) if pd.notna(n_value) else None

        result["rows"].append(row_data)

    return result
