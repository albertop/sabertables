"""
Calculate Break-Even Points and Sac Bunt RE Impact from RE24 and RE288 data.

This script reads the RE24 values parquet file (event_cd == -1, the "All Events"
run expectancy matrix) and the RE288 values parquet file (base-out × count),
and produces three parquet files:

1) breakeven_points.parquet — Break-Even Points
    Columns: season (int32), type (string), scenario (string), outs (uint8),
                breakeven_pct (float32), n (int32), label (string, optional)

    NOTE: This parquet is a unified table of multiple analysis families.
    Column semantics vary by type (for example, scenario/outs meaning differs by
    analysis). Consumers must interpret rows using the type field.

   NOTE on the n column: n is the frequency of the base-out state in the run
   expectancy matrix — i.e. how often that (season, outs, runners) combination
   occurred across all plate appearances. It is NOT the number of actual
   opportunities for the specific event (e.g. steal attempts, tag-up chances).
   It is useful as a stability/reliability indicator for the underlying RE value,
   but should not be interpreted as event-opportunity volume.

    type = "stealing" — Stolen Base Break-Even (4 scenarios × 3 out states per season)
     a. Runner on 1B steals 2B (no other runners)
        Before: 100  |  SB success: 010  |  CS: 000 + 1 out
     b. Runner on 2B steals 3B (no other runners)
        Before: 010  |  SB success: 001  |  CS: 000 + 1 out
     c. Runner on 1B steals 2B with runner on 3B
        Before: 101  |  SB success: 011  |  CS: 001 + 1 out
      d. Double steal — runners on 1B & 2B (weighted failure mix)
          Before: 110  |  SB success: 011  |  CS mix: 75% lead caught (010 + 1 out),
                                                                    25% trail caught (001 + 1 out)
     Formula: BE% = (RE_before - RE_CS) / (RE_SB - RE_CS)

   type = "running" — Aggressive Running Break-Even (6 scenarios × 3 out states per season)
     Before states represent the post-hit baseline: the batter has already
     safely reached base, and the runner is deciding whether to take the
     extra base. These are NOT pre-pitch states.
     For example, "Runner on 2B scores on a single" uses Before: 101 — that
     means the batter has singled to 1B and the runner from 2B is now deciding
     whether to try for home. The 101 state is the decision point, not the
     lineup configuration before the pitch.
     a. Single stretched to double
        Before: 100  |  Success: 010  |  Fail: 000 + 1 out
     b. Double stretched to triple
        Before: 010  |  Success: 001  |  Fail: 000 + 1 out
     c. Triple stretched to home run (runner scores → +1 run)
        Before: 001  |  Success: 000 + 1 run  |  Fail: 000 + 1 out
     d. 1B to 3B on a single (batter on 1B, runner deciding at 2B→3B)
        Before: 110  |  Success: 101  |  Fail: 100 + 1 out
     e. Runner on 2B scores on a single (batter on 1B, runner deciding at 3B→home)
        Before: 101  |  Success: 100 + 1 run  |  Fail: 100 + 1 out
     f. Runner on 1B scores on a double (batter on 2B, runner deciding at 3B→home)
        Before: 011  |  Success: 010 + 1 run  |  Fail: 010 + 1 out
     Formula: BE% = (RE_before - RE_fail) / (bonus + RE_success - RE_fail)
              where bonus = 1 if a run scores, else 0.

    type = "tagging" — Tag-Up Break-Even (3 scenarios × 2 out states per season)
     NOTE: n for tag-up rows is the base-out state frequency before the fly
     ball, not the number of actual fly-ball/tag-up opportunities.

   When outs == 2 and failure occurs, the inning ends → RE_fail = 0.

    2) re_impact.parquet — Run Expectancy Impact by Event Type
    Unified parquet for all RE-impact analyses, distinguished by the type column.
    Columns: season (int32), type (string), scenario (string), label (string),
                outs (uint8), delta_re (Float32, nullable), n (int32),
                baseline_re (Float32, nullable), delta_re_pct (Float32, nullable),
                                platoon (boolean, nullable), handedness_split (string, nullable),
                                weighted_mean_outs (Float32, nullable)

        type values currently include:
            - sac_bunt
            - ibb
            - ph_position
            - ph_pitcher
            - platoon_baseline

    type = "ibb" — Intentional Walk Run Impact
     3 scenarios × 3 out states per season (where data exists)
     Uses real per-event run values from re24_values.parquet (event_cd == 15).
     Scenarios:
       a. Runner on 3B — 001 → 101
       b. Runner on 2B — 010 → 110 (most common IBB situation)
       c. Runners on 2B & 3B — 011 → 111

     IMPORTANTE sobre run_value en re24_values.parquet: para las filas de evento
     esa columna es la RE ABSOLUTA posterior al evento (RE del estado resultante
     mas las carreras anotadas en la jugada), no el cambio de RE. Verificado:
     HR (event_cd=23) desde 0 outs/000 vale 1.5416 ≈ 1 carrera + RE(0,000)=0.5004,
     y ponche (event_cd=3) vale 0.2520 ≈ RE(1,000)=0.2681.
     Por eso delta_re = run_value − RE(estado inicial). Publicar run_value crudo
     como si fuera un delta inflaba la cifra hasta 50× y forzaba el signo a ser
     siempre positivo.

     delta_re es el cambio medio de RE causado por las IBB de esa celda
     (season, outs, runners). Positivo = la IBB costo carreras al equipo
     defensivo; negativo = le salio bien.
     n es el numero real de IBB en la celda; se descartan las celdas con
     n < MIN_IBB_N porque el promedio de 1-5 eventos es ruido puro.

     type = "ph_position" / "ph_pitcher" — Pinch-Hit Run Impact (pre-aggregated from SQL views)
         Up to 8 base-state scenarios × 3 out states × 2 platoon values per season per PH type.
         Each (season, base-state, outs) cell stratified by:
             - platoon: parsed from handness in the SQL views (Platoon vs Same)
             - PH type: ph_pitcher (pitcher replacement view) vs ph_position (position-player replacement view)
         Source views:
                 SELECT [season], [outs], [runners], [handness], [n], [delta_run_value]
                 FROM [dbo].[v_re_analysis_ph_position_players]

                 SELECT [season], [outs], [runners], [handness], [n], [delta_run_value]
                 FROM [dbo].[v_re_analysis_ph_pitchers]
         n is the pre-aggregated event count from the SQL views.

         Operational note: PH generation now depends on these SQL views being reachable
         at runtime (no parquet fallback path).

3) sb_breakeven_by_count.parquet — SB Break-Even by Pitch Count (RE288-based)
   4 SB scenarios × 12 counts × 3 out states per season (1989–present)
   Columns: season (int32), scenario (string), count (string), outs (uint8),
            breakeven_pct (float32), n (int32)

   Same formula as RE24 stealing, but keyed on (season, outs, runners, count).
   After a SB or CS, the count stays the same (same at-bat continues).
   n is the frequency of the (season, outs, runners, count) state in the RE288
   matrix, not actual steal-attempt volume.
"""

import os
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

# El nombre de este fichero empieza por digito, asi que no se puede importar como
# modulo; se ejecuta siempre como script. Anadir su propio directorio a sys.path
# permite compartir db.py y que el modulo siga siendo cargable por ruta desde los
# tests.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from datetime import UTC

import cli
import db
import seasons

# En Windows la consola usa cp1252 y redirigir la salida a un fichero reventaba
# con UnicodeEncodeError por los simbolos que imprime el script.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

# logging.getLogger devuelve siempre la misma instancia para un nombre dado, asi
# que setup_logging() en main() reconfigura ESTE mismo objeto: se puede tomar la
# referencia al importar sin perder la configuracion posterior.
log = cli.get_logger()


# Stolen Base scenarios: (label, before_state, sb_success_state, cs_state)
SB_SCENARIOS = [
    ("Runner on 1B steals 2B",                              "100", "010", "000"),
    ("Runner on 2B steals 3B",                              "010", "001", "000"),
    ("Runners on 1B & 3B, runner on 1B steals 2B",         "101", "011", "001"),
    ("Double steal",                                        "110", "011", "010"),
]

# Reparto del doble robo fallido entre "atrapan al de delante" y "atrapan al de
# atras". Antes era 0.75 sin fuente.
#
# Medido sobre dbo.r24Transitions, todas las temporadas: transiciones con
# event_cd=6 (caught stealing) desde el estado 110 que suman un out.
#
#     to_runners='010'  1,897  el de 2B es atrapado en 3B, el de 1B llega a 2B
#     to_runners='001'    301  el de 1B es atrapado en 2B, el de 2B llega a 3B
#     -> peso del de delante = 1897 / (1897 + 301) = 0.863
#
# Quedan fuera a proposito otras dos transiciones frecuentes desde 110:
#   to='100' (26.5%) es el corredor de 2B robando SOLO, no un doble robo.
#   to='END' (27.4%) es el tercer out, que el codigo ya trata como RE=0.
#
# El reparto es estable por decadas (0.79-0.96) y casi igual con 0 y 1 out
# (0.885 vs 0.855, ~1.9 sigma), asi que se usa un unico valor global.
# Para volver a medirlo basta reejecutar esa consulta sobre r24Transitions.
#
# Sensibilidad: pasar de 0.75 a 0.863 mueve el break-even como mucho +0.013 y
# de media +0.004. Con 2 outs no cambia nada, porque el CS termina la entrada y
# RE=0 sea cual sea el corredor atrapado. Con 1 out el efecto es de solo +0.002
# porque RE(2out,010) y RE(2out,001) son casi iguales. Es decir: el parametro
# estaba mal, pero el analisis nunca fue muy sensible a el.
DOUBLE_STEAL_LEAD_CAUGHT_WEIGHT = float(os.getenv("DOUBLE_STEAL_LEAD_CAUGHT_WEIGHT", "0.863"))
DOUBLE_STEAL_TRAIL_CAUGHT_STATE = "001"

# Running (aggressive baserunning) scenarios:
# before_state = post-hit baseline (batter safely on base) before the extra-base attempt.
# (label, before_state, success_state, failure_state, scores_run)

# Tag-up scenarios: (label, runners_state, success_state, failure_state, scores_run)
# Fly ball is caught (+1 out), runner decides whether to tag up.
# Stay = runners_state at outs+1 | Success = success_state at outs+1 | Fail = failure_state at outs+2
# Only valid at 0 and 1 out (fly ball at 2 outs ends the inning).

# IBB scenarios: (label, runners_before)
# The IBB always puts a runner on 1B, shifting existing runners up if forced.
# Real per-event run values are read from re24_values.parquet (event_cd == 15).
# No theoretical transition is computed — delta_re comes from the actual data.
IBB_SCENARIOS = [
    ("Runner on 3B",              "001"),   # 001 → 101
    ("Runner on 2B",              "010"),   # 010 → 110  (most common)
    ("Runners on 2B & 3B",        "011"),   # 011 → 111
]

# Sac bunt RE impact scenarios: (label, before_state, after_state)
# Batter sacrifices: runner(s) advance one base, batter is out.
# Only computed at 0 and 1 outs (bunting with 2 outs is strategically irrelevant).
# delta_re = RE(after_state, outs+1) − RE(before_state, outs)
SAC_BUNT_SCENARIOS = [
    ("Runner on 1B",           "100", "010"),  # 100 → 010, outs+1
    ("Runner on 2B",           "010", "001"),  # 010 → 001, outs+1
    ("Runners on 1B & 2B",     "110", "011"),  # 110 → 011, outs+1
]

# Los OCHO estados de corredores posibles. No hay mas: tres bases, ocupada o no.
RUNNERS_CANONICOS = frozenset({"000", "100", "010", "001", "110", "101", "011", "111"})


def _normalizar_runners(serie: pd.Series, origen: str) -> pd.Series:
    """
    Rellena a tres digitos y VALIDA contra el dominio canonico.

    El relleno hace falta porque SQL puede devolver el estado como numero: `1`
    es en realidad `001`, corredor en TERCERA, y sin rellenar se buscaria una
    clave que no existe.

    La validacion hace falta porque estos estados son la LLAVE de RE24 y RE288.
    Uno mal normalizado no da error: contamina el lookup, y con el los
    escenarios de tag-up, swing/take, platoon y vueltas al orden. El resultado
    sale plausible y describe otra situacion de juego. Mejor abortar la
    generacion que publicar eso.
    """
    salida = serie.astype(str).str.strip().str.zfill(3)

    malos = sorted(set(salida) - RUNNERS_CANONICOS)
    if malos:
        raise ValueError(
            f"{origen}: estados de corredores fuera del dominio canonico: "
            f"{', '.join(malos[:10])}. Solo existen "
            f"{', '.join(sorted(RUNNERS_CANONICOS))}."
        )
    return salida


RUNNERS_LABELS = {
    "000": "Empty",
    "001": "3rd",
    "010": "2nd",
    "011": "2nd & 3rd",
    "100": "1st",
    "101": "1st & 3rd",
    "110": "1st & 2nd",
    "111": "Loaded",
}

QUERY_PH_POSITION_PLAYERS = """
SELECT [season], [outs], [handedness_bucket], [n], [delta_run_value]
FROM [dbo].[v_re_analysis_ph_position_players]
"""

QUERY_PH_PITCHERS = """
SELECT [season], [outs], [handedness_bucket], [n], [delta_run_value]
FROM [dbo].[v_re_analysis_ph_pitchers]
"""

QUERY_RE_IMPACT_PLATOON_BASELINE = """
SELECT [season]
      ,[outs]
      ,[handedness_split]
      ,[n]
      ,[delta_run_value]
      ,[baseline_avg_run_value]
      ,[delta_run_value_scaled]
  FROM [dbo].[v_re_analysis_platoon_impact_final]
"""

QUERY_RE_IMPACT_TTO = """
SELECT [season]
            ,[runners]
            ,[handedness_split]
            ,[tto_pass]
            ,[n]
            ,[delta_run_value]
            ,[delta_run_value_scaled]
    FROM [dbo].[v_re_analysis_tto_impact_final_same_platoon_no_outs]
"""

QUERY_ADVANCE = """
SELECT [season], [outs], [direction], [bucket], [n], [mean_rv], [sd_rv]
FROM [dbo].[v_re_analysis_advance]
"""

QUERY_STRETCH = """
SELECT [season], [outs], [direction], [bucket], [n], [mean_rv], [sd_rv]
FROM [dbo].[v_re_analysis_stretch]
"""

QUERY_TAG_UP = """
SELECT [season], [outs], [runners], [direction], [bucket], [n], [mean_rv], [sd_rv]
FROM [dbo].[v_re_analysis_tag_up]
"""

QUERY_GIDP = """
SELECT [season], [outs], [runners], [es_dp], [tipo_batazo], [runners_fin],
       [outs_fin], [carreras], [n]
FROM [dbo].[v_re_analysis_gidp]
"""

QUERY_BUNT = """
SELECT [season], [outs], [scenario], [bucket], [n], [mean_rv], [sd_rv]
FROM [dbo].[v_re_analysis_bunt]
"""

QUERY_BUNT_BATTER_EFFECT = """
SELECT [season], [batter_id], [n_pa], [effect]
FROM [dbo].[v_re_analysis_bunt_batter_effect]
"""

QUERY_BUNT_WEIGHTS = """
SELECT [season], [outs], [scenario], [batter_id], [n_bunts], [lg_rv]
FROM [dbo].[v_re_analysis_bunt_weights]
"""

# Agregado en el servidor, no en pandas: la vista tiene ~12.8M filas a nivel de
# lanzamiento y antes se traian enteras para hacer el GROUP BY en memoria.
# Se devuelven ~15k filas (una por celda x resultado) con media, desviacion y n,
# que es todo lo que necesita el break-even y su prueba de significancia.
QUERY_SWING_TAKE_AGG = """
SELECT
    [season],
    [balls],
    [strikes],
    [outs],
    [runners],
    [pitch_outcome],
    AVG(CAST([delta_run_exp] AS float)) AS ev,
    STDEV(CAST([delta_run_exp] AS float)) AS sd,
    COUNT_BIG(*) AS n
FROM [dbo].[v_re_analysis_swing_take]
WHERE [season] <= {season_cap}
  AND [delta_run_exp] IS NOT NULL
GROUP BY [season], [balls], [strikes], [outs], [runners], [pitch_outcome]
"""

QUERY_SWING_TAKE_ZONE = """
WITH pitch_base AS (
    SELECT
        [season],
        [zone],
        CASE
            WHEN [p_throws] = 'R' AND [stand] = 'R' THEN 'RHP vs RHB'
            WHEN [p_throws] = 'L' AND [stand] = 'R' THEN 'LHP vs RHB'
            WHEN [p_throws] = 'R' AND [stand] = 'L' THEN 'RHP vs LHB'
            WHEN [p_throws] = 'L' AND [stand] = 'L' THEN 'LHP vs LHB'
        END AS handedness_split,
        CONCAT(CAST([balls] AS varchar(1)), '-', CAST([strikes] AS varchar(1))) AS [count],
        -- swing_outcome trae los cuatro desenlaces ya separados desde la vista.
        -- Antes se derivaba de is_contact, que agrupa el foul CON la bola en
        -- juego, mientras que la familia por conteo lo agrupaba con el abanico:
        -- las dos tablas de swing/take de la app respondian preguntas distintas.
        [swing_outcome] AS outcome_bucket,
        CAST([delta_run_exp] AS float) AS delta_run_exp
    FROM [dbo].[v_re_analysis_swing_take_zone]
        WHERE [zone] IN (1,2,3,4,5,6,7,8,9,11,12,13,14)
      AND [p_throws] IN ('L', 'R')
      AND [stand] IN ('L', 'R')
      AND [balls] BETWEEN 0 AND 3
      AND [strikes] BETWEEN 0 AND 2
      AND [delta_run_exp] IS NOT NULL
),
bucketed AS (
    SELECT
        [season],
        [zone],
        handedness_split,
        [count],
        MAX(CASE WHEN outcome_bucket = 'contact' THEN avg_delta END) AS ev_contact,
        MAX(CASE WHEN outcome_bucket = 'foul'    THEN avg_delta END) AS ev_foul,
        MAX(CASE WHEN outcome_bucket = 'whiff'   THEN avg_delta END) AS ev_whiff,
        MAX(CASE WHEN outcome_bucket = 'take'    THEN avg_delta END) AS ev_take,
        MAX(CASE WHEN outcome_bucket = 'contact' THEN sd_delta END) AS sd_contact,
        MAX(CASE WHEN outcome_bucket = 'whiff'   THEN sd_delta END) AS sd_whiff,
        MAX(CASE WHEN outcome_bucket = 'contact' THEN bucket_n END) AS n_contact,
        MAX(CASE WHEN outcome_bucket = 'foul'    THEN bucket_n END) AS n_foul,
        MAX(CASE WHEN outcome_bucket = 'whiff'   THEN bucket_n END) AS n_whiff,
        MAX(CASE WHEN outcome_bucket = 'take'    THEN bucket_n END) AS n_take
    FROM (
        SELECT
            [season],
            [zone],
            handedness_split,
            [count],
            outcome_bucket,
            AVG(delta_run_exp) AS avg_delta,
            STDEV(delta_run_exp) AS sd_delta,
            COUNT(*) AS bucket_n
        FROM pitch_base
        WHERE handedness_split IS NOT NULL
          AND outcome_bucket IS NOT NULL
        GROUP BY [season], [zone], handedness_split, [count], outcome_bucket
    ) src
    GROUP BY [season], [zone], handedness_split, [count]
),
season_count_be AS (
    -- Mismo modelo de tres desenlaces que la version sin zona:
    --
    --   p_c* = [EV_take - p_f*EV_foul - (1-p_f)*EV_whiff] / (EV_contact - EV_whiff)
    --
    -- donde p_f es la fraccion de SWINGS que acaban en foul. El denominador es
    -- el recorrido entre bola en juego y abanico en falso, que es grande y
    -- estable; el anterior (EV_contact - EV_miss) se degeneraba y disparaba el
    -- resultado en conteos de bateador.
    --
    -- El break-even solo esta definido cuando ese denominador es
    -- SIGNIFICATIVAMENTE positivo. La guarda original era
    -- ABS(ev_contact - ev_miss) <= 1e-12, que dejaba pasar denominadores
    -- negativos y producia break-evens sin lectura sabermetrica.
    SELECT
        [season],
        [zone],
        handedness_split,
        [count],
        CASE
            WHEN ev_contact IS NULL OR ev_foul IS NULL OR ev_whiff IS NULL OR ev_take IS NULL THEN NULL
            WHEN sd_contact IS NULL OR sd_whiff IS NULL THEN NULL
            WHEN n_contact < {min_bucket_n} OR n_foul < {min_bucket_n}
              OR n_whiff < {min_bucket_n} OR n_take < {min_bucket_n} THEN NULL
            WHEN (ev_contact - ev_whiff)
                 / NULLIF(SQRT(SQUARE(sd_contact) / n_contact
                             + SQUARE(sd_whiff) / n_whiff), 0) < {min_z} THEN NULL
            ELSE (
                    ev_take
                  - (1.0 * n_foul / (n_contact + n_foul + n_whiff)) * ev_foul
                  - (1.0 - 1.0 * n_foul / (n_contact + n_foul + n_whiff)) * ev_whiff
                 ) / (ev_contact - ev_whiff)
        END AS be_pct
    FROM bucketed
)
SELECT
    [season],
    [zone],
    handedness_split,
    MAX(CASE WHEN [count] = '0-2' THEN be_pct END) AS [c_0_2],
    MAX(CASE WHEN [count] = '1-2' THEN be_pct END) AS [c_1_2],
    MAX(CASE WHEN [count] = '0-1' THEN be_pct END) AS [c_0_1],
    MAX(CASE WHEN [count] = '2-2' THEN be_pct END) AS [c_2_2],
    MAX(CASE WHEN [count] = '1-1' THEN be_pct END) AS [c_1_1],
    MAX(CASE WHEN [count] = '0-0' THEN be_pct END) AS [c_0_0],
    MAX(CASE WHEN [count] = '1-0' THEN be_pct END) AS [c_1_0],
    MAX(CASE WHEN [count] = '2-1' THEN be_pct END) AS [c_2_1],
    MAX(CASE WHEN [count] = '3-2' THEN be_pct END) AS [c_3_2],
    MAX(CASE WHEN [count] = '2-0' THEN be_pct END) AS [c_2_0],
    MAX(CASE WHEN [count] = '3-1' THEN be_pct END) AS [c_3_1],
    MAX(CASE WHEN [count] = '3-0' THEN be_pct END) AS [c_3_0]
FROM season_count_be
GROUP BY [season], [zone], handedness_split
ORDER BY
    [season],
    [zone],
    CASE handedness_split
        WHEN 'RHP vs RHB' THEN 1
        WHEN 'LHP vs LHB' THEN 2
        WHEN 'RHP vs LHB' THEN 3
        WHEN 'LHP vs RHB' THEN 4
        ELSE 99
    END
"""

QUERY_SWING_TAKE_ZONE_N = """
SELECT
    [season],
    [zone],
    CASE
        WHEN [p_throws] = 'R' AND [stand] = 'R' THEN 'RHP vs RHB'
        WHEN [p_throws] = 'L' AND [stand] = 'R' THEN 'LHP vs RHB'
        WHEN [p_throws] = 'R' AND [stand] = 'L' THEN 'RHP vs LHB'
        WHEN [p_throws] = 'L' AND [stand] = 'L' THEN 'LHP vs LHB'
    END AS handedness_split,
    CONCAT(CAST([balls] AS varchar(1)), '-', CAST([strikes] AS varchar(1))) AS [count],
    COUNT(*) AS n_total
FROM [dbo].[v_re_analysis_swing_take_zone]
WHERE [zone] IN (1,2,3,4,5,6,7,8,9,11,12,13,14)
  AND [p_throws] IN ('L', 'R')
  AND [stand] IN ('L', 'R')
  AND [balls] BETWEEN 0 AND 3
  AND [strikes] BETWEEN 0 AND 2
  AND [delta_run_exp] IS NOT NULL
GROUP BY
    [season],
    [zone],
    CASE
        WHEN [p_throws] = 'R' AND [stand] = 'R' THEN 'RHP vs RHB'
        WHEN [p_throws] = 'L' AND [stand] = 'R' THEN 'LHP vs RHB'
        WHEN [p_throws] = 'R' AND [stand] = 'L' THEN 'RHP vs LHB'
        WHEN [p_throws] = 'L' AND [stand] = 'L' THEN 'LHP vs LHB'
    END,
    CONCAT(CAST([balls] AS varchar(1)), '-', CAST([strikes] AS varchar(1)))
"""

PH_QUERY_TIMEOUT_SECONDS = int(os.getenv("PH_QUERY_TIMEOUT_SECONDS", "0"))

# Suppress unstable RE impact percentages when baseline RE is too close to zero.
# Values remain in ratio scale (not multiplied by 100).
# Default 0 means only exact-zero baselines are suppressed.
RE_IMPACT_PCT_MIN_BASELINE_ABS = float(os.getenv("RE_IMPACT_PCT_MIN_BASELINE_ABS", "0"))

# Minimum sample size per outcome bucket (contact, miss, take) for swing/take break-even.
# Ensures all three outcome types have sufficient representation before computing BE%.
MIN_SWING_TAKE_BUCKET_N = 20

# El break-even de swing/take es
#     BE = (EV_take − EV_miss) / (EV_contact − EV_miss)
# y solo esta definido cuando el denominador es POSITIVO, es decir cuando hacer
# contacto es mejor que abanicar en falso. La guarda anterior era
# abs(EV_contact − EV_miss) > 1e-12, y ese abs() era el bug: dejaba pasar las
# celdas con denominador negativo, que producen break-evens negativos sin lectura
# sabermetrica (implicarian que el swing mejora la RE aun fallando siempre).
#
# El gap real es pequeno en conteos de bateador (mediana 0.017 carreras en 3-1),
# asi que un umbral absoluto no separa senal de ruido: ni con n>=1000 por bucket
# ni con |gap|>=0.08 bajaba el porcentaje de basura. Lo que si funciona es exigir
# que el gap sea SIGNIFICATIVAMENTE positivo, escalando con el tamano de muestra:
#     z = (EV_contact − EV_miss) / sqrt(var_contact/n_contact + var_miss/n_miss)
# Medido sobre 5,118 celdas: z>=0 ya elimina el 100% de los negativos y z>=1.96
# conserva el 70% de las celdas.
SWING_TAKE_MIN_Z = float(os.getenv("SWING_TAKE_MIN_Z", "1.96"))

# --- Umbrales minimos de muestra, todos juntos y configurables --------------
#
# Estaban dispersos por el fichero y dos de ellos no se podian tocar sin editar
# codigo: minimum_bunt_for_hit_n era una variable local dentro de la funcion y
# el 30 de sb_by_count estaba escrito en linea dentro del bucle. Son distintos a
# proposito, porque la unidad de cada analisis es distinta (eventos de toque,
# turnos, lanzamientos), pero conviene verlos de un vistazo y poder ajustarlos.

# Eventos IBB por celda (season, outs, runners). Sin filtro habia celdas con n=1.
MIN_IBB_N = int(os.getenv("MIN_IBB_N", "30"))

# Turnos en el estado (season, outs, runners, count) para el robo por conteo.
MIN_SB_BY_COUNT_N = int(os.getenv("MIN_SB_BY_COUNT_N", "30"))

# Intentos de toque en la celda (season, outs, scenario).
#
# El umbral es por ESCENARIO porque las dos jugadas viven en escalas distintas:
# el toque sorpresa tiene ~460 intentos por temporada y el squeeze ~24. Con un
# umbral unico de 50, el squeeze desaparecia entero.
#
# El del squeeze es bajo a proposito. La agregacion por rango de temporadas la
# hace el frontend con sum(be*n)/sum(n), asi que una celda pequeña pesa poco, y
# la poca muestra ya se comunica con el marcador de fiabilidad. El suelo real no
# es este numero: es que la celda necesita al menos un exito Y un fallo para que
# el break-even exista.
# El segundo numero es el minimo de FALLOS, y en el squeeze es el que manda.
# Los seis break-even negativos que aparecieron tenian todos n_fail = 1 o 2 con
# hasta 19 exitos: con un solo fallo observado, mean_rv_fail es el delta de un
# evento suelto y se va a cualquier parte. Mismo diagnostico que en tag-up, donde
# n se publica como n_fail porque la varianza la domina ese cubo.
#
# Medido sobre el squeeze, barriendo el minimo de fallos:
#
#     n_fail >=   celdas   intentos   BE<0    BE ponderado
#         1         147      2,816      6        0.6059
#         2         120      2,587      3        0.6326
#         3          94      2,296      1        0.6286
#         4          70      1,882      0        0.6350
#         5          47      1,403      0        0.6380
#
# Se elige 4: limpia todos los negativos conservando el 67% de los intentos. Lo
# tranquilizador es que el break-even ponderado apenas se mueve en todo el
# barrido (0.606 a 0.640), o sea que la guarda quita ruido y no señal.
#
# En el toque sorpresa los fallos nunca escasean, asi que ahi manda el total.
MIN_BUNT_N_BY_SCENARIO = {
    "surprise_bunt": {"total": int(os.getenv("MIN_SURPRISE_BUNT_N", "50")),
                      "fail":  int(os.getenv("MIN_SURPRISE_BUNT_FAIL_N", "1"))},
    "squeeze":       {"total": int(os.getenv("MIN_SQUEEZE_N", "5")),
                      "fail":  int(os.getenv("MIN_SQUEEZE_FAIL_N", "4"))},
}

# Break-even type names. Keep legacy values to avoid breaking downstream consumers.
BREAKEVEN_TYPE_STEALING = "stealing"
BREAKEVEN_TYPE_RUNNING = "running"
BREAKEVEN_TYPE_TAGGING = "tagging"
BREAKEVEN_TYPE_BUNT_FOR_HIT = "bunt-for-hit"
BREAKEVEN_TYPE_SB_BY_COUNT = "sb_by_count"
BREAKEVEN_TYPE_SWING_TAKE = "swing-take"
BREAKEVEN_TYPE_SWING_TAKE_ZONE = "swing_take_zone"


def load_re24_matrix(data_dir: Path) -> pd.DataFrame:
    """
    Load the All Events RE24 matrix (event_cd == -1) from parquet.

    Returns a DataFrame with columns: season, outs, runners, run_value
    """
    parquet_path = data_dir / "re24_values.parquet"
    log.info(f"Reading RE24 data from: {parquet_path.name}")

    df = pd.read_parquet(parquet_path, engine="pyarrow")
    log.info(f"  Total rows in file: {len(df):,}")

    # Keep only the All Events rollup rows
    re24 = df.loc[df["event_cd"] == -1, ["season", "outs", "runners", "n", "run_value"]].copy()
    log.info(f"  All-Events rows (event_cd == -1): {len(re24):,}")
    log.info(f"  Seasons: {re24['season'].min()} – {re24['season'].max()}")

    return re24


def load_re288_matrix(data_dir: Path) -> pd.DataFrame:
    """
    Load the RE288 matrix (base-out × count) from parquet.

    Returns a DataFrame with columns: season, outs, runners, count, n, run_value
    """
    parquet_path = data_dir / "re288_values.parquet"
    log.info(f"\nReading RE288 data from: {parquet_path.name}")

    df = pd.read_parquet(parquet_path, engine="pyarrow")
    log.info(f"  Total rows: {len(df):,}")
    log.info(f"  Seasons: {df['season'].min()} – {df['season'].max()}")
    log.info(f"  Counts: {sorted(df['count'].unique())}")

    return df


def impacto_en_carreras(quedarse, exito, fallo, p_exito=None) -> dict:
    """
    Convierte los tres estados de una decision en CARRERAS.

        ganancia = RE_exito     - RE_quedarse
        coste    = RE_quedarse  - RE_fallo
        neto     = p*ganancia - (1-p)*coste

    POR QUE HACE FALTA, teniendo ya el break-even. El BE es adimensional: dice
    que tasa de exito necesitas, pero no cuanto te juegas. Dos celdas con
    BE = 0.70 pueden estar arriesgando 0.05 carreras o 0.5, y la tabla actual no
    las distingue. Son los MISMOS tres numeros; lo unico que cambia es que aqui
    se publican en vez de reducirlos a un cociente.

    LA IDENTIDAD QUE CUADRA LAS DOS TABLAS:

        BE = coste / (ganancia + coste)

    Se comprueba en los tests. Si deja de cumplirse, las dos vistas del mismo
    numero han divergido.

    SIN p NO HAY NETO. `delta_re` queda a None en vez de suponer una tasa: es el
    dato mas importante de la fila y inventarlo seria peor que no publicarlo.
    Sirve para las series historicas donde no hay conteos de intentos.

    ENTRADAS EN DELTAS O EN ABSOLUTO, da igual: las tres se restan entre si, asi
    que una RE inicial comun se cancela. Las vistas de estirar el batazo y de
    tag-up dan RUN_VALUE (delta); la matriz RE24 da RE absoluta.
    """
    ganancia = exito - quedarse
    coste = quedarse - fallo

    neto = None
    if p_exito is not None:
        neto = p_exito * ganancia - (1.0 - p_exito) * coste

    return {
        "gain_runs": ganancia,
        "cost_runs": coste,
        "delta_re": neto,
    }


def _steal_gain_is_possible(re_before, re_after_steal) -> bool:
    """
    Un robo consumado NUNCA puede bajar la esperanza de carreras.

    Mismo numero de outs y un corredor en mejor base: el estado posterior domina
    al anterior, sin excepcion. Si la matriz dice lo contrario, la celda esta mal
    estimada y el break-even que sale de ella no significa nada.

    Y no es un caso raro que se pueda ignorar: en la tabla por conteo son 391 de
    5,168 celdas (7.57%), casi todas en estados con varios corredores y conteos
    poco frecuentes. Ejemplo, 2020 conteo 3-0 con 0 outs y corredores en 1B y 3B:

        RE(1B y 3B) = 2.6591    RE(2B y 3B) = 1.4737    ganancia = -1.19

    con solo 19 eventos en la celda de destino.

    Estas celdas son EXACTAMENTE las que producian break-even por encima de 1, en
    las dos tablas y sin un solo caso cruzado (tabla RE24: 2 de 2; tabla por
    conteo: 391 de 391). Es algebra, no coincidencia: con
    BE = (RE_antes - RE_cs)/(RE_sb - RE_cs) y denominador positivo,
    BE > 1 equivale a RE_antes > RE_sb.

    Por eso en un break-even de ROBO un valor por encima de 1 nunca es un "no
    conviene ni al 100%": siempre es ruido. Ojo, esto no vale para swing/take,
    donde BE > 1 si es legitimo porque la formula es otra.
    """
    return (re_after_steal - re_before) > 0


def calculate_sb_breakeven(re_lookup: dict, n_lookup: dict, seasons: list) -> list:
    """
    Stolen-base break-even: BE% = (RE_before - RE_CS) / (RE_SB - RE_CS)

        Double steal uses a weighted caught-stealing RE:
            RE_CS = 0.75 * RE(lead caught) + 0.25 * RE(trail caught)
    """
    rows = []
    for season in seasons:
        for label, before, after_sb, after_cs in SB_SCENARIOS:
            for outs in range(3):  # 0, 1, 2
                re_before = re_lookup.get((season, outs, before))
                re_sb     = re_lookup.get((season, outs, after_sb))

                # On caught stealing the outs increase by 1
                cs_outs = outs + 1
                if cs_outs >= 3:
                    re_cs = 0.0
                else:
                    if label == "Double steal":
                        re_cs_lead = re_lookup.get((season, cs_outs, after_cs))
                        re_cs_trail = re_lookup.get((season, cs_outs, DOUBLE_STEAL_TRAIL_CAUGHT_STATE))
                        if re_cs_lead is None or re_cs_trail is None:
                            re_cs = None
                        else:
                            re_cs = (
                                DOUBLE_STEAL_LEAD_CAUGHT_WEIGHT * re_cs_lead
                                + (1.0 - DOUBLE_STEAL_LEAD_CAUGHT_WEIGHT) * re_cs_trail
                            )
                    else:
                        re_cs = re_lookup.get((season, cs_outs, after_cs))

                if re_before is None or re_sb is None or re_cs is None:
                    continue

                denominator = re_sb - re_cs
                if denominator == 0:
                    continue

                be_pct = (re_before - re_cs) / denominator
                if not _steal_gain_is_possible(re_before, re_sb):
                    be_pct = float("nan")

                n_before = n_lookup.get((season, outs, before), 0)
                rows.append({
                    "season":        season,
                    "type":          BREAKEVEN_TYPE_STEALING,
                    "scenario":      label,
                    "label":         label,
                    "outs":          outs,
                    "breakeven_pct": be_pct,
                    "n":             n_before,
                })
    return rows


def calculate_running_breakeven(stretch_df: pd.DataFrame) -> list:
    """
    Break-even de estirar un batazo una base de mas, con eventos reales.

    Antes se calculaba con transiciones teoricas sobre la matriz RE24, y por eso
    "estirar un sencillo a doble" daba exactamente lo mismo que "robar 2B desde
    1B": la terna (inicio, exito, fallo) era la misma y la matriz no sabe como
    llegaste al estado.

    El exito no se anota como tal -- un sencillo estirado con exito se registra
    como DOBLE, indistinguible de un doble limpio -- asi que la vista condiciona
    por localizacion y trayectoria del batazo, y se restringe a bases limpias.
    Ver v_re_analysis_stretch para el detalle y para por que las bases limpias no
    son opcionales.

    Como RUN_VALUE es un delta, la RE inicial se cancela:

        BE = (delta_stop - delta_fail) / (delta_success - delta_fail)

    n es el numero de FALLOS observados, por la misma razon que en tag-up: es el
    termino que domina la varianza y el peso correcto para el promedio del
    frontend.
    """
    df = stretch_df.copy()
    df.columns = [str(c).lower() for c in df.columns]

    required = {"season", "outs", "direction", "bucket", "n", "mean_rv", "sd_rv"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Stretch view data missing required columns: {missing}")

    pivot = df.pivot_table(
        index=["season", "outs", "direction"],
        columns="bucket",
        values=["n", "mean_rv", "sd_rv"],
        aggfunc="first",
    ).reset_index()
    pivot.columns = ["_".join(c).strip("_") if c[1] else c[0] for c in pivot.columns]

    for col in ["mean_rv_stop", "mean_rv_success", "mean_rv_fail", "n_fail"]:
        if col not in pivot.columns:
            pivot[col] = pd.NA

    total_cells = len(pivot)
    pivot = pivot.dropna(subset=["mean_rv_stop", "mean_rv_success", "mean_rv_fail"]).copy()
    dropped = total_cells - len(pivot)

    gap = pivot["mean_rv_success"] - pivot["mean_rv_fail"]
    pivot = pivot[gap.abs() > 1e-9].copy()
    gap = pivot["mean_rv_success"] - pivot["mean_rv_fail"]

    pivot["breakeven_pct"] = (pivot["mean_rv_stop"] - pivot["mean_rv_fail"]) / gap
    pivot["n"] = pivot["n_fail"]

    log.info(f"  Stretch: {total_cells:,} celdas evaluadas")
    if dropped:
        log.info(f"    - {dropped:,} sin los tres desenlaces observados")
    log.info(f"    = {len(pivot):,} publicadas")
    if len(pivot):
        log.info(f"    BE: min={pivot['breakeven_pct'].min():.3f} max={pivot['breakeven_pct'].max():.3f}")

    # Fuera del if: con pivot vacio esto quedaba sin asignar y la funcion moria
    # con UnboundLocalError en vez de devolver [].
    rows = []
    for _, row in pivot.iterrows():
        label = LABELS_RUNNING.get(row["direction"], row["direction"])
        rows.append({
            "season":        int(row["season"]),
            "type":          BREAKEVEN_TYPE_RUNNING,
            "scenario":      label,
            "label":         label,
            "outs":          int(row["outs"]),
            "breakeven_pct": float(row["breakeven_pct"]),
            "n":             int(row["n"]),
        })
    return rows


LABELS_RUNNING = {
    "double":         "Single stretched to double",
    "triple":         "Double stretched to triple",
    "first_to_third": "Runner on 1B to 3B on a single",
    "second_to_home": "Runner on 2B scores on a single",
    "first_to_home":  "Runner on 1B scores on a double",
}


def _impacto_desde_cubos(df, cubo_quedarse: str, indice: list,
                        tipo: str, etiqueta_de, origen: str,
                        escenario_de=None) -> list:
    """
    Convierte una vista de tres cubos en filas de impacto en CARRERAS.

    Comun a estirar el batazo y a tag-up: las dos vistas tienen la misma forma
    --un cubo por desenlace, con su media y su n-- y solo cambian el nombre del
    cubo de quedarse (`stop` frente a `stay`) y como se arma la etiqueta.

    La tasa observada sale de los propios cubos, `n_success / (n_success +
    n_fail)`, asi que aqui NO hace falta ninguna vista nueva: al robo si, porque
    su break-even se calcula solo con la matriz RE24 y no sabe cuantas veces se
    intento.

    El `n` publicado son los INTENTOS. El break-even publica solo los fallos,
    porque son el termino que domina su varianza; esta tabla mide el valor
    esperado y su muestra es otra.
    """
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]

    pivot = df.pivot_table(index=indice, columns="bucket",
                           values=["n", "mean_rv"], aggfunc="first").reset_index()
    pivot.columns = ["_".join(c).strip("_") if c[1] else c[0] for c in pivot.columns]

    for col in (f"mean_rv_{cubo_quedarse}", "mean_rv_success", "mean_rv_fail",
                f"n_{cubo_quedarse}", "n_success", "n_fail"):
        if col not in pivot.columns:
            pivot[col] = pd.NA

    # Sin los tres desenlaces no hay nada que calcular, igual que en el
    # break-even: no es que sea poco fiable, es que no existe.
    pivot = pivot.dropna(subset=[f"mean_rv_{cubo_quedarse}", "mean_rv_success",
                                 "mean_rv_fail", "n_success", "n_fail"]).copy()

    rows = []
    for _, row in pivot.iterrows():
        intentos = int(row["n_success"]) + int(row["n_fail"])
        if intentos == 0:
            continue

        carreras = impacto_en_carreras(
            quedarse=float(row[f"mean_rv_{cubo_quedarse}"]),
            exito=float(row["mean_rv_success"]),
            fallo=float(row["mean_rv_fail"]),
            p_exito=int(row["n_success"]) / intentos,
        )
        etiqueta = etiqueta_de(row)
        # El escenario alimenta el rombo de bases de la tabla, que espera "100",
        # "010"... Cuando el analisis tiene estado de bases se pasa; estirar el
        # batazo no lo tiene --el que corre es el bateador-- y ahi el escenario
        # es la etiqueta y el rombo no aplica.
        escenario = escenario_de(row) if escenario_de else etiqueta
        # EL BASELINE ES QUEDARSE QUIETO, que es contra lo que se compara el
        # intento. Sin el, estas tres filas salian sin `delta_re_pct` y el
        # interruptor de Percentages no tenia nada que ensenar: se pulsaba, la
        # tabla se repintaba igual, y parecia que el control estaba muerto.
        #
        # Es la misma referencia que usan sac_bunt y gidp --la expectativa que ya
        # tenias-- y se divide entre su VALOR ABSOLUTO, la definicion unica del
        # sitio.
        baseline = float(row[f"mean_rv_{cubo_quedarse}"])
        rows.append({
            "season": int(row["season"]),
            "type": tipo,
            "scenario": escenario,
            "label": etiqueta,
            "outs": int(row["outs"]),
            "delta_re": carreras["delta_re"],
            "baseline_re": baseline,
            "delta_re_pct": (carreras["delta_re"] / abs(baseline)) if baseline else None,
            "gain_runs": carreras["gain_runs"],
            "cost_runs": carreras["cost_runs"],
            "n": intentos,
        })

    log.info(f"  {origen}: {len(rows):,} filas de impacto en carreras")
    return rows


def calculate_running_impact(stretch_df: pd.DataFrame) -> list:
    """Estirar el batazo, en carreras. Mismas etiquetas que el break-even."""
    return _impacto_desde_cubos(
        stretch_df, "stop", ["season", "outs", "direction"], "running_runs",
        lambda row: LABELS_RUNNING.get(row["direction"], row["direction"]),
        "Running",
    )


def calculate_advance_impact(adv_df: pd.DataFrame) -> list:
    """
    Avanzar una base de mas en un batazo ajeno, en carreras.

    Va al MISMO tipo `running_runs` que estirar el batazo, porque en la tabla de
    break-even las dos vistas forman una sola familia: `running` = stretch +
    advance, cinco escenarios. Publicar solo stretch dejaba RE Impact con dos
    filas donde Break-Even ensena cinco -- el mismo calculo contado a medias.
    """
    return _impacto_desde_cubos(
        adv_df, "stop", ["season", "outs", "direction"], "running_runs",
        lambda row: LABELS_RUNNING.get(row["direction"], row["direction"]),
        "Advance",
    )


def calculate_tagging_impact(tag_df: pd.DataFrame) -> list:
    """
    Tag-up, en carreras.

    El cubo de quedarse se llama `stay` aqui y `stop` en estirar el batazo: no
    es lo mismo quedarse quieto en tercera que frenar al doblar primera.
    """
    def etiqueta(row):
        estado = RUNNERS_LABELS.get(str(row["runners"]), str(row["runners"]))
        if row["direction"] == "home":
            return f"Runner on 3B tags up to score ({estado})"
        return f"Runner on 2B tags up to 3B ({estado})"

    return _impacto_desde_cubos(
        tag_df, "stay", ["season", "outs", "runners", "direction"], "tagging_runs",
        etiqueta, "Tag-up",
    )


def calculate_advance_breakeven(adv_df: pd.DataFrame) -> list:
    """
    Break-even de un corredor ya en base tomando una base de mas en un batazo.

    Misma forma que el estirado del bateador, pero aqui los tres desenlaces si
    son observables. Ver v_re_analysis_advance para el criterio: notacion y
    campo de destino a la vez, condicionado por el destino del bateador y
    restringido a zonas donde avanzar es una decision viva.
    """
    df = adv_df.copy()
    df.columns = [str(c).lower() for c in df.columns]

    required = {"season", "outs", "direction", "bucket", "n", "mean_rv", "sd_rv"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Advance view data missing required columns: {missing}")

    pivot = df.pivot_table(
        index=["season", "outs", "direction"],
        columns="bucket",
        values=["n", "mean_rv", "sd_rv"],
        aggfunc="first",
    ).reset_index()
    pivot.columns = ["_".join(c).strip("_") if c[1] else c[0] for c in pivot.columns]

    for col in ["mean_rv_stop", "mean_rv_success", "mean_rv_fail", "n_fail"]:
        if col not in pivot.columns:
            pivot[col] = pd.NA

    total_cells = len(pivot)
    pivot = pivot.dropna(subset=["mean_rv_stop", "mean_rv_success", "mean_rv_fail"]).copy()
    dropped = total_cells - len(pivot)

    gap = pivot["mean_rv_success"] - pivot["mean_rv_fail"]
    pivot = pivot[gap.abs() > 1e-9].copy()
    gap = pivot["mean_rv_success"] - pivot["mean_rv_fail"]

    pivot["breakeven_pct"] = (pivot["mean_rv_stop"] - pivot["mean_rv_fail"]) / gap
    pivot["n"] = pivot["n_fail"]

    log.info(f"  Advance: {total_cells:,} celdas evaluadas")
    if dropped:
        log.info(f"    - {dropped:,} sin los tres desenlaces observados")
    log.info(f"    = {len(pivot):,} publicadas")
    if len(pivot):
        log.info(f"    BE: min={pivot['breakeven_pct'].min():.3f} max={pivot['breakeven_pct'].max():.3f}")

    rows = []
    for _, row in pivot.iterrows():
        label = LABELS_RUNNING.get(row["direction"], row["direction"])
        rows.append({
            "season":        int(row["season"]),
            "type":          BREAKEVEN_TYPE_RUNNING,
            "scenario":      label,
            "label":         label,
            "outs":          int(row["outs"]),
            "breakeven_pct": float(row["breakeven_pct"]),
            "n":             int(row["n"]),
        })
    return rows


def load_advance_break_even_rows_from_db() -> list:
    """Lee los avances de corredores de la vista y los convierte en filas de break-even."""
    log.info("")
    log.info("Extracting runner-advance break-even rows from SQL view...")
    engine = connect_to_database()
    adv_df = _read_sql_view(engine, QUERY_ADVANCE, "dbo.v_re_analysis_advance")
    rows = calculate_advance_breakeven(adv_df)
    log.info(f"  Normalized advance BE rows: {len(rows):,}")
    return rows


def load_stretch_break_even_rows_from_db() -> list:
    """Lee los batazos estirados de la vista y los convierte en filas de break-even."""
    log.info("")
    log.info("Extracting stretch break-even rows from SQL view...")
    engine = connect_to_database()
    stretch_df = _read_sql_view(engine, QUERY_STRETCH, "dbo.v_re_analysis_stretch")
    rows = calculate_running_breakeven(stretch_df)
    log.info(f"  Normalized stretch BE rows: {len(rows):,}")
    return rows


def calculate_tagging_breakeven(tag_df: pd.DataFrame) -> list:
    """
    Break-even del tag-up a partir de elevados atrapados REALES.

    Antes esto se calculaba con transiciones teoricas sobre la matriz RE24, sin
    mirar un solo elevado. El resultado era que las seis celdas de la familia
    coincidian con robos: "tocar y avanzar de 1B a 2B con 0 outs" daba
    exactamente lo mismo que "robar 2B con 1 out", porque la matriz RE24 solo
    conoce estados base-out y la terna (inicio, exito, fallo) era la misma.

    Los tres desenlaces son observables en el dato (ver v_re_analysis_tag_up):
    el corredor avanza, lo ponen out, o se queda. Como RUN_VALUE ya es un delta,
    la RE inicial se cancela y el break-even queda:

        BE = (delta_stay - delta_fail) / (delta_success - delta_fail)

    Se publica una fila por temporada; el frontend agrega el rango que elija el
    usuario ponderando por n, igual que con las demas familias.

    n es el numero de FALLOS observados, no las oportunidades totales. Puede
    parecer contraintuitivo, pero es lo correcto por dos razones:

      1. Es el tamano de muestra que manda. El termino que domina la varianza
         del break-even es delta_fail, cuyo error escala con 1/n_fail. Publicar
         las oportunidades totales haria que una celda con 245 elevados y UN
         solo fallo saliera sin marcador de confianza, como si fuera solida.
         Medido: la mediana de fallos por fila es 2, y el 36% de las filas se
         calcula con uno solo.

      2. Es el peso correcto para la agregacion. El frontend promedia el rango
         de temporadas con sum(be*n)/sum(n); con n = n_fail eso aproxima una
         ponderacion por inverso de varianza.
    """
    df = tag_df.copy()
    df.columns = [str(c).lower() for c in df.columns]

    required = {"season", "outs", "runners", "direction", "bucket", "n", "mean_rv", "sd_rv"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Tag-up view data missing required columns: {missing}")

    df["runners"] = _normalizar_runners(df["runners"], "dbo.v_re_analysis_tag_up")

    pivot = df.pivot_table(
        index=["season", "outs", "runners", "direction"],
        columns="bucket",
        values=["n", "mean_rv", "sd_rv"],
        aggfunc="first",
    ).reset_index()
    pivot.columns = ["_".join(c).strip("_") if c[1] else c[0] for c in pivot.columns]

    needed = ["mean_rv_stay", "mean_rv_success", "mean_rv_fail",
              "sd_rv_success", "sd_rv_fail",
              "n_stay", "n_success", "n_fail"]
    for col in needed:
        if col not in pivot.columns:
            pivot[col] = pd.NA

    total_cells = len(pivot)
    # Sin fallos observados no hay break-even que calcular. Ocurre sobre todo con
    # bases llenas y 0 outs: poner out a un corredor tagueando es raro porque
    # solo van cuando estan seguros.
    pivot = pivot.dropna(subset=["mean_rv_stay", "mean_rv_success", "mean_rv_fail"]).copy()
    dropped_no_fail = total_cells - len(pivot)

    gap = pivot["mean_rv_success"] - pivot["mean_rv_fail"]
    pivot = pivot[gap.abs() > 1e-9].copy()
    gap = pivot["mean_rv_success"] - pivot["mean_rv_fail"]

    pivot["breakeven_pct"] = (pivot["mean_rv_stay"] - pivot["mean_rv_fail"]) / gap
    pivot["n"] = pivot["n_fail"]

    log.info(f"  Tag-up: {total_cells:,} celdas evaluadas")
    if dropped_no_fail:
        log.info(f"    - {dropped_no_fail:,} sin fallos observados (break-even indefinido)")
    log.info(f"    = {len(pivot):,} publicadas")
    if len(pivot):
        log.info(f"    BE: min={pivot['breakeven_pct'].min():.3f} max={pivot['breakeven_pct'].max():.3f}")
        log.info(f"    n (fallos observados): mediana {pivot['n'].median():.0f}, max {pivot['n'].max():.0f}")

    rows = []
    for _, row in pivot.iterrows():
        runners = str(row["runners"])
        state = RUNNERS_LABELS.get(runners, runners)
        if row["direction"] == "home":
            label = f"Runner on 3B tags up to score ({state})"
        else:
            label = f"Runner on 2B tags up to 3B ({state})"
        rows.append({
            "season":        int(row["season"]),
            "type":          BREAKEVEN_TYPE_TAGGING,
            "scenario":      label,
            "label":         label,
            "outs":          int(row["outs"]),
            "breakeven_pct": float(row["breakeven_pct"]),
            "n":             int(row["n"]),
        })
    return rows


def load_advance_rows_from_db() -> tuple:
    """Una sola consulta, break-even y carreras. Ver load_stretch_rows_from_db."""
    log.info("")
    log.info("Extracting runner-advance rows from SQL view...")
    engine = connect_to_database()
    adv_df = _read_sql_view(engine, QUERY_ADVANCE, "dbo.v_re_analysis_advance")
    return calculate_advance_breakeven(adv_df), calculate_advance_impact(adv_df)


def load_stretch_rows_from_db() -> tuple:
    """
    Una sola consulta, las DOS familias: break-even y carreras.

    La vista de estirar el batazo es lenta y sus tres cubos sirven para las dos
    cosas. Llamar por separado a los dos cargadores la consultaria dos veces
    para nada.
    """
    log.info("")
    log.info("Extracting stretch rows from SQL view...")
    engine = connect_to_database()
    stretch_df = _read_sql_view(engine, QUERY_STRETCH, "dbo.v_re_analysis_stretch")
    return calculate_running_breakeven(stretch_df), calculate_running_impact(stretch_df)


def load_tag_up_rows_from_db() -> tuple:
    """Una sola consulta, break-even y carreras. Ver load_stretch_rows_from_db."""
    log.info("")
    log.info("Extracting tag-up rows from SQL view...")
    engine = connect_to_database()
    tag_df = _read_sql_view(engine, QUERY_TAG_UP, "dbo.v_re_analysis_tag_up")
    return calculate_tagging_breakeven(tag_df), calculate_tagging_impact(tag_df)


# Los cuatro estados con forzado en primera. Sin corredor ahi la tasa de doble
# matanza va del 0% al 2%: no hay jugada que medir y no se publican.
GIDP_SCENARIOS = [
    ("100", "Runner on 1B"),
    ("101", "Runners on 1B & 3B"),
    ("110", "Runners on 1B & 2B"),
    ("111", "Bases loaded"),
]


def calculate_gidp_impact(gidp_df: pd.DataFrame, re_lookup: dict) -> list:
    """
    Coste OBSERVADO de la doble matanza, en carreras.

        valor = carreras de la jugada + RE(estado final) - RE(estado inicial)

    y lo que se publica es ese valor MENOS el del out sencillo comparable en el
    mismo estado base-out. Es lo que el usuario pidio: cuanto cuesta rodar a
    doble play FRENTE A un out normal.

    DOS DECISIONES QUE NO SON COSMETICAS.

    1) Con tres outs la entrada se acaba: RE(estado final) es 0, no hay que
       buscarlo en la matriz. Sin esto el valor del doble play con un out sale
       disparatado, porque la matriz no tiene entrada para tres outs.

    2) EL BRAZO DE COMPARACION SE ACOTA A RODADOS Y A UN SOLO OUT. Sin acotar a
       rodados se llena de elevados, que nunca pudieron ser doble play: con
       corredor en tercera y cero outs ese brazo anota 0,603 carreras porque son
       flies de sacrificio, contra 0,699 en rodados. Y `EVENT_OUTS_CT = 1` deja
       fuera ocho eventos por temporada que suman dos outs sin llevar DP_FL --son
       dobles matanzas sin marcar, y colarlos en el brazo del "out sencillo"
       ensucia justo lo que se quiere medir.

    El valor del doble play SI usa toda la poblacion: ahi no hay que elegir el
    tipo de batazo, porque el suceso ya ocurrio.
    """
    if not len(gidp_df):
        return []

    df = gidp_df.copy()
    df["re_pre"] = [
        re_lookup.get((s, int(o), r))
        for s, o, r in zip(df["season"], df["outs"], df["runners"], strict=True)
    ]
    df["re_post"] = [
        0.0 if int(of) >= 3 else re_lookup.get((s, int(of), rf))
        for s, of, rf in zip(df["season"], df["outs_fin"], df["runners_fin"], strict=True)
    ]
    df = df.dropna(subset=["re_pre", "re_post"])
    if not len(df):
        return []

    df["valor"] = df["carreras"] + df["re_post"] - df["re_pre"]

    def media(sub: pd.DataFrame) -> tuple:
        n = int(sub["n"].sum())
        return ((sub["valor"] * sub["n"]).sum() / n, n) if n else (None, 0)

    rows = []
    for (season, outs, runners), grupo in df.groupby(["season", "outs", "runners"]):
        label = dict(GIDP_SCENARIOS).get(runners)
        if label is None:
            continue
        valor_dp, n_dp = media(grupo[grupo["es_dp"] == 1])
        valor_out, n_out = media(grupo[
            (grupo["es_dp"] == 0)
            & (grupo["outs_fin"] - grupo["outs"] == 1)
            & (grupo["tipo_batazo"] == "G")
        ])
        if valor_dp is None or valor_out is None:
            continue
        re_pre = re_lookup.get((season, int(outs), runners))
        delta_re = valor_dp - valor_out
        rows.append({
            "season":   int(season),
            "type":     "gidp",
            "scenario": runners,
            "label":    label,
            "outs":     int(outs),
            "delta_re": delta_re,
            "baseline_re": re_pre,
            "delta_re_pct": (delta_re / abs(re_pre)) if re_pre else None,
            "n":        n_dp,
        })
    return rows


def load_gidp_rows_from_db(re_lookup: dict) -> list:
    """Lee la rejilla de dobles matanzas y la convierte en filas de carreras."""
    log.info("")
    log.info("Extracting GIDP rows from SQL view...")
    engine = connect_to_database()
    gidp_df = _read_sql_view(engine, QUERY_GIDP, "dbo.v_re_analysis_gidp")
    rows = calculate_gidp_impact(gidp_df, re_lookup)
    log.info(f"  Normalized GIDP impact rows: {len(rows):,}")
    return rows


def load_tag_up_break_even_rows_from_db() -> list:
    """Lee los elevados atrapados de la vista y los convierte en filas de break-even."""
    log.info("")
    log.info("Extracting tag-up break-even rows from SQL view...")
    engine = connect_to_database()
    tag_df = _read_sql_view(engine, QUERY_TAG_UP, "dbo.v_re_analysis_tag_up")
    rows = calculate_tagging_breakeven(tag_df)
    log.info(f"  Normalized tag-up BE rows: {len(rows):,}")
    return rows


# El baseline "Standard PA" (event_cd = 95) ya no se lee: era la alternativa del
# break-even del toque y resulto no ser un codigo de Retrosheet sino una
# agrupacion que fabrica extract_re_data.py con los codigos 2,3,14,16,18,19,20,
# 21,22,23. Cubre el 99.98% de los eventos, asi que valia lo mismo que el
# promedio de la liga. Ver 18_v_re_analysis_bunt.sql.


# Los dos escenarios que produce v_re_analysis_bunt. Las etiquetas viajan al
# parquet y el frontend las tiene declaradas en BREAKEVEN_SCENARIOS, asi que
# cambiarlas aqui sin cambiarlas alli deja la tabla vacia (ver el test de
# contrato de escenarios).
LABELS_BUNT = {
    "surprise_bunt": "Bunt for a hit",
    "squeeze":       "Squeeze",
}


def build_bunt_swing_away(effect_df: pd.DataFrame, weights_df: pd.DataFrame) -> pd.DataFrame:
    """
    La alternativa a tocar: que habria valido el turno si estos mismos bateadores
    hubieran bateado.

        swing_away(celda) = liga(celda) + SUM(toques_b * efecto_b) / SUM(toques_b)

    Se ensambla aqui y no en SQL por rendimiento medido: v_re_analysis_bunt_batter_effect
    tarda 8s suelta, pero al unirla aguas abajo dentro de la misma sentencia el
    optimizador re-expande el recorrido de los 15.5M turnos y la consulta deja de
    terminar. Aqui son 48k x 31k filas en pandas.

    Devuelve un DataFrame indexado por (season, outs, scenario) con la
    alternativa y la fraccion de toques que quedo respaldada por un efecto de
    bateador -- los que no llegan a 50 turnos no tienen efecto y no pesan.
    """
    effect = effect_df.copy()
    weights = weights_df.copy()
    for d in (effect, weights):
        d.columns = [str(c).lower() for c in d.columns]

    missing = {"season", "batter_id", "effect"} - set(effect.columns)
    if missing:
        raise ValueError(f"Bunt batter-effect view missing required columns: {missing}")
    missing = {"season", "outs", "scenario", "batter_id", "n_bunts", "lg_rv"} - set(weights.columns)
    if missing:
        raise ValueError(f"Bunt weights view missing required columns: {missing}")

    keys = ["season", "outs", "scenario"]
    total = weights.groupby(keys)["n_bunts"].sum()

    merged = weights.merge(effect[["season", "batter_id", "effect"]],
                           on=["season", "batter_id"], how="inner")
    if merged.empty:
        return pd.DataFrame(columns=keys + ["swing_away", "effect_coverage"]).set_index(keys)

    merged["weighted"] = merged["n_bunts"] * merged["effect"]
    grouped = merged.groupby(keys).agg(
        weighted=("weighted", "sum"),
        covered=("n_bunts", "sum"),
        lg_rv=("lg_rv", "first"),
    )
    grouped["swing_away"] = grouped["lg_rv"] + grouped["weighted"] / grouped["covered"]
    grouped["effect_coverage"] = grouped["covered"] / total.reindex(grouped.index)

    return grouped[["swing_away", "effect_coverage"]]


def calculate_bunt_breakeven(bunt_df: pd.DataFrame,
                             effect_df: pd.DataFrame,
                             weights_df: pd.DataFrame) -> list:
    """
    Break-even del toque sorpresa y del squeeze, con eventos reales.

    Sustituye al calculo contra el "Standard PA" (event_cd = 95), que no es un
    codigo de Retrosheet sino una agrupacion que fabrica este repositorio y que
    cubre el 99.98% de los eventos: el baseline salia indistinguible del promedio
    de la liga y la formula se reducia a BE = -EV_fallo / (EV_exito - EV_fallo).

    Ahora la alternativa es el cubo swing_away de la vista: lo que habrian valido
    esos turnos si esos MISMOS bateadores hubieran bateado. Ver
    18_v_re_analysis_bunt.sql para como se construye y por que el ajuste por
    bateador es el arreglo y no un adorno.

        BE = (rv_swing_away - rv_fail) / (rv_success - rv_fail)

    n = intentos de toque (exitos + fallos). En tagging n era n_fail porque los
    fallos eran el cubo escaso -- 23 frente a 1,500 exitos; aqui los dos estan
    poblados y lo que limita la precision es cuantas veces se intento la jugada.
    """
    df = bunt_df.copy()
    df.columns = [str(c).lower() for c in df.columns]

    required = {"season", "outs", "scenario", "bucket", "n", "mean_rv", "sd_rv"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Bunt view data missing required columns: {missing}")

    pivot = df.pivot_table(
        index=["season", "outs", "scenario"],
        columns="bucket",
        values=["n", "mean_rv", "sd_rv"],
        aggfunc="first",
    ).reset_index()
    pivot.columns = ["_".join(c).strip("_") if c[1] else c[0] for c in pivot.columns]

    swing_away = build_bunt_swing_away(effect_df, weights_df)
    pivot = pivot.merge(swing_away.reset_index(), on=["season", "outs", "scenario"], how="left")
    pivot = pivot.rename(columns={"swing_away": "mean_rv_swing_away"})

    needed = ["mean_rv_success", "mean_rv_fail", "mean_rv_swing_away", "n_success", "n_fail"]
    for col in needed:
        if col not in pivot.columns:
            pivot[col] = pd.NA

    total_cells = len(pivot)
    pivot = pivot.dropna(subset=needed).copy()
    dropped_incomplete = total_cells - len(pivot)

    pivot["n"] = pivot["n_success"] + pivot["n_fail"]
    before_n = len(pivot)
    desconocidos = sorted(set(pivot["scenario"]) - set(MIN_BUNT_N_BY_SCENARIO))
    if desconocidos:
        raise ValueError(
            f"La vista devolvio escenarios sin umbral declarado: {desconocidos}. "
            f"Anadelos a MIN_BUNT_N_BY_SCENARIO."
        )
    min_total = pivot["scenario"].map(lambda s: MIN_BUNT_N_BY_SCENARIO[s]["total"])
    min_fail = pivot["scenario"].map(lambda s: MIN_BUNT_N_BY_SCENARIO[s]["fail"])
    pivot = pivot[(pivot["n"] >= min_total) & (pivot["n_fail"] >= min_fail)].copy()
    dropped_small = before_n - len(pivot)

    gap = pivot["mean_rv_success"] - pivot["mean_rv_fail"]
    pivot = pivot[gap > 0].copy()
    gap = pivot["mean_rv_success"] - pivot["mean_rv_fail"]

    pivot["breakeven_pct"] = (pivot["mean_rv_swing_away"] - pivot["mean_rv_fail"]) / gap

    log.info(f"  Bunt: {total_cells:,} celdas evaluadas")
    if dropped_incomplete:
        log.info(f"    - {dropped_incomplete:,} sin los tres cubos (success, fail, swing_away)")
    if dropped_small:
        log.info(f"    - {dropped_small:,} con menos intentos que el minimo de su escenario "
                     f"({MIN_BUNT_N_BY_SCENARIO})")
    log.info(f"    = {len(pivot):,} publicadas")
    if len(pivot):
        for scenario, g in pivot.groupby("scenario"):
            log.info(f"    {scenario}: BE min={g['breakeven_pct'].min():.3f} "
                     f"max={g['breakeven_pct'].max():.3f} intentos={int(g['n'].sum()):,}")
        # Los bateadores con menos de 50 turnos no tienen efecto propio, asi que
        # sus toques no pesan en la alternativa aunque si cuenten en el
        # desenlace. Se registra para que el recorte no sea silencioso.
        cov = pivot["effect_coverage"]
        log.info(f"    Toques respaldados por un efecto de bateador: "
                 f"mediana {cov.median():.1%}, minimo {cov.min():.1%}")

    rows = []
    for _, row in pivot.iterrows():
        label = LABELS_BUNT.get(row["scenario"], row["scenario"])
        rows.append({
            "season":        int(row["season"]),
            "type":          BREAKEVEN_TYPE_BUNT_FOR_HIT,
            "scenario":      label,
            "label":         label,
            "outs":          int(row["outs"]),
            "breakeven_pct": float(row["breakeven_pct"]),
            "n":             int(row["n"]),
        })

    return rows


def calculate_swing_take_breakeven(agg_df: pd.DataFrame) -> list:
    """
    Swing/take break-even a partir del agregado por celda que devuelve SQL.

    Entrada: una fila por (season, balls, strikes, outs, runners, pitch_outcome)
    con ev (media de delta_run_exp), sd (desviacion) y n.

    Resultados (cuatro, no tres):
      - contact: bola en juego
      - foul:    el turno CONTINUA
      - whiff:   abanico en falso
      - take:    strike cantado o bola

    Abanicar tiene tres desenlaces con valores muy distintos, y el modelo de dos
    resultados que habia antes no podia representarlos. El foul estaba agrupado
    con el whiff pese a que con dos strikes son opuestos: el whiff es el tercer
    strike, el foul no cuesta nada. Medido: EV_whiff -0.324 vs EV_foul -0.014 en
    3-2, y el foul es el 39-41% de los swings.

    Break-even de tres resultados, manteniendo fija la tasa de foul observada:

        p_c*EV_c + p_f*EV_f + (1 - p_c - p_f)*EV_w = EV_take

        p_c* = [EV_take - p_f*EV_f - (1 - p_f)*EV_w] / (EV_c - EV_w)

    Se lee: dado que una fraccion p_f de los swings va a ser foul de todas
    formas, que fraccion tiene que poner la bola en juego para que abanicar
    iguale a tomar.

    El denominador pasa a ser EV_c - EV_w, el recorrido completo entre bola en
    juego y abanico en falso, que es grande y estable. El denominador anterior
    (EV_c - EV_miss) se degeneraba en conteos de bateador y disparaba el
    resultado: en 3-2 daba 2.73 donde este da 1.12.

    Supuesto explicito: al variar la tasa de contacto, la de foul se mantiene y
    lo que cambia es el whiff. La alternativa seria mantener la proporcion
    foul:whiff, que es lo que hacia el codigo anterior sin declararlo.

    Solo se publica la celda si EV_c - EV_w es significativamente positivo
    (ver SWING_TAKE_MIN_Z). Un valor > 1 SI se publica: significa que ni
    poniendo la bola en juego en todos los swings conviene batear, que es el
    resultado esperado en conteos como 3-1.
    """
    df = agg_df.copy()
    df.columns = [str(col).lower() for col in df.columns]

    required = {"season", "balls", "strikes", "outs", "runners", "pitch_outcome", "ev", "sd", "n"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Swing/take aggregate missing required columns: {missing}")

    df["runners"] = _normalizar_runners(df["runners"], "dbo.v_re_analysis_swing_take")

    pivot = df.pivot_table(
        index=["season", "balls", "strikes", "outs", "runners"],
        columns="pitch_outcome",
        values=["ev", "sd", "n"],
        aggfunc="first",
    ).reset_index()
    pivot.columns = ["_".join(col).strip("_") if col[1] else col[0] for col in pivot.columns]

    needed = ["ev_contact", "ev_foul", "ev_whiff", "ev_take",
              "sd_contact", "sd_whiff",
              "n_contact", "n_foul", "n_whiff", "n_take"]
    for col in needed:
        if col not in pivot.columns:
            pivot[col] = pd.NA

    total_cells = len(pivot)
    pivot = pivot.dropna(subset=needed).copy()
    dropped_incomplete = total_cells - len(pivot)

    # Las cuatro categorias deben tener representacion suficiente; STDEV ademas
    # necesita n >= 2 para estar definida.
    min_bucket = pivot[["n_contact", "n_foul", "n_whiff", "n_take"]].min(axis=1)
    pivot = pivot[min_bucket >= MIN_SWING_TAKE_BUCKET_N].copy()
    dropped_small_n = total_cells - dropped_incomplete - len(pivot)

    # Denominador significativamente positivo (prueba de una cola).
    # Ahora es EV_contact − EV_whiff: el recorrido entre bola en juego y abanico
    # en falso, no el que quedaba tras promediar el foul dentro del fallo.
    se = ((pivot["sd_contact"] ** 2 / pivot["n_contact"])
          + (pivot["sd_whiff"] ** 2 / pivot["n_whiff"])) ** 0.5
    pivot["z_gap"] = (pivot["ev_contact"] - pivot["ev_whiff"]) / se.where(se > 0)

    before_z = len(pivot)
    pivot = pivot[pivot["z_gap"] >= SWING_TAKE_MIN_Z].copy()
    dropped_not_significant = before_z - len(pivot)

    # gap y p_foul se recalculan DESPUES de filtrar. Con el indice previo al
    # filtro, si los filtros vacian pivot, asignar una Serie no vacia a un
    # DataFrame vacio no lo deja vacio: pandas adopta el indice de la Serie y
    # reaparece una fila entera de NaN, que revienta mas abajo en astype(int).
    gap = pivot["ev_contact"] - pivot["ev_whiff"]

    # Fraccion de SWINGS que acaban en foul (no de lanzamientos: el take queda
    # fuera, porque la tasa de foul solo tiene sentido condicionada a abanicar).
    swings = pivot["n_contact"] + pivot["n_foul"] + pivot["n_whiff"]
    p_foul = pivot["n_foul"] / swings

    pivot["breakeven_pct"] = (
        pivot["ev_take"] - p_foul * pivot["ev_foul"] - (1.0 - p_foul) * pivot["ev_whiff"]
    ) / gap
    pivot["foul_rate"] = p_foul

    # n = total de lanzamientos de la celda, no el subgrupo menor.
    pivot["n"] = pivot[["n_contact", "n_foul", "n_whiff", "n_take"]].sum(axis=1)
    pivot["count"] = pivot["balls"].astype(int).astype(str) + "-" + pivot["strikes"].astype(int).astype(str)

    log.info(f"  Swing/take: {total_cells:,} celdas evaluadas")
    log.info(f"    − {dropped_incomplete:,} sin las cuatro categorias")
    log.info(f"    − {dropped_small_n:,} con algun bucket n < {MIN_SWING_TAKE_BUCKET_N}")
    log.info(f"    − {dropped_not_significant:,} con EV_contact − EV_whiff no significativo (z < {SWING_TAKE_MIN_Z})")
    log.info(f"    = {len(pivot):,} publicadas")
    if len(pivot):
        log.info(f"    BE publicado: min={pivot['breakeven_pct'].min():.3f} "
              f"max={pivot['breakeven_pct'].max():.3f} "
              f"(>1 = nunca conviene batear: {int((pivot['breakeven_pct'] > 1).sum()):,} celdas)")
        log.info(f"    Tasa de foul entre swings: mediana {pivot['foul_rate'].median():.3f}")
        # La supervivencia por conteo es diagnostica, no solo informativa: en 3-0
        # casi nadie abanica, asi que el gap no es estimable y es esperable que
        # caiga casi todo. Se registra para que el recorte nunca sea silencioso.
        by_count = pivot.groupby("count").size().sort_index()
        log.info(f"    Celdas por conteo: {', '.join(f'{c}={k}' for c, k in by_count.items())}")

    rows = []
    for _, row in pivot.iterrows():
        runners = str(row["runners"])
        runners_label = RUNNERS_LABELS.get(runners, f"Runners state {runners}")
        rows.append({
            "season": int(row["season"]),
            "type": BREAKEVEN_TYPE_SWING_TAKE,
            "scenario": f"Count {row['count']} | {runners_label}",
            "label": runners_label,
            "outs": int(row["outs"]),
            "breakeven_pct": float(row["breakeven_pct"]) if pd.notna(row["breakeven_pct"]) else float("nan"),
            "n": int(row["n"]),
        })

    return rows


def calculate_swing_take_zone_breakeven(zone_df: pd.DataFrame, n_lookup: dict) -> list:
    """
    Swing/take break-even by Statcast zone and handedness split.

    Source threshold query returns season-level break-even values pivoted by count.
    Sample-size view provides season-matched n values by
    (season, zone, handedness, count).
    """
    zone_df = zone_df.copy()
    zone_df.columns = [str(col).lower() for col in zone_df.columns]

    required = {
        "season", "zone", "handedness_split",
        "c_0_2", "c_1_2", "c_0_1", "c_2_2", "c_1_1", "c_0_0",
        "c_1_0", "c_2_1", "c_3_2", "c_2_0", "c_3_1", "c_3_0",
    }
    missing = required - set(zone_df.columns)
    if missing:
        raise ValueError(f"Swing/take zone view missing required columns: {missing}")

    count_col_map = {
        "c_0_2": "0-2",
        "c_1_2": "1-2",
        "c_0_1": "0-1",
        "c_2_2": "2-2",
        "c_1_1": "1-1",
        "c_0_0": "0-0",
        "c_1_0": "1-0",
        "c_2_1": "2-1",
        "c_3_2": "3-2",
        "c_2_0": "2-0",
        "c_3_1": "3-1",
        "c_3_0": "3-0",
    }

    rows = []
    floored = 0
    floored_worst = 0.0
    for _, row in zone_df.iterrows():
        season = int(row["season"])
        zone = int(row["zone"])
        handedness = str(row["handedness_split"]).strip()
        label = f"Zone {zone} | {handedness}"

        for col, count in count_col_map.items():
            value = row[col]
            if pd.isna(value):
                continue
            value = float(value)

            # Un break-even negativo aqui SI tiene lectura, y con el modelo de
            # tres desenlaces dejo de ser marginal: pasa de 245 a 1,380 celdas.
            #
            # Con dos strikes y lanzamiento en zona, tomar es strike cantado tres.
            # Un swing que nunca pone la bola en juego todavia produce foules una
            # fraccion del tiempo, y el foul no cuesta nada. Asi que el valor de
            # "abanicar sin llegar a conectar" SUPERA al de tomar, y el numerador
            # se vuelve negativo. Significa: abanica igualmente, aunque tu
            # probabilidad de poner la bola en juego fuera cero.
            #
            # Medido: el 100% de los negativos cae dentro de la zona (0% fuera).
            # Con 2 strikes son el 55.8% de las celdas en zona, con mediana -0.161;
            # con 0 y 1 strikes son residuales (9.7% y 4.7%, mediana -0.006), que
            # si es el ruido alrededor de cero de antes.
            #
            # Se lleva a 0 porque la DECISION es la misma -- 0% de contacto
            # necesario es "abanica siempre" -- y un porcentaje negativo en la
            # tabla se lee como un error. Se pierde el margen, no la conclusion.
            if value < 0.0:
                floored += 1
                floored_worst = min(floored_worst, value)
                value = 0.0

            rows.append({
                "season": season,
                "type": BREAKEVEN_TYPE_SWING_TAKE_ZONE,
                "scenario": f"Count {count} | {label}",
                "label": label,
                "outs": 0,
                "breakeven_pct": value,
                "n": int(n_lookup.get((season, zone, handedness, count), 0)),
            })

    if floored:
        log.info(f"  Swing/take zona: {floored:,} celdas con break-even negativo llevadas a 0 "
              f"(mas negativo: {floored_worst:.4f}). Son sobre todo lanzamientos en zona "
              f"con 2 strikes: abanicar bate a tomar aunque no se conecte.")
    return rows


def load_ibb_real_values(data_dir: Path) -> pd.DataFrame:
    """
    Load real IBB run values (event_cd == 15) from re24_values.parquet.

    Returns a DataFrame with columns: season, outs, runners, n, run_value
    already aggregated per (season, outs, runners).
    """
    parquet_path = data_dir / "re24_values.parquet"
    log.info(f"\nReading IBB data (event_cd=15) from: {parquet_path.name}")

    df = pd.read_parquet(parquet_path, engine="pyarrow")
    ibb = df.loc[df["event_cd"] == 15, ["season", "outs", "runners", "n", "run_value"]].copy()
    log.info(f"  IBB rows (event_cd == 15): {len(ibb):,}")
    if len(ibb) > 0:
        log.info(f"  Seasons: {ibb['season'].min()} – {ibb['season'].max()}")
    return ibb


def calculate_sac_bunt_delta_re(re_lookup: dict, n_lookup: dict, seasons: list) -> list:
    """
    Theoretical sac bunt RE impact.

    For each season and scenario the batter bunts successfully: the runner(s)
    advance one base and the batter is retired.  Only 0- and 1-out situations
    are included (bunting with 2 outs is strategically irrelevant).

    delta_re = RE(after_state, outs + 1) − RE(before_state, outs)
    n        = frequency of the before base-out state in the RE24 matrix.
    """
    rows = []
    for season in seasons:
        for label, before_state, after_state in SAC_BUNT_SCENARIOS:
            for outs in range(2):  # 0 and 1 outs only
                re_before = re_lookup.get((season, outs, before_state))
                re_after  = re_lookup.get((season, outs + 1, after_state))
                n_before  = n_lookup.get((season, outs, before_state))
                if re_before is None or re_after is None or n_before is None:
                    continue
                delta_re = re_after - re_before
                # Store percentage as a ratio in [0, 1] scale, not [0, 100].
                delta_re_pct = (delta_re / abs(re_before)) if re_before != 0 else None
                rows.append({
                    "season":   season,
                    "type":     "sac_bunt",
                    "scenario": before_state,
                    "label":    label,
                    "outs":     outs,
                    "delta_re": delta_re,
                    "baseline_re": re_before,
                    "delta_re_pct": delta_re_pct,
                    "n":        n_before,
                })
    return rows


def calculate_ibb_impact(ibb_df: pd.DataFrame, all_seasons: list, re_lookup: dict) -> list:
    """
    IBB real run-value impact from per-event data.

    run_value en las filas de evento (event_cd=15) es la RE ABSOLUTA posterior
    al evento, no un delta — ver la nota del docstring del modulo. El impacto de
    la IBB es por tanto:

        delta_re = run_value_post_ibb − RE(estado inicial)

    Only the scenarios listed in IBB_SCENARIOS are emitted.
    Currently this is 3 scenarios: 001, 010, 011.
    """
    # Build lookup: (season, outs, runners) → (post-event absolute RE, n)
    ibb_lookup = {}
    for _, row in ibb_df.iterrows():
        key = (int(row["season"]), int(row["outs"]), row["runners"])
        ibb_lookup[key] = (float(row["run_value"]), int(row["n"]))

    rows = []
    skipped_small_n = 0
    skipped_no_baseline = 0
    for season in all_seasons:
        for label, runners in IBB_SCENARIOS:
            for outs in range(3):
                result = ibb_lookup.get((season, outs, runners))
                if result is None:
                    continue
                post_ibb_re, n = result

                if n < MIN_IBB_N:
                    skipped_small_n += 1
                    continue

                baseline_re = re_lookup.get((season, outs, runners))
                if baseline_re is None:
                    # Sin RE del estado inicial no hay delta que calcular.
                    skipped_no_baseline += 1
                    continue

                delta_re = post_ibb_re - float(baseline_re)
                delta_re_pct = (delta_re / abs(baseline_re)) if baseline_re != 0 else None
                rows.append({
                    "season":   season,
                    "type":     "ibb",
                    "scenario": runners,  # Scenario code for data matching
                    "label":    label,    # Descriptive label for display
                    "outs":     outs,
                    "delta_re": delta_re,
                    "baseline_re": baseline_re,
                    "delta_re_pct": delta_re_pct,
                    "n":        n,
                })

    log.info(f"  IBB: {len(rows):,} celdas publicadas")
    if skipped_small_n:
        log.info(f"  IBB: {skipped_small_n:,} celdas descartadas por n < {MIN_IBB_N}")
    if skipped_no_baseline:
        log.info(f"  IBB: {skipped_no_baseline:,} celdas descartadas por falta de RE base")
    return rows


def connect_to_database():
    """
    Engine compartido hacia la base DGL (ver utils/db.py).

    El engine se cachea: este script consulta seis vistas y antes abria una
    conexion nueva para cada una. Los servidores se configuran con
    DGL_SQL_SERVERS en el .env, no en el codigo.
    """
    return db.get_engine()


def _handness_to_platoon(value) -> object:
    """Normalize SQL handness values to boolean platoon flags (True/False) or pd.NA."""
    if pd.isna(value):
        return pd.NA

    if isinstance(value, bool):
        return value

    text_value = str(value).strip().lower()
    if text_value in {"platoon", "true", "1", "t", "yes", "y"}:
        return True
    if text_value in {"same", "false", "0", "f", "no", "n"}:
        return False
    return pd.NA


def _build_ph_handedness_synthetic_rows(base_df: pd.DataFrame, impact_type: str) -> pd.DataFrame:
    """
    Create synthetic handedness rows at each (season, outs):
      - Same    := weighted mean of (RHP vs RHB, LHP vs LHB)
      - Platoon := weighted mean of (RHP vs LHB, LHP vs RHB)
    """
    synthetic_specs = {
        "Same": ["RHP vs RHB", "LHP vs LHB"],
        "Platoon": ["RHP vs LHB", "LHP vs RHB"],
    }

    rows = []
    grouped = base_df.groupby(["season", "outs"], dropna=False)
    for (season, outs), grp in grouped:
        for synthetic_label, source_labels in synthetic_specs.items():
            src = grp[grp["handedness_split"].isin(source_labels)]
            if src.empty:
                continue

            total_n = int(src["n"].sum())
            if total_n <= 0:
                continue

            weighted_delta = float((src["delta_re"] * src["n"]).sum() / total_n)
            rows.append({
                "season": int(season),
                "type": impact_type,
                "scenario": synthetic_label,
                "label": synthetic_label,
                "outs": int(outs),
                "delta_re": weighted_delta,
                "baseline_re": pd.NA,
                "delta_re_pct": pd.NA,
                "n": total_n,
                "platoon": pd.NA,
                "handedness_split": synthetic_label,
            })

    if not rows:
        return pd.DataFrame(columns=base_df.columns)
    return pd.DataFrame(rows)


PH_POSITION_BUCKET_ORDER = [
    "RHP vs RHB",
    "LHP vs LHB",
    "RHP vs LHB",
    "LHP vs RHB",
    "Same",
    "Platoon",
]


def _normalize_ph_position_bucket(value: object) -> object:
    if pd.isna(value):
        return pd.NA
    v = str(value).strip().upper()
    mapping = {
        "RHP VS RHB": "RHP vs RHB",
        "LHP VS LHB": "LHP vs LHB",
        "RHP VS LHB": "RHP vs LHB",
        "LHP VS RHB": "LHP vs RHB",
    }
    return mapping.get(v, pd.NA)


def _normalize_ph_view_df(df: pd.DataFrame, impact_type: str, source_label: str) -> pd.DataFrame:
    """
    Normalize PH rows into handedness-bucket shape.

    Sirve para las dos vistas de bateador emergente -- reemplazo de jugador de
    posicion y reemplazo de lanzador -- que tienen exactamente la misma forma.
    Antes eran dos funciones de 45 lineas identicas salvo el valor de 'type' y
    el texto del aviso.

    Expected SQL columns:
      season, outs, handedness_bucket, n, delta_run_value
    """
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]

    required = {"season", "outs", "handedness_bucket", "n", "delta_run_value"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"PH {source_label} view data missing required columns: {missing}")

    out = df[["season", "outs", "handedness_bucket", "n", "delta_run_value"]].copy()
    out["handedness_split"] = out["handedness_bucket"].map(_normalize_ph_position_bucket)

    pre_filter_rows = len(out)
    out = out[out["handedness_split"].notna()].copy()
    dropped_rows = pre_filter_rows - len(out)
    if dropped_rows > 0:
        log.info(f"  Warning: Dropped {dropped_rows:,} PH {source_label} rows due to unmapped handedness_bucket values")

    out["type"] = impact_type
    out["scenario"] = out["handedness_split"]
    out["label"] = out["handedness_split"]
    out["delta_re"] = out["delta_run_value"]
    out["baseline_re"] = pd.NA
    out["delta_re_pct"] = pd.NA
    out["platoon"] = pd.NA

    out = out[[
        "season", "type", "scenario", "label", "outs", "delta_re",
        "baseline_re", "delta_re_pct", "n", "platoon", "handedness_split"
    ]]

    synthetic = _build_ph_handedness_synthetic_rows(out, impact_type=impact_type)
    if not synthetic.empty:
        out = pd.concat([out, synthetic], ignore_index=True)

    order_map = {name: idx for idx, name in enumerate(PH_POSITION_BUCKET_ORDER)}
    out["_bucket_order"] = out["handedness_split"].map(order_map).fillna(999)
    out = out.sort_values(["season", "outs", "_bucket_order"], kind="mergesort").drop(columns=["_bucket_order"])

    return out


def _read_sql_view(engine, query: str, view_name: str,
                   por_lanzamiento: bool = False) -> pd.DataFrame:
    """
    Lee una vista SQL, con log de progreso y timeout opcional.

    PUNTO UNICO DONDE SE ACOTA LA TEMPORADA. Todas las vistas pasan por aqui, asi
    que este es el sitio donde el rango de utils/seasons.py se cumple para todas
    a la vez -- incluidas las que se anadan manana y se olviden de acotarse.

    Hacia falta: `v_re_analysis_swing_take` ya venia con un tope, derivado del
    maximo del parquet de re24, pero `..._swing_take_zone` NO tenia ninguno.
    Por esa unica puerta se colaban 302 filas de la temporada EN CURSO, que se
    presentaban en la tabla igual que un anio entero y sin marca alguna.

    UNA VISTA PUEDE SER MAS ESTRICTA QUE EL RANGO, y eso no se toca desde aqui:

    - `_stretch` y `_advance` arrancan en 1989 por su cuenta. NO es el suelo de
      conteo aunque el anio coincida: es cuando la localizacion del batazo llega
      al 95.8% de cobertura, frente al 89.9% de 1988 y el 45% de 1987. Es una
      restriccion de calidad de ESE analisis, medida y anotada en la propia
      vista, asi que vive alli y no cuelga de MIN_PER_PITCH_SEASON -- si algun
      dia ese parametro se moviera, estas dos no deben seguirlo.
    - `_ph_pitchers` termina en 2021, ultimo anio en que los lanzadores batearon
      con regularidad antes del DH universal.

    El rango de seasons.py acota; no obliga a nadie a ampliar.
    """
    log.info(f"  Querying {view_name}...")

    try:
        with engine.connect() as conn:
            if PH_QUERY_TIMEOUT_SECONDS > 0:
                # Fail faster only on lock waits; avoids pandas warning from raw DBAPI usage.
                conn.exec_driver_sql(f"SET LOCK_TIMEOUT {int(PH_QUERY_TIMEOUT_SECONDS * 1000)}")
            df = pd.read_sql_query(text(query), conn)
    except KeyboardInterrupt as exc:
        raise RuntimeError(
            f"Query for {view_name} was interrupted. "
            "The SQL view is likely slow or blocked. "
            "If you want the script to fail faster, set PH_QUERY_TIMEOUT_SECONDS."
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"Failed to query {view_name}: {exc}") from exc

    lo, hi = seasons.rango(por_lanzamiento)
    df, descartadas = seasons.recortar(df, "season", por_lanzamiento)
    if descartadas:
        log.info(
            f"  {view_name}: {descartadas:,} filas fuera de {lo}-{hi} descartadas "
            f"(la vista no acota; se acota aqui)"
        )

    log.info(f"  ✓ {view_name}: {len(df):,} rows")
    return df


def load_bunt_break_even_rows_from_db() -> list:
    """
    Toque sorpresa y squeeze desde la vista, ya agregados por celda y cubo.
    """
    log.info("\nExtracting bunt break-even rows from SQL view...")
    engine = connect_to_database()

    bunt_df    = _read_sql_view(engine, QUERY_BUNT, "dbo.v_re_analysis_bunt")
    effect_df  = _read_sql_view(engine, QUERY_BUNT_BATTER_EFFECT,
                                "dbo.v_re_analysis_bunt_batter_effect")
    weights_df = _read_sql_view(engine, QUERY_BUNT_WEIGHTS,
                                "dbo.v_re_analysis_bunt_weights")

    rows = calculate_bunt_breakeven(bunt_df, effect_df, weights_df)
    log.info(f"  Normalized bunt BE rows: {len(rows):,}")
    return rows


def load_swing_take_break_even_rows_from_db(season_cap: int) -> list:
    """
    Load swing/take pitch-level rows from SQL and convert them into break-even rows.

    season_cap is applied so Statcast data does not exceed max season present in re24 parquet.

    La agregacion ocurre en el servidor: la vista tiene ~12.8M filas a nivel de
    lanzamiento y solo se necesitan media, desviacion y n por celda.
    """
    log.info("\nExtracting swing/take break-even rows from SQL view...")
    engine = connect_to_database()

    query = QUERY_SWING_TAKE_AGG.format(season_cap=int(season_cap))

    swing_agg = _read_sql_view(engine, query, f"dbo.v_re_analysis_swing_take (agregado, <= {int(season_cap)})")

    rows = calculate_swing_take_breakeven(swing_agg)
    log.info(f"  Normalized swing/take BE rows: {len(rows):,}")
    return rows


def load_swing_take_zone_break_even_rows_from_db() -> list:
    """
    Load swing/take zone break-even rows from the final handedness SQL view.

    Threshold values are joined with season-specific sample sizes so output
    rows carry an explicit season dimension.
    """
    log.info("\nExtracting swing/take zone break-even rows from SQL view...")
    engine = connect_to_database()

    zone_query = QUERY_SWING_TAKE_ZONE.format(
        min_bucket_n=int(MIN_SWING_TAKE_BUCKET_N),
        min_z=float(SWING_TAKE_MIN_Z),
    )

    # La etiqueta decia 'v_re_analysis_swing_take_zone_final_handedness', pero
    # esta consulta lee la vista BASE y hace su propio agregado. Aquella vista, y
    # la _aggr_handedness de la que depende, no las consulta nadie del pipeline.
    zone_df = _read_sql_view(engine, zone_query, "dbo.v_re_analysis_swing_take_zone (agregado por zona)")
    n_df = _read_sql_view(engine, QUERY_SWING_TAKE_ZONE_N, "dbo.v_re_analysis_swing_take_zone (n by season/zone/handedness/count)")

    n_df = n_df.copy()
    n_df.columns = [str(col).lower() for col in n_df.columns]
    required_n = {"season", "zone", "handedness_split", "count", "n_total"}
    missing_n = required_n - set(n_df.columns)
    if missing_n:
        raise ValueError(f"Swing/take zone n query missing required columns: {missing_n}")

    n_lookup = {
        (
            int(r["season"]),
            int(r["zone"]),
            str(r["handedness_split"]).strip(),
            str(r["count"]).strip(),
        ): int(r["n_total"])
        for _, r in n_df.iterrows()
        if pd.notna(r["season"]) and pd.notna(r["zone"]) and pd.notna(r["handedness_split"]) and pd.notna(r["count"]) and pd.notna(r["n_total"])
    }

    rows = calculate_swing_take_zone_breakeven(zone_df, n_lookup=n_lookup)
    log.info(f"  Normalized swing/take zone BE rows: {len(rows):,}")
    return rows


def load_ph_impact_rows_from_db(data_dir: Path, re_lookup: dict) -> list:
    """
    Load PH impact rows directly from pre-aggregated SQL views and save a raw snapshot parquet.

    Views:
      - dbo.v_re_analysis_ph_position_players
      - dbo.v_re_analysis_ph_pitchers
    """
    log.info("\nExtracting PH impact from SQL views...")
    engine = connect_to_database()

    df_position = _read_sql_view(engine, QUERY_PH_POSITION_PLAYERS, "dbo.v_re_analysis_ph_position_players")
    df_pitcher = _read_sql_view(engine, QUERY_PH_PITCHERS, "dbo.v_re_analysis_ph_pitchers")

    log.info(f"  Position-player PH rows from view: {len(df_position):,}")
    log.info(f"  Pitcher PH rows from view:         {len(df_pitcher):,}")

    # Save raw snapshot for reproducibility/debugging of upstream SQL view outputs.
    raw_position = df_position.copy()
    raw_position["ph_group"] = "position_players"
    raw_pitcher = df_pitcher.copy()
    raw_pitcher["ph_group"] = "pitchers"
    raw_snapshot = pd.concat([raw_position, raw_pitcher], ignore_index=True)
    # Las consultas de las vistas PH no llevan ORDER BY, asi que el orden de
    # filas que devuelve SQL Server no esta garantizado entre ejecuciones. Este
    # fichero existe para poder reproducir y depurar la salida de las vistas, y
    # sin un orden estable no cumple ese proposito: dos lotes identicos no se
    # pueden comparar.
    #
    # (Los VALORES tampoco son bit-identicos entre corridas, por la agregacion
    # paralela de SQL Server; ver la nota de QUERY_WIN_EXPECTANCY en 01. Aqui se
    # nota mas porque la columna se guarda en float64 y no hay un cast a float32
    # que absorba el ruido: 1,926 de 2,475 filas difieren, pero como maximo en
    # 2.9e-16.)
    raw_snapshot = raw_snapshot.sort_values(
        ["ph_group", "season", "outs", "handedness_bucket"], kind="mergesort"
    ).reset_index(drop=True)
    raw_snapshot.to_parquet(data_dir / "ph_impact_source.parquet", index=False, engine="pyarrow")
    log.info("  ✓ Saved source snapshot: ph_impact_source.parquet")

    normalized_position = _normalize_ph_view_df(df_position, "ph_position", "position")
    normalized_pitcher = _normalize_ph_view_df(df_pitcher, "ph_pitcher", "pitcher")
    ph_df = pd.concat([normalized_position, normalized_pitcher], ignore_index=True)

    log.info(f"  Normalized PH impact rows:         {len(ph_df):,}")
    if len(ph_df) > 0:
        log.info(f"  Seasons: {ph_df['season'].min()} – {ph_df['season'].max()}")

    return ph_df.to_dict(orient="records")


def _normalize_platoon_impact_view_df(df: pd.DataFrame, re_lookup: dict) -> pd.DataFrame:
    """Normalize platoon baseline RE impact rows into the common re_impact schema shape."""
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]

    required = {"season", "outs", "handedness_split", "n", "delta_run_value"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Platoon baseline impact view data missing required columns: {missing}")

    base_cols = ["season", "outs", "handedness_split", "n", "delta_run_value"]
    out = df[base_cols].copy()
    out["handedness_split"] = out["handedness_split"].astype(str).str.strip()
    out["delta_re"] = out["delta_run_value"]
    out["type"] = "platoon_baseline"
    out["scenario"] = out["handedness_split"]
    out["label"] = out["handedness_split"]
    out["platoon"] = pd.NA

    if "baseline_avg_run_value" in df.columns:
        out["baseline_re"] = df["baseline_avg_run_value"]
    else:
        if "runners" not in df.columns:
            raise ValueError(
                "Platoon baseline view missing baseline_avg_run_value and runners. "
                "Provide baseline_avg_run_value in SQL view or include runners for fallback lookup."
            )
        out["runners"] = _normalizar_runners(df["runners"], "dbo.v_re_analysis_platoon_baseline")
        out["baseline_re"] = out.apply(
            lambda r: re_lookup.get((int(r["season"]), int(r["outs"]), str(r["runners"]))),
            axis=1,
        )

    if False:  # El porcentaje de SQL ya no se usa. Ver la nota de abajo.
        # POR QUE SE DEJO DE USAR delta_run_value_scaled.
        #
        # La vista lo divide entre `baseline_abs_run_value`, que NO es el valor
        # absoluto de `baseline_avg_run_value`: es otra cantidad. Convivian por
        # tanto dos definiciones de la misma columna, y el interruptor de
        # Percentages las presentaba como si fueran la misma metrica: un -22% en
        # sac_bunt significa "pierdes el 22% de tu expectativa" y en platoon
        # significaba otra cosa que se lee igual.
        #
        # Ahora platoon pasa por el mismo camino que el resto: delta / |baseline|.
        out["delta_re_pct"] = df["delta_run_value_scaled"]
        nulls_from_sql = int(out["delta_re_pct"].isna().sum())
        if nulls_from_sql > 0:
            log.info(
                f"  Note: {nulls_from_sql:,} filas platoon_baseline llegan con "
                f"delta_run_value_scaled nulo desde la vista SQL"
            )
    else:
        # Solo esta rama suprime por baseline cercano a cero, asi que solo aqui
        # tiene sentido informar de ello. El log anterior contaba TODOS los nulos
        # -- incluidos los que venian nulos del SQL en la rama de arriba -- y los
        # atribuia a la supresion por baseline, que ni siquiera se habia
        # ejecutado.
        suppressed = {"missing": 0, "zero": 0, "small": 0}

        def _compute_pct_row(r):
            baseline = r["baseline_re"]
            delta = r["delta_re"]
            if pd.isna(baseline) or pd.isna(delta):
                suppressed["missing"] += 1
                return pd.NA
            baseline_f = float(baseline)
            if baseline_f == 0.0:
                suppressed["zero"] += 1
                return pd.NA
            if RE_IMPACT_PCT_MIN_BASELINE_ABS > 0 and abs(baseline_f) < RE_IMPACT_PCT_MIN_BASELINE_ABS:
                suppressed["small"] += 1
                return pd.NA
            # SIEMPRE el valor absoluto: con baseline negativo, dividir con
            # signo invierte la lectura. Ver el test de esta invariante.
            return float(delta) / abs(baseline_f)

        out["delta_re_pct"] = out.apply(_compute_pct_row, axis=1)

        if any(suppressed.values()):
            log.info(
                f"  Note: delta_re_pct de platoon_baseline suprimido en "
                f"{sum(suppressed.values()):,} filas -> "
                f"{suppressed['missing']:,} sin baseline o sin delta, "
                f"{suppressed['zero']:,} con baseline exactamente 0, "
                f"{suppressed['small']:,} con |baseline| < {RE_IMPACT_PCT_MIN_BASELINE_ABS}"
            )

    return out[[
        "season", "type", "scenario", "label", "outs", "delta_re",
        "baseline_re", "delta_re_pct", "n", "platoon", "handedness_split"
    ]]


def load_platoon_baseline_impact_rows_from_db(re_lookup: dict) -> list:
    """
    Load handedness-split RE impact rows from SQL view.

    Source view:
      - dbo.v_re_analysis_platoon_impact_final
    """
    log.info("\nExtracting platoon baseline RE impact from SQL view...")
    engine = connect_to_database()

    df_platoon = _read_sql_view(engine, QUERY_RE_IMPACT_PLATOON_BASELINE, "dbo.v_re_analysis_platoon_impact_final")

    normalized = _normalize_platoon_impact_view_df(df_platoon, re_lookup=re_lookup)
    log.info(f"  Normalized platoon baseline impact rows: {len(normalized):,}")
    if len(normalized) > 0:
        log.info(f"  Seasons: {normalized['season'].min()} – {normalized['season'].max()}")

    return normalized.to_dict(orient="records")


def _normalize_tto_impact_view_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize TTO RE impact rows into the common re_impact schema shape."""
    required = {"season", "runners", "handedness_split", "tto_pass", "n", "delta_run_value"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"TTO impact view data missing required columns: {missing}")

    out = df[["season", "runners", "handedness_split", "tto_pass", "n", "delta_run_value"]].copy()
    out["runners"] = _normalizar_runners(out["runners"], "dbo.v_re_analysis_tto_impact")
    out["handedness_split"] = out["handedness_split"].astype(str).str.strip()
    out["tto_pass"] = out["tto_pass"].astype(str).str.strip()

    def _normalize_tto_pass(v: str) -> str:
        u = str(v).strip().upper()
        if u in {"1", "TTO1", "TTO-1"}:
            return "TTO-1"
        if u in {"2", "TTO2", "TTO-2"}:
            return "TTO-2"
        if u in {"3", "TTO3", "TTO-3"}:
            return "TTO-3"
        if u in {"4+", "4PLUS", "TTO4+", "TTO-4+", "TTO-4PLUS"}:
            return "TTO-3"
        return str(v).strip()

    out["tto_pass"] = out["tto_pass"].map(_normalize_tto_pass)

    out["type"] = "tto"
    out["scenario"] = out["runners"]
    out["label"] = out["runners"].map(RUNNERS_LABELS).fillna(out["runners"])
    out["outs"] = 0
    out["delta_re"] = out["delta_run_value"]
    out["baseline_re"] = pd.NA
    out["delta_re_pct"] = df["delta_run_value_scaled"] if "delta_run_value_scaled" in df.columns else pd.NA
    out["platoon"] = pd.NA

    # TTO-1 is the baseline bucket by definition; force exact zeros to avoid -0.00 artifacts.
    tto1_mask = out["tto_pass"] == "TTO-1"
    out.loc[tto1_mask, "delta_re"] = 0.0
    out.loc[tto1_mask, "delta_re_pct"] = 0.0

    return out[[
        "season", "type", "scenario", "label", "outs", "delta_re",
        "baseline_re", "delta_re_pct", "n", "platoon", "handedness_split", "tto_pass"
    ]]


QUERY_SB_ATTEMPTS = """
SELECT season, outs, runners, scenario, n_success, n_fail, n_attempts
FROM dbo.v_re_analysis_sb_attempts
"""


def load_steal_impact_rows_from_db(re_lookup: dict, seasons_list: list) -> list:
    """
    El robo expresado en CARRERAS, no como porcentaje de break-even.

    Los tres componentes salen de la matriz RE24, igual que el break-even; lo
    que aporta la vista `v_re_analysis_sb_attempts` es la TASA DE EXITO
    OBSERVADA, que el break-even no necesita y el valor esperado si:

        neto = p * ganancia - (1 - p) * coste

    Sin ella solo se podian publicar ganancia y coste por separado.

    Se reutilizan las mismas guardas que el break-even, porque los datos son los
    mismos: si robar con exito BAJA la RE, la celda esta mal estimada --mismos
    outs, corredor en mejor base, no puede bajar-- y no se publica un numero en
    carreras que le daria credibilidad.
    """
    log.info("")
    log.info("Extracting steal impact (runs) from SQL view...")
    engine = connect_to_database()

    intentos = _read_sql_view(engine, QUERY_SB_ATTEMPTS, "dbo.v_re_analysis_sb_attempts")
    if intentos.empty:
        log.info("  sin intentos de robo registrados")
        return []

    requeridas = {"season", "outs", "scenario", "n_success", "n_attempts"}
    faltan = requeridas - set(intentos.columns)
    if faltan:
        raise ValueError(f"SB attempts view missing required columns: {faltan}")

    por_celda = {
        (int(r["season"]), int(r["outs"]), str(r["scenario"])): (int(r["n_success"]), int(r["n_attempts"]))
        for _, r in intentos.iterrows()
    }

    rows = []
    for season in seasons_list:
        for label, antes, despues, tras_cs in SB_SCENARIOS:
            for outs in range(3):
                clave = (season, outs, label)
                if clave not in por_celda:
                    continue
                exitos, intentos_n = por_celda[clave]
                if intentos_n == 0:
                    continue

                re_antes = re_lookup.get((season, outs, antes))
                re_robo = re_lookup.get((season, outs, despues))
                # Con 2 outs el atrapado es el tercero: la entrada acaba y la RE
                # es 0 por definicion, no un valor que buscar en la matriz.
                re_cs: float | None
                if outs == 2:
                    re_cs = 0.0
                elif label == "Double steal":
                    # El atrapado del doble robo es una MEZCLA: 0.863 el de
                    # delante, 0.137 el de atras, medido sobre r24Transitions.
                    # Usar un solo estado hace que esta tabla y la de break-even
                    # dejen de cuadrar -- lo detecto el test de la identidad, con
                    # 232 celdas descuadradas de 348.
                    lead = re_lookup.get((season, outs + 1, tras_cs))
                    trail = re_lookup.get((season, outs + 1, DOUBLE_STEAL_TRAIL_CAUGHT_STATE))
                    re_cs = None if lead is None or trail is None else (
                        DOUBLE_STEAL_LEAD_CAUGHT_WEIGHT * lead
                        + (1.0 - DOUBLE_STEAL_LEAD_CAUGHT_WEIGHT) * trail
                    )
                else:
                    re_cs = re_lookup.get((season, outs + 1, tras_cs))
                if re_antes is None or re_robo is None or re_cs is None:
                    continue

                carreras = impacto_en_carreras(
                    quedarse=re_antes, exito=re_robo, fallo=re_cs,
                    p_exito=exitos / intentos_n,
                )
                if not _steal_gain_is_possible(re_antes, re_robo):
                    carreras["delta_re"] = None

                rows.append({
                    "season": season,
                    "type": "steal_runs",
                    # scenario ES EL TEXTO, igual que en breakeven_points.
                    #
                    # Llego a ser el estado de bases para que la tabla dibujara
                    # el rombo del campo, y sobraba: el frontend ya tiene ese
                    # mapeo en BREAKEVEN_SCENARIOS, que ademas define que filas
                    # se ensenan. Duplicarlo aqui rompia el cruce con la tabla de
                    # break-even, que es lo que vigila la identidad
                    # BE = coste / (ganancia + coste).
                    "scenario": label,
                    "label": label,
                    "outs": outs,
                    "delta_re": carreras["delta_re"],
                    "baseline_re": re_antes,
                    # Ya tenia el baseline y le faltaba solo esto, asi que el
                    # interruptor de Percentages salia vacio en Stolen Bases.
                    "delta_re_pct": (carreras["delta_re"] / abs(re_antes))
                                    if (re_antes and carreras["delta_re"] is not None) else None,
                    "gain_runs": carreras["gain_runs"],
                    "cost_runs": carreras["cost_runs"],
                    # Los INTENTOS, no los fallos: el break-even publica n_fail
                    # porque domina su varianza, pero esta tabla mide otra cosa.
                    "n": intentos_n,
                })

    log.info(f"  Steal impact rows (runs): {len(rows):,}")
    return rows


def load_tto_impact_rows_from_db() -> list:
    """
    Load TTO RE impact rows from SQL view.

    Source view:
      - dbo.v_re_analysis_tto_impact_final_same_platoon_no_outs
    """
    log.info("\nExtracting TTO RE impact from SQL view...")
    engine = connect_to_database()

    df_tto = _read_sql_view(
        engine,
        QUERY_RE_IMPACT_TTO,
        "dbo.v_re_analysis_tto_impact_final_same_platoon_no_outs",
    )

    normalized = _normalize_tto_impact_view_df(df_tto)
    log.info(f"  Normalized TTO impact rows: {len(normalized):,}")
    if len(normalized) > 0:
        log.info(f"  Seasons: {normalized['season'].min()} - {normalized['season'].max()}")

    return normalized.to_dict(orient="records")


ALL_COUNTS = ["0-0", "0-1", "0-2", "1-0", "1-1", "1-2", "2-0", "2-1", "2-2", "3-0", "3-1", "3-2"]


def calculate_sb_breakeven_by_count(re288_lookup: dict, n288_lookup: dict, seasons: list) -> list:
    """
    Stolen-base break-even by pitch count using RE288.
    Same formula as RE24 version but keyed on (season, outs, runners, count).
    After a SB, the count stays the same (same at-bat continues).
    After a CS, the same batter continues with the same count.

        Double steal uses a weighted caught-stealing RE:
            RE_CS = 0.75 * RE(lead caught) + 0.25 * RE(trail caught)
    """
    rows = []
    for season in seasons:
        for label, before, after_sb, after_cs in SB_SCENARIOS:
            for count in ALL_COUNTS:
                for outs in range(3):  # 0, 1, 2
                    re_before = re288_lookup.get((season, outs, before, count))
                    re_sb     = re288_lookup.get((season, outs, after_sb, count))

                    cs_outs = outs + 1
                    if cs_outs >= 3:
                        re_cs = 0.0
                    else:
                        # After CS mid-PA, same batter continues with the same count
                        if label == "Double steal":
                            re_cs_lead = re288_lookup.get((season, cs_outs, after_cs, count))
                            re_cs_trail = re288_lookup.get((season, cs_outs, DOUBLE_STEAL_TRAIL_CAUGHT_STATE, count))
                            if re_cs_lead is None or re_cs_trail is None:
                                re_cs = None
                            else:
                                re_cs = (
                                    DOUBLE_STEAL_LEAD_CAUGHT_WEIGHT * re_cs_lead
                                    + (1.0 - DOUBLE_STEAL_LEAD_CAUGHT_WEIGHT) * re_cs_trail
                                )
                        else:
                            re_cs = re288_lookup.get((season, cs_outs, after_cs, count))

                    if re_before is None or re_sb is None or re_cs is None:
                        continue

                    denominator = re_sb - re_cs
                    if denominator == 0:
                        continue

                    be_pct = (re_before - re_cs) / denominator
                    n_before = n288_lookup.get((season, outs, before, count), 0)

                    # El umbral se aplicaba solo a la celda de PARTIDA, que es la
                    # mayor de las tres en el 94.1% de las filas (mediana 879
                    # frente a 374 del minimo). La fragil es la del robo
                    # consumado, que ademas va en el denominador: hay filas con
                    # n_before = 170 y n_sb = 81, o n_before = 44 y n_sb = 19.
                    # Ahora manda la mas pequeña de las que entran en la formula.
                    n_after_sb = n288_lookup.get((season, outs, after_sb, count), 0)
                    n_cells = [n_before, n_after_sb]
                    if cs_outs < 3:
                        n_cells.append(n288_lookup.get((season, cs_outs, after_cs, count), 0))
                    n_min = min(n_cells)

                    # Con 3-2 y 2 outs el corredor sale con el lanzamiento pase lo
                    # que pase, asi que no hay decision que evaluar.
                    if (count == "3-2" and outs == 2) or n_min < MIN_SB_BY_COUNT_N:
                        be_pct = float('nan')
                    elif not _steal_gain_is_possible(re_before, re_sb):
                        be_pct = float('nan')

                    rows.append({
                        "season":        season,
                        "scenario":      label,
                        "label":         label,
                        "count":         count,
                        "outs":          outs,
                        "breakeven_pct": be_pct,
                        "n":             n_before,
                    })
    return rows


# --- Red de seguridad sobre los break-even antes de escribir a disco -----------
#
# Cotas calibradas con los datos, no inventadas:
#
#   Cota inferior 0 (DURA). Un break-even negativo implicaria que el intento
#   mejora la RE aun fallando siempre: no tiene lectura sabermetrica y es
#   siempre un artefacto de dividir por un denominador ~0 o de signo invertido.
#   Ninguna familia legitima produce negativos.
#
#   Cota superior 25 (BLANDA). Un break-even > 1 SI es legitimo y significa
#   "no conviene ni al 100% de exito": ocurre al robar 3B con 2 outs y, sobre
#   todo, al decidir abanicar fuera de la zona (mediana 1.2-2.5). Maximos
#   legitimos observados: 9.84 swing-take, 7.01 zona, 3.25 sb_by_count, 1.19
#   en las familias RE24. 25 deja holgura de sobra y aun asi habria atrapado
#   la basura previa (-5239 a +5617).
BREAKEVEN_HARD_MIN = 0.0
BREAKEVEN_SANITY_MAX = float(os.getenv("BREAKEVEN_SANITY_MAX", "25"))
# Si mas de esta fraccion supera la cota blanda, no es una celda rara: es un bug.
BREAKEVEN_MAX_BAD_SHARE = float(os.getenv("BREAKEVEN_MAX_BAD_SHARE", "0.01"))


def _validate_breakeven_column(df: pd.DataFrame, dataset: str, group_col: str | None = None) -> pd.DataFrame:
    """
    Valida breakeven_pct antes de escribir el parquet.

    - Negativos: invariante duro; aborta con el desglose por grupo.
    - Por encima de BREAKEVEN_SANITY_MAX: se anulan a NaN y se reporta; si la
      proporcion supera BREAKEVEN_MAX_BAD_SHARE se aborta.

    Nunca recorta en silencio: todo lo que se descarta se registra.
    """
    col = df["breakeven_pct"]
    valid = col.notna()
    total = int(valid.sum())
    if total == 0:
        raise ValueError(f"{dataset}: no hay ningun breakeven_pct no nulo")

    def _breakdown(mask: pd.Series) -> str:
        if group_col and group_col in df.columns:
            g = df.loc[mask].groupby(group_col).size().sort_values(ascending=False)
            return ", ".join(f"{k}={v:,}" for k, v in g.items())
        return f"{int(mask.sum()):,} filas"

    negatives = valid & (col < BREAKEVEN_HARD_MIN)
    if negatives.any():
        raise ValueError(
            f"{dataset}: {int(negatives.sum()):,}/{total:,} break-even negativos "
            f"(minimo {col[negatives].min():.4f}). Un break-even negativo no tiene "
            f"lectura sabermetrica; revisa el signo del denominador. "
            f"Desglose: {_breakdown(negatives)}"
        )

    absurd = valid & (col > BREAKEVEN_SANITY_MAX)
    n_absurd = int(absurd.sum())
    if n_absurd:
        share = n_absurd / total
        msg = (f"{dataset}: {n_absurd:,}/{total:,} ({share:.1%}) break-even por encima de "
               f"{BREAKEVEN_SANITY_MAX} (max {col[absurd].max():.2f}). "
               f"Desglose: {_breakdown(absurd)}")
        if share > BREAKEVEN_MAX_BAD_SHARE:
            raise ValueError(msg + " — proporcion demasiado alta para ser casuistica; abortando.")
        log.info(f"  AVISO: {msg}")
        log.info(f"  AVISO: esas {n_absurd:,} filas se anulan a NaN.")
        df = df.copy()
        df.loc[absurd, "breakeven_pct"] = float("nan")

    over_one = int((valid & (col > 1.0)).sum())
    log.info(f"  Validacion: {total:,} break-even, 0 negativos, "
          f"{over_one:,} por encima de 1.0 (legitimo: nunca conviene intentarlo)")
    return df


def save_breakeven_points(sb_rows: list, data_dir: Path) -> pd.DataFrame:
    """
    Save break-even points parquet.
    Types currently include: stealing, running, tagging, bunt-for-hit,
    swing-take, and swing_take_zone.
    """
    df = pd.DataFrame(sb_rows)
    log.info(f"\n{'='*60}")
    log.info(f"Break-Even Points: {len(df):,} rows")
    log.info(f"  Scenarios: {df['scenario'].nunique()}")
    log.info(f"  Seasons:   {df['season'].min()} – {df['season'].max()}")

    df["season"]        = df["season"].astype("int32")
    df["type"]          = df["type"].astype("string")
    df["scenario"]      = df["scenario"].astype("string")
    if "label" not in df.columns:
        df["label"] = df["scenario"]
    df["label"]         = df["label"].astype("string")
    df["outs"]          = df["outs"].astype("uint8")
    df["breakeven_pct"] = df["breakeven_pct"].astype("float32")
    df["n"]             = df["n"].astype("int32")

    df = _validate_breakeven_column(df, "breakeven_points", group_col="type")

    output_file = data_dir / "breakeven_points.parquet"
    df.to_parquet(output_file, index=False, engine="pyarrow")
    log.info(f"✓ Saved to: {output_file.name}")
    return df


def save_re_impact(rows: list, data_dir: Path) -> pd.DataFrame:
    """
    Save RE impact parquet (sac_bunt, and future types like ibb, etc.).
    """
    df = pd.DataFrame(rows)
    log.info(f"\n{'='*60}")
    log.info(f"RE Impact: {len(df):,} rows")
    log.info(f"  Types:     {sorted(df['type'].unique().tolist())}")
    log.info(f"  Scenarios: {df['scenario'].nunique()}")
    log.info(f"  Seasons:   {df['season'].min()} – {df['season'].max()}")

    # baseline_re se calculaba en calculate_sac_bunt_delta_re, calculate_ibb_impact
    # y _normalize_platoon_impact_view_df, y luego se descartaba aqui, pese a que
    # el docstring del modulo lo lista como columna de salida. Se conserva: es el
    # denominador de delta_re_pct, asi que sin el ese porcentaje no se puede
    # verificar, y para las filas de IBB es la RE del estado inicial, que da el
    # contexto de si un delta de +0.02 es mucho o poco.
    keep_cols = [
        "season", "type", "scenario", "label", "outs", "delta_re",
        "baseline_re", "delta_re_pct", "n", "platoon", "handedness_split", "tto_pass",
        # Solo las traen los tipos *_runs: la ganancia si sale bien y el coste si
        # sale mal. El break-even las reduce a un cociente y pierde la magnitud.
        "gain_runs", "cost_runs"
    ]
    # reindex CREA todas las columnas de keep_cols, las traigan las filas o
    # no. Aqui habia seis `if "x" not in df.columns` justo despues, uno por
    # columna opcional, y ninguno podia ejecutarse: reindex ya las garantiza.
    df = df.reindex(columns=keep_cols)

    df["season"]   = df["season"].astype("int16")
    df["type"]     = df["type"].astype("string")
    df["scenario"] = df["scenario"].astype("string")
    # Label is used by the frontend for row display; keep schema explicit.
    df["label"] = df["label"].astype("string")
    df["outs"]     = df["outs"].astype("uint8")
    df["delta_re"] = df["delta_re"].astype("Float32")  # Nullable float for pd.NA support
    df["baseline_re"] = df["baseline_re"].astype("Float32")
    df["delta_re_pct"] = df["delta_re_pct"].astype("Float32")
    df["n"]        = df["n"].astype("int32")
    # platoon populated for PH rows only; NaN for all other types
    df["platoon"] = df["platoon"].astype("boolean")
    # Optional split label for handedness-based impact rows.
    df["handedness_split"] = df["handedness_split"].astype("string")
    # Optional TTO pass label for tto rows.
    df["tto_pass"] = df["tto_pass"].astype("string")
    df["gain_runs"] = df["gain_runs"].astype("Float32")
    df["cost_runs"] = df["cost_runs"].astype("Float32")
    output_file = data_dir / "re_impact.parquet"
    # La clave incluye handedness_split y tto_pass porque sin ellas no identifica
    # una fila de forma unica: las filas ph se distinguen por handedness y las tto
    # por pase. Con una clave incompleta los empates quedaban en el orden que
    # devolviera SQL y el parquet salia distinto en cada corrida con los mismos
    # datos, lo que impide comparar dos lotes byte a byte.
    df = df.sort_values(
        ["type", "season", "scenario", "outs", "handedness_split", "tto_pass"],
        kind="mergesort",
        na_position="last",
    )
    df.to_parquet(output_file, index=False, engine="pyarrow", compression="zstd")
    log.info(f"✓ Saved to: {output_file.name}")
    return df


def save_sb_breakeven_by_count(rows: list, data_dir: Path) -> pd.DataFrame:
    """
    Save stolen base break-even by count parquet.
    """
    df = pd.DataFrame(rows)
    log.info(f"\n{'='*60}")
    log.info(f"SB Break-Even by Count: {len(df):,} rows")
    log.info(f"  Scenarios: {df['scenario'].nunique()}")
    log.info(f"  Counts:    {df['count'].nunique()}")
    log.info(f"  Seasons:   {df['season'].min()} – {df['season'].max()}")

    df["season"]        = df["season"].astype("int32")
    df["scenario"]      = df["scenario"].astype("string")
    if "label" not in df.columns:
        df["label"] = df["scenario"]
    df["label"]         = df["label"].astype("string")
    df["count"]         = df["count"].astype("string")
    df["outs"]          = df["outs"].astype("uint8")
    df["breakeven_pct"] = df["breakeven_pct"].astype("float32")
    df["n"]             = df["n"].astype("int32")

    df = _validate_breakeven_column(df, "sb_breakeven_by_count", group_col="count")

    output_file = data_dir / "sb_breakeven_by_count.parquet"
    df.to_parquet(output_file, index=False, engine="pyarrow")
    log.info(f"✓ Saved to: {output_file.name}")
    return df


# Ficheros que este script LEE de extract_re_data.py. Si cambian y no se
# vuelve a ejecutar 03, las salidas de abajo quedan calculadas sobre una matriz
# de run expectancy distinta de la que se publica, y nada lo delata.
INPUTS_FROM_01 = ["re24_values.parquet", "re288_values.parquet"]

# Ficheros que este script ESCRIBE.
OUTPUTS = [
    "breakeven_points.parquet",
    "re_impact.parquet",
    "sb_breakeven_by_count.parquet",
    "ph_impact_source.parquet",
]

# Manifiesto de procedencia: deja constancia de QUE version de las entradas se
# uso para calcular las salidas.
DERIVED_MANIFEST = "derived_from.json"


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_derived_manifest(data_dir: Path) -> None:
    """
    Registra el hash de las entradas con las que se calcularon las salidas.

    Sin esto no habia forma de saber si breakeven_points.parquet y compania
    correspondian a la matriz RE publicada o a una anterior: ejecutar 01 sin
    ejecutar 03 despues los deja desfasados en silencio, y ya ocurrio durante la
    revision (al refrescar 01 cambiaron re24_values en +476 filas y
    re24_transitions en -4,144).

    Lo comprueba tests/test_data_invariants.py, que compara estos hashes contra
    los ficheros de entrada actuales.
    """
    import json
    from datetime import datetime

    manifest = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "producer": "create_break_even_points.py",
        "inputs": {
            name: _sha256(data_dir / name)
            for name in INPUTS_FROM_01
            if (data_dir / name).exists()
        },
        "outputs": [name for name in OUTPUTS if (data_dir / name).exists()],
    }

    path = data_dir / DERIVED_MANIFEST
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    log.info(f"  Procedencia registrada en {DERIVED_MANIFEST} "
             f"({len(manifest['inputs'])} entradas, {len(manifest['outputs'])} salidas)")


def main(data_dir=None):
    # data_dir es un parametro con valor por defecto y no una ruta fija dentro,
    # para poder escribir a un directorio temporal en los tests. En produccion
    # nadie lo pasa. Mismo patron que save_pythag_data y extract_and_save_data.
    data_dir = Path(data_dir) if data_dir is not None else Path(__file__).parent.parent / "app" / "data"

    log.info("=" * 60)
    log.info("Break-Even & Sac Bunt RE Calculator")
    log.info("=" * 60)

    re24 = load_re24_matrix(data_dir)

    re_lookup = (
        re24.set_index(["season", "outs", "runners"])["run_value"]
        .to_dict()
    )
    n_lookup = (
        re24.set_index(["season", "outs", "runners"])["n"]
        .to_dict()
    )
    # OJO: NO llamar a esto `seasons`. Ese nombre es el del modulo
    # utils/seasons.py, importado arriba, y una local con el mismo nombre lo
    # TAPA para el resto de la funcion. Paso: `seasons.MAX_SEASON` unas lineas
    # mas abajo reventaba con "'list' object has no attribute 'MAX_SEASON'"
    # en mitad del pipeline anual, despues de 20 minutos de consultas.
    temporadas = sorted(re24["season"].unique())

    sb_rows      = calculate_sb_breakeven(re_lookup, n_lookup, temporadas)
    stretch_be, stretch_runs = load_stretch_rows_from_db()
    advance_be, advance_runs = load_advance_rows_from_db()
    running_rows = stretch_be + advance_be
    # `running` es UNA familia de cinco escenarios: dos de estirar el batazo y
    # tres de avanzar en un batazo ajeno. Publicar solo los dos primeros dejaba
    # RE Impact con dos filas donde Break-Even ensena cinco.
    stretch_runs = stretch_runs + advance_runs
    tagging_be, tagging_runs = load_tag_up_rows_from_db()
    tagging_rows = tagging_be
    bunt_for_hit_be_rows = load_bunt_break_even_rows_from_db()
    # El tope sale del parametro y NO del maximo del parquet de re24.
    # Derivarlo del dato hacia que el rango publicado cambiara solo en cuanto
    # alguien cargara una temporada nueva, sin tocar una linea de codigo.
    swing_take_be_rows = load_swing_take_break_even_rows_from_db(
        season_cap=seasons.MAX_SEASON)
    swing_take_zone_rows = load_swing_take_zone_break_even_rows_from_db()

    # IBB real run values (event_cd == 15)
    ibb_df   = load_ibb_real_values(data_dir)
    ibb_rows = calculate_ibb_impact(ibb_df, temporadas, re_lookup=re_lookup)

    # Sac bunt RE impact (theoretical RE24 transition)
    sac_bunt_impact_rows = calculate_sac_bunt_delta_re(re_lookup, n_lookup, temporadas)

    # PH impact rows from pre-aggregated SQL views
    ph_rows = load_ph_impact_rows_from_db(data_dir, re_lookup=re_lookup)

    # Handedness split baseline RE impact from SQL view
    platoon_baseline_rows = load_platoon_baseline_impact_rows_from_db(re_lookup=re_lookup)

    # TTO RE impact from SQL view
    tto_impact_rows = load_tto_impact_rows_from_db()

    all_breakeven = sb_rows + running_rows + tagging_rows + bunt_for_hit_be_rows + swing_take_be_rows + swing_take_zone_rows
    df_be  = save_breakeven_points(all_breakeven, data_dir)
    # Los tres analisis de break-even, tambien en CARRERAS (seccion 10 del TODO):
    # el break-even dice que tasa hace falta, estas filas cuantas carreras se
    # juega uno. Robo, estirar el batazo y tag-up.
    steal_runs_rows = load_steal_impact_rows_from_db(re_lookup, temporadas)

    # Doble matanza: coste observado frente al out sencillo comparable (8.7).
    gidp_rows = load_gidp_rows_from_db(re_lookup)

    all_impact = (sac_bunt_impact_rows + ibb_rows + ph_rows + platoon_baseline_rows
                  + tto_impact_rows + steal_runs_rows + stretch_runs + tagging_runs
                  + gidp_rows)
    df_impact = save_re_impact(all_impact, data_dir)

    # RE288-based: SB break-even by count
    re288 = load_re288_matrix(data_dir)
    re288_lookup = (
        re288.set_index(["season", "outs", "runners", "count"])["run_value"]
        .to_dict()
    )
    n288_lookup = (
        re288.set_index(["season", "outs", "runners", "count"])["n"]
        .to_dict()
    )
    seasons_288 = sorted(re288["season"].unique())

    sb_by_count_rows = calculate_sb_breakeven_by_count(re288_lookup, n288_lookup, seasons_288)
    df_bc = save_sb_breakeven_by_count(sb_by_count_rows, data_dir)

    # Quick sanity check
    sample_season = df_be["season"].max()
    log.info(f"\nSample — {sample_season}:")
    for t in df_be["type"].unique():
        log.info(f"\n  Break-Even Points (type={t}):")
        log.info(df_be[(df_be["season"] == sample_season) & (df_be["type"] == t)].sort_values(["scenario", "outs"]).to_string(index=False))
    log.info("\n  RE Impact (ibb):")
    ibb_sample = df_impact[(df_impact["season"] == sample_season) & (df_impact["type"] == "ibb")].sort_values(["scenario", "outs"])
    if len(ibb_sample) > 0:
        log.info(ibb_sample.to_string(index=False))
    else:
        log.info("  (no IBB data for this season)")

    log.info("\n  RE Impact (ph_position):")
    ph_pos_sample = df_impact[
        (df_impact["season"] == sample_season) &
        (df_impact["type"] == "ph_position")
    ].sort_values(["scenario", "outs"])
    if len(ph_pos_sample) > 0:
        log.info(ph_pos_sample.to_string(index=False))
    else:
        log.info("  (no PH position data for this season)")

    log.info("\n  RE Impact (ph_pitcher):")
    ph_pitch_sample = df_impact[
        (df_impact["season"] == sample_season) &
        (df_impact["type"] == "ph_pitcher")
    ].sort_values(["scenario", "outs"])
    if len(ph_pitch_sample) > 0:
        log.info(ph_pitch_sample.to_string(index=False))
    else:
        log.info("  (no PH pitcher data for this season)")

    # La temporada de la muestra sale del parametro y no de un 2025 escrito
    # aqui: en cuanto se subiera el techo este resumen seguiria mirando 2025
    # y diria "no sac bunt data" sin que eso significara nada.
    ultima = seasons.MAX_SEASON
    log.info(f"\n  RE Impact (sac_bunt, {ultima}) - values and percentages:")
    sac_bunt_ultima = df_impact[
        (df_impact["season"] == ultima) &
        (df_impact["type"] == "sac_bunt")
    ].sort_values(["scenario", "outs"])
    if len(sac_bunt_ultima) > 0:
        cols = ["label", "scenario", "outs", "delta_re", "delta_re_pct", "n"]
        log.info(
            sac_bunt_ultima[cols]
            .to_string(index=False)
        )
    else:
        log.info(f"  (no sac bunt data for {ultima})")

    # SB by count sample — show just "Runner on 1B steals 2B" for the latest season
    sample_288 = df_bc["season"].max()
    log.info(f"\n  SB Break-Even by Count — {sample_288}, 'Runner on 1B steals 2B':")
    mask = (df_bc["season"] == sample_288) & (df_bc["scenario"] == "Runner on 1B steals 2B")
    log.info(df_bc[mask].sort_values(["count", "outs"]).to_string(index=False))

    write_derived_manifest(data_dir)

    log.info("\n✓ Script completed successfully!")


def _cli(argv=None):
    parser = cli.base_parser(
        "Calcula los puntos de break-even y el impacto en run expectancy, "
        "a partir de los parquets de 01 y de las vistas de analisis en SQL."
    )
    args = parser.parse_args(argv)
    cli.setup_logging(verbose=args.verbose, quiet=args.quiet, log_file=args.log_file)
    cli.aplicar_temporadas(args)

    try:
        main()
    finally:
        # El engine se comparte entre las seis vistas consultadas; se cierra
        # una sola vez, aqui.
        db.dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
