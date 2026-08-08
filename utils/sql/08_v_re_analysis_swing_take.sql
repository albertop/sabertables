CREATE OR ALTER VIEW [dbo].[v_re_analysis_swing_take]
AS
SELECT
YEAR(game_date) AS season,
balls,
strikes,
outs,
CONCAT(
CASE WHEN on_1b IS NOT NULL THEN '1' ELSE '0' END,
CASE WHEN on_2b IS NOT NULL THEN '1' ELSE '0' END,
CASE WHEN on_3b IS NOT NULL THEN '1' ELSE '0' END
) AS runners,
-- Cuatro resultados, no tres. El foul estaba metido en 'miss' junto a los
-- abanicos en falso, y con dos strikes esos dos eventos son opuestos: el
-- abanico es el tercer strike y termina el turno, el foul deja el conteo igual
-- y el turno sigue. Medido sobre todas las temporadas de Statcast:
--
--   conteo   EV whiff   EV foul
--     0-0     -0.041     -0.041     <- con <2 strikes son indistinguibles
--     1-1     -0.064     -0.064
--     0-2     -0.166     -0.009     <- con 2 strikes difieren 20x
--     3-2     -0.324     -0.014
--
-- Y el foul es el 39-41% de los swings, asi que no es un caso marginal.
-- Se exponen los cuatro por separado; quien quiera el agrupado viejo solo tiene
-- que sumar 'foul' + 'whiff'.
CASE
WHEN description = 'hit_into_play' THEN 'contact'
WHEN description IN (
'foul',
'foul_tip',
'foul_bunt',
'foul_pitchout',
'bunt_foul_tip'
) THEN 'foul'
WHEN description IN (
'swinging_strike',
'swinging_strike_blocked',
'swinging_pitchout',
'missed_bunt'
) THEN 'whiff'
WHEN description IN (
'ball',
'blocked_ball',
'called_strike'
) THEN 'take'
END AS pitch_outcome,
delta_run_exp
FROM dbo.scastPitch
WHERE game_type = 'R'
AND delta_run_exp IS NOT NULL
AND description IN (
'hit_into_play',
'swinging_strike',
'swinging_strike_blocked',
'swinging_pitchout',
'missed_bunt',
'foul',
'foul_tip',
'foul_bunt',
'foul_pitchout',
'bunt_foul_tip',
'ball',
'blocked_ball',
'called_strike'
);
