CREATE OR ALTER VIEW [dbo].[v_re_analysis_sb_attempts]
AS
-- Intentos de robo REALES, contados por escenario y estado de base-out.
--
-- Existe para poder expresar el robo en CARRERAS y no solo como porcentaje de
-- break-even. El break-even sale de la matriz RE24 y no necesita saber cuantas
-- veces se intento; el valor esperado de intentarlo si:
--
--     neto = p * ganancia - (1 - p) * coste
--
-- y esa p es lo que cuenta esta vista. Ver la seccion 10 de docs/TODO.md.
--
-- LA FUENTE SON LAS BANDERAS POR CORREDOR, NO event_cd.
--
-- Un robo ocurre en un lanzamiento cualquiera y no tiene por que ser el evento
-- de la jugada: contarlo por `event_cd = 4` pierde todos los que suceden
-- mientras el bateador acaba haciendo otra cosa. `RUN1_SB_FL` marca al corredor
-- que salio de primera, `RUN2_SB_FL` al que salio de segunda, y asi.
--
-- LOS PICKOFFS SE EXCLUYEN, y no es un detalle menor.
--
-- `RUN1_CS_FL` se marca tambien cuando al corredor lo sorprenden fuera de base.
-- Eso NO es un atrapado robando: es otra jugada, con otra decision detras (ver
-- 8.1 en docs/TODO.md). En 2025, solo con corredor en primera, hubo 365. Si se
-- cuelan como CS, la tasa de exito sale artificialmente baja y todo el analisis
-- en carreras se inclina hacia "no robes".
--
-- EL ESTADO DE BASES SE DERIVA AQUI.
--
-- v_retroAllEventsRegAndPost no tiene columna `RUNNERS`; se construye desde
-- BASE1_RUN_ID / BASE2_RUN_ID / BASE3_RUN_ID, en el mismo orden 1B-2B-3B que usa
-- el resto del proyecto.
--
-- LOS NOMBRES DE ESCENARIO SON LOS DE breakeven_points.parquet, carácter a
-- carácter. Es lo que permite cruzar las dos tablas y comprobar la identidad
--
--     BE = coste / (ganancia + coste)
--
-- Si divergen, el test que compara las dos vistas del mismo numero deja de
-- cuadrar, que es exactamente lo que se quiere que ocurra.
--
-- VALIDACION (temporada 2025): corredor en 1B da 2.367 robos y 668 atrapados,
-- un 77,99% de exito, que es la tasa conocida de las Grandes Ligas tras el
-- cambio de reglas de 2023. Con 1B y 3B sube a ~93%: la defensa concede esa base
-- antes que arriesgar la carrera.
WITH eventos AS (
    SELECT
        SEASON,
        OUTS_CT,
        CONCAT(
            CASE WHEN BASE1_RUN_ID <> '' THEN '1' ELSE '0' END,
            CASE WHEN BASE2_RUN_ID <> '' THEN '1' ELSE '0' END,
            CASE WHEN BASE3_RUN_ID <> '' THEN '1' ELSE '0' END
        ) AS runners,
        -- Robo consumado por el corredor de primera y por el de segunda.
        CASE WHEN RUN1_SB_FL = 'T' THEN 1 ELSE 0 END AS sb1,
        CASE WHEN RUN2_SB_FL = 'T' THEN 1 ELSE 0 END AS sb2,
        -- Atrapado robando, SIN los sorprendidos fuera de base.
        CASE WHEN RUN1_CS_FL = 'T' AND RUN1_PK_FL <> 'T' THEN 1 ELSE 0 END AS cs1,
        CASE WHEN RUN2_CS_FL = 'T' AND RUN2_PK_FL <> 'T' THEN 1 ELSE 0 END AS cs2
    FROM dbo.v_retroAllEventsRegAndPost
),
-- Un escenario por cada uno de los cuatro que publica el break-even de robo.
-- Se hace con UNION ALL y no con un CASE porque el doble robo NO es exclusivo
-- con los otros: en el estado 110 se cuenta aparte cuando salen los dos.
intentos AS (
    -- 1) Corredor solo en primera.
    SELECT SEASON, OUTS_CT, runners,
           'Runner on 1B steals 2B' AS scenario,
           sb1 AS success, cs1 AS fail
    FROM eventos
    WHERE runners = '100' AND (sb1 = 1 OR cs1 = 1)

    UNION ALL

    -- 2) Corredor solo en segunda.
    SELECT SEASON, OUTS_CT, runners,
           'Runner on 2B steals 3B',
           sb2, cs2
    FROM eventos
    WHERE runners = '010' AND (sb2 = 1 OR cs2 = 1)

    UNION ALL

    -- 3) Primera y tercera: solo se mira al de primera. El de tercera casi nunca
    --    sale, y cuando lo hace es otra jugada distinta.
    SELECT SEASON, OUTS_CT, runners,
           'Runners on 1B & 3B, runner on 1B steals 2B',
           sb1, cs1
    FROM eventos
    WHERE runners = '101' AND (sb1 = 1 OR cs1 = 1)

    UNION ALL

    -- 4) Doble robo: salen LOS DOS a la vez desde primera y segunda.
    --    Exito solo si los dos llegan; si atrapan a cualquiera, es fallo.
    SELECT SEASON, OUTS_CT, runners,
           'Double steal',
           CASE WHEN sb1 = 1 AND sb2 = 1 THEN 1 ELSE 0 END,
           CASE WHEN (cs1 = 1 OR cs2 = 1) AND (sb1 = 1 OR sb2 = 1 OR cs1 = 1 OR cs2 = 1)
                THEN 1 ELSE 0 END
    FROM eventos
    WHERE runners = '110'
      AND (sb1 = 1 OR sb2 = 1 OR cs1 = 1 OR cs2 = 1)
)
SELECT
    SEASON        AS season,
    OUTS_CT       AS outs,
    runners,
    scenario,
    SUM(success)  AS n_success,
    SUM(fail)     AS n_fail,
    SUM(success) + SUM(fail) AS n_attempts
FROM intentos
GROUP BY SEASON, OUTS_CT, runners, scenario
HAVING SUM(success) + SUM(fail) > 0;
