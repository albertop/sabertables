CREATE OR ALTER VIEW [dbo].[v_wrc_league_inputs]
AS
-- Insumos de liga para wRAA, wRC y wRC+.
--
--     wRAA = ((wOBA - lgwOBA) / escala) * PA
--     wRC  = (((wOBA - lgwOBA) / escala) + lgR_PA) * PA
--     wRC+ = ((wRAA/PA + lgR_PA) + (lgR_PA - PF * lgR_PA)) / lgwRC_PA_sin_lanzadores * 100
--
-- Esta vista devuelve UNA FILA POR TEMPORADA con lo que esas tres formulas
-- necesitan del lado de la liga. La escala wOBA y los coeficientes NO estan aqui:
-- viven en wOBA_Coeffs.parquet y no se duplican.
--
-- SOLO TEMPORADA REGULAR. GAME_TYPE = 'R'. La postemporada tiene otra poblacion
-- de bateadores y mezclarla desplaza el lgwOBA.
--
-- EL DENOMINADOR DE wOBA NO ES PA.
--
-- Es AB + BB - IBB + SF + HBP, que aqui se construye por el otro lado: PA menos
-- intencionales, menos toques de sacrificio, menos interferencia del receptor.
-- Es la misma cantidad y evita tener que decidir que es turno oficial.
--
-- Se comprobo contra wOBA_Coeffs.League_Avg_OBP, que resulta ser el lgwOBA ya
-- calculado: coinciden EXACTO en las 116 temporadas. Si algun dia dejan de
-- coincidir, el denominador es lo primero que hay que mirar.
--
-- POR QUE HAY COLUMNAS "SIN LANZADORES".
--
-- El 100 del wRC+ se fija con el wRC/PA de la liga excluyendo a los lanzadores
-- bateando. Medido: excluirlos mueve el lgwOBA +0,0074 de media historica y hasta
-- +0,011 en los anos setenta, cuando los lanzadores eran el 8,9% de las
-- apariciones.
--
-- DESDE 2022 ESO SE ACABO. Con el bateador designado universal los lanzadores
-- bateando son el 0,02% de las apariciones y el efecto cae a +0,00001: tres
-- ordenes de magnitud menos. De 2022 en adelante las columnas con y sin
-- lanzadores son la misma cosa a efectos practicos. Se publican las dos igual,
-- porque la serie llega a 1910 y ahi la diferencia si importa.
--
-- El lanzador bateando se marca con BAT_FLD_CD = 1, que es su posicion en el
-- campo. OJO: el bateador emergente que sustituye al lanzador NO lleva esa marca,
-- asi que cuenta como no-lanzador. Es una decision, no un descuido; FanGraphs y
-- Baseball-Reference no la tratan igual.
--
-- LAS CARRERAS SE CUENTAN EN TODOS LOS EVENTOS, NO SOLO EN LOS DE BATEO.
--
-- BAT_EVENT_FL = 'T' estaba en el WHERE y filtraba las dos cosas a la vez. Las
-- apariciones al plato SI son eventos de bateo, pero las carreras no: se anota
-- tambien en un robo de home, un wild pitch, un passed ball o un balk, y esos no
-- lo son.
--
-- Medido en 2025: con el filtro salian 21.276 carreras; sin el, 21.613. Faltaban
-- 337, un 1,6% del total, y todo el lgR/PA salia bajo. Ese numero entra directo
-- en wRC y en wRC+ de cualquier bateador.
--
-- Se cuenta aqui y no en v_mlb_team_season_pythag_input --que da 21.579 para
-- 2025, 34 menos-- para que numerador y denominador salgan de la misma poblacion
-- de eventos. Esa diferencia de 34 esta sin explicar y merece mirarse aparte.
--
-- EL FACTOR DE PARQUE NO ESTA AQUI. Es por parque y temporada, no por liga; vive
-- en parkFactors.parquet (PF_R) y se cruza aguas abajo.
WITH eventos AS (
    SELECT
        SEASON,
        CASE WHEN BAT_FLD_CD = 1 THEN 1 ELSE 0 END AS lanzador_bateando,
        -- Denominador wOBA: PA - IBB - SH - interferencia.
        -- LAS APARICIONES AL PLATO SI SE FILTRAN POR BAT_EVENT_FL; LAS CARRERAS NO.
        -- Ver la nota de abajo.
        CASE WHEN BAT_EVENT_FL = 'T' AND EVENT_CD NOT IN (15, 17)
                  AND SH_FL <> 'T' THEN 1 ELSE 0 END AS den_woba,
        CASE WHEN BAT_EVENT_FL = 'T' AND EVENT_CD <> 17 THEN 1 ELSE 0 END AS pa,
        CASE WHEN EVENT_CD = 14 THEN 1 ELSE 0 END AS uBB,
        CASE WHEN EVENT_CD = 15 THEN 1 ELSE 0 END AS iBB,
        CASE WHEN EVENT_CD = 16 THEN 1 ELSE 0 END AS HBP,
        CASE WHEN EVENT_CD = 20 THEN 1 ELSE 0 END AS h1B,
        CASE WHEN EVENT_CD = 21 THEN 1 ELSE 0 END AS h2B,
        CASE WHEN EVENT_CD = 22 THEN 1 ELSE 0 END AS h3B,
        CASE WHEN EVENT_CD = 23 THEN 1 ELSE 0 END AS hHR,
        CASE WHEN SF_FL = 'T' THEN 1 ELSE 0 END AS SF,
        CASE WHEN SH_FL = 'T' THEN 1 ELSE 0 END AS SH,
        -- Carreras anotadas en la jugada, leidas de los destinos.
        CASE WHEN BAT_DEST_ID  >= 4 THEN 1 ELSE 0 END +
        CASE WHEN RUN1_DEST_ID >= 4 THEN 1 ELSE 0 END +
        CASE WHEN RUN2_DEST_ID >= 4 THEN 1 ELSE 0 END +
        CASE WHEN RUN3_DEST_ID >= 4 THEN 1 ELSE 0 END AS carreras
    FROM dbo.v_retroAllEventsRegAndPost
    WHERE GAME_TYPE = 'R'
)
SELECT
    SEASON AS season,

    -- Totales de liga, todos los bateadores.
    SUM(pa)                                 AS lg_pa,
    SUM(carreras)                           AS lg_runs,
    CAST(SUM(carreras) AS float) / NULLIF(SUM(pa), 0)  AS lg_r_per_pa,

    -- Los mismos, excluyendo lanzadores bateando. Es el denominador del 100 del
    -- wRC+. Desde 2022 vale practicamente lo mismo que el total.
    SUM(CASE WHEN lanzador_bateando = 0 THEN pa       ELSE 0 END) AS lg_pa_sin_p,
    SUM(CASE WHEN lanzador_bateando = 0 THEN den_woba ELSE 0 END) AS lg_woba_den_sin_p,
    SUM(CASE WHEN lanzador_bateando = 0 THEN carreras ELSE 0 END) AS lg_runs_sin_p,
    CAST(SUM(CASE WHEN lanzador_bateando = 0 THEN carreras ELSE 0 END) AS float)
        / NULLIF(SUM(CASE WHEN lanzador_bateando = 0 THEN pa ELSE 0 END), 0)
                                            AS lg_r_per_pa_sin_p,

    -- Cuanto pesan los lanzadores bateando. Es la cifra que dice si la columna
    -- "sin lanzadores" aporta algo en esa temporada: 8,9% en 1910, 0,02% en 2025.
    CAST(SUM(CASE WHEN lanzador_bateando = 1 THEN pa ELSE 0 END) AS float)
        / NULLIF(SUM(pa), 0)                AS lg_share_pa_pitchers,

    -- Sucesos del numerador de wOBA, SOLO SIN LANZADORES. Se publican en crudo,
    -- sin ponderar: los coeficientes cambian por temporada y viven en
    -- wOBA_Coeffs.parquet, y aplicarlos aqui obligaria a mantener los mismos
    -- numeros en dos sitios.
    --
    -- LOS DE LIGA ENTERA NO ESTAN, y es deliberado. Servirian para reconstruir el
    -- lgwOBA, pero ese dato ya existe: es wOBA_Coeffs.League_Avg_OBP, verificado
    -- identico al reconstruido en las 116 temporadas. Publicar aqui con que
    -- recalcularlo seria ofrecer una segunda fuente para el mismo numero.
    --
    -- Los sin lanzadores SI hacen falta: no hay otro sitio de donde sacar el
    -- lgwOBA excluyendolos, que es lo que fija el 100 del wRC+.
    SUM(CASE WHEN lanzador_bateando = 0 THEN uBB ELSE 0 END) AS lg_ubb_sin_p,
    SUM(CASE WHEN lanzador_bateando = 0 THEN HBP ELSE 0 END) AS lg_hbp_sin_p,
    SUM(CASE WHEN lanzador_bateando = 0 THEN h1B ELSE 0 END) AS lg_1b_sin_p,
    SUM(CASE WHEN lanzador_bateando = 0 THEN h2B ELSE 0 END) AS lg_2b_sin_p,
    SUM(CASE WHEN lanzador_bateando = 0 THEN h3B ELSE 0 END) AS lg_3b_sin_p,
    SUM(CASE WHEN lanzador_bateando = 0 THEN hHR ELSE 0 END) AS lg_hr_sin_p
FROM eventos
GROUP BY SEASON
HAVING SUM(pa) > 0;
