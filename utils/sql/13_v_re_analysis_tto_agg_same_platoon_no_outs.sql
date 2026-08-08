
/* ========================================================================== */
/* 2) Aggregate view (minimal output): only fields needed by final view       */
/*    Output fields kept: season, runners, handedness_split, tto_pass, n, avg_run_value */
/* ========================================================================== */
CREATE OR ALTER VIEW dbo.v_re_analysis_tto_agg_same_platoon_no_outs
AS
SELECT
    season,
    runners,
    handedness_split,
    tto_pass,
    COUNT(*) AS n,
    AVG(run_value) AS avg_run_value
FROM dbo.v_re_analysis_tto_base_same_platoon_no_outs
GROUP BY
    season,
    runners,
    handedness_split,
    tto_pass;
