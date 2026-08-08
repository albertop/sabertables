CREATE OR ALTER VIEW [dbo].[v_re_analysis_tag_up]
AS
-- Tag-ups medidos con eventos reales, no con transiciones teoricas sobre la
-- matriz RE24.
--
-- Una fila por (season, outs, runners, direction, bucket) con la media y la
-- desviacion del RUN_VALUE observado. La agregacion por rango de temporadas la
-- hace el frontend ponderando por n, igual que con el resto de break-evens.
--
-- Los tres desenlaces son observables:
--   success  el corredor avanza          '3-H'  /  '2-3'
--   fail     lo ponen out                '3XH(' /  '2X3('
--   stay     se queda
-- El fallo no hay que inferirlo: si lo ponen out en el plato, estaba yendo.
-- Por regla el SF solo se acredita cuando anota, asi que los fallos llevan SF=0
-- y no sirven para identificarlos.
--
-- Filtros:
--   EVENT_CD = 2        out
--   OUTS_CT < 2         con 2 outs la entrada acaba y no hay decision
--   fildeador 7/8/9     atrapada en los jardines. En el cuadro la tasa de
--                       intento es del 2.8% frente al 79.8% en jardin, asi que
--                       incluirlo llenaria el cubo 'stay' de palomitas donde
--                       nadie podia taguear
--   /F                  solo elevados. Las lineas atrapadas se excluyen
--   NOT /FL             sin fouls
--   ERR_CT = 0          sin errores, para que un avance por error no cuente
--                       como exito
--
-- 2B -> 3B exige tercera VACIA: con un corredor en 3B el de segunda solo puede
-- avanzar si aquel deja la base, y esa es otra decision.
WITH fly_outs AS (
    SELECT
        t.SEASON,
        t.OUTS_CT,
        t.RUNNERS,
        r.EVENT_TX,
        r.RUN2_DEST_ID,
        r.RUN3_DEST_ID,
        CAST(t.RUN_VALUE AS float) AS run_value
    FROM dbo.r24Events t
    JOIN dbo.v_retroAllEventsRegAndPost r
      ON r.GAME_ID = t.GAME_ID
     AND r.EVENT_ID = t.EVENT_ID
    WHERE t.EVENT_CD = 2
      AND t.OUTS_CT < 2
      AND LEFT(r.EVENT_TX, 1) IN ('7', '8', '9')
      AND (r.EVENT_TX LIKE '%/F[1-9]%' OR r.EVENT_TX LIKE '%/SF/F%')
      AND r.EVENT_TX NOT LIKE '%/FL%'
      AND r.ERR_CT = 0
),
classified AS (
    -- Corredor en 3B decidiendo si anota.
    --
    -- Se exigen a la vez la NOTACION y el CAMPO de destino, porque cada fuente
    -- por separado cuela casos mal clasificados:
    --
    --   El texto solo: la 'X' no implica out. Segun la especificacion de
    --   Retrosheet, un error dentro del parentesis ANULA el out --
    --   "BX2(7E4) means the batter reached second safely" -- asi que
    --   '3XH(5E2)' es un corredor a salvo, no un out.
    --
    --   El campo solo: RUN3_DEST_ID = 0 es "out", pero incluye 7,943 corredores
    --   puestos out en otras jugadas (forzados, doble play, atrapados fuera de
    --   base) que no son tag-ups fallidos. Solo 8,951 llevan '3XH'.
    --
    -- Ademas hay 2 eventos con '3XH' y destino 3 -- out en home y parado en
    -- tercera a la vez -- que exigir ambas condiciones descarta.
    --
    -- Destinos, determinados contra la notacion: 0 = out, 3 = se quedo,
    -- 4/5/6/7 = anoto (varian por el tratamiento de carrera limpia).
    SELECT
        SEASON, OUTS_CT, RUNNERS,
        'home' AS direction,
        CASE WHEN EVENT_TX LIKE '%3XH(%' AND RUN3_DEST_ID = 0  THEN 'fail'
             WHEN EVENT_TX LIKE '%3-H%'  AND RUN3_DEST_ID >= 4 THEN 'success'
             WHEN RUN3_DEST_ID = 3                             THEN 'stay'
        END AS bucket,
        run_value
    FROM fly_outs
    WHERE RIGHT(RUNNERS, 1) = '1'

    UNION ALL

    -- Corredor en 2B decidiendo si avanza a 3B, con tercera vacia.
    -- Mismos destinos: 0 = out, 2 = se quedo, 3 = avanzo.
    SELECT
        SEASON, OUTS_CT, RUNNERS,
        'third' AS direction,
        CASE WHEN EVENT_TX LIKE '%2X3(%' AND RUN2_DEST_ID = 0 THEN 'fail'
             WHEN EVENT_TX LIKE '%2-3%'  AND RUN2_DEST_ID = 3 THEN 'success'
             WHEN RUN2_DEST_ID = 2                            THEN 'stay'
        END AS bucket,
        run_value
    FROM fly_outs
    WHERE SUBSTRING(RUNNERS, 2, 1) = '1'
      AND RIGHT(RUNNERS, 1) = '0'
)
SELECT
    SEASON   AS season,
    OUTS_CT  AS outs,
    RUNNERS  AS runners,
    direction,
    bucket,
    COUNT_BIG(*)      AS n,
    AVG(run_value)    AS mean_rv,
    STDEV(run_value)  AS sd_rv
FROM classified
GROUP BY SEASON, OUTS_CT, RUNNERS, direction, bucket;
