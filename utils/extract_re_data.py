"""
Extract Run Expectancy data from SQL Server and save as parquet files.

This script connects to a SQL Server 2025 instance using SQLAlchemy and extracts:
1. RE288 values (run expectancy by count)
2. RE24 values (run expectancy by base-out state)
3. RE24 probabilities (run scoring probabilities)

The data is saved as separate parquet files in the data folder.
"""

import json
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

# Este script imprime ✓ y ✗. En Windows la consola usa cp1252 y redirigir la
# salida a un fichero reventaba con UnicodeEncodeError, asi que no se podia
# guardar un log de la extraccion sin exportar PYTHONIOENCODING a mano.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

# El nombre de este fichero empieza por digito y no se puede importar como
# modulo; anadir su directorio a sys.path permite compartir db.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli  # noqa: E402
import db  # noqa: E402
import seasons  # noqa: E402

# SQL Queries
QUERY_RE24_PROBABILITIES = """
SELECT
    r.SEASON as season,
    CAST(RIGHT(r.STATE, 1) AS tinyint) AS outs,
    CAST(LEFT(r.STATE, 3) AS char(3))  AS runners,
    v.bucket_sort_order,
    r.STATE_COUNT AS n,
    v.RUNS_BUCKET as bucket,
    v.PERCENTAGE as [percentage],
    r.inning
FROM dbo.r24Percentages r
CROSS APPLY (VALUES
    (1, 'no_runs',              r.P_0R),
    (2, 'at_least_one_run',     1 - r.P_0R),
    (3, 'one_run',              r.P_1R),
    (4, 'two_runs',             r.P_2R),
    (5, 'three_runs',           r.P_3R),
    (6, 'four_runs',            r.P_4R),
    (7, 'five_runs',            r.P_5R),
    (8, 'more_than_five_runs',  r.P_MORE)
) v(bucket_sort_order, RUNS_BUCKET, PERCENTAGE)
ORDER BY
    season,
    outs,
    -- runner count (0..3)
    (CASE SUBSTRING(r.STATE, 1, 1) WHEN '1' THEN 1 ELSE 0 END) +
    (CASE SUBSTRING(r.STATE, 2, 1) WHEN '1' THEN 1 ELSE 0 END) +
    (CASE SUBSTRING(r.STATE, 3, 1) WHEN '1' THEN 1 ELSE 0 END),
    -- within same count: 100,010,001 then 110,101,011 etc.
    CAST(LEFT(r.STATE, 3) AS char(3)) DESC,
    v.bucket_sort_order,
    r.inning
"""

QUERY_RE288 = """
SELECT [year] as season
      ,[outs]
      ,[runners]
      ,left([count],1)+'-'+right([count],1) as [count]
      ,CASE [count]
        WHEN '02' THEN 1
        WHEN '12' THEN 2
        WHEN '01' THEN 3
        WHEN '22' THEN 4
        WHEN '11' THEN 5
        WHEN '00' THEN 6
        WHEN '10' THEN 7
        WHEN '21' THEN 8
        WHEN '32' THEN 9
        WHEN '20' THEN 10
        WHEN '31' THEN 11
        WHEN '30' THEN 12
        ELSE 99
      END as sort_order_count
      ,[n]
      ,[run_value]
      ,[run_value_smooth]
      ,[runs_per_half_inning]
  FROM [dbo].[r288Values]
  ORDER BY
      season,
      outs,
      -- runner count (0..3)
      (CASE SUBSTRING([RUNNERS], 1, 1) WHEN '1' THEN 1 ELSE 0 END) +
      (CASE SUBSTRING([RUNNERS], 2, 1) WHEN '1' THEN 1 ELSE 0 END) +
      (CASE SUBSTRING([RUNNERS], 3, 1) WHEN '1' THEN 1 ELSE 0 END),
      -- within same count: 100,010,001 then 110,101,011 etc.
      CAST(LEFT([RUNNERS], 3) AS char(3)) DESC,
      sort_order_count
"""

# OJO CON EL NOMBRE: esto NO es lo mismo que r288Values.frequency.
# Son tres diferencias, no una:
#   unidad      -> aqui TURNOS; en r288Values, LANZAMIENTOS
#   evento      -> aqui el conteo en que TERMINA el turno (ultimo de COUNT_SEQ);
#                  en r288Values, cada conteo VISITADO
#   denominador -> aqui los turnos del estado base-outs; en r288Values, todos
#                  los lanzamientos de la temporada
# Ademas salen de universos distintos (retro completo vs statcast filtrado).
# Las formas son opuestas: r288Values tiene su maximo en 0-0 y decae; esta lo
# tiene en 1-2, 2-2 y 3-2, que es donde los turnos terminan.
# Por eso la columna se llama pa_final_count_share y no 'frequency'.
QUERY_RE288_FREQUENCIES = """
WITH COUNTS AS (
    SELECT T1.[GAME_ID]
          ,T1.[EVENT_ID]
          ,T2.SEASON
          ,T2.OUTS_CT
          ,T2.INN_CT
          ,t2.RUNNERS
          ,LEFT(RIGHT([COUNT_SEQ],2),1)+'-'+RIGHT(RIGHT([COUNT_SEQ],2),1) AS COUNT
          ,CASE RIGHT([COUNT_SEQ],2)
            WHEN '02' THEN 1
            WHEN '12' THEN 2
            WHEN '01' THEN 3
            WHEN '22' THEN 4
            WHEN '11' THEN 5
            WHEN '00' THEN 6
            WHEN '10' THEN 7
            WHEN '21' THEN 8
            WHEN '32' THEN 9
            WHEN '20' THEN 10
            WHEN '31' THEN 11
            WHEN '30' THEN 12
            ELSE 99
          END as sort_order_count
      FROM [dbo].[r24Events_PitchCount] t1
      inner join dbo.r24Events t2 on t1.game_id=t2.game_id and t1.EVENT_ID=t2.EVENT_ID
      -- 1989 es donde empieza la matriz RE288 (dbo.r288Values). Estaba en 1988,
      -- que no corresponde a ninguna fuente: r24Events_PitchCount llega a 1910.
      -- El efecto era una temporada de 288 filas publicada en re288_frequencies
      -- sin contrapartida en re288_values ni re288_transitions.
      where t2.SEASON BETWEEN {SEASON_PP_MIN} AND {SEASON_MAX}
)

SELECT
    SEASON as season,
    OUTS_CT as outs,
    RUNNERS as runners,
    [COUNT] as [count],
    MAX(sort_order_count) AS sort_order_count,
    COUNT(*) AS n,
    1.0 * COUNT(*) / SUM(COUNT(*)) OVER (
        PARTITION BY SEASON, OUTS_CT, RUNNERS
    ) AS pa_final_count_share
FROM COUNTS
GROUP BY
    SEASON,
    OUTS_CT,
    RUNNERS,
    [COUNT]
ORDER BY
    SEASON,
    OUTS_CT,
    RUNNERS,
    [COUNT],
    MAX(sort_order_count);
"""

QUERY_RE288_TRANSITIONS = """
SELECT
    r.[year] AS season,
    r.outs,
    r.runners,
    LEFT(r.[count],1) + '-' + RIGHT(r.[count],1) AS [count],
    CASE r.[count]
        WHEN '02' THEN 1
        WHEN '12' THEN 2
        WHEN '01' THEN 3
        WHEN '22' THEN 4
        WHEN '11' THEN 5
        WHEN '00' THEN 6
        WHEN '10' THEN 7
        WHEN '21' THEN 8
        WHEN '32' THEN 9
        WHEN '20' THEN 10
        WHEN '31' THEN 11
        WHEN '30' THEN 12
        ELSE 99
    END AS sort_order_count,
    v.transition_type,
    v.n,
    v.transition_value,

    -- Probabilidad de que el lanzamiento tenga ESTE resultado desde este estado.
    -- El denominador incluye AHORA las cinco categorias. Antes sumaba solo
    -- bolas + strikes + bola en juego, dejando fuera los foules y los golpeados,
    -- asi que las probabilidades sumaban 1 sobre un espacio INCOMPLETO y salian
    -- todas infladas. En conteos de 2 strikes, donde el foul es ~35-40% de los
    -- lanzamientos, la distorsion era grande.
    CAST(v.n AS float) / NULLIF(
        (r.[n_balls] + r.[n_strikes] + r.[n_fouls] + r.[n_ball_in_play] + r.[n_hbp]),
        0
    ) AS transition_prob
FROM dbo.r288Transitions AS r
-- Se anaden 'F' (foul) y 'H' (golpeado), que antes se excluian del CROSS APPLY.
-- to_foul se calculaba y se guardaba, pero ningun consumidor lo usaba.
-- Para la bola en juego se expone el valor SUAVIZADO: el crudo es ruido en los
-- estados poco frecuentes (mediana 111 observaciones por estado-temporada, y
-- por debajo de 50 un ano no predice al siguiente).
CROSS APPLY (VALUES
    ('B',  r.to_ball,                  r.[n_balls]),
    ('S',  r.to_strike,                r.[n_strikes]),
    ('F',  r.to_foul,                  r.[n_fouls]),
    ('X',  r.to_ball_in_play_smooth,   r.[n_ball_in_play]),
    ('H',  r.to_hbp,                   r.[n_hbp])
) AS v(transition_type, transition_value, n)
ORDER BY
    season,
    outs,
    (CASE SUBSTRING(r.[RUNNERS], 1, 1) WHEN '1' THEN 1 ELSE 0 END) +
    (CASE SUBSTRING(r.[RUNNERS], 2, 1) WHEN '1' THEN 1 ELSE 0 END) +
    (CASE SUBSTRING(r.[RUNNERS], 3, 1) WHEN '1' THEN 1 ELSE 0 END),
    CAST(LEFT(r.[RUNNERS], 3) AS char(3)) DESC,
    sort_order_count,
    v.transition_type;
"""

QUERY_RE24 = """
WITH base AS (
    -- Real event rows only (exclude foul error 13 AND the rollup -1)
    SELECT
        season   = SEASON,
        outs     = OUTS,
        runners  = RUNNERS,
        event_cd = EVENT_CD,
        n        = EVENT_COUNT,
        run_value = CAST(RUN_VALUE AS float),
        runner_cnt =
            (CASE SUBSTRING(RUNNERS, 1, 1) WHEN '1' THEN 1 ELSE 0 END) +
            (CASE SUBSTRING(RUNNERS, 2, 1) WHEN '1' THEN 1 ELSE 0 END) +
            (CASE SUBSTRING(RUNNERS, 3, 1) WHEN '1' THEN 1 ELSE 0 END),

        runners_key = CAST(LEFT(RUNNERS, 3) AS char(3))
    FROM dbo.r24Values
    WHERE EVENT_CD NOT IN (13, -1)
),
state_totals AS (
    -- Denominators based only on real events
    SELECT season, outs, runners, total_n = SUM(n)
    FROM base
    GROUP BY season, outs, runners
),
base_rows AS (
    SELECT
        b.season, b.outs, b.runners,
        b.n, b.run_value, b.event_cd,
        prob = CAST(b.n AS float) / NULLIF(CAST(t.total_n AS float), 0),
        b.runner_cnt, b.runners_key
    FROM base b
    JOIN state_totals t
      ON t.season=b.season AND t.outs=b.outs AND t.runners=b.runners
),
mapped AS (
    -- Overlapping buckets preserved by UNION ALL mapping blocks

    -- 90 Swing & Contact: 2,18,19,20,21,22,23
    SELECT season, outs, runners, n, run_value, bucket_cd = 90
    FROM base
    WHERE event_cd IN (2,18,19,20,21,22,23)

    UNION ALL
    -- 91 Hit Safely: 20,21,22,23
    SELECT season, outs, runners, n, run_value, bucket_cd = 91
    FROM base
    WHERE event_cd IN (20,21,22,23)

    UNION ALL
    -- 92 Free base (no swing): 14,15,16,17
    SELECT season, outs, runners, n, run_value, bucket_cd = 92
    FROM base
    WHERE event_cd IN (14,15,16,17)

    UNION ALL
    -- 93 Steal attempt: 4,6
    SELECT season, outs, runners, n, run_value, bucket_cd = 93
    FROM base
    WHERE event_cd IN (4,6)

    UNION ALL
    -- 94 Out recorded: 2,3,6,8
    SELECT season, outs, runners, n, run_value, bucket_cd = 94
    FROM base
    WHERE event_cd IN (2,3,6,8)

    UNION ALL
    -- 95 Out recorded: 2,3,14,16,18,19,20,21,22,23
    SELECT season, outs, runners, n, run_value, bucket_cd = 95
    FROM base
    WHERE event_cd IN (2,3,14,16,18,19,20,21,22,23)

),
bucket_agg AS (
    SELECT
        season, outs, runners,
        event_cd = CAST(bucket_cd AS int),
        n        = SUM(n),
        run_value =
            SUM(CAST(n AS float) * CAST(run_value AS float))
            / NULLIF(SUM(CAST(n AS float)), 0)
    FROM mapped
    GROUP BY season, outs, runners, bucket_cd
),
bucket_rows AS (
    SELECT
        ba.season, ba.outs, ba.runners,
        ba.n, ba.run_value, ba.event_cd,
        prob = CAST(ba.n AS float) / NULLIF(CAST(t.total_n AS float), 0),

        runner_cnt =
            (CASE SUBSTRING(ba.runners, 1, 1) WHEN '1' THEN 1 ELSE 0 END) +
            (CASE SUBSTRING(ba.runners, 2, 1) WHEN '1' THEN 1 ELSE 0 END) +
            (CASE SUBSTRING(ba.runners, 3, 1) WHEN '1' THEN 1 ELSE 0 END),

        runners_key = CAST(LEFT(ba.runners, 3) AS char(3))
    FROM bucket_agg ba
    JOIN state_totals t
      ON t.season=ba.season AND t.outs=ba.outs AND t.runners=ba.runners
),
all_events_rows AS (
    -- Add back the rollup row (-1) WITHOUT affecting denominators.
    -- prob = 1.0 by definition: "all events combined" within that state.
    SELECT
        season   = SEASON,
        outs     = OUTS,
        runners  = RUNNERS,
        n        = EVENT_COUNT,
        run_value = CAST(RUN_VALUE AS float),
        event_cd = CAST(-1 AS int),
        prob     = CAST(1.0 AS float),

        runner_cnt =
            (CASE SUBSTRING(RUNNERS, 1, 1) WHEN '1' THEN 1 ELSE 0 END) +
            (CASE SUBSTRING(RUNNERS, 2, 1) WHEN '1' THEN 1 ELSE 0 END) +
            (CASE SUBSTRING(RUNNERS, 3, 1) WHEN '1' THEN 1 ELSE 0 END),

        runners_key = CAST(LEFT(RUNNERS, 3) AS char(3))
    FROM dbo.r24Values
    WHERE EVENT_CD = -1
)
SELECT
    season,
    outs,
    runners,
    n,
    run_value,
    event_cd,
    prob
FROM (
    SELECT season, outs, runners, n, run_value, event_cd, prob, runner_cnt, runners_key
    FROM base_rows

    UNION ALL
    SELECT season, outs, runners, n, run_value, event_cd, prob, runner_cnt, runners_key
    FROM bucket_rows

    UNION ALL
    SELECT season, outs, runners, n, run_value, event_cd, prob, runner_cnt, runners_key
    FROM all_events_rows
) u
ORDER BY
    season,
    outs,
    runner_cnt,
    runners_key DESC,
    event_cd;
"""

# RE24 event types (with All Events row)
QUERY_RE24_EVENT_TYPES = """
select event_cd,event from
(
    select distinct t1.event_cd,t2.name as [event]  from [dbo].[r24Values] t1
    inner join dbo.retroEventTypes t2 on t1.EVENT_CD=t2.id and t1.event_cd<>13 --removing foul error
    union
    select -1 as event_cd,'All Events' as [Event]
    union
    select 90 as event_cd,'Swing & Contact (Group)' as [Event]
    union
    select 91 as event_cd,'Hit Safely (Group)' as [Event]
    union
    select 92 as event_cd,'Free Base (Group)' as [Event]
    union
    select 93 as event_cd,'Steal Attempt (Group)' as [Event]
    union
    select 94 as event_cd,'Out recorded (Group)' as [Event]
    union
    select 95 as event_cd,'Standard PA (Group)' as [Event]
) x
order by event_cd
"""

# Events counts by season
QUERY_EVENTS_COUNTS = """
;WITH bounds AS (
    SELECT
        r24_min  = (SELECT MIN(season) FROM dbo.r24Values),
        r24_max  = (SELECT MAX(season) FROM dbo.r24Values),
        r288_min = (SELECT MIN([year])  FROM dbo.r288Values),
        r288_max = (SELECT MAX([year])  FROM dbo.r288Values)
),
r24 AS (
    SELECT
        e.season,
        r24_n  = COUNT_BIG(*),
        r288_n = CAST(0 AS bigint)
    FROM dbo.v_retroAllEventsRegAndPost AS e
    CROSS JOIN bounds AS b
    WHERE e.season BETWEEN b.r24_min AND b.r24_max
      AND e.season BETWEEN {SEASON_MIN} AND {SEASON_MAX}
    GROUP BY e.season
),
r288 AS (
    SELECT
        s.season,
        r24_n  = CAST(0 AS bigint),
        r288_n = SUM(pc.pitches)
    FROM dbo.r24Events_PitchCount AS pc
    CROSS APPLY (VALUES (TRY_CONVERT(int, SUBSTRING(pc.game_id, 4, 4)))) AS s(season)
    CROSS JOIN bounds AS b
    WHERE s.season BETWEEN b.r288_min AND b.r288_max
      AND s.season BETWEEN {SEASON_PP_MIN} AND {SEASON_MAX}
    GROUP BY s.season
)
SELECT
    season,
    r24_n  = SUM(r24_n),
    r288_n = SUM(r288_n)
FROM (
    SELECT * FROM r24
    UNION ALL
    SELECT * FROM r288
) AS x
GROUP BY season
ORDER BY season
"""

# Innings lookup
QUERY_INNINGS = """
;WITH n AS (
    SELECT -1 AS v
    UNION ALL
    SELECT v + 1
    FROM n
    WHERE v < 10
)
SELECT
    inn_id = v,
    inn_name =
        CASE v
            WHEN -1 THEN 'All Innings'
            WHEN 1 THEN '1st'
            WHEN 2 THEN '2nd'
            WHEN 3 THEN '3rd'
            WHEN 4 THEN '4th'
            WHEN 5 THEN '5th'
            WHEN 6 THEN '6th'
            WHEN 7 THEN '7th'
            WHEN 8 THEN '8th'
            WHEN 9 THEN '9th'
            WHEN 10 THEN 'Extra Innings'
        END
FROM n
where v<>0
ORDER BY v
OPTION (MAXRECURSION 0)
"""

# wOBA Coefficients
QUERY_WOBA_COEFFS = """
SELECT
    s.season,
    s.OBP AS [League_Avg_OBP],
    s.WOBA_Scale,
    -- wOBA Weights (scaled)
    MAX(CASE WHEN f.EVENT_CD = 14 THEN f.WOBA_FACTOR_SCALED END) AS [wuBB],
    MAX(CASE WHEN f.EVENT_CD = 16 THEN f.WOBA_FACTOR_SCALED END) AS [wHBP],
    MAX(CASE WHEN f.EVENT_CD = 20 THEN f.WOBA_FACTOR_SCALED END) AS [w1B],
    MAX(CASE WHEN f.EVENT_CD = 21 THEN f.WOBA_FACTOR_SCALED END) AS [w2B],
    MAX(CASE WHEN f.EVENT_CD = 22 THEN f.WOBA_FACTOR_SCALED END) AS [w3B],
    MAX(CASE WHEN f.EVENT_CD = 23 THEN f.WOBA_FACTOR_SCALED END) AS [wHR]
FROM dbo.r24_WOBA_Seasons s
LEFT JOIN dbo.r24_WOBA_Factors f
    ON s.SEASON = f.SEASON
    AND f.EVENT_CD IN (14, 16, 20, 21, 22, 23)  -- uBB, HBP, 1B, 2B, 3B, HR
where s.season BETWEEN {SEASON_MIN} AND {SEASON_MAX}
GROUP BY
    s.SEASON,
    s.OBP,
    s.WOBA,
    s.WOBA_Scale
ORDER BY s.SEASON DESC
"""

QUERY_FIP = """
SELECT
    season,
    -- League ERA (earned runs / innings pitched * 9)
    CASE WHEN SUM(CAST(IP AS INT) + ((IP - CAST(IP AS INT)) * 10.0) / 3.0) > 0
         THEN (SUM(ER) * 9.0) / SUM(CAST(IP AS INT) + ((IP - CAST(IP AS INT)) * 10.0) / 3.0)
         ELSE NULL
    END AS lgERA,
    -- FIP numerator component
    (13.0 * SUM(HR) + 3.0 * (SUM(BB) + SUM(HBP)) - 2.0 * SUM(SO))
        / NULLIF(SUM(CAST(IP AS INT) + ((IP - CAST(IP AS INT)) * 10.0) / 3.0), 0) AS FIP_Component,
    -- FIP Constant
    CASE WHEN SUM(CAST(IP AS INT) + ((IP - CAST(IP AS INT)) * 10.0) / 3.0) > 0
         THEN ((SUM(ER) * 9.0) / SUM(CAST(IP AS INT) + ((IP - CAST(IP AS INT)) * 10.0) / 3.0))
              - ((13.0 * SUM(HR) + 3.0 * (SUM(BB) + SUM(HBP)) - 2.0 * SUM(SO))
                  / SUM(CAST(IP AS INT) + ((IP - CAST(IP AS INT)) * 10.0) / 3.0))
         ELSE NULL
    END AS FIP_Constant,
    -- Convert IP to actual innings (IP like 5.1 = 5 + 1/3 innings)
    SUM(CAST(IP AS INT) + ((IP - CAST(IP AS INT)) * 10.0) / 3.0) AS lgIP,
    SUM(HR) AS lgHR,
    SUM(BB) AS lgBB,
    SUM(HBP) AS lgHBP,
    SUM(SO) AS lgK,
    SUM(ER) AS lgER
FROM dbo.mlbPitching  -- Adjust table name if different
where season BETWEEN {SEASON_MIN} AND {SEASON_MAX}
GROUP BY season
ORDER BY season DESC;
"""

QUERY_BALLPARKS = """
-- Get list of ballparks per year with home team owner
-- Using retroGamesReg and retroGamesPost tables

SELECT DISTINCT
    CAST(SUBSTRING(GAME_ID, 4, 4) AS INT) AS Season,
    PARK_ID,
    HOME_TEAM_ID AS TEAM_ID,
    ISNULL(NULLIF(LTRIM(RTRIM(t.nickname)), ''), HOME_TEAM_ID) AS Team_Name,
    --ISNULL(NULLIF(LTRIM(RTRIM(t.city)), ''), 'Unknown') AS Team_City,
    ISNULL(NULLIF(LTRIM(RTRIM(CASE
        WHEN bp.name IS NULL OR LTRIM(RTRIM(bp.name)) = '' THEN bp.aka
        ELSE bp.name
    END)), ''), 'Unknown') AS Park_Name
FROM (
    -- Regular season games
    SELECT GAME_ID, PARK_ID, HOME_TEAM_ID
    FROM dbo.retroGamesReg

    UNION ALL

    -- Postseason games
    SELECT GAME_ID, PARK_ID, HOME_TEAM_ID
    FROM dbo.retroGamesPost
) games
LEFT JOIN dbo.retroBallParks bp
    ON games.PARK_ID = bp.parkId
LEFT JOIN dbo.retroTeams t
    ON games.HOME_TEAM_ID = t.team
    AND CAST(SUBSTRING(games.GAME_ID, 4, 4) AS INT) BETWEEN t.firstSeason AND t.lastSeason
where CAST(SUBSTRING(GAME_ID, 4, 4) AS INT) BETWEEN {SEASON_MIN} AND {SEASON_MAX}
ORDER BY Season DESC, PARK_ID;
"""

QUERY_PARK_FACTORS = """
SELECT [PARK_ID]
      ,[SEASON]
      ,SEASONS_AVG as SEASONS
      ,[PF_R]
      ,[PF_1B]
      ,[PF_2B]
      ,[PF_3B]
      ,[PF_HR]
      ,[PF_BB]
      ,[PF_SO]
  FROM [dbo].[retroParkFactors]
ORDER BY PARK_ID, SEASON, SEASONS_AVG;
"""

# Los mismos factores, CON el equipo local. Tabla distinta a proposito.
#
# QUERY_PARK_FACTORS DE ARRIBA NO SE TOCA, y esa es la razon de que esto exista.
# Anadirle HOME_TEAM_ID a la consulta de siempre habria roto la tabla de Park
# Factors: la app cruza con `ballparks`, que ya tiene una fila por equipo, asi que
# una segunda fila del lado de los factores produce un PRODUCTO CARTESIANO. Medido
# antes de revertirlo: 1914 pasaba de 17 filas a 21, y dos de ellas emparejaban a
# los Red Sox con el factor de los Braves. Ver
# docs/PLAN_PARK_FACTORS_POR_EQUIPO.md.
#
# Los VALORES son identicos a los de parkFactors.parquet --el factor es del parque,
# no del inquilino-- y lo unico que anade es el enlace al equipo, que es lo que
# necesita wRC+, mas la ventana que se uso de verdad.
#
# SEASONS_EFF DICE SI LA FILA ES ESTABLE. La ventana centrada mira dos anos
# adelante, asi que en el borde de la serie se recorta: el "5 anos" de la ultima
# temporada son 3. Si SEASONS_EFF < SEASONS, esa fila CAMBIARA cuando lleguen
# datos nuevos. Comprobado contra FanGraphs usando la fila cuya ventana coincide
# con la suya: diferencia media de 0,33 puntos sobre 28 equipos.
QUERY_PARK_FACTORS_BY_TEAM = """
SELECT [PARK_ID]
      ,[SEASON]
      ,[HOME_TEAM_ID]
      ,SEASONS_AVG as SEASONS
      ,[SEASONS_EFF]
      ,[WINDOW_FROM]
      ,[WINDOW_TO]
      ,[G]
      ,[PF_R]
      ,[PF_1B]
      ,[PF_2B]
      ,[PF_3B]
      ,[PF_HR]
      ,[PF_BB]
      ,[PF_SO]
  FROM [dbo].[retroParkFactorsByTeam]
ORDER BY PARK_ID, SEASON, HOME_TEAM_ID, SEASONS_AVG;
"""

# Insumos de LIGA para wRAA, wRC y wRC+ (ver 23_v_wrc_league_inputs.sql).
#
#     wRAA = ((wOBA - lgwOBA) / escala) * PA
#     wRC  = (((wOBA - lgwOBA) / escala) + lg_r_per_pa) * PA
#
# La escala y los coeficientes NO estan aqui: viven en wOBA_Coeffs.parquet y no se
# duplican. Los sucesos van en crudo para que los pesos se apliquen en un solo
# sitio.
#
# Las columnas "_sin_p" son el denominador del 100 del wRC+, que se fija con la
# liga SIN lanzadores bateando. Medido: excluirlos mueve el lgwOBA +0,0074 de media
# historica y hasta +0,011 en los setenta; desde el bateador designado universal de
# 2022 cae a +0,00001, porque los lanzadores pasaron del 8,9% de las apariciones al
# 0,02%.
QUERY_WRC_LEAGUE_INPUTS = """
SELECT * FROM [dbo].[v_wrc_league_inputs] ORDER BY season;
"""

QUERY_LINEAR_WEIGHTS = """
select * from
(
    SELECT
        SEASON,
        OUTS_CT as OUTS,
        RUNNERS,
        EVENT_CD,
        COUNT(*) AS n,
        AVG(RUN_VALUE) AS LINEAR_WEIGHT
    FROM dbo.r24Events
    GROUP BY
        SEASON, OUTS_CT, RUNNERS, EVENT_CD

    UNION ALL

    SELECT
        SEASON,
        -1 as OUTS,
        RUNNERS,
        EVENT_CD,
        COUNT(*) AS n,
        AVG(RUN_VALUE) AS LINEAR_WEIGHT
    FROM dbo.r24Events
    GROUP BY
        SEASON, RUNNERS, EVENT_CD

    UNION ALL

    SELECT
        SEASON,
        OUTS_CT as OUTS,
        'AVG' as RUNNERS,
        EVENT_CD,
        COUNT(*) AS n,
        AVG(RUN_VALUE) AS LINEAR_WEIGHT
    FROM dbo.r24Events
    GROUP BY
        SEASON, OUTS_CT, EVENT_CD

    UNION ALL

    SELECT
        SEASON,
        -1 as OUTS,
        'AVG' as RUNNERS,
        EVENT_CD,
        COUNT(*) AS n,
        AVG(RUN_VALUE) AS LINEAR_WEIGHT
    FROM dbo.r24Events
    GROUP BY
        SEASON, EVENT_CD

) x
WHERE EVENT_CD <> 13  -- removing foul error
ORDER BY
    SEASON, OUTS, RUNNERS, EVENT_CD;
"""

QUERY_R24_FREQUENCIES = """
SELECT [SEASON], [RUNNERS], [OUTS], [EVENT_CD], [EVENT_COUNT] AS n, [FREQUENCY]
FROM [dbo].[r24Values]
WHERE [EVENT_CD] = -1
ORDER BY [SEASON], [RUNNERS], [OUTS]
"""

QUERY_RE24_TRANSITIONS = """
WITH base_rows AS (
    -- base rows EXCLUDING the rollup (-1)
    SELECT
        season,
        from_outs,
        from_runners,
        to_outs,
        to_runners,
        event_cd,
        n                   = [count],
        avg_prob            = [prob],
        avg_delta_run_value = [run_value]
    FROM dbo.r24Transitions
    WHERE event_cd <> -1
),
all_rows AS (
    -- keep the rollup (-1) separate so it doesn't interfere with anything
    SELECT
        season,
        from_outs,
        from_runners,
        to_outs,
        to_runners,
        event_cd = CAST(-1 AS int),
        n                   = [count],
        avg_prob            = [prob],
        avg_delta_run_value = [run_value]
    FROM dbo.r24Transitions
    WHERE event_cd = -1
),
mapped AS (
    -- Overlaps preserved by UNION ALL mapping blocks

    -- 90 Swing & Contact: 2,18,19,20,21,22,23
    SELECT
        season, from_outs, from_runners, to_outs, to_runners,
        [count], run_value,
        bucket_cd = 90
    FROM dbo.r24Transitions
    WHERE event_cd IN (2,18,19,20,21,22,23)

    UNION ALL

    -- 91 Hit Safely: 20,21,22,23
    SELECT
        season, from_outs, from_runners, to_outs, to_runners,
        [count], run_value,
        bucket_cd = 91
    FROM dbo.r24Transitions
    WHERE event_cd IN (20,21,22,23)

    UNION ALL

    -- 92 Free base: 14,15,16,17
    SELECT
        season, from_outs, from_runners, to_outs, to_runners,
        [count], run_value,
        bucket_cd = 92
    FROM dbo.r24Transitions
    WHERE event_cd IN (14,15,16,17)

    UNION ALL

    -- 93 Steal attempt: 4,6
    SELECT
        season, from_outs, from_runners, to_outs, to_runners,
        [count], run_value,
        bucket_cd = 93
    FROM dbo.r24Transitions
    WHERE event_cd IN (4,6)

    UNION ALL

    -- 94 Out recorded: 2,3,6,8
    SELECT
        season, from_outs, from_runners, to_outs, to_runners,
        [count], run_value,
        bucket_cd = 94
    FROM dbo.r24Transitions
    WHERE event_cd IN (2,3,6,8)

    UNION ALL

    -- 95 Standard PA: 2,3,14,16,18,19,20,21,22,23
    SELECT
        season, from_outs, from_runners, to_outs, to_runners,
        [count], run_value,
        bucket_cd = 95
    FROM dbo.r24Transitions
    WHERE event_cd IN (2,3,14,16,18,19,20,21,22,23)
),
bucket_transitions AS (
    -- Pool counts (and weighted run_value) at the (from -> to) level for each bucket
    SELECT
        season,
        from_outs,
        from_runners,
        to_outs,
        to_runners,
        event_cd = CAST(bucket_cd AS int),
        n = SUM([count]),
        avg_delta_run_value =
            SUM(CAST([count] AS float) * CAST(run_value AS float))
            / NULLIF(SUM(CAST([count] AS float)), 0)
    FROM mapped
    GROUP BY
        season, from_outs, from_runners, to_outs, to_runners, bucket_cd
),
bucket_totals AS (
    -- Denominator for probabilities: total pooled count for each (season, from_state, bucket)
    SELECT
        season,
        from_outs,
        from_runners,
        event_cd,
        total_n = SUM(n)
    FROM bucket_transitions
    GROUP BY
        season, from_outs, from_runners, event_cd
),
bucket_rows AS (
    SELECT
        bt.season,
        bt.from_outs,
        bt.from_runners,
        bt.to_outs,
        bt.to_runners,
        bt.event_cd,
        bt.n,
        avg_prob = CAST(bt.n AS float) / NULLIF(CAST(t.total_n AS float), 0),
        bt.avg_delta_run_value
    FROM bucket_transitions bt
    JOIN bucket_totals t
      ON  t.season = bt.season
      AND t.from_outs = bt.from_outs
      AND t.from_runners = bt.from_runners
      AND t.event_cd = bt.event_cd
)
SELECT
    season,
    from_outs,
    from_runners,
    to_outs,
    to_runners,
    event_cd,
    n,
    avg_prob,
    avg_delta_run_value
FROM base_rows

UNION ALL

SELECT
    season,
    from_outs,
    from_runners,
    to_outs,
    to_runners,
    event_cd,
    n,
    avg_prob,
    avg_delta_run_value
FROM bucket_rows

UNION ALL

SELECT
    season,
    from_outs,
    from_runners,
    to_outs,
    to_runners,
    event_cd,
    n,
    avg_prob,
    avg_delta_run_value
FROM all_rows

ORDER BY
    season, from_outs, from_runners, to_outs, to_runners, event_cd;
"""

# NO DETERMINISTA A NIVEL DE BIT, y es esperado: no lo persigas como si fuera un bug.
#
# Los AVG() de abajo se calculan en paralelo. SQL Server reparte las filas entre
# hilos, suma cada particion por separado y despues combina las sumas parciales.
# La suma en punto flotante no es asociativa -- (a+b)+c no da exactamente lo mismo
# que a+(b+c), porque cada paso redondea -- y el orden en que se combinan las
# parciales depende de como el planificador reparta el trabajo, que varia entre
# ejecuciones.
#
# Medido: la misma consulta ejecutada dos veces seguidas, sin tocar una linea de
# Python, devuelve 227,396 filas con WPA distinto. Todas a nivel 1e-16, o sea
# ruido de float64.
#
# Al castear a float32 esas perturbaciones desaparecen casi siempre, porque
# ambos valores redondean al mismo float32. Solo sobreviven cuando un valor cae
# practicamente en el punto medio entre dos float32 consecutivos y el empujon de
# 1e-16 decide hacia que lado redondea. Sobre 450,488 filas eso ocurrio en UNA:
#
#   1999, entrada 9, local bateando, 2 outs, bases limpias, perdiendo por 4
#   WE = 0.000499876391 vs 0.000499876449  -> 1 ULP, la minima diferencia posible
#
# Esa celda tiene 318 eventos, asi que su error de muestreo es del orden de 1e-3:
# siete ordenes de magnitud por encima de la discrepancia. Impacto analitico cero.
#
# Consecuencia practica: win_expectancy.parquet NO es reproducible byte a byte.
# Al comparar dos lotes hay que usar tolerancia (np.allclose con rtol=1e-6), no
# igualdad exacta.
#
# Se descarto forzarlo a determinista:
#   OPTION (MAXDOP 1) lo consigue, pero impone un plan serial en una agregacion
#   grande y encarece cada extraccion para arreglar un digito que nadie mira.
#   Redondear antes del cast es peor: el espaciado de float32 varia con la
#   magnitud (6e-8 cerca de 0.5, pero 5.8e-11 cerca de 0.0005), asi que cualquier
#   redondeo a decimales fijos destruiria precision real en los valores pequenos,
#   que es justo donde se concentra el problema.
QUERY_WIN_EXPECTANCY = """
    SELECT [SEASON]
          ,INN_CT
          ,[BAT_HOME_ID]
          ,OUTS_CT
          ,RUNNERS
                    ,CASE
                         WHEN [RUNS_DIFF] <= -4 THEN -4
                         WHEN [RUNS_DIFF] >= 4 THEN 4
                         ELSE [RUNS_DIFF]
                     END AS [RUNS_DIFF]
          ,count(*) as n
          ,avg([WE]) as WE
          ,avg(LI) as LI
          ,avg(WPA) as WPA
    FROM [dbo].[r24Events]
        where season BETWEEN {SEASON_MIN} AND {SEASON_MAX} and inn_ct<=10 --and game_type='R'
    and game_id not in (SELECT distinct game_id from dbo.r24Events where event_id=1 and BAT_HOME_ID=1) -- juegos raros en donde el home club batea primero
        group by season,inn_ct,[BAT_HOME_ID],outs_ct,RUNNERS,
                CASE
                    WHEN [RUNS_DIFF] <= -4 THEN -4
                    WHEN [RUNS_DIFF] >= 4 THEN 4
                    ELSE [RUNS_DIFF]
                END

    union all

    SELECT [SEASON]
          ,INN_CT
          ,[BAT_HOME_ID]
            ,OUTS_CT
          ,RUNNERS
                    ,CASE
                         WHEN [RUNS_DIFF] <= -4 THEN -4
                         WHEN [RUNS_DIFF] >= 4 THEN 4
                         ELSE [RUNS_DIFF]
                     END AS [RUNS_DIFF]
          ,count(*) as n
          ,avg([WE]) as WE
          ,avg(LI) as LI
          ,avg(WPA) as WPA
    FROM (
       select season,11 as inn_ct,BAT_HOME_ID,outs_ct,RUNNERS,RUNS_DIFF,we,li,wpa from [dbo].[r24Events]
       where inn_ct>10 --and game_type='R'
       and game_id not in (SELECT distinct game_id from dbo.r24Events where event_id=1 and BAT_HOME_ID=1) -- juegos raros en donde el home club batea primero
    ) as ex_inn
        where season BETWEEN {SEASON_MIN} AND {SEASON_MAX}
        group by season,inn_ct,[BAT_HOME_ID],outs_ct,RUNNERS,
                CASE
                    WHEN [RUNS_DIFF] <= -4 THEN -4
                    WHEN [RUNS_DIFF] >= 4 THEN 4
                    ELSE [RUNS_DIFF]
                END
    order by season,inn_ct,[BAT_HOME_ID],outs_ct,RUNNERS,[RUNS_DIFF]
"""



# ---------------------------------------------------------------------------
# Catalogo de datasets
# ---------------------------------------------------------------------------
#
# Antes cada dataset tenia su propio bloque de ~35 lineas copiadas: leer, medir,
# imprimir, castear columna por columna, escribir. Quince bloques casi iguales
# que habia que mantener en paralelo, y de los que solo tres validaban el tipo
# resultante. De ahi venian los dos casteos olvidados (re288_transitions.n y
# re24_probabilities.inning se quedaban en int64) que nadie detecto.
#
# Con el catalogo, anadir un dataset es anadir una entrada, y la validacion de
# tipos aplica a los dieciseis por construccion.

# Marca para las columnas de exactamente tres caracteres: el estado de
# corredores ('000'..'111') y el conteo ('0-0'..'3-2'). El resultado es dtype
# 'string'.
STRING3 = "__string3__"


def _resolver_temporadas(sql: str) -> str:
    """
    Sustituye los marcadores de temporada de las consultas por los valores de
    utils/seasons.py.

    Se hace por marcador y no con un f-string porque las consultas viven en
    constantes de modulo y asi siguen siendo copiables tal cual a un cliente de
    SQL -- basta con mirar el log, que imprime el rango aplicado.
    """
    return (sql
            .replace("{SEASON_MIN}", str(int(seasons.MIN_SEASON)))
            .replace("{SEASON_PP_MIN}", str(int(seasons.MIN_PER_PITCH_SEASON)))
            .replace("{SEASON_MAX}", str(int(seasons.MAX_SEASON))))


class Dataset:
    """Un dataset a extraer: de donde sale, como se llama y de que tipo es cada columna."""

    __slots__ = ("dtypes", "extra_summary", "fmt", "key", "output", "por_lanzamiento",
                 "query", "season_col", "title")

    def __init__(self, key, title, query, output, dtypes,
                 fmt="parquet", season_col="season", extra_summary=None,
                 por_lanzamiento=False):
        # por_lanzamiento: el dataset necesita el detalle de cada lanzamiento
        # (conteo, zona), que no existe antes de 1989. Marca cual de los dos
        # suelos se le aplica. Ver utils/seasons.py.
        self.key = key
        self.title = title
        self.query = query
        self.output = output
        self.dtypes = dtypes
        self.fmt = fmt
        self.season_col = season_col
        self.extra_summary = extra_summary or []
        self.por_lanzamiento = por_lanzamiento


DATASETS = [
    Dataset(
        key="re288_values",
        por_lanzamiento=True,
        title="RE288 data (Run Expectancy by Count)",
        query=QUERY_RE288,
        output="re288_values.parquet",
        dtypes={
            "season": "int32",
            "outs": "uint8",
            "runners": STRING3,
            "count": STRING3,
            "sort_order_count": "uint8",
            "n": "int32",
            "run_value": "float32",
            "run_value_smooth": "float32",
            "runs_per_half_inning": "float32",
        },
    ),
    Dataset(
        key="re288_frequencies",
        por_lanzamiento=True,
        title="RE288 Frequencies (State Frequencies by Count)",
        query=QUERY_RE288_FREQUENCIES,
        output="re288_frequencies.parquet",
        dtypes={
            "season": "int32",
            "outs": "uint8",
            "runners": STRING3,
            "count": STRING3,
            "sort_order_count": "uint8",
            "n": "int32",
            "pa_final_count_share": "float32",
        },
    ),
    Dataset(
        key="re288_transitions",
        por_lanzamiento=True,
        title="RE288 Transitions (Pitch Result Transitions by Count)",
        query=QUERY_RE288_TRANSITIONS,
        output="re288_transitions.parquet",
        dtypes={
            "season": "int32",
            "outs": "uint8",
            "runners": STRING3,
            "count": STRING3,
            "sort_order_count": "uint8",
            "transition_type": "string",
            "transition_value": "float32",
            "transition_prob": "float32",
            # 'n' se quedaba sin castear y salia en int64.
            "n": "int32",
        },
    ),
    Dataset(
        key="re24_values",
        title="RE24 data (Run Expectancy by Base-Out State)",
        query=QUERY_RE24,
        output="re24_values.parquet",
        dtypes={
            "season": "int32",
            "outs": "uint8",
            "runners": STRING3,
            "n": "int32",
            "run_value": "float32",
            "event_cd": "int32",
            # Probabilidad en [0,1]: float32 igual que run_value y percentage.
            # Se publicaba en float64 solo por no estar declarada. La perdida
            # medida es 6e-8 relativo, cuatro ordenes por debajo de lo que
            # muestra la UI.
            "prob": "float32",
        },
    ),
    Dataset(
        key="re24_probabilities",
        title="RE24 Probabilities (Run Scoring Probabilities)",
        query=QUERY_RE24_PROBABILITIES,
        output="re24_probabilities.parquet",
        dtypes={
            "season": "int32",
            "outs": "uint8",
            "runners": STRING3,
            "bucket_sort_order": "uint8",
            "n": "int32",
            "bucket": "string",
            "percentage": "float32",
            # 'inning' se quedaba sin castear y salia en int64 (rango real -1..10).
            "inning": "int8",
        },
        extra_summary=["bucket"],
    ),
    Dataset(
        key="re24_event_types",
        title="RE24 Event Types",
        query=QUERY_RE24_EVENT_TYPES,
        output="re24_eventTypes.parquet",
        dtypes={"event_cd": "int32", "event": "string"},
        season_col=None,
    ),
    Dataset(
        key="events_counts",
        title="Events Counts by Season",
        query=QUERY_EVENTS_COUNTS,
        output="events_counts.parquet",
        dtypes={"season": "int32", "r24_n": "int64", "r288_n": "int64"},
    ),
    Dataset(
        key="woba_coeffs",
        title="wOBA Coefficients",
        query=QUERY_WOBA_COEFFS,
        output="wOBA_Coeffs.parquet",
        dtypes={
            "season": "int32",
            "League_Avg_OBP": "float32",
            "WOBA_Scale": "float32",
            "wuBB": "float32",
            "wHBP": "float32",
            "w1B": "float32",
            "w2B": "float32",
            "w3B": "float32",
            "wHR": "float32",
        },
    ),
    Dataset(
        key="fip",
        title="FIP Constants",
        query=QUERY_FIP,
        output="fip.parquet",
        dtypes={
            "season": "int32",
            "lgERA": "float32",
            "FIP_Component": "float32",
            "lgHR": "int32",
            "lgBB": "int32",
            "lgHBP": "int32",
            "lgK": "int32",
            "lgIP": "float32",
            "lgER": "int32",
            "FIP_Constant": "float32",
        },
    ),
    Dataset(
        key="ballparks",
        title="Ballparks",
        query=QUERY_BALLPARKS,
        output="ballparks.parquet",
        dtypes={
            "Season": "int32",
            "PARK_ID": "string",
            "TEAM_ID": "string",
            "Team_Name": "string",
            "Park_Name": "string",
        },
        season_col="Season",
    ),
    Dataset(
        key="park_factors",
        title="Park Factors",
        query=QUERY_PARK_FACTORS,
        output="parkFactors.parquet",
        dtypes={
            "PARK_ID": "string",
            "SEASON": "int32",
            "SEASONS": "int32",
            "PF_R": "float32",
            "PF_1B": "float32",
            "PF_2B": "float32",
            "PF_3B": "float32",
            "PF_HR": "float32",
            "PF_BB": "float32",
            "PF_SO": "float32",
        },
        season_col="SEASON",
    ),
    Dataset(
        key="park_factors_by_team",
        title="Park Factors by Team",
        query=QUERY_PARK_FACTORS_BY_TEAM,
        output="parkFactorsByTeam.parquet",
        dtypes={
            "PARK_ID": "string",
            "SEASON": "int32",
            "HOME_TEAM_ID": "string",
            "SEASONS": "int32",
            "SEASONS_EFF": "int32",
            "WINDOW_FROM": "int32",
            "WINDOW_TO": "int32",
            "G": "int32",
            "PF_R": "float32",
            "PF_1B": "float32",
            "PF_2B": "float32",
            "PF_3B": "float32",
            "PF_HR": "float32",
            "PF_BB": "float32",
            "PF_SO": "float32",
        },
        season_col="SEASON",
    ),
    Dataset(
        key="wrc_league_inputs",
        title="wRC League Inputs",
        query=QUERY_WRC_LEAGUE_INPUTS,
        output="wrc_league_inputs.parquet",
        dtypes={
            "season": "int32",
            "lg_pa": "int32",
            "lg_runs": "int32",
            "lg_pa_sin_p": "int32",
            "lg_woba_den_sin_p": "int32",
            "lg_runs_sin_p": "int32",
            "lg_ubb_sin_p": "int32",
            "lg_hbp_sin_p": "int32",
            "lg_1b_sin_p": "int32",
            "lg_2b_sin_p": "int32",
            "lg_3b_sin_p": "int32",
            "lg_hr_sin_p": "int32",
            "lg_r_per_pa": "float64",
            "lg_r_per_pa_sin_p": "float64",
            "lg_share_pa_pitchers": "float64",
        },
        season_col="season",
    ),
    Dataset(
        key="linear_weights",
        title="Linear Weights",
        query=QUERY_LINEAR_WEIGHTS,
        output="linear_weights.parquet",
        dtypes={
            "SEASON": "int32",
            "OUTS": "int8",
            "RUNNERS": STRING3,
            "EVENT_CD": "int32",
            "n": "int32",
            "LINEAR_WEIGHT": "float32",
        },
        season_col="SEASON",
    ),
    Dataset(
        key="re24_frequencies",
        title="R24 Frequencies",
        query=QUERY_R24_FREQUENCIES,
        output="re24_frequencies.parquet",
        dtypes={
            "SEASON": "int32",
            "RUNNERS": STRING3,
            "OUTS": "int32",
            "EVENT_CD": "int32",
            "n": "int32",
            "FREQUENCY": "float32",
        },
        season_col="SEASON",
    ),
    Dataset(
        key="re24_transitions",
        title="RE24 Transitions",
        query=QUERY_RE24_TRANSITIONS,
        output="re24_transitions.parquet",
        dtypes={
            "season": "int32",
            "from_outs": "int8",
            "from_runners": STRING3,
            "event_cd": "int32",
            "to_outs": "int8",
            "to_runners": STRING3,
            "n": "int32",
            "avg_prob": "float32",
            "avg_delta_run_value": "float32",
        },
    ),
    Dataset(
        key="win_expectancy",
        title="Win Expectancy",
        query=QUERY_WIN_EXPECTANCY,
        output="win_expectancy.parquet",
        dtypes={
            "SEASON": "int16",
            "INN_CT": "int8",
            "BAT_HOME_ID": "int8",
            "OUTS_CT": "int8",
            "RUNNERS": "string",
            "RUNS_DIFF": "int8",
            # Eventos de la celda. Maximo observado 2,477 sobre 450k celdas, asi
            # que int32 sobra; se publicaba en int64 solo por no estar declarada.
            "n": "int32",
            "WE": "float32",
            "LI": "float32",
            "WPA": "float32",
        },
        season_col="SEASON",
        extra_summary=["INN_CT", "RUNS_DIFF"],
    ),
    Dataset(
        key="innings",
        title="Innings Lookup",
        query=QUERY_INNINGS,
        output="innings.json",
        dtypes={"inn_id": "int32", "inn_name": "string"},
        fmt="json",
        season_col=None,
    ),
]


def get_connection():
    """
    Engine de SQLAlchemy hacia la base DGL (ver utils/db.py).

    Los servidores a probar se configuran con DGL_SQL_SERVERS en el .env; antes
    estaban escritos en el codigo y no coincidian con los de 03.
    """
    return db.get_engine()



def _normalizar_string3(df: pd.DataFrame, col: str, dataset_key: str) -> pd.Series:
    """
    Limpia y VALIDA una columna de tres caracteres. Antes la recortaba.

    Era `astype("string").str[:3]`, o sea que cualquier valor mas largo se
    cortaba en silencio. Un `100X` de origen --una columna renombrada, un JOIN
    que trae basura pegada-- se publicaba como `100`: un estado base-out
    perfectamente valido en apariencia, que entra en los denominadores y en las
    medias ponderadas de media docena de tablas sin que nada lo delate.

    Ahora se quitan los espacios y se comprueba la longitud. Si algo no encaja,
    ABORTA con los valores culpables: el estado base-out es la unidad atomica de
    casi todo lo que publica el sitio, y es preferible no publicar a publicar una
    corrupcion que parece un dato.

    No se valida contra un conjunto canonico porque la marca la comparten dos
    columnas con alfabetos distintos --corredores y conteo--; la longitud es lo
    que tienen en comun y es lo que atrapa el caso real.
    """
    serie = df[col].astype("string").str.strip()

    malos = serie[serie.notna() & (serie.str.len() != 3)]
    if len(malos):
        muestra = sorted(malos.unique().tolist())[:10]
        raise ValueError(
            f"{dataset_key}: la columna '{col}' trae {len(malos):,} valores que no miden "
            f"tres caracteres. Antes se recortaban en silencio y quedaban como estados "
            f"validos en apariencia. Valores: {muestra}"
        )

    return serie


def _apply_dtypes(df, dataset):
    """
    Aplica y despues COMPRUEBA los tipos declarados en el catalogo.

    La comprobacion existia solo para tres de los dieciseis datasets, escrita a
    mano en cada bloque. Aqui es automatica: si un cast no deja la columna en el
    tipo declarado, se aborta en vez de publicar el parquet.
    """
    log = cli.get_logger()

    missing = [col for col in dataset.dtypes if col not in df.columns]
    if missing:
        raise ValueError(
            f"{dataset.key}: la consulta no devolvio las columnas {missing}. "
            f"Devolvio: {list(df.columns)}"
        )

    for col, dtype in dataset.dtypes.items():
        if dtype == STRING3:
            df[col] = _normalizar_string3(df, col, dataset.key)
        else:
            df[col] = df[col].astype(dtype)

    for col, dtype in dataset.dtypes.items():
        expected = "string" if dtype == STRING3 else dtype
        actual = str(df[col].dtype)
        if actual != expected:
            raise ValueError(
                f"{dataset.key}: la columna '{col}' quedo en {actual} y se esperaba {expected}"
            )

    unexpected = [c for c in df.columns if c not in dataset.dtypes]
    if unexpected:
        log.warning(f"  Columnas sin tipo declarado (se publican tal cual): {unexpected}")

    return df


def _extract_one(dataset, engine, data_dir):
    """Extrae, tipa y escribe un dataset. Devuelve el DataFrame resultante."""
    log = cli.get_logger()
    cli.section(log, f"Extrayendo {dataset.title}")

    lo, hi = seasons.rango(dataset.por_lanzamiento)
    suelo = "por lanzamiento" if dataset.por_lanzamiento else "general"
    log.info(f"  Temporadas pedidas: {lo}-{hi} (suelo {suelo})")

    started = time.monotonic()
    df = pd.read_sql(_resolver_temporadas(dataset.query), engine)
    log.info(f"  {len(df):,} filas en {time.monotonic() - started:.1f}s")

    # Recorte de garantia, ADEMAS del filtro que ya lleva la consulta.
    #
    # No es redundancia inutil: el de SQL esta por no traerse un siglo de filas
    # para tirarlas, y este es lo que hace que la promesa se cumpla aunque una
    # consulta nueva se olvide del marcador o una vista se rehaga sin la cota.
    # Que es exactamente lo que paso con swing/take, que sacaba la temporada de
    # YEAR(game_date) sin ninguna cota y colaba la temporada en curso.
    if dataset.season_col:
        df, descartadas = seasons.recortar(df, dataset.season_col, dataset.por_lanzamiento)
        if descartadas:
            log.warning(
                f"  AVISO: {descartadas:,} filas fuera de {lo}-{hi} descartadas despues "
                f"de la consulta. El filtro de SQL de este dataset no esta acotando; "
                f"revisa la consulta o la vista."
            )
    log.debug(f"  Columnas: {', '.join(df.columns.tolist())}")

    if dataset.season_col and dataset.season_col in df.columns:
        log.info(f"  Temporadas: {df[dataset.season_col].min()} - {df[dataset.season_col].max()}")

    df = _apply_dtypes(df, dataset)
    log.debug("  Tipos aplicados y validados")

    for col in dataset.extra_summary:
        if col not in df.columns:
            continue
        values = df[col].unique().tolist()
        if len(values) <= 12:
            log.debug(f"  {col}: {values}")
        else:
            log.debug(f"  {col}: {df[col].min()} - {df[col].max()}")

    output_file = data_dir / dataset.output
    if dataset.fmt == "json":
        with open(output_file, "w") as f:
            json.dump(df.to_dict(orient="records"), f, indent=2)
            f.flush()
            os.fsync(f.fileno())
    else:
        df.to_parquet(output_file, index=False, engine="pyarrow")

    log.info(f"  -> {output_file.name}")
    return df


def _promote_staging(staging_dir: Path, final_dir: Path, expected_files: list,
                     completa: bool = True) -> None:
    """
    Mueve el lote de staging a app/data solo si esta completo.

    Comprueba primero que estan los ficheros esperados y que ninguno esta vacio;
    si falta alguno aborta sin tocar nada. La promocion usa os.replace, que es
    atomico por fichero dentro del mismo volumen. El conjunto no es atomico como
    un todo, pero la ventana de inconsistencia pasa de los minutos que dura la
    extraccion a los milisegundos que tardan los renombres.

    last_updated.json se mueve el ULTIMO, para que sirva de marca de commit: si
    su timestamp es nuevo, el lote entero esta completo.

    Y en una extraccion COMPLETA es obligatorio, no opcional. Antes el bucle
    hacia `if not source.exists(): continue`, o sea que si por una regresion
    dejara de escribirse, el lote se promovia igual: datos nuevos publicados con
    una marca de frescura vieja, que es lo que la web ensena al usuario. La
    semantica que dice este docstring era mas fuerte que el codigo.

    En una extraccion PARCIAL (--dataset) no se reescribe a proposito, porque
    seguiria describiendo un lote que ya no es el que hay; ahi `completa=False`.
    """
    log = cli.get_logger()
    cli.section(log, "VALIDANDO STAGING ANTES DE PROMOVER")

    missing = [name for name in expected_files if not (staging_dir / name).exists()]
    if missing:
        raise RuntimeError(
            f"Staging incompleto, faltan {len(missing)} fichero(s): {', '.join(missing)}. "
            f"No se promueve nada; app/data queda intacto."
        )

    empty = [name for name in expected_files if (staging_dir / name).stat().st_size == 0]
    if empty:
        raise RuntimeError(
            f"Ficheros vacios en staging: {', '.join(empty)}. No se promueve nada."
        )

    total_mb = sum((staging_dir / n).stat().st_size for n in expected_files) / (1024 * 1024)
    log.info(f"  {len(expected_files)} ficheros presentes y no vacios ({total_mb:.1f} MB)")

    # last_updated.json al final: es la marca de que el lote esta completo.
    marca = staging_dir / "last_updated.json"
    if completa and not marca.exists():
        raise RuntimeError(
            "La extraccion completa no genero last_updated.json, que es la marca "
            "de commit del lote. No se promueve nada: app/data queda intacto. "
            "Publicar datos nuevos con una marca de frescura vieja es peor que "
            "no publicarlos."
        )

    ordered = list(expected_files) + ["last_updated.json"]
    promoted = 0
    for name in ordered:
        source = staging_dir / name
        if not source.exists():
            continue
        os.replace(source, final_dir / name)
        promoted += 1

    shutil.rmtree(staging_dir, ignore_errors=True)
    log.info(f"  Promovidos {promoted} ficheros a {final_dir}")


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _warn_if_derived_data_now_stale(final_dir: Path, promoted: list) -> None:
    """
    Avisa si esta extraccion deja obsoletas las salidas de 03.

    create_break_even_points.py lee re24_values.parquet y re288_values.parquet
    y de ahi calcula breakeven_points, re_impact y sb_breakeven_by_count. Si se
    ejecuta 01 y no se ejecuta 03 despues, esas tres tablas quedan calculadas
    sobre una matriz de run expectancy distinta de la publicada.

    No es hipotetico: durante la revision de agosto de 2026, refrescar 01 cambio
    re24_values en +476 filas y re24_transitions en -4,144, y hubo que reejecutar
    03 a mano para volver a dejarlo coherente. Nada avisaba.

    Aqui solo se avisa; no se ejecuta 03 automaticamente porque necesita conexion
    a las vistas de analisis y tarda varios minutos. La comprobacion dura la hace
    tests/test_data_invariants.py.
    """
    log = cli.get_logger()

    manifest_path = final_dir / "derived_from.json"
    if not manifest_path.exists():
        return

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        recorded = manifest.get("inputs", {})
    except Exception:
        return

    changed = [
        name for name, old_hash in recorded.items()
        if name in promoted
        and (final_dir / name).exists()
        and _sha256(final_dir / name) != old_hash
    ]
    if not changed:
        return

    outputs = ", ".join(manifest.get("outputs", [])) or "las salidas de 03"
    log.warning("")
    log.warning("=" * 60)
    log.warning("ATENCION: los datos derivados quedan OBSOLETOS")
    log.warning("=" * 60)
    log.warning(f"  Cambiaron: {', '.join(changed)}")
    log.warning(f"  Dependen de ellos: {outputs}")
    log.warning("  Ejecuta ahora:  python utils/create_break_even_points.py")


def extract_and_save_data(only=None, data_dir=None):
    """
    Extract data from SQL Server and save as parquet files.

    Los ficheros se escriben primero en un directorio de staging y solo se
    promueven a app/data cuando TODOS se han generado. Antes se escribian in
    place uno a uno: si la extraccion fallaba en el decimo, app/data quedaba con
    diez ficheros nuevos y seis viejos, mutuamente inconsistentes, y la app los
    servia igual sin enterarse.

    only: lista de claves del catalogo a extraer. Si se pasa, se regeneran solo
    esos y last_updated.json NO se reescribe, porque seguiria describiendo un
    lote que ya no es homogeneo: el resto de ficheros conserva su version previa.
    """
    log = cli.get_logger()

    datasets = DATASETS
    if only:
        known = {ds.key for ds in DATASETS}
        unknown = [k for k in only if k not in known]
        if unknown:
            raise SystemExit(
                f"Dataset desconocido: {', '.join(unknown)}.\n"
                f"Disponibles: {', '.join(sorted(known))}"
            )
        datasets = [ds for ds in DATASETS if ds.key in set(only)]

    # data_dir es un parametro con valor por defecto y no una ruta fija dentro,
    # para poder escribir a un directorio temporal en los tests. En produccion
    # nadie lo pasa y el comportamiento es el mismo de siempre. Mismo patron que
    # save_pythag_data y load_re24_matrix.
    final_dir = Path(data_dir) if data_dir is not None else Path(__file__).parent.parent / "app" / "data"
    final_dir.mkdir(parents=True, exist_ok=True)

    # El staging vive DENTRO de app/data para garantizar que esta en el mismo
    # sistema de ficheros: os.replace solo es atomico dentro del mismo volumen.
    # La app lee app/data/*.parquet por nombre, asi que un subdirectorio no le
    # afecta.
    staging_dir = final_dir / ".staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    data_dir = staging_dir

    # Security: Set directory permissions to owner read/write/execute only (if possible).
    # En Windows chmod sobre un directorio es practicamente un no-op; se deja por
    # los despliegues POSIX.
    for target in (final_dir, staging_dir):
        try:
            os.chmod(target, 0o700)
        except Exception as perm_err:
            log.warning(f"No se pudieron ajustar permisos en {target}: {perm_err}")

    log.info(f"Staging: {staging_dir}")
    log.info(f"Destino final tras validar: {final_dir}")
    if only:
        log.warning(f"Extraccion PARCIAL: {len(datasets)} de {len(DATASETS)} datasets")

    engine = get_connection()
    started = time.monotonic()

    try:
        row_counts = {}
        for dataset in datasets:
            df = _extract_one(dataset, engine, data_dir)
            row_counts[dataset.output] = len(df)

        output_files = [ds.output for ds in datasets]

        if only:
            log.info("")
            log.info("last_updated.json NO se reescribe en una extraccion parcial: "
                     "describiria como nuevo un lote que solo lo es en parte.")
        else:
            cli.section(log, "Creando last_updated.json")
            last_updated_data = {
                "last_updated_utc": datetime.now(UTC).isoformat(),
                "files": output_files,
            }
            last_updated_file = data_dir / "last_updated.json"
            with open(last_updated_file, "w") as f:
                json.dump(last_updated_data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            log.info(f"  Timestamp: {last_updated_data['last_updated_utc']}")

        cli.section(log, "EXTRACCION COMPLETA")
        for name in output_files:
            log.info(f"  {name} ({row_counts[name]:,} filas)")
        log.info(f"  Tiempo total: {time.monotonic() - started:.1f}s")

        _promote_staging(staging_dir, final_dir, output_files, completa=not only)
        _warn_if_derived_data_now_stale(final_dir, output_files)

    except Exception as e:
        log.error(f"Error durante la extraccion: {e}")
        log.error(f"app/data queda INTACTO; se descarta el staging {staging_dir}")
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    finally:
        db.dispose_engine()
        log.debug("Conexion cerrada")


def main(argv=None):
    parser = cli.base_parser(
        "Extrae los datos de run expectancy de SQL Server y los publica como parquet."
    )
    parser.add_argument(
        "--dataset",
        action="append",
        metavar="CLAVE",
        help="extraer solo este dataset (repetible). Por defecto se extraen todos.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="listar los datasets disponibles y salir",
    )
    args = parser.parse_args(argv)

    log = cli.setup_logging(verbose=args.verbose, quiet=args.quiet, log_file=args.log_file)
    cli.aplicar_temporadas(args)

    if args.list:
        log.info(f"{len(DATASETS)} datasets en el catalogo:")
        for ds in DATASETS:
            log.info(f"  {ds.key:22s} -> {ds.output:30s} {len(ds.dtypes)} columnas tipadas")
        return 0

    cli.section(log, "Run Expectancy Data Extraction Tool")
    extract_and_save_data(only=args.dataset)
    log.info("")
    log.info("Script completado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
