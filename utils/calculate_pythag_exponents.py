"""
Calculate Pythagorean Exponents from MLB team season data.

This script:
1. Connects to SQL Server and extracts team season data
2. Calculates pythagorean exponents by season (pooled leagues, game-weighted)
3. Generates final JSON output for the web app
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

# El nombre de este fichero empieza por digito y no se puede importar como
# modulo; anadir su directorio a sys.path permite compartir db.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli
import db
import seasons

# En Windows la consola usa cp1252 y redirigir la salida a un fichero reventaba
# con UnicodeEncodeError por los ✓ que imprime el script.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

# Misma instancia que reconfigura setup_logging() en main().
log = cli.get_logger()


QUERY_PYTHAG_INPUT = """
SELECT [season]
      ,[team]
      ,[team_full_name]
      ,[games_played]
      ,[wins]
      ,[losses]
      ,[ties]
      ,[runs_scored]
      ,[runs_allowed]
      ,[win_pct]
  FROM [dbo].[v_mlb_team_season_pythag_input]
 WHERE [season] BETWEEN {SEASON_MIN} AND {SEASON_MAX}
"""

QUERY_TOTAL_IP = """
SELECT
    [season],
    SUM(FLOOR([IP]) + (([IP] - FLOOR([IP])) * 10.0) / 3.0) AS Total_IP
FROM [dbo].[mlbPitching]
WHERE [season] BETWEEN {SEASON_MIN} AND {SEASON_MAX}
  AND team <> 'XXX'
  AND team IS NOT NULL
  AND team <> ''
GROUP BY [season];
"""


def get_connection():
    """
    Engine de SQLAlchemy hacia la base DGL (ver utils/db.py).

    Los servidores a probar se configuran con DGL_SQL_SERVERS en el .env.
    """
    return db.get_engine()

# ---------------------------------------------------------------------------
# Pythagorean calculations
# ---------------------------------------------------------------------------
def reconcile_games_played(df):
    """
    Deriva games_played y win_pct de los juegos efectivamente decididos.

    El view v_mlb_team_season_pythag_input calcula games_played como COUNT(*)
    sobre los juegos con statusCode='F', pero clasifica victoria/derrota
    comparando marcadores. Si un juego esta marcado como final y tiene el
    marcador en NULL, entra en games_played y no entra en W, L ni T: el
    denominador crece sin que crezca el numerador y el win_pct sale sesgado
    a la baja.

    Medido sobre 1901-2025: solo la temporada 2000 esta afectada, con 35 juegos
    sin marcador que dejan a 26 de sus 30 equipos descuadrados. El win_pct de la
    liga suma 14.801 en vez de 15.0 y el exponente de un ano se desplaza de
    1.9569 a 1.8882 (-0.069), un error mayor que la senal ano a ano que se
    quiere medir. Las ventanas moviles que contienen 2000 heredan el sesgo.

    runs_scored y runs_allowed vienen de SUM, que ignora los NULL, asi que ya
    son coherentes con los juegos decididos; solo hay que ajustar el conteo.

    Se corrige aqui y no en el view para no tocar la base de datos, y porque asi
    el calculo es robusto ante cualquier hueco futuro.
    """
    df = df.copy()
    decided = df["wins"] + df["losses"] + df["ties"]
    mismatch = df["games_played"] - decided

    affected = df[mismatch > 0]
    if len(affected):
        seasons = sorted(affected["season"].unique())
        log.info(
            f"Aviso: {len(affected)} fila(s) equipo-temporada con juegos sin clasificar "
            f"(marcador ausente). Temporadas: {', '.join(str(s) for s in seasons)}. "
            f"Total de juegos descartados del conteo: {int(mismatch[mismatch > 0].sum())}."
        )
        log.info("       Se recalculan games_played y win_pct a partir de W+L+T.")

    df["games_played"] = decided
    df["win_pct"] = (df["wins"] + 0.5 * df["ties"]) / df["games_played"].replace(0, np.nan)
    return df


def fit_pythag_exponent_weighted(group):
    """
    Fit the Pythagorean exponent by minimizing weighted squared error
    between actual win_pct (ties = half-wins) and predicted Pythagorean win_pct.

    argmin_x  sum( w_i * (win_pct_i - RS_i^x / (RS_i^x + RA_i^x))^2 )

    where w_i = games_played.

    (win_pct viene del view como (wins + 0.5*ties)/games_played, verificado con
    diferencia maxima 0 contra el recalculo, asi que "ties = half-wins" del
    enunciado se cumple de verdad y es coherente con el win_diff de mas abajo.)

    INCERTIDUMBRE. El ajuste estima UN parametro a partir de un punto por
    equipo, y la mediana historica es de 20 equipos por temporada (minimo 15 en
    1901). Error estandar por bootstrap sobre equipos, B=300:

        ventana    SE     IC95
          1 ano  0.126   +/- 0.25
          2 anos 0.091   +/- 0.18
          3 anos 0.076   +/- 0.15
          5 anos 0.060   +/- 0.12
          8 anos 0.049   +/- 0.10
         10 anos 0.045   +/- 0.09
         12 anos 0.040   +/- 0.08

    O sea que la columna de 1 ano tiene un intervalo de +/-0.25 sobre valores
    que van de 1.6 a 2.0: es practicamente no informativa por si sola. El
    aparente desplome de 1.796 (2024) a 1.627 (2025) da z=0.95, es decir ruido,
    no un cambio en como se convierten carreras en victorias.

    Conviene tenerlo presente antes de leer diferencias ano a ano en la tabla:
    las columnas de ventana larga son las que soportan interpretacion.
    """
    g = group[(group['runs_scored'] > 0) & (group['runs_allowed'] > 0)].copy()
    if len(g) < 2:
        return np.nan

    rs = g['runs_scored'].values.astype(float)
    ra = g['runs_allowed'].values.astype(float)
    wp = g['win_pct'].values.astype(float)
    w = g['games_played'].values.astype(float)

    def objective(x):
        rs_x = rs ** x
        pred = rs_x / (rs_x + ra ** x)
        return (w * (wp - pred) ** 2).sum()

    result = minimize_scalar(objective, bounds=(0.5, 3.5), method='bounded')
    return result.x if result.success else np.nan


def calculate_pythag_win_pct(rs, ra, exponent):
    """
    Win% = RS^x / (RS^x + RA^x)
    """
    rs_x = rs ** exponent
    ra_x = ra ** exponent
    return rs_x / (rs_x + ra_x)


def process_pythag(df):
    """
    Given the raw team-season DataFrame, fit Pythagorean exponents per season:
      - pythag_exponent      : single-season fit (pooled leagues, game-weighted)
      - pythag_exponent_2yr  : 2-year rolling fit
      - pythag_exponent_3yr  : 3-year rolling fit
      - pythag_exponent_5yr  : 5-year rolling fit
      - pythag_exponent_8yr  : 8-year rolling fit
      - pythag_exponent_10yr : 10-year rolling fit
    Expected wins/losses use the 3yr exponent when available, else single-season.
    """
    seasons = sorted(df['season'].unique())
    season_set = set(seasons)
    windows = [2, 3, 5, 8, 10, 12]

    # 1. Fit single-season and rolling exponents
    exponent_rows = []
    for season in seasons:
        # Single-season
        single_df = df[df['season'] == season]
        exp_1yr = fit_pythag_exponent_weighted(single_df)

        row = {'season': season, 'pythag_exponent': exp_1yr}

        for w in windows:
            col = f'pythag_exponent_{w}yr'
            # Se cuentan las temporadas REALMENTE presentes, no los anos
            # transcurridos. El gate anterior era 'season - min_season >= w - 1',
            # que da por completa una ventana de 5 anos aunque falten temporadas
            # dentro de ella. Hoy la serie 1901-2025 no tiene huecos y ambos
            # criterios coinciden, pero con este el sesgo es imposible en vez de
            # simplemente improbable.
            window_seasons = [s for s in range(season - w + 1, season + 1) if s in season_set]
            if len(window_seasons) == w:
                window_df = df[df['season'].isin(window_seasons)]
                row[col] = fit_pythag_exponent_weighted(window_df)
            else:
                row[col] = np.nan

        exponent_rows.append(row)

    exponents = pd.DataFrame(exponent_rows)

    # 2. Merge exponents back to team rows
    df = df.merge(exponents, on='season')

    # 2b. Pythagenpat exponent per team: ((RS + RA) / G) ^ 0.287
    df['pythag_exponent_pat'] = ((df['runs_scored'] + df['runs_allowed']) / df['games_played']) ** 0.287

    # 3. Per-team calculations — prefer 3yr exponent when available
    df['_exp'] = df['pythag_exponent_3yr'].fillna(df['pythag_exponent'])
    df['pythag_win_pct'] = df.apply(
        lambda r: calculate_pythag_win_pct(r['runs_scored'], r['runs_allowed'], r['_exp']),
        axis=1,
    )
    df['expected_wins'] = (df['pythag_win_pct'] * df['games_played']).round(1)
    df['expected_losses'] = (df['games_played'] - df['expected_wins']).round(1)
    df['adjusted_wins'] = df['wins'] + 0.5 * df['ties']
    df['win_diff'] = (df['adjusted_wins'] - df['expected_wins']).round(1)
    df.drop(columns=['_exp', 'adjusted_wins'], inplace=True)

    return df


# Columnas publicadas y ventana (en temporadas) de la que sale cada una.
# Pythagenpat es un promedio ponderado por temporada, asi que su ventana es 1.
WINDOW_BY_COLUMN = {
    '1 year': 1,
    '2 years': 2,
    '3 years': 3,
    '5 years': 5,
    '8 years': 8,
    '10 years': 10,
    '12 years': 12,
    'Pythagenpat': 1,
}


def build_window_sample_sizes(df_team):
    """
    Equipo-temporadas que alimentan el ajuste de cada columna y temporada.

    Es el tamano de muestra REAL de cada estimacion: el exponente de un ano se
    ajusta con un punto por equipo (30 hoy, 15 en 1901) y el de doce anos con
    doce veces mas. Publicarlo permite que la UI marque la confianza con la misma
    maquinaria que ya usa el resto de tablas, en vez de presentar la columna de
    un ano con el mismo peso visual que la de doce.

    Contrastado con el error estandar por bootstrap (B=300):

        ventana    n tipico (2025)    SE
          1 ano          30          0.126
          3 anos         90          0.076
         12 anos        361          0.040

    Devuelve un DataFrame indexado por season con una columna '<nombre>_n' por
    cada columna publicada. NaN donde la ventana no esta completa, igual que el
    propio exponente.
    """
    teams_per_season = df_team.groupby('season').size()
    seasons = sorted(teams_per_season.index)
    season_set = set(seasons)

    rows = []
    for season in seasons:
        row = {'season': season}
        for col, window in WINDOW_BY_COLUMN.items():
            in_window = [s for s in range(season - window + 1, season + 1) if s in season_set]
            # Misma condicion que usa el ajuste: la ventana debe estar completa.
            row[f'{col}_n'] = int(teams_per_season.loc[in_window].sum()) if len(in_window) == window else np.nan
        rows.append(row)

    return pd.DataFrame(rows)


def save_pythag_data(df, df_team, data_dir=None):
    """Save pythagorean exponent data to parquet in app/data/."""
    # data_dir es un parametro con valor por defecto y no una ruta fija dentro,
    # para poder escribir a un directorio temporal en los tests. En produccion
    # nadie lo pasa y el comportamiento es el mismo de siempre.
    data_dir = Path(data_dir) if data_dir is not None else Path(__file__).parent.parent / "app" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # --- Season-level exponents (one row per season) ---
    exp_cols = ['season', 'pythag_exponent', 'pythag_exponent_2yr',
                'pythag_exponent_3yr', 'pythag_exponent_5yr',
                'pythag_exponent_8yr', 'pythag_exponent_10yr', 'pythag_exponent_12yr']
    df_exponents = df[exp_cols].drop_duplicates().sort_values('season').reset_index(drop=True)

    # JUEGOS DE LA TEMPORADA, publicados. Se suman los de los equipos y se dividen
    # entre dos, porque cada partido aparece en los dos.
    #
    # Existe para poder VIGILAR la cobertura desde fuera. La vista filtraba
    # `statusCode IN ('F','FT')` y dejaba fuera 176 partidos jugados en 42
    # temporadas --'FR' de lluvia, 'FO', 'FG', 'RV'--; 2025 salia con 2.425 juegos
    # en vez de 2.430. Nada fallaba: los exponentes se calculaban sobre temporadas
    # incompletas y no habia forma de notarlo desde el parquet.
    juegos = (df.groupby('season')['games_played'].sum() / 2).round().astype('int32')
    df_exponents = df_exponents.merge(
        juegos.rename('games_played').reset_index(), on='season', how='left')

    # Season-level Pythagenpat: weighted average of per-team ((RS+RA)/G)^0.287 by games_played
    pat_avg = (df.groupby('season')
               .apply(lambda g: np.average(g['pythag_exponent_pat'], weights=g['games_played']), include_groups=False)
               .reset_index()
               .rename(columns={0: 'pythag_exponent_pat'}))
    df_exponents = df_exponents.merge(pat_avg, on='season')

    # --- Structure: one row per season with Pythagenpat as a separate column ---
    df_final = df_exponents[['season', 'games_played', 'pythag_exponent', 'pythag_exponent_2yr',
                              'pythag_exponent_3yr', 'pythag_exponent_5yr',
                              'pythag_exponent_8yr', 'pythag_exponent_10yr',
                              'pythag_exponent_12yr', 'pythag_exponent_pat']].copy()

    df_final.rename(columns={
        'pythag_exponent': '1 year',
        'pythag_exponent_2yr': '2 years',
        'pythag_exponent_3yr': '3 years',
        'pythag_exponent_5yr': '5 years',
        'pythag_exponent_8yr': '8 years',
        'pythag_exponent_10yr': '10 years',
        'pythag_exponent_12yr': '12 years',
        'pythag_exponent_pat': 'Pythagenpat'
    }, inplace=True)

    value_cols = ['1 year', '2 years', '3 years', '5 years', '8 years', '10 years', '12 years', 'Pythagenpat']
    df_final = df_final[['season', 'games_played'] + value_cols]
    df_final.sort_values('season', inplace=True)
    df_final.reset_index(drop=True, inplace=True)

    # Tamano de muestra de cada ajuste, para que la UI pueda senalar la confianza.
    df_final = df_final.merge(build_window_sample_sizes(df_team), on='season', how='left')

    # Type casting
    df_final['season'] = df_final['season'].astype('int32')
    df_final['games_played'] = df_final['games_played'].astype('int32')
    for col in value_cols:
        df_final[col] = df_final[col].astype('float32')
        # Nullable: la ventana incompleta no tiene muestra, igual que no tiene valor.
        df_final[f'{col}_n'] = df_final[f'{col}_n'].astype('Int32')

    df_final = df_final[['season', 'games_played'] + value_cols + [f'{c}_n' for c in value_cols]]

    output_file = data_dir / "pythag_exponents.parquet"
    df_final.to_parquet(output_file, index=False, engine='pyarrow')
    log.info(f"Guardadas {len(df_final):,} filas en {output_file.name}")

    return df_final


def save_rpw_data(df_exponents, df_team, df_ip, data_dir=None):
    """Calculate and save Runs Per Win data to parquet in app/data/."""
    # data_dir es un parametro con valor por defecto y no una ruta fija dentro,
    # para poder escribir a un directorio temporal en los tests. En produccion
    # nadie lo pasa y el comportamiento es el mismo de siempre.
    data_dir = Path(data_dir) if data_dir is not None else Path(__file__).parent.parent / "app" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Per-season totals for rolling RPG calculation
    season_totals = (df_team.groupby('season')
                     .agg(total_runs=('runs_scored', lambda g: g.sum() + df_team.loc[g.index, 'runs_allowed'].sum()),
                          total_games=('games_played', 'sum'))
                     .reset_index()
                     .sort_values('season'))

    # Build rolling-window RPG for each window size, matching exponent windows
    seasons = sorted(season_totals['season'].unique())
    season_set = set(seasons)
    window_map = {
        '1 year': 1, '2 years': 2, '3 years': 3, '5 years': 5,
        '8 years': 8, '10 years': 10, '12 years': 12
    }

    rpg_rows = []
    for season in seasons:
        row = {'season': season}
        for col_name, w in window_map.items():
            # Mismo criterio que process_pythag: se cuentan las temporadas
            # presentes, no los anos transcurridos, para que un hueco no de por
            # completa una ventana que no lo esta.
            window_seasons = [s for s in range(season - w + 1, season + 1) if s in season_set]
            if len(window_seasons) == w:
                window = season_totals[season_totals['season'].isin(window_seasons)]
                row[f'rpg_{col_name}'] = window['total_runs'].sum() / window['total_games'].sum()
            else:
                row[f'rpg_{col_name}'] = np.nan
        rpg_rows.append(row)

    df_rpg = pd.DataFrame(rpg_rows)

    # Merge exponents with rolling RPGs and IP
    df_rpw = df_exponents.merge(df_rpg, on='season')
    df_rpw = df_rpw.merge(df_ip, on='season', how='left')

    # Total RS per season (for FanGraphs formula: 9 * (Total_RS / Total_IP) * 1.5 + 3)
    league_rs = (df_team.groupby('season')['runs_scored'].sum()
                 .reset_index()
                 .rename(columns={'runs_scored': 'total_rs'}))
    df_rpw = df_rpw.merge(league_rs, on='season')

    # Pythagorean derivative RPW = 2 * RPG / exponent for each column
    # Each column uses the matching rolling-window RPG
    exp_cols = ['1 year', '2 years', '3 years', '5 years', '8 years', '10 years', '12 years']
    for col in exp_cols:
        df_rpw[col] = 2.0 * df_rpw[f'rpg_{col}'] / df_rpw[col]

    # Pythagenpat: use single-season RPG (Pythagenpat is per-season weighted avg)
    df_rpw['Pythagenpat'] = 2.0 * df_rpw['rpg_1 year'] / df_rpw['Pythagenpat']

    # FanGraphs RPW = 9 * (Total_RS / Total_IP) * 1.5 + 3
    df_rpw['FanGraphs'] = 9.0 * (df_rpw['total_rs'] / df_rpw['Total_IP']) * 1.5 + 3.0

    # Select final columns
    all_cols = exp_cols + ['Pythagenpat']
    value_cols = all_cols + ['FanGraphs']

    # Cada RPW se deriva del exponente de su misma ventana, asi que hereda su
    # tamano de muestra. Esas columnas ya vienen en df_exponents.
    # FanGraphs no usa el exponente: sale de los totales de UNA temporada, asi
    # que su muestra es la misma que la de la ventana de un ano.
    df_rpw['FanGraphs_n'] = df_rpw['1 year_n']

    df_final = df_rpw[['season'] + value_cols + [f'{c}_n' for c in value_cols]].copy()
    df_final.sort_values('season', inplace=True)
    df_final.reset_index(drop=True, inplace=True)

    # Type casting
    df_final['season'] = df_final['season'].astype('int32')
    for col in value_cols:
        df_final[col] = df_final[col].astype('float32')
        df_final[f'{col}_n'] = df_final[f'{col}_n'].astype('Int32')

    output_file = data_dir / "runs_per_win.parquet"
    df_final.to_parquet(output_file, index=False, engine='pyarrow')
    log.info(f"Guardadas {len(df_final):,} filas en {output_file.name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _cli(argv=None):
    parser = cli.base_parser(
        "Ajusta los exponentes pitagoricos por temporada y ventana movil, "
        "y deriva las carreras por victoria."
    )
    args = parser.parse_args(argv)
    cli.setup_logging(verbose=args.verbose, quiet=args.quiet, log_file=args.log_file)
    cli.aplicar_temporadas(args)
    run()
    return 0


def run():
    seasons.validar()
    lo, hi = seasons.rango()
    log.info(f"Temporadas: {lo}-{hi}")

    def acotada(sql: str) -> str:
        return (sql.replace("{SEASON_MIN}", str(int(seasons.MIN_SEASON)))
                   .replace("{SEASON_MAX}", str(int(seasons.MAX_SEASON))))

    engine = get_connection()
    df = pd.read_sql(acotada(QUERY_PYTHAG_INPUT), engine)
    df_ip = pd.read_sql(acotada(QUERY_TOTAL_IP), engine)
    db.dispose_engine()

    # Recorte de garantia, ademas del de SQL.
    #
    # OJO CON EL SUELO. Las clasificaciones existen desde 1901 y son datos
    # buenos: este ajuste NO depende de Retrosheet ni del detalle jugada a
    # jugada, asi que tecnicamente podria llegar nueve temporadas mas atras que
    # el resto. Se acota igualmente a MIN_SEASON por decision explicita, para que
    # todas las tablas del sitio digan el mismo rango -- que es ademas el que
    # anuncia la portada. Si algun dia se quieren esas nueve temporadas de
    # vuelta, se baja MIN_SEASON y se rehace; no hay nada mas que tocar.
    for nombre, datos in (("pythag", df), ("IP", df_ip)):
        _, descartadas = seasons.recortar(datos, "season")
        if descartadas:
            log.info(f"  {nombre}: {descartadas:,} filas fuera de {lo}-{hi} descartadas")
    df, _ = seasons.recortar(df, "season")
    df_ip, _ = seasons.recortar(df_ip, "season")

    # Descarta del conteo los juegos que el view no pudo clasificar.
    df = reconcile_games_played(df)

    # Run calculations
    df = process_pythag(df)

    # Save output files
    df_exponents = save_pythag_data(df, df_team=df)
    save_rpw_data(df_exponents, df, df_ip)


if __name__ == "__main__":
    raise SystemExit(_cli())
