
/* ========================================================================== */
/* 1) Base view (minimal output): only fields needed downstream               */
/*    Output fields kept: season, runners, handedness_split, tto_pass, run_value */
/* ========================================================================== */
CREATE OR ALTER VIEW dbo.v_re_analysis_tto_base_same_platoon_no_outs
AS
WITH pa AS (
    SELECT
        t1.GAME_ID,
        t1.EVENT_ID,
        t1.SEASON,
        RIGHT('000' + CAST(t2.RUNNERS AS varchar(3)), 3) AS runners,
        CAST(t2.RUN_VALUE AS float) AS run_value,
        t1.RESP_PIT_ID,
        t1.RESP_BAT_ID,
        t1.RESP_PIT_HAND_CD,
        t1.RESP_BAT_HAND_CD
    FROM dbo.v_retroAllEventsRegAndPost AS t1
    INNER JOIN dbo.r24Events AS t2
        ON t1.GAME_ID = t2.GAME_ID
       AND t1.EVENT_ID = t2.EVENT_ID
    WHERE t1.BAT_EVENT_FL = 'T'
      AND t2.RUN_VALUE IS NOT NULL
      AND t1.RESP_PIT_ID IS NOT NULL
      AND t1.RESP_BAT_ID IS NOT NULL
      AND t1.RESP_PIT_HAND_CD IN ('R', 'L')
      AND t1.RESP_BAT_HAND_CD IN ('R', 'L')
),
encountered AS (
    SELECT
        p.SEASON,
        p.runners,
        p.run_value,
        p.RESP_PIT_HAND_CD,
        p.RESP_BAT_HAND_CD,
        ROW_NUMBER() OVER (
            PARTITION BY p.GAME_ID, p.RESP_PIT_ID, p.RESP_BAT_ID
            ORDER BY p.EVENT_ID
        ) AS encounter_num
    FROM pa AS p
)
SELECT
    SEASON AS season,
    runners,
    CASE
        WHEN RESP_PIT_HAND_CD = RESP_BAT_HAND_CD THEN 'Same'
        ELSE 'Platoon'
    END AS handedness_split,
    CASE
        WHEN encounter_num = 1 THEN 'TTO-1'
        WHEN encounter_num = 2 THEN 'TTO-2'
        ELSE 'TTO-3'
    END AS tto_pass,
    run_value
FROM encountered;
