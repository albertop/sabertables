CREATE OR ALTER VIEW [dbo].[v_re_analysis_platoon_baseline] AS
WITH joined_events AS (
    SELECT
        r.SEASON,
        r.OUTS_CT AS outs,
        CASE
            WHEN e.RESP_PIT_HAND_CD = 'R' AND e.RESP_BAT_HAND_CD = 'R' THEN 'RHP vs RHB'
            WHEN e.RESP_PIT_HAND_CD = 'L' AND e.RESP_BAT_HAND_CD = 'R' THEN 'LHP vs RHB'
            WHEN e.RESP_PIT_HAND_CD = 'R' AND e.RESP_BAT_HAND_CD = 'L' THEN 'RHP vs LHB'
            WHEN e.RESP_PIT_HAND_CD = 'L' AND e.RESP_BAT_HAND_CD = 'L' THEN 'LHP vs LHB'
        END AS handedness_split,
        CAST(r.RUN_VALUE AS float) AS run_value
    FROM dbo.r24Events r
    INNER JOIN dbo.v_retroAllEventsRegAndPost e
        ON r.GAME_ID = e.GAME_ID
       AND r.EVENT_ID = e.EVENT_ID
    WHERE r.BAT_EVENT_FL = 1
      AND r.RUN_VALUE IS NOT NULL
      AND r.OUTS_CT IN (0,1,2)
      AND e.RESP_PIT_HAND_CD IN ('R','L')
      AND e.RESP_BAT_HAND_CD IN ('R','L')
),
agg AS (
    SELECT
        season,
        outs,
        handedness_split,
        COUNT(*) AS n,
        AVG(run_value) AS split_avg_run_value
    FROM joined_events
    WHERE handedness_split IS NOT NULL
    GROUP BY season, outs, handedness_split
)
SELECT
    season,
    outs,
    handedness_split,
    n,
    split_avg_run_value
FROM agg;
