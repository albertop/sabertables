CREATE OR ALTER VIEW [dbo].[v_re_analysis_advance]
AS
-- Corredores ya en base tomando una base de mas en un batazo, medido con
-- eventos reales.
--
--   first_to_third   corredor de 1B a 3B en sencillo
--   second_to_home   corredor de 2B anota en sencillo
--   first_to_home    corredor de 1B anota en doble
--
-- A diferencia de estirar el propio batazo, aqui los tres desenlaces SI son
-- observables: el corredor avanza, lo ponen out, o se queda.
--
-- CRITERIO ENDURECIDO: se exigen a la vez la NOTACION y el CAMPO de destino.
-- Cada fuente por separado cuela casos mal clasificados:
--
--   El texto solo: la 'X' no implica out. Segun la especificacion de Retrosheet
--   un error dentro del parentesis ANULA el out -- "BX2(7E4) means the batter
--   reached second safely" -- asi que '1X3(5E4)' es un corredor a salvo.
--
--   El campo solo: DEST_ID = 0 es "out", pero incluye corredores puestos out en
--   otras jugadas (forzados, doble play, atrapados fuera de base) que no son
--   intentos de avance fallidos.
--
-- Destinos, determinados contra la notacion sobre 400k+ eventos:
--   0 = out, 1 = se quedo en 1B, 2 = a 2B, 3 = a 3B, 4/5/6/7 = anoto.
--
-- SE CONDICIONA POR EL DESTINO DEL BATEADOR. Sin eso los cubos no son
-- comparables: cuando ponen out al corredor es porque el tiro fue a esa base,
-- lo cual correlaciona con que el bateador tomo una base extra. Se fija al
-- destino modal -- 1B en un sencillo, 2B en un doble -- que es el caso normal y
-- aisla la decision del corredor.
--
-- SOLO UN CORREDOR EN BASE, por la misma razon: con mas corredores reaparece el
-- confundidor por lo que hicieron los otros.
--
-- ZONAS DE DECISION VIVA: se restringe a las combinaciones de localizacion y
-- trayectoria donde la tasa de exito esta entre el 10% y el 75% con al menos
-- 500 batazos. Fuera de esa banda no hay decision: un rodado al hueco del
-- shortstop nunca permite ir de primera a tercera (0.6%), y una linea a la raya
-- casi siempre si (81.5%).
--
-- Desde 1989, que es cuando la localizacion del batazo alcanza el 95.8% de
-- cobertura.
WITH plays AS (
    SELECT
        v.season,
        t.OUTS_CT,
        t.RUNNERS,
        v.EVENT_TX,
        v.H_CD,
        v.BAT_DEST_ID,
        v.RUN1_DEST_ID,
        v.RUN2_DEST_ID,
        v.BATTEDBALL_LOC_TX AS loc,
        v.BATTEDBALL_CD     AS bb,
        CAST(t.RUN_VALUE AS float) AS run_value
    FROM dbo.v_retroAllEventsRegAndPost v
    JOIN dbo.r24Events t
      ON t.GAME_ID = v.GAME_ID
     AND t.EVENT_ID = v.EVENT_ID
    WHERE v.season >= 1989
      AND v.ERR_CT = 0
      AND v.BATTEDBALL_LOC_TX IS NOT NULL
      AND v.BATTEDBALL_LOC_TX <> ''
),
-- Universo de cada escenario: estado de corredores, tipo de hit y destino modal
-- del bateador.
u_1to3 AS (
    SELECT * FROM plays WHERE RUNNERS = '100' AND H_CD = 1 AND BAT_DEST_ID = 1
),
u_2toh AS (
    SELECT * FROM plays WHERE RUNNERS = '010' AND H_CD = 1 AND BAT_DEST_ID = 1
),
u_1toh AS (
    SELECT * FROM plays WHERE RUNNERS = '100' AND H_CD = 2 AND BAT_DEST_ID = 2
),
z_1to3 AS (
    SELECT loc, bb FROM u_1to3 GROUP BY loc, bb
    HAVING COUNT_BIG(*) >= 500
       AND 1.0 * SUM(CASE WHEN RUN1_DEST_ID = 3 THEN 1 ELSE 0 END) / COUNT_BIG(*) BETWEEN 0.10 AND 0.75
),
z_2toh AS (
    SELECT loc, bb FROM u_2toh GROUP BY loc, bb
    HAVING COUNT_BIG(*) >= 500
       AND 1.0 * SUM(CASE WHEN RUN2_DEST_ID >= 4 THEN 1 ELSE 0 END) / COUNT_BIG(*) BETWEEN 0.10 AND 0.75
),
z_1toh AS (
    SELECT loc, bb FROM u_1toh GROUP BY loc, bb
    HAVING COUNT_BIG(*) >= 500
       AND 1.0 * SUM(CASE WHEN RUN1_DEST_ID >= 4 THEN 1 ELSE 0 END) / COUNT_BIG(*) BETWEEN 0.10 AND 0.75
),
classified AS (
    SELECT u.season, u.OUTS_CT, 'first_to_third' AS direction,
        CASE WHEN u.EVENT_TX LIKE '%1X3(%' AND u.RUN1_DEST_ID = 0 THEN 'fail'
             WHEN u.EVENT_TX LIKE '%1-3%'  AND u.RUN1_DEST_ID = 3 THEN 'success'
             WHEN u.RUN1_DEST_ID = 2                              THEN 'stop'
        END AS bucket,
        u.run_value
    FROM u_1to3 u JOIN z_1to3 z ON z.loc = u.loc AND z.bb = u.bb

    UNION ALL

    SELECT u.season, u.OUTS_CT, 'second_to_home',
        CASE WHEN u.EVENT_TX LIKE '%2XH(%' AND u.RUN2_DEST_ID = 0  THEN 'fail'
             WHEN u.EVENT_TX LIKE '%2-H%'  AND u.RUN2_DEST_ID >= 4 THEN 'success'
             WHEN u.RUN2_DEST_ID = 3                               THEN 'stop'
        END,
        u.run_value
    FROM u_2toh u JOIN z_2toh z ON z.loc = u.loc AND z.bb = u.bb

    UNION ALL

    SELECT u.season, u.OUTS_CT, 'first_to_home',
        CASE WHEN u.EVENT_TX LIKE '%1XH(%' AND u.RUN1_DEST_ID = 0  THEN 'fail'
             WHEN u.EVENT_TX LIKE '%1-H%'  AND u.RUN1_DEST_ID >= 4 THEN 'success'
             WHEN u.RUN1_DEST_ID = 3                               THEN 'stop'
        END,
        u.run_value
    FROM u_1toh u JOIN z_1toh z ON z.loc = u.loc AND z.bb = u.bb
)
SELECT
    season,
    OUTS_CT AS outs,
    direction,
    bucket,
    COUNT_BIG(*)     AS n,
    AVG(run_value)   AS mean_rv,
    STDEV(run_value) AS sd_rv
FROM classified
WHERE bucket IS NOT NULL
GROUP BY season, OUTS_CT, direction, bucket;
