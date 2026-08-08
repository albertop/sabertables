
CREATE OR ALTER VIEW [dbo].[v_re_analysis_ph_pitchers]
AS
WITH all_pitchers_events AS
(
    SELECT
        t1.season,
        t1.outs_ct AS outs,
        CASE
            WHEN t1.RESP_PIT_HAND_CD = 'R' AND t1.RESP_BAT_HAND_CD = 'R' THEN 'RHP vs RHB'
            WHEN t1.RESP_PIT_HAND_CD = 'L' AND t1.RESP_BAT_HAND_CD = 'L' THEN 'LHP vs LHB'
            WHEN t1.RESP_PIT_HAND_CD = 'R' AND t1.RESP_BAT_HAND_CD = 'L' THEN 'RHP vs LHB'
            WHEN t1.RESP_PIT_HAND_CD = 'L' AND t1.RESP_BAT_HAND_CD = 'R' THEN 'LHP vs RHB'
        END AS handedness_bucket,
        CASE
            WHEN t1.RESP_BAT_HAND_CD <> t1.RESP_PIT_HAND_CD THEN 'platoon'
            ELSE 'same'
        END AS handness,
        COUNT(*) AS n_universe,
        AVG(t2.run_value) AS avg_run_value
    FROM v_retroAllEventsRegAndPost t1
    INNER JOIN r24Events t2
        ON t1.game_id = t2.game_id
       AND t1.event_id = t2.event_id
    -- TOPE DELIBERADO EN 2021, no es un filtro olvidado.
    --
    -- 2021 fue el ultimo anio en que los lanzadores batearon con regularidad:
    -- en 2022 la Liga Nacional adopto el bateador designado, que la Americana
    -- ya usaba desde 1973. Desde entonces un lanzador en el cajon de bateo es
    -- una rareza -- una entrada de paliza, un partido que se queda sin banco --
    -- y esas apariciones no describen el universo que este analisis quiere
    -- medir: cuanto se gana al sacar del cajon a un lanzador.
    --
    -- Sin el tope, el baseline mezclaria dos regimenes distintos y las
    -- temporadas de 2022 en adelante lo contaminarian con casos que no son
    -- comparables. Subir esta fecha NO tiene sentido mientras el DH sea
    -- universal.
    WHERE t1.season <= 2021
      AND t1.RESP_BAT_HAND_CD IN ('L', 'R')
      AND t1.RESP_PIT_HAND_CD IN ('L', 'R')
      AND t1.PH_FL = 'F'
      AND t1.BAT_FLD_CD = 1
      AND t1.ERR_CT = 0
    GROUP BY
        t1.season,
        t1.outs_ct,
        CASE
            WHEN t1.RESP_PIT_HAND_CD = 'R' AND t1.RESP_BAT_HAND_CD = 'R' THEN 'RHP vs RHB'
            WHEN t1.RESP_PIT_HAND_CD = 'L' AND t1.RESP_BAT_HAND_CD = 'L' THEN 'LHP vs LHB'
            WHEN t1.RESP_PIT_HAND_CD = 'R' AND t1.RESP_BAT_HAND_CD = 'L' THEN 'RHP vs LHB'
            WHEN t1.RESP_PIT_HAND_CD = 'L' AND t1.RESP_BAT_HAND_CD = 'R' THEN 'LHP vs RHB'
        END,
        CASE
            WHEN t1.RESP_BAT_HAND_CD <> t1.RESP_PIT_HAND_CD THEN 'platoon'
            ELSE 'same'
        END
),
baseline_by_bucket_outs AS
(
    SELECT
        season,
        outs,
        handedness_bucket,
        SUM(n_universe) AS n_outs_bucket_level,
        SUM(avg_run_value * n_universe) / NULLIF(SUM(n_universe), 0) AS avg_run_value_outs_bucket_level
    FROM all_pitchers_events
    GROUP BY
        season,
        outs,
        handedness_bucket
),
baseline_by_bucket_season AS
(
    SELECT
        season,
        handedness_bucket,
        SUM(n_universe) AS n_bucket_season_level,
        SUM(avg_run_value * n_universe) / NULLIF(SUM(n_universe), 0) AS avg_run_value_bucket_season_level
    FROM all_pitchers_events
    GROUP BY
        season,
        handedness_bucket
),
baseline_by_handness_season AS
(
    SELECT
        season,
        handness,
        SUM(n_universe) AS n_handness_season_level,
        SUM(avg_run_value * n_universe) / NULLIF(SUM(n_universe), 0) AS avg_run_value_handness_season_level
    FROM all_pitchers_events
    GROUP BY
        season,
        handness
),
baseline_by_season AS
(
    SELECT
        season,
        SUM(n_universe) AS n_season_level,
        SUM(avg_run_value * n_universe) / NULLIF(SUM(n_universe), 0) AS avg_run_value_season_level
    FROM all_pitchers_events
    GROUP BY season
),
pinch_hit_for_pitchers_events AS
(
    SELECT
        t1.season,
        t1.outs_ct AS outs,
        CASE
            WHEN t1.RESP_PIT_HAND_CD = 'R' AND t1.RESP_BAT_HAND_CD = 'R' THEN 'RHP vs RHB'
            WHEN t1.RESP_PIT_HAND_CD = 'L' AND t1.RESP_BAT_HAND_CD = 'L' THEN 'LHP vs LHB'
            WHEN t1.RESP_PIT_HAND_CD = 'R' AND t1.RESP_BAT_HAND_CD = 'L' THEN 'RHP vs LHB'
            WHEN t1.RESP_PIT_HAND_CD = 'L' AND t1.RESP_BAT_HAND_CD = 'R' THEN 'LHP vs RHB'
        END AS handedness_bucket,
        CASE
            WHEN t1.RESP_BAT_HAND_CD <> t1.RESP_PIT_HAND_CD THEN 'platoon'
            ELSE 'same'
        END AS handness,
        COUNT(*) AS n_ph,
        AVG(t2.run_value) AS ph_avg_run_value
    FROM v_retroAllEventsRegAndPost t1
    INNER JOIN r24Events t2
        ON t1.game_id = t2.game_id
       AND t1.event_id = t2.event_id
    -- TOPE DELIBERADO EN 2021, no es un filtro olvidado.
    --
    -- 2021 fue el ultimo anio en que los lanzadores batearon con regularidad:
    -- en 2022 la Liga Nacional adopto el bateador designado, que la Americana
    -- ya usaba desde 1973. Desde entonces un lanzador en el cajon de bateo es
    -- una rareza -- una entrada de paliza, un partido que se queda sin banco --
    -- y esas apariciones no describen el universo que este analisis quiere
    -- medir: cuanto se gana al sacar del cajon a un lanzador.
    --
    -- Sin el tope, el baseline mezclaria dos regimenes distintos y las
    -- temporadas de 2022 en adelante lo contaminarian con casos que no son
    -- comparables. Subir esta fecha NO tiene sentido mientras el DH sea
    -- universal.
    WHERE t1.season <= 2021
      AND t1.RESP_BAT_HAND_CD IN ('L', 'R')
      AND t1.RESP_PIT_HAND_CD IN ('L', 'R')
      AND t1.PH_FL = 'T'
      AND t1.REMOVED_FOR_PH_BAT_FLD_CD = 1
      AND t1.ERR_CT = 0
    GROUP BY
        t1.season,
        t1.outs_ct,
        CASE
            WHEN t1.RESP_PIT_HAND_CD = 'R' AND t1.RESP_BAT_HAND_CD = 'R' THEN 'RHP vs RHB'
            WHEN t1.RESP_PIT_HAND_CD = 'L' AND t1.RESP_BAT_HAND_CD = 'L' THEN 'LHP vs LHB'
            WHEN t1.RESP_PIT_HAND_CD = 'R' AND t1.RESP_BAT_HAND_CD = 'L' THEN 'RHP vs LHB'
            WHEN t1.RESP_PIT_HAND_CD = 'L' AND t1.RESP_BAT_HAND_CD = 'R' THEN 'LHP vs RHB'
        END,
        CASE
            WHEN t1.RESP_BAT_HAND_CD <> t1.RESP_PIT_HAND_CD THEN 'platoon'
            ELSE 'same'
        END
)
SELECT
    ph.season,
    ph.outs,
    ph.handedness_bucket,
    ph.handness,
    ph.n_ph AS n,
    ph.ph_avg_run_value
      - COALESCE(
            b1.avg_run_value_outs_bucket_level,
            b2.avg_run_value_bucket_season_level,
            b3.avg_run_value_handness_season_level,
            b4.avg_run_value_season_level,
            0
        ) AS delta_run_value
FROM pinch_hit_for_pitchers_events ph
LEFT JOIN baseline_by_bucket_outs b1
    ON ph.season = b1.season
   AND ph.outs = b1.outs
   AND ph.handedness_bucket = b1.handedness_bucket
LEFT JOIN baseline_by_bucket_season b2
    ON ph.season = b2.season
   AND ph.handedness_bucket = b2.handedness_bucket
LEFT JOIN baseline_by_handness_season b3
    ON ph.season = b3.season
   AND ph.handness = b3.handness
LEFT JOIN baseline_by_season b4
    ON ph.season = b4.season
WHERE ph.n_ph >= 10;
