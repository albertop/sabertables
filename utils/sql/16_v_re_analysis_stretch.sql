CREATE OR ALTER VIEW [dbo].[v_re_analysis_stretch]
AS
-- Estirar un batazo una base de mas, medido con eventos reales.
--
--   direction 'double'  sencillo estirado a doble
--   direction 'triple'  doble estirado a triple
--
-- El problema de este analisis es que el EXITO no se anota como tal: un sencillo
-- estirado con exito se registra como DOBLE, indistinguible de un doble limpio
-- de pared. Solo el fallo es directamente observable (H_CD=1 con BAT_DEST_ID=0,
-- el bateador puesto out en segunda: 'S9/L89S.BX2(96)').
--
-- Se resuelve condicionando por LOCALIZACION Y TRAYECTORIA del batazo. Entre
-- batazos a la misma zona y con la misma trayectoria, unos bateadores se
-- quedaron y otros avanzaron: eso si son comparables. La zona controla por
-- "cuan estirable era la pelota", que es justo el confundidor.
--
-- Se restringe a las zonas donde estirar es una DECISION VIVA, definida como una
-- tasa de exito entre el 10% y el 75% con al menos 500 batazos. Fuera de esa
-- banda no hay decision que evaluar:
--
--   '56' rodado (hueco del shortstop)   0.8% de dobles  -> nunca se estira
--   '7LS' linea a la raya, corta       68.4% de dobles  -> es doble automatico
--
-- Para dobles quedan 48 zonas y 281,163 batazos (23% del total); para triples,
-- 34 zonas y 136,135.
--
-- Desde 1989 porque es cuando la localizacion del batazo pasa a estar completa:
-- 95.8% de cobertura ese ano, frente al 89.9% en 1988, 45% en 1987 y 12% antes
-- de 1986.
--
-- Una fila por (season, outs, runners, direction, bucket). La agregacion por
-- rango de temporadas la hace el frontend ponderando por n.
WITH hits AS (
    SELECT
        v.season,
        t.OUTS_CT,
        t.RUNNERS,
        v.H_CD,
        v.BAT_DEST_ID,
        v.BATTEDBALL_LOC_TX,
        v.BATTEDBALL_CD,
        CAST(t.RUN_VALUE AS float) AS run_value
    FROM dbo.v_retroAllEventsRegAndPost v
    JOIN dbo.r24Events t
      ON t.GAME_ID = v.GAME_ID
     AND t.EVENT_ID = v.EVENT_ID
    WHERE v.season >= 1989
      AND v.ERR_CT = 0
      AND v.BATTEDBALL_LOC_TX IS NOT NULL
      AND v.BATTEDBALL_LOC_TX <> ''
      -- Solo bases limpias, y esto NO es opcional.
      --
      -- Con corredores en base los cubos dejan de ser comparables. Cuando ponen
      -- out al bateador estirando es porque el tiro fue a esa base, lo cual
      -- correlaciona con que el corredor de delante ya anoto. Medido en el caso
      -- 'doble a triple, 2 outs, corredor en 1B': ese corredor anota en el 97%
      -- de los fallos (283 de 292) frente a la mitad de las veces cuando el
      -- bateador se para. Los dos grupos difieren en lo que hizo OTRO corredor,
      -- no solo en lo que hizo el bateador, y el break-even salia 0.025 --
      -- absurdo. Condicionando por el destino del corredor daba 0.891.
      --
      -- Con las bases limpias el bateador es el unico corredor y el confundidor
      -- desaparece por construccion.
      AND t.RUNNERS = '000'
),
zones_double AS (
    SELECT BATTEDBALL_LOC_TX AS loc, BATTEDBALL_CD AS bb
    FROM hits
    WHERE H_CD IN (1, 2)
    GROUP BY BATTEDBALL_LOC_TX, BATTEDBALL_CD
    HAVING COUNT_BIG(*) >= 500
       AND 1.0 * SUM(CASE WHEN H_CD = 2 THEN 1 ELSE 0 END) / COUNT_BIG(*) BETWEEN 0.10 AND 0.75
),
zones_triple AS (
    SELECT BATTEDBALL_LOC_TX AS loc, BATTEDBALL_CD AS bb
    FROM hits
    WHERE H_CD IN (2, 3)
    GROUP BY BATTEDBALL_LOC_TX, BATTEDBALL_CD
    HAVING COUNT_BIG(*) >= 500
       AND 1.0 * SUM(CASE WHEN H_CD = 3 THEN 1 ELSE 0 END) / COUNT_BIG(*) BETWEEN 0.10 AND 0.75
),
classified AS (
    -- Sencillo estirado a doble
    SELECT
        h.season, h.OUTS_CT, h.RUNNERS,
        'double' AS direction,
        CASE WHEN h.H_CD = 1 AND h.BAT_DEST_ID = 0 THEN 'fail'
             WHEN h.H_CD = 2                       THEN 'success'
             WHEN h.H_CD = 1 AND h.BAT_DEST_ID = 1 THEN 'stop'
        END AS bucket,
        h.run_value
    FROM hits h
    JOIN zones_double z
      ON z.loc = h.BATTEDBALL_LOC_TX
     AND z.bb  = h.BATTEDBALL_CD
    WHERE h.H_CD IN (1, 2)
      AND (h.H_CD = 2 OR h.BAT_DEST_ID IN (0, 1))

    UNION ALL

    -- Doble estirado a triple
    SELECT
        h.season, h.OUTS_CT, h.RUNNERS,
        'triple' AS direction,
        CASE WHEN h.H_CD = 2 AND h.BAT_DEST_ID = 0 THEN 'fail'
             WHEN h.H_CD = 3                       THEN 'success'
             WHEN h.H_CD = 2 AND h.BAT_DEST_ID = 2 THEN 'stop'
        END AS bucket,
        h.run_value
    FROM hits h
    JOIN zones_triple z
      ON z.loc = h.BATTEDBALL_LOC_TX
     AND z.bb  = h.BATTEDBALL_CD
    WHERE h.H_CD IN (2, 3)
      AND (h.H_CD = 3 OR h.BAT_DEST_ID IN (0, 2))
)
SELECT
    season,
    OUTS_CT AS outs,
    RUNNERS AS runners,
    direction,
    bucket,
    COUNT_BIG(*)     AS n,
    AVG(run_value)   AS mean_rv,
    STDEV(run_value) AS sd_rv
FROM classified
WHERE bucket IS NOT NULL
GROUP BY season, OUTS_CT, RUNNERS, direction, bucket;
