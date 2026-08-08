CREATE OR ALTER VIEW [dbo].[v_re_analysis_bunt_batter_effect]
AS
-- Cuanto se desvia cada bateador de la liga, en el mismo estado base-out.
--
-- Es la pieza que arregla el break-even del toque. La alternativa a tocar no es
-- "un turno promedio de la liga" sino "un turno de ESTE bateador", y quien toca
-- no es el bateador promedio. Medido sobre 1989-2025 con bases limpias: la liga
-- vale ~0.000, los bateadores que tocan -0.001 a -0.004 sin ponderar, y -0.007 a
-- -0.011 ponderando por cuantos toques hace cada uno. Los que mas tocan son los
-- que peor batean.
--
--     effect(b) = media sobre sus turnos de ( RUN_VALUE - media_liga(estado) )
--
-- Se mide sobre TODOS sus turnos y no solo los del estado de la celda porque si
-- no, el squeeze se queda sin alternativa: con corredor solo en 3B y 0 outs casi
-- ningun bateador junta turnos suficientes en una temporada.
--
-- SE EXCLUYEN LOS SACRIFICIOS con t.SH, columna de r24Events que coincide
-- exactamente con SH_FL de la vista de retro sobre los toques: 181,525 con 1/T y
-- 120,891 con 0/F, sin un solo caso cruzado. Para un lanzador el sacrificio es
-- buena parte de sus turnos y su delta es muy negativo, asi que dejarlos dentro
-- hundiria el baseline justo de los bateadores que mas pesan.
--
-- Quedan dentro los ~121k toques que NO son sacrificio, el 0.78% de los 15.56M
-- turnos. Sacarlos exigiria un anti-join contra v_retroAllEventsRegAndPost, que
-- es una union y no se puede indexar por BUNT_FL: la consulta pasaba de 8
-- segundos a no terminar en 20 minutos.
--
-- 50 turnos minimos: con menos, el efecto es mas ruidoso que el promedio que
-- pretende corregir.
--
-- POR QUE ESTA SUELTA Y NO DENTRO DE v_re_analysis_bunt. Medido: esta agregacion
-- tarda 8s por si sola, pero al unirla aguas abajo dentro de la misma sentencia
-- el optimizador re-expande el recorrido de los 15.5M turnos varias veces y la
-- consulta deja de terminar. Se deja suelta y el ensamblado lo hace pandas en
-- create_break_even_points.py sobre 48k filas.
WITH plain AS (
    SELECT t.SEASON, t.OUTS_CT, t.RUNNERS, t.BATTER_ID,
           run_value = CAST(t.RUN_VALUE AS float)
    FROM dbo.r24Events t
    -- BAT_EVENT_FL en r24Events es bit, no el 'T'/'F' de la vista de retro.
    WHERE t.BAT_EVENT_FL = 1
      AND t.SH = 0
),
league AS (
    SELECT SEASON, OUTS_CT, RUNNERS, lg_rv = AVG(run_value)
    FROM plain
    GROUP BY SEASON, OUTS_CT, RUNNERS
)
SELECT
    season     = p.SEASON,
    batter_id  = p.BATTER_ID,
    n_pa       = COUNT_BIG(*),
    effect     = AVG(p.run_value - l.lg_rv)
FROM plain p
JOIN league l
  ON l.SEASON = p.SEASON AND l.OUTS_CT = p.OUTS_CT AND l.RUNNERS = p.RUNNERS
GROUP BY p.SEASON, p.BATTER_ID
HAVING COUNT_BIG(*) >= 50;
