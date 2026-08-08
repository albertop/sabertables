

CREATE OR ALTER VIEW [dbo].[v_re_analysis_swing_take_zone] 
as

WITH pitch_base AS (
    SELECT
        game_pk,
        at_bat_number,
        pitch_number,
        CAST(game_date AS date) AS game_date,
        YEAR(game_date) AS season,

        pitcher,
        batter,
        p_throws,
        stand,

        balls,
        strikes,
        CONCAT(CAST(balls AS varchar(1)), '-', CAST(strikes AS varchar(1))) AS cnt,

        outs,
        CASE WHEN on_1b IS NULL THEN 0 ELSE 1 END AS on_1b_flag,
        CASE WHEN on_2b IS NULL THEN 0 ELSE 1 END AS on_2b_flag,
        CASE WHEN on_3b IS NULL THEN 0 ELSE 1 END AS on_3b_flag,

        zone,
        plate_x,
        plate_z,
        sz_top,
        sz_bot,

        pitch_type,
        release_speed,
        release_spin_rate,

        description,
        events,

        delta_run_exp
    FROM dbo.scastPitch
    WHERE
        game_type = 'R'
        AND balls BETWEEN 0 AND 3
        AND strikes BETWEEN 0 AND 2
        AND outs BETWEEN 0 AND 2
        AND zone IS NOT NULL
        AND delta_run_exp IS NOT NULL
),
classified AS (
    SELECT
        *,
        CASE
            WHEN description IN (
                'swinging_strike', 'swinging_strike_blocked', 'foul', 'foul_tip',
                'foul_bunt', 'missed_bunt',
                'hit_into_play', 'hit_into_play_no_out', 'hit_into_play_score'
            ) THEN 1
            WHEN description IN ('called_strike', 'ball', 'blocked_ball') THEN 0
            ELSE NULL
        END AS is_swing,

        CASE
            WHEN description IN (
                'foul', 'foul_tip', 'foul_bunt',
                'hit_into_play', 'hit_into_play_no_out', 'hit_into_play_score'
            ) THEN 1
            WHEN description IN ('swinging_strike', 'swinging_strike_blocked', 'missed_bunt') THEN 0
            ELSE NULL
        END AS is_contact,

        -- Desglose de cuatro resultados, alineado con v_re_analysis_swing_take.
        --
        -- is_contact agrupa el foul CON la bola en juego (convencion estandar,
        -- Contact% = 1 - SwStr%), mientras que la vista por conteo lo agrupaba
        -- con el abanico en falso: las dos familias usaban definiciones
        -- opuestas. Ninguna agrupacion de dos cubos sirve, porque con dos
        -- strikes el foul (EV -0.01) no se parece ni a la bola en juego
        -- (+0.06) ni al abanico (-0.23): es el unico de los tres que no
        -- resuelve el turno.
        --
        -- is_swing e is_contact se dejan intactos para no cambiar el significado
        -- de las vistas que los consumen.
        CASE
            WHEN description IN ('hit_into_play', 'hit_into_play_no_out', 'hit_into_play_score') THEN 'contact'
            WHEN description IN ('foul', 'foul_tip', 'foul_bunt') THEN 'foul'
            WHEN description IN ('swinging_strike', 'swinging_strike_blocked', 'missed_bunt') THEN 'whiff'
            WHEN description IN ('called_strike', 'ball', 'blocked_ball') THEN 'take'
            ELSE NULL
        END AS swing_outcome
    FROM pitch_base
)
SELECT
    game_pk,
    at_bat_number,
    pitch_number,
    game_date,
    season,
    pitcher,
    batter,
    p_throws,
    stand,
    balls,
    strikes,
    cnt,
    outs,
    on_1b_flag,
    on_2b_flag,
    on_3b_flag,
    zone,
    plate_x,
    plate_z,
    sz_top,
    sz_bot,
    pitch_type,
    release_speed,
    release_spin_rate,
    description,
    events,
    is_swing,
    is_contact,
    swing_outcome,
    delta_run_exp
FROM classified
WHERE is_swing IS NOT NULL;
