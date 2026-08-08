








CREATE OR ALTER VIEW [dbo].[v_retroAllEventsRegAndPost]
as

--este view lo usa CreateR24Files.R
SELECT
    u.SEASON,
    u.GAME_TYPE,
    u.GAME_ID,
    u.AWAY_TEAM_ID,
    u.INN_CT,
    u.BAT_HOME_ID,
    u.OUTS_CT,
    u.BALLS_CT,
    u.STRIKES_CT,
    COALESCE(u.PITCH_SEQ_TX, '') AS PITCH_SEQ_TX,
    u.AWAY_SCORE_CT,
    u.HOME_SCORE_CT,
    u.BAT_ID,
    u.RESP_BAT_ID,
    u.RESP_BAT_HAND_CD,
    u.PIT_ID,
    u.RESP_PIT_ID,
    u.RESP_PIT_HAND_CD,
    u.POS2_FLD_ID,
    u.POS3_FLD_ID,
    u.POS4_FLD_ID,
    u.POS5_FLD_ID,
    u.POS6_FLD_ID,
    u.POS7_FLD_ID,
    u.POS8_FLD_ID,
    u.POS9_FLD_ID,
    COALESCE(u.BASE1_RUN_ID, '') AS BASE1_RUN_ID,
    COALESCE(u.BASE2_RUN_ID, '') AS BASE2_RUN_ID,
    COALESCE(u.BASE3_RUN_ID, '') AS BASE3_RUN_ID,
    u.EVENT_TX,
    u.LEADOFF_FL,
    u.PH_FL,
    u.BAT_FLD_CD,
    u.BAT_LINEUP_ID,
    u.EVENT_CD,
    u.BAT_EVENT_FL,
    u.AB_FL,
    u.H_CD,
    u.SH_FL,
    u.SF_FL,
    u.EVENT_OUTS_CT,
    u.DP_FL,
    u.TP_FL,
    u.RBI_CT,
    u.WP_FL,
    u.PB_FL,
    u.FLD_CD,
    COALESCE(u.BATTEDBALL_CD, '') AS BATTEDBALL_CD,
    u.BUNT_FL,
    u.FOUL_FL,
    COALESCE(u.BATTEDBALL_LOC_TX, '') AS BATTEDBALL_LOC_TX,
    u.ERR_CT,
    u.ERR1_FLD_CD,
    u.ERR1_CD,
    u.ERR2_FLD_CD,
    u.ERR2_CD,
    u.ERR3_FLD_CD,
    u.ERR3_CD,
    u.BAT_DEST_ID,   -- smallint (kept numeric)
    u.RUN1_DEST_ID,  -- smallint (kept numeric)
    u.RUN2_DEST_ID,  -- smallint (kept numeric)
    u.RUN3_DEST_ID,  -- smallint (kept numeric)
    COALESCE(u.BAT_PLAY_TX, '') AS BAT_PLAY_TX,
    COALESCE(u.RUN1_PLAY_TX, '') AS RUN1_PLAY_TX,
    COALESCE(u.RUN2_PLAY_TX, '') AS RUN2_PLAY_TX,
    COALESCE(u.RUN3_PLAY_TX, '') AS RUN3_PLAY_TX,
    u.RUN1_SB_FL,
    u.RUN2_SB_FL,
    u.RUN3_SB_FL,
    u.RUN1_CS_FL,
    u.RUN2_CS_FL,
    u.RUN3_CS_FL,
    u.RUN1_PK_FL,
    u.RUN2_PK_FL,
    u.RUN3_PK_FL,
    COALESCE(u.RUN1_RESP_PIT_ID, '') AS RUN1_RESP_PIT_ID,
    COALESCE(u.RUN2_RESP_PIT_ID, '') AS RUN2_RESP_PIT_ID,
    COALESCE(u.RUN3_RESP_PIT_ID, '') AS RUN3_RESP_PIT_ID,
    u.GAME_NEW_FL,
    u.GAME_END_FL,
    u.PR_RUN1_FL,
    u.PR_RUN2_FL,
    u.PR_RUN3_FL,
    COALESCE(u.REMOVED_FOR_PR_RUN1_ID, '') AS REMOVED_FOR_PR_RUN1_ID,
    COALESCE(u.REMOVED_FOR_PR_RUN2_ID, '') AS REMOVED_FOR_PR_RUN2_ID,
    COALESCE(u.REMOVED_FOR_PR_RUN3_ID, '') AS REMOVED_FOR_PR_RUN3_ID,
    COALESCE(u.REMOVED_FOR_PH_BAT_ID, '')  AS REMOVED_FOR_PH_BAT_ID,
    u.REMOVED_FOR_PH_BAT_FLD_CD, -- smallint
    u.PO1_FLD_CD,
    u.PO2_FLD_CD,
    u.PO3_FLD_CD,
    u.ASS1_FLD_CD,
    u.ASS2_FLD_CD,
    u.ASS3_FLD_CD,
    u.ASS4_FLD_CD,
    u.ASS5_FLD_CD,
    u.EVENT_ID,
    u.EV_STATE,
    u.PARK_ID,
    u.INNINGS_PLAYED
FROM (
    SELECT
        t1.SEASON,
        'R' AS GAME_TYPE,
        t1.GAME_ID,
        t1.AWAY_TEAM_ID,
        t1.INN_CT,
        t1.BAT_HOME_ID,
        t1.OUTS_CT,
        t1.BALLS_CT,
        t1.STRIKES_CT,
        t1.PITCH_SEQ_TX,
        t1.AWAY_SCORE_CT,
        t1.HOME_SCORE_CT,
        t1.BAT_ID,
        t1.RESP_BAT_ID,
        t1.RESP_BAT_HAND_CD,
        t1.PIT_ID,
        t1.RESP_PIT_ID,
        t1.RESP_PIT_HAND_CD,
        t1.POS2_FLD_ID,
        t1.POS3_FLD_ID,
        t1.POS4_FLD_ID,
        t1.POS5_FLD_ID,
        t1.POS6_FLD_ID,
        t1.POS7_FLD_ID,
        t1.POS8_FLD_ID,
        t1.POS9_FLD_ID,
        t1.BASE1_RUN_ID,
        t1.BASE2_RUN_ID,
        t1.BASE3_RUN_ID,
        t1.EVENT_TX,
        t1.LEADOFF_FL,
        t1.PH_FL,
        t1.BAT_FLD_CD,
        t1.BAT_LINEUP_ID,
        t1.EVENT_CD,
        t1.BAT_EVENT_FL,
        t1.AB_FL,
        t1.H_CD,
        t1.SH_FL,
        t1.SF_FL,
        t1.EVENT_OUTS_CT,
        t1.DP_FL,
        t1.TP_FL,
        t1.RBI_CT,
        t1.WP_FL,
        t1.PB_FL,
        t1.FLD_CD,
        t1.BATTEDBALL_CD,
        t1.BUNT_FL,
        t1.FOUL_FL,
        t1.BATTEDBALL_LOC_TX,
        t1.ERR_CT,
        t1.ERR1_FLD_CD,
        t1.ERR1_CD,
        t1.ERR2_FLD_CD,
        t1.ERR2_CD,
        t1.ERR3_FLD_CD,
        t1.ERR3_CD,
        t1.BAT_DEST_ID,
        t1.RUN1_DEST_ID,
        t1.RUN2_DEST_ID,
        t1.RUN3_DEST_ID,
        t1.BAT_PLAY_TX,
        t1.RUN1_PLAY_TX,
        t1.RUN2_PLAY_TX,
        t1.RUN3_PLAY_TX,
        t1.RUN1_SB_FL,
        t1.RUN2_SB_FL,
        t1.RUN3_SB_FL,
        t1.RUN1_CS_FL,
        t1.RUN2_CS_FL,
        t1.RUN3_CS_FL,
        t1.RUN1_PK_FL,
        t1.RUN2_PK_FL,
        t1.RUN3_PK_FL,
        t1.RUN1_RESP_PIT_ID,
        t1.RUN2_RESP_PIT_ID,
        t1.RUN3_RESP_PIT_ID,
        t1.GAME_NEW_FL,
        t1.GAME_END_FL,
        t1.PR_RUN1_FL,
        t1.PR_RUN2_FL,
        t1.PR_RUN3_FL,
        t1.REMOVED_FOR_PR_RUN1_ID,
        t1.REMOVED_FOR_PR_RUN2_ID,
        t1.REMOVED_FOR_PR_RUN3_ID,
        t1.REMOVED_FOR_PH_BAT_ID,
        t1.REMOVED_FOR_PH_BAT_FLD_CD,
        t1.PO1_FLD_CD,
        t1.PO2_FLD_CD,
        t1.PO3_FLD_CD,
        t1.ASS1_FLD_CD,
        t1.ASS2_FLD_CD,
        t1.ASS3_FLD_CD,
        t1.ASS4_FLD_CD,
        t1.ASS5_FLD_CD,
        t1.EVENT_ID,
        RIGHT('00' + CAST(CASE WHEN t1.INN_CT > 10 THEN 10 ELSE t1.INN_CT END AS varchar(2)), 2) +
            CAST(t1.BAT_HOME_ID AS char(1)) + '-' +
            CASE WHEN COALESCE(t1.BASE1_RUN_ID, '') = '' THEN '0' ELSE '1' END +
            CASE WHEN COALESCE(t1.BASE2_RUN_ID, '') = '' THEN '0' ELSE '1' END +
            CASE WHEN COALESCE(t1.BASE3_RUN_ID, '') = '' THEN '0' ELSE '1' END + '-' +
            CAST(t1.OUTS_CT AS varchar(1)) + '-' +
            CASE WHEN t1.AWAY_SCORE_CT = t1.HOME_SCORE_CT THEN '' ELSE
                CASE
                    WHEN (t1.BAT_HOME_ID = 0 AND t1.AWAY_SCORE_CT > t1.HOME_SCORE_CT)
                      OR (t1.BAT_HOME_ID = 1 AND t1.HOME_SCORE_CT > t1.AWAY_SCORE_CT)
                    THEN 'W' ELSE 'L'
                END
            END +
            CAST(CASE WHEN ABS(t1.AWAY_SCORE_CT - t1.HOME_SCORE_CT) > 5 THEN 6 ELSE ABS(t1.AWAY_SCORE_CT - t1.HOME_SCORE_CT) END AS varchar(1))
            AS EV_STATE,
        t2.PARK_ID,
        t2.INN_CT AS INNINGS_PLAYED
    FROM dbo.retroEventsReg t1
    INNER JOIN dbo.retroGamesReg t2
        ON t1.GAME_ID = t2.GAME_ID
    WHERE t1.AWAY_SCORE_CT IS NOT NULL
      AND t1.HOME_SCORE_CT IS NOT NULL
      AND t1.GAME_ID IS NOT NULL
      AND t1.INN_CT IS NOT NULL
      AND t1.BAT_ID IS NOT NULL
      AND t1.PIT_ID IS NOT NULL
      AND t1.BAT_HOME_ID IS NOT NULL
      AND t1.EVENT_ID IS NOT NULL

    UNION ALL

    SELECT
        t1.SEASON,
        'P' AS GAME_TYPE,
        t1.GAME_ID,
        t1.AWAY_TEAM_ID,
        t1.INN_CT,
        t1.BAT_HOME_ID,
        t1.OUTS_CT,
        t1.BALLS_CT,
        t1.STRIKES_CT,
        t1.PITCH_SEQ_TX,
        t1.AWAY_SCORE_CT,
        t1.HOME_SCORE_CT,
        t1.BAT_ID,
        t1.RESP_BAT_ID,
        t1.RESP_BAT_HAND_CD,
        t1.PIT_ID,
        t1.RESP_PIT_ID,
        t1.RESP_PIT_HAND_CD,
        t1.POS2_FLD_ID,
        t1.POS3_FLD_ID,
        t1.POS4_FLD_ID,
        t1.POS5_FLD_ID,
        t1.POS6_FLD_ID,
        t1.POS7_FLD_ID,
        t1.POS8_FLD_ID,
        t1.POS9_FLD_ID,
        t1.BASE1_RUN_ID,
        t1.BASE2_RUN_ID,
        t1.BASE3_RUN_ID,
        t1.EVENT_TX,
        t1.LEADOFF_FL,
        t1.PH_FL,
        t1.BAT_FLD_CD,
        t1.BAT_LINEUP_ID,
        t1.EVENT_CD,
        t1.BAT_EVENT_FL,
        t1.AB_FL,
        t1.H_CD,
        t1.SH_FL,
        t1.SF_FL,
        t1.EVENT_OUTS_CT,
        t1.DP_FL,
        t1.TP_FL,
        t1.RBI_CT,
        t1.WP_FL,
        t1.PB_FL,
        t1.FLD_CD,
        t1.BATTEDBALL_CD,
        t1.BUNT_FL,
        t1.FOUL_FL,
        t1.BATTEDBALL_LOC_TX,
        t1.ERR_CT,
        t1.ERR1_FLD_CD,
        t1.ERR1_CD,
        t1.ERR2_FLD_CD,
        t1.ERR2_CD,
        t1.ERR3_FLD_CD,
        t1.ERR3_CD,
        t1.BAT_DEST_ID,
        t1.RUN1_DEST_ID,
        t1.RUN2_DEST_ID,
        t1.RUN3_DEST_ID,
        t1.BAT_PLAY_TX,
        t1.RUN1_PLAY_TX,
        t1.RUN2_PLAY_TX,
        t1.RUN3_PLAY_TX,
        t1.RUN1_SB_FL,
        t1.RUN2_SB_FL,
        t1.RUN3_SB_FL,
        t1.RUN1_CS_FL,
        t1.RUN2_CS_FL,
        t1.RUN3_CS_FL,
        t1.RUN1_PK_FL,
        t1.RUN2_PK_FL,
        t1.RUN3_PK_FL,
        t1.RUN1_RESP_PIT_ID,
        t1.RUN2_RESP_PIT_ID,
        t1.RUN3_RESP_PIT_ID,
        t1.GAME_NEW_FL,
        t1.GAME_END_FL,
        t1.PR_RUN1_FL,
        t1.PR_RUN2_FL,
        t1.PR_RUN3_FL,
        t1.REMOVED_FOR_PR_RUN1_ID,
        t1.REMOVED_FOR_PR_RUN2_ID,
        t1.REMOVED_FOR_PR_RUN3_ID,
        t1.REMOVED_FOR_PH_BAT_ID,
        t1.REMOVED_FOR_PH_BAT_FLD_CD,
        t1.PO1_FLD_CD,
        t1.PO2_FLD_CD,
        t1.PO3_FLD_CD,
        t1.ASS1_FLD_CD,
        t1.ASS2_FLD_CD,
        t1.ASS3_FLD_CD,
        t1.ASS4_FLD_CD,
        t1.ASS5_FLD_CD,
        t1.EVENT_ID,
        RIGHT('00' + CAST(CASE WHEN t1.INN_CT > 10 THEN 10 ELSE t1.INN_CT END AS varchar(2)), 2) +
            CAST(t1.BAT_HOME_ID AS char(1)) + '-' +
            CASE WHEN COALESCE(t1.BASE1_RUN_ID, '') = '' THEN '0' ELSE '1' END +
            CASE WHEN COALESCE(t1.BASE2_RUN_ID, '') = '' THEN '0' ELSE '1' END +
            CASE WHEN COALESCE(t1.BASE3_RUN_ID, '') = '' THEN '0' ELSE '1' END + '-' +
            CAST(t1.OUTS_CT AS varchar(1)) + '-' +
            CASE WHEN t1.AWAY_SCORE_CT = t1.HOME_SCORE_CT THEN '' ELSE
                CASE
                    WHEN (t1.BAT_HOME_ID = 0 AND t1.AWAY_SCORE_CT > t1.HOME_SCORE_CT)
                      OR (t1.BAT_HOME_ID = 1 AND t1.HOME_SCORE_CT > t1.AWAY_SCORE_CT)
                    THEN 'W' ELSE 'L'
                END
            END +
            CAST(CASE WHEN ABS(t1.AWAY_SCORE_CT - t1.HOME_SCORE_CT) > 5 THEN 6 ELSE ABS(t1.AWAY_SCORE_CT - t1.HOME_SCORE_CT) END AS varchar(1))
            AS EV_STATE,
        t2.PARK_ID,
        t2.INN_CT AS INNINGS_PLAYED
    FROM dbo.retroEventsPost t1
    INNER JOIN dbo.retroGamesPost t2
        ON t1.GAME_ID = t2.GAME_ID
    WHERE t1.AWAY_SCORE_CT IS NOT NULL
      AND t1.HOME_SCORE_CT IS NOT NULL
      AND t1.GAME_ID IS NOT NULL
      AND t1.INN_CT IS NOT NULL
      AND t1.BAT_ID IS NOT NULL
      AND t1.PIT_ID IS NOT NULL
      AND t1.BAT_HOME_ID IS NOT NULL
      AND t1.EVENT_ID IS NOT NULL
) u;
