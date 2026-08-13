CREATE OR ALTER VIEW [dbo].[v_re_analysis_missed_calls]
AS
-- Cantos equivocados: strikes que se cantan bola y bolas que se cantan strike.
--
-- Ver 8.9 en docs/TODO.md y docs/PLAN_MISSED_CALLS.md.
--
-- NO ES CATCHER FRAMING, Y ESA ES LA GRACIA.
--
-- Framing exige ATRIBUIR: separar lo que hizo el receptor de lo que hicieron el
-- lanzador, el arbitro y el bateador. Eso obliga a un modelo y a pelear con el
-- sesgo de seleccion --el mismo muro que freno el emparejamiento del bullpen--.
-- Esta vista mide EL SUCESO, no a quien culpar, y sin atribucion no hay confusion
-- que deshacer.
--
-- "CORRECTO" SIGNIFICA ZONA DE REGLAMENTO.
--
-- dbo.fn_is_strike da el rectangulo del reglamento: 17 pulgadas mas dos radios de
-- pelota de ancho, y de sz_bot a sz_top de alto. La zona que los arbitros cantan
-- de verdad NO es ese rectangulo: es ovalada y se mueve con el conteo y la
-- lateralidad. Asi que esto mide DESVIACION RESPECTO AL REGLAMENTO, que es una
-- definicion limpia y auditable, pero no dice que el arbitro se equivocara segun
-- el criterio vigente. Hay que escribirlo en el subtitulo de la tabla.
--
-- EL CONTEO SALE YA EN EL FORMATO DE re288_values: 'B-S', con guion.
--
-- No es un capricho. La primera medicion de esto devolvio 892 fallos en vez de
-- 30.839 porque scastPitchValues guarda el conteo como '12' y re288_values como
-- '1-2': todas las busquedas fallaban y solo sobrevivian los cantos terminales,
-- que van por otra rama. NO FALLO NADA -- devolvio un numero plausible y
-- equivocado. Formateandolo aqui, el lado de Python no tiene que reformatear y la
-- clase de fallo desaparece de raiz.
--
-- SOLO TEMPORADA REGULAR. En 2025 hay 14.245 lanzamientos de postemporada, un 2%.
-- No es catastrofico, pero es otra poblacion: en octubre ni los arbitros ni los
-- bateadores son una muestra de la temporada.
--
-- ESTA VISTA NO VALORA NADA. Devuelve la rejilla contada; las carreras salen de
-- re288_values y re24_values y se aplican en Python, como en GIDP y en el resto.
SELECT
    YEAR(p.game_date)                AS season,
    v.runners,
    v.outs,
    -- El conteo ANTES del lanzamiento, en el formato de la matriz RE288.
    CONCAT(p.balls, '-', p.strikes)  AS [count],
    p.balls,
    p.strikes,
    -- El canto real y el que dicta el reglamento.
    CASE WHEN p.description = 'called_strike' THEN 'S' ELSE 'B' END AS canto_real,
    dbo.fn_is_strike(p.plate_x, p.plate_z, p.sz_top, p.sz_bot)      AS canto_reglamento,
    COUNT(*)                         AS n
FROM dbo.scastPitch p
JOIN dbo.scastPitchValues v
      ON  v.game_pk       = p.game_pk
      AND v.at_bat_number = p.at_bat_number
      AND v.pitch_number  = p.pitch_number
WHERE p.game_type = 'R'
  AND p.description IN ('called_strike', 'ball')   -- solo lanzamientos TOMADOS
  -- Sin coordenadas no hay zona con la que comparar. Son 12.308 en 2008 y 76 en
  -- 2025; se excluyen aqui y el conteo de lo excluido se publica aparte, porque
  -- callarlo haria parecer que la cobertura es total desde el principio.
  AND p.plate_x IS NOT NULL
  AND p.plate_z IS NOT NULL
  AND p.sz_top  IS NOT NULL
  AND p.sz_bot  IS NOT NULL
GROUP BY
    YEAR(p.game_date), v.runners, v.outs,
    CONCAT(p.balls, '-', p.strikes), p.balls, p.strikes,
    CASE WHEN p.description = 'called_strike' THEN 'S' ELSE 'B' END,
    dbo.fn_is_strike(p.plate_x, p.plate_z, p.sz_top, p.sz_bot);
