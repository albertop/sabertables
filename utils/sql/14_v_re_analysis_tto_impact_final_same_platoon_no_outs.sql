
/* ========================================================================== */
/* 3) Final impact view (MINIMAL for Python/parquet)                          */
/*    Output fields kept:                                                     */
/*      season, runners, handedness_split, tto_pass, n,                       */
/*      delta_run_value, delta_run_value_scaled                               */
/* ========================================================================== */
CREATE OR ALTER VIEW dbo.v_re_analysis_tto_impact_final_same_platoon_no_outs
AS
WITH baseline AS (
    SELECT
        season,
        runners,
        handedness_split,
        AVG(run_value) AS baseline_avg_run_value,
        AVG(ABS(run_value)) AS baseline_abs_run_value
    FROM dbo.v_re_analysis_tto_base_same_platoon_no_outs
    WHERE tto_pass = 'TTO-1'
    GROUP BY
        season,
        runners,
        handedness_split
),
agg AS (
    SELECT
        season,
        runners,
        handedness_split,
        tto_pass,
        n,
        avg_run_value
    FROM dbo.v_re_analysis_tto_agg_same_platoon_no_outs
)
SELECT
    a.season,
    a.runners,
    a.handedness_split,
    a.tto_pass,
    a.n,
    CASE
        WHEN a.tto_pass = 'TTO-1' THEN CAST(0.0 AS float)
        ELSE (a.avg_run_value - b.baseline_avg_run_value)
    END AS delta_run_value,
    CASE
        WHEN a.tto_pass = 'TTO-1' THEN CAST(0.0 AS float)
        WHEN b.baseline_abs_run_value IS NULL OR b.baseline_abs_run_value = 0 THEN NULL
        ELSE (a.avg_run_value - b.baseline_avg_run_value) / b.baseline_abs_run_value
    END AS delta_run_value_scaled
FROM agg AS a
LEFT JOIN baseline AS b
    ON b.season = a.season
   AND b.runners = a.runners
   AND b.handedness_split = a.handedness_split;
