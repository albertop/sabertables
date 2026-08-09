CREATE OR ALTER VIEW [dbo].[v_re_analysis_gidp]
AS
-- Doble matanza: cuanto cuesta en carreras, contra el out sencillo comparable.
--
-- Ver la seccion 8.7 de docs/TODO.md, con la medicion previa.
--
-- BATTEDBALL_CD VA COMO COLUMNA, NO COMO FILTRO. Es la decision de diseno.
--
-- Lo natural seria acotar a rodados --GIDP es "ground into double play"-- pero
-- ese campo solo cubre el 66,7% de la poblacion en 2025, y BAJA con los anos:
-- 75,2% en 1910, 81,4% en 1950, 70,1% en 2015. Meterlo en el denominador tira un
-- tercio de las oportunidades, y si el codigo falta de forma correlacionada con
-- el desenlace la tasa sale sesgada sin que nada avise.
--
-- DP_FL, en cambio, esta completo desde 1910: entre 19 y 26 dobles matanzas por
-- cada 1.000 eventos en todas las decadas, sin saltos.
--
-- Pero dejarlo fuera del todo tampoco vale, y esto se vio MIDIENDO la primera
-- version de la vista: con la poblacion entera, el brazo de comparacion --el out
-- sencillo-- se llena de ELEVADOS, que nunca pudieron ser doble play. Con
-- corredor en tercera y cero outs ese brazo anotaba 0,603 carreras, porque son
-- flies de sacrificio. Comparar la doble matanza contra eso mide otra cosa.
--
-- Solucion: la poblacion es todo batazo en juego que produce al menos un out,
-- con corredor en primera y menos de dos outs --cobertura del 100%--, y el tipo
-- de batazo viaja como columna. Asi el valor de la doble matanza se calcula
-- sobre todo lo que hay, y el contraste contra el out sencillo puede acotarse a
-- rodados donde el codigo existe, sin tener que elegir entre las dos cosas.
--
-- Entran los dobles plays de linea --262 de 3.600 en 2025--, que no son "ground
-- into". Son dobles matanzas de verdad y la tabla mide el coste de la doble
-- matanza, asi que entran, y quedan separables por la columna.
--
-- SE EXCLUYE EL PONCHE. EVENT_CD IN (2, 19) deja fuera el 3, que es el ponche:
-- ponche mas corredor fuera es una doble matanza, pero es OTRA JUGADA, con otra
-- decision detras. Mismo criterio que separa el pickoff del atrapado robando en
-- la vista 21.
--
-- SE EXCLUYE EL TOQUE. El sacrificio ya tiene su propio analisis, y ahi el out
-- del bateador es intencionado.
--
-- LOS CUATRO ESTADOS SON LOS QUE TIENEN FORZADO EN PRIMERA. Sin corredor en
-- primera la tasa de doble play va del 0% al 2%; con el, del 23% al 48%. Los
-- otros cuatro estados no se publican porque no hay jugada que medir.
--
-- EL ESTADO FINAL SE LEE, NO SE INFIERE. BAT_DEST_ID y RUN1..3_DEST_ID dan el
-- destino de cada corredor: 0 es out, 1 a 3 la base, 4 o mas anoto. De ahi salen
-- el estado de bases posterior y las carreras de la jugada, sin suponer nada
-- sobre quien avanzo.
--
-- ESTA VISTA NO CALCULA delta_re. Devuelve la rejilla de desenlaces contada; la
-- expectativa de carreras vive en el parquet de RE24 y se aplica en Python, como
-- en el resto del proyecto.
WITH eventos AS (
    SELECT
        SEASON,
        OUTS_CT,
        CONCAT(
            CASE WHEN BASE1_RUN_ID <> '' THEN '1' ELSE '0' END,
            CASE WHEN BASE2_RUN_ID <> '' THEN '1' ELSE '0' END,
            CASE WHEN BASE3_RUN_ID <> '' THEN '1' ELSE '0' END
        ) AS runners,
        CASE WHEN DP_FL = 'T' THEN 1 ELSE 0 END AS es_dp,
        -- Rodado, elevado, linea, palomita, o sin codigo. Ver la nota de arriba:
        -- separa el brazo de comparacion sin recortar la poblacion.
        CASE WHEN BATTEDBALL_CD IN ('G', 'F', 'L', 'P') THEN BATTEDBALL_CD
             ELSE '?' END AS tipo_batazo,
        EVENT_OUTS_CT,
        -- Estado de bases DESPUES de la jugada, leido de los destinos.
        CONCAT(
            CASE WHEN BAT_DEST_ID = 1 OR RUN1_DEST_ID = 1 THEN '1' ELSE '0' END,
            CASE WHEN BAT_DEST_ID = 2 OR RUN1_DEST_ID = 2
                      OR RUN2_DEST_ID = 2 THEN '1' ELSE '0' END,
            CASE WHEN BAT_DEST_ID = 3 OR RUN1_DEST_ID = 3 OR RUN2_DEST_ID = 3
                      OR RUN3_DEST_ID = 3 THEN '1' ELSE '0' END
        ) AS runners_fin,
        CASE WHEN BAT_DEST_ID  >= 4 THEN 1 ELSE 0 END +
        CASE WHEN RUN1_DEST_ID >= 4 THEN 1 ELSE 0 END +
        CASE WHEN RUN2_DEST_ID >= 4 THEN 1 ELSE 0 END +
        CASE WHEN RUN3_DEST_ID >= 4 THEN 1 ELSE 0 END AS carreras
    FROM dbo.v_retroAllEventsRegAndPost
    WHERE OUTS_CT < 2
      AND EVENT_OUTS_CT >= 1
      AND EVENT_CD IN (2, 19)   -- out en juego y eleccion defensiva; el ponche es el 3
      AND BUNT_FL <> 'T'
      AND BASE1_RUN_ID <> ''    -- forzado en primera: sin el no hay doble matanza
)
SELECT
    SEASON              AS season,
    OUTS_CT             AS outs,
    runners,
    es_dp,
    tipo_batazo,
    runners_fin,
    -- Outs tras la jugada. Puede llegar a 3, que cierra la entrada: en ese caso
    -- la expectativa posterior es 0 y las carreras de la jugada tampoco cuentan
    -- si el tercer out es forzado. Eso lo resuelve Python, que es quien tiene la
    -- matriz; aqui solo se cuenta.
    OUTS_CT + EVENT_OUTS_CT AS outs_fin,
    carreras,
    COUNT(*)            AS n
FROM eventos
GROUP BY SEASON, OUTS_CT, runners, es_dp, tipo_batazo, runners_fin,
         OUTS_CT + EVENT_OUTS_CT, carreras
HAVING COUNT(*) > 0;
