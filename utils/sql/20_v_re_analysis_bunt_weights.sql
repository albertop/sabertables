CREATE OR ALTER VIEW [dbo].[v_re_analysis_bunt_weights]
AS
-- Cuantos toques hizo cada bateador en cada celda, y cuanto vale esa celda para
-- la liga. Son los pesos con los que se arma la alternativa "swing_away".
--
--     swing_away(celda) = lg_rv(celda)
--                       + SUM(n_bunts_b * effect_b) / SUM(n_bunts_b)
--
-- El efecto por bateador esta en v_re_analysis_bunt_batter_effect y la union la
-- hace create_break_even_points.py; ver alli por que no van en una sola vista.
--
-- lg_rv se repite en todas las filas de la misma celda a proposito: asi el
-- ensamblado necesita un solo merge y no dos.
--
-- Mismo universo que v_re_analysis_bunt: bases limpias para el toque sorpresa,
-- corredor solo en 3B con menos de 2 outs para el squeeze.
WITH plain AS (
    SELECT t.SEASON, t.OUTS_CT, t.RUNNERS,
           run_value = CAST(t.RUN_VALUE AS float)
    FROM dbo.r24Events t
    WHERE t.BAT_EVENT_FL = 1
      AND t.SH = 0
),
league AS (
    SELECT SEASON, OUTS_CT, RUNNERS, lg_rv = AVG(run_value)
    FROM plain
    GROUP BY SEASON, OUTS_CT, RUNNERS
),
bunt_counts AS (
    SELECT
        t.SEASON,
        t.OUTS_CT,
        t.RUNNERS,
        t.BATTER_ID,
        scenario = CASE WHEN t.RUNNERS = '000' THEN 'surprise_bunt' ELSE 'squeeze' END,
        n_bunts  = COUNT_BIG(*)
    FROM dbo.v_retroAllEventsRegAndPost v
    JOIN dbo.r24Events t
      ON t.GAME_ID = v.GAME_ID
     AND t.EVENT_ID = v.EVENT_ID
    WHERE v.BAT_EVENT_FL = 'T'
      AND v.BUNT_FL = 'T'
      AND (t.RUNNERS = '000' OR (t.RUNNERS = '001' AND t.OUTS_CT < 2))
    GROUP BY t.SEASON, t.OUTS_CT, t.RUNNERS, t.BATTER_ID,
             CASE WHEN t.RUNNERS = '000' THEN 'surprise_bunt' ELSE 'squeeze' END
)
SELECT
    season    = bc.SEASON,
    outs      = bc.OUTS_CT,
    bc.scenario,
    batter_id = bc.BATTER_ID,
    bc.n_bunts,
    lg.lg_rv
FROM bunt_counts bc
JOIN league lg
  ON lg.SEASON = bc.SEASON AND lg.OUTS_CT = bc.OUTS_CT AND lg.RUNNERS = bc.RUNNERS;
