CREATE OR ALTER VIEW [dbo].[v_re_analysis_platoon_impact_final] AS
WITH split_agg AS (
    SELECT
        CAST(season AS int) AS season,
        CAST(outs AS int) AS outs,
        handedness_split,
        CAST(n AS bigint) AS n,
        CAST(split_avg_run_value AS float) AS split_avg_run_value
    FROM dbo.v_re_analysis_platoon_baseline
),
baseline AS (
    SELECT
        CAST(SEASON AS int) AS season,
        CAST(OUTS_CT AS int) AS outs,
        AVG(CAST(RUN_VALUE AS float)) AS baseline_avg_run_value,
        AVG(ABS(CAST(RUN_VALUE AS float))) AS baseline_abs_run_value
    FROM dbo.r24Events
    WHERE BAT_EVENT_FL = 1
      AND RUN_VALUE IS NOT NULL
      AND OUTS_CT IN (0,1,2)
    GROUP BY CAST(SEASON AS int), CAST(OUTS_CT AS int)
)
SELECT
    s.season,
    s.outs,
    s.handedness_split,
    s.n,
    s.split_avg_run_value - b.baseline_avg_run_value AS delta_run_value,
    b.baseline_avg_run_value,
    (s.split_avg_run_value - b.baseline_avg_run_value) / NULLIF(b.baseline_abs_run_value, 0.0) AS delta_run_value_scaled
FROM split_agg s
LEFT JOIN baseline b
    ON b.season = s.season
   AND b.outs = s.outs;
