
CREATE OR ALTER VIEW [dbo].[v_re_analysis_ph_position_players]
AS
WITH ph_events AS
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
        AVG(t2.run_value) AS ph_avg_run_value,
        AVG(t2.li) AS avg_li
    FROM v_retroAllEventsRegAndPost t1
    INNER JOIN r24Events t2
        ON t1.game_id = t2.game_id
       AND t1.event_id = t2.event_id
    WHERE t1.inn_ct >= 7
      AND t1.RESP_BAT_HAND_CD IN ('L','R')
      AND t1.RESP_PIT_HAND_CD IN ('L','R')
      AND t1.PH_FL = 'T'
      AND t1.REMOVED_FOR_PH_BAT_FLD_CD <> 1
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
all_events AS
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
        t2.li,
        t2.run_value
    FROM v_retroAllEventsRegAndPost t1
    INNER JOIN r24Events t2
        ON t1.game_id = t2.game_id
       AND t1.event_id = t2.event_id
    WHERE t1.inn_ct >= 7
      AND t1.RESP_BAT_HAND_CD IN ('L','R')
      AND t1.RESP_PIT_HAND_CD IN ('L','R')
      AND t1.PH_FL = 'F'
      AND t1.BAT_FLD_CD <> 1
      AND t1.ERR_CT = 0
),
matched_exact AS
(
    SELECT
        ph.season,
        ph.outs,
        ph.handedness_bucket,
        COUNT(*) AS n_exact,
        AVG(u.run_value) AS avg_run_value_exact
    FROM ph_events ph
    INNER JOIN all_events u
        ON ph.season = u.season
       AND ph.outs = u.outs
       AND ph.handedness_bucket = u.handedness_bucket
       AND u.li BETWEEN ph.avg_li - 0.25 AND ph.avg_li + 0.25
    GROUP BY
        ph.season,
        ph.outs,
        ph.handedness_bucket
),
matched_by_bucket AS
(
    SELECT
        ph.season,
        ph.handedness_bucket,
        COUNT(*) AS n_bucket_level,
        AVG(u.run_value) AS avg_run_value_bucket_level
    FROM ph_events ph
    INNER JOIN all_events u
        ON ph.season = u.season
       AND ph.handedness_bucket = u.handedness_bucket
       AND u.li BETWEEN ph.avg_li - 0.25 AND ph.avg_li + 0.25
    GROUP BY
        ph.season,
        ph.handedness_bucket
),
matched_by_handness AS
(
    SELECT
        ph.season,
        ph.handness,
        COUNT(*) AS n_handness_level,
        AVG(u.run_value) AS avg_run_value_handness_level
    FROM ph_events ph
    INNER JOIN all_events u
        ON ph.season = u.season
       AND ph.handness = u.handness
       AND u.li BETWEEN ph.avg_li - 0.25 AND ph.avg_li + 0.25
    GROUP BY
        ph.season,
        ph.handness
),
matched_by_season AS
(
    SELECT
        ph.season,
        COUNT(*) AS n_season_level,
        AVG(u.run_value) AS avg_run_value_season_level
    FROM ph_events ph
    INNER JOIN all_events u
        ON ph.season = u.season
       AND u.li BETWEEN ph.avg_li - 0.25 AND ph.avg_li + 0.25
    GROUP BY
        ph.season
)
SELECT
    ph.season,
    ph.outs,
    ph.handedness_bucket,
    ph.handness,
    ph.n_ph AS n,
    ph.ph_avg_run_value
      - COALESCE(
            e.avg_run_value_exact,
            b.avg_run_value_bucket_level,
            h.avg_run_value_handness_level,
            s.avg_run_value_season_level,
            0
        ) AS delta_run_value
FROM ph_events ph
LEFT JOIN matched_exact e
    ON ph.season = e.season
   AND ph.outs = e.outs
   AND ph.handedness_bucket = e.handedness_bucket
LEFT JOIN matched_by_bucket b
    ON ph.season = b.season
   AND ph.handedness_bucket = b.handedness_bucket
LEFT JOIN matched_by_handness h
    ON ph.season = h.season
   AND ph.handness = h.handness
LEFT JOIN matched_by_season s
    ON ph.season = s.season
WHERE ph.n_ph >= 10;
