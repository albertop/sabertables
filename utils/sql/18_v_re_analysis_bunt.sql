CREATE OR ALTER VIEW [dbo].[v_re_analysis_bunt]
AS
-- Desenlace del toque sorpresa y del squeeze.
--
--   surprise_bunt   tocar buscando el hit, con bases limpias
--   squeeze         tocar para que anote el corredor de tercera
--
-- POR QUE SE REHIZO. El calculo anterior comparaba el toque contra el "Standard
-- PA", event_cd = 95. Ese codigo NO existe en Retrosheet: lo fabrica
-- extract_re_data.py agrupando los codigos 2,3,14,16,18,19,20,21,22,23, o sea el
-- turno que termina de forma normal. Cubre el 99.98% de los eventos, asi que el
-- baseline era indistinguible del promedio de la liga:
--
--     Standard PA (95):   0.500289      medido en 2025, 0 outs, bases limpias
--     All Events  (-1):   0.500446
--     delta:             -0.000157
--
-- OJO CON LA CONCLUSION FACIL: cambiar ese baseline por "la media de la liga en
-- el mismo estado" NO arregla nada, porque RUN_VALUE es un delta y esa media
-- vale ~0.000 por construccion. Lo que estaba mal es que la alternativa se
-- tomaba del bateador PROMEDIO, y quien toca no lo es. El ajuste por bateador no
-- es un refinamiento: es el arreglo. Vive en v_re_analysis_bunt_batter_effect y
-- v_re_analysis_bunt_weights, y se ensambla en create_break_even_points.py.
--
-- LA INTENCION NO ESTA ANOTADA, asi que se aisla con el estado de corredores:
--
--   Toque sorpresa -> BASES LIMPIAS. No hay a quien sacrificar, asi que un toque
--   solo puede buscar el hit. Se confirma en los datos: 0 de 24,810 toques con
--   bases limpias llevan SH_FL = 'T'. En cambio con corredor en 1B y 0 outs,
--   21,451 de 29,313 son sacrificios, y el resto mezcla toques para hit con
--   sacrificios que fallaron -- porque un sacrificio fallido no se anota como
--   SH. Filtrar por SH_FL, que es lo que hacia la vista anterior, deja dentro
--   toda esa contaminacion.
--
--   Squeeze -> SOLO CORREDOR EN 3B, con menos de 2 outs. Misma razon: sin otro
--   corredor no hay ambiguedad sobre a quien se intenta adelantar. Con 2 outs se
--   excluye porque el corredor no puede anotar si ponen out al bateador, asi que
--   no es un squeeze sino un toque para hit.
--
-- NO SE PUEDE separar suicide de safety. Se busco la huella del suicide -- el
-- corredor colgado en un ponche o un foul -- y hay UN caso en todo el corpus:
-- los 400 K/BF con corredor en tercera lo dejan en la base. Retrosheet no
-- registra si el corredor salio con el lanzamiento.
--
-- El cubo de fallo del squeeze incluye tanto el corredor puesto out en el plato
-- como el toque que no lo mueve: en ambos la carrera no entro. Excluir los que
-- se quedan tiraria el 13% de los intentos y subiria la tasa de exito observada
-- de 0.809 a 0.898 sin que nada haya mejorado.
--
-- A DIFERENCIA de running, esto no depende de la localizacion del batazo, asi
-- que no hay que cortar en 1989: BUNT_FL esta presente en todas las decadas
-- desde 1910, entre el 0.34% y el 0.86% de los turnos con bases limpias.
--
-- Los cubos PARTICIONAN el universo -- todo toque cae en exactamente uno -- asi
-- que aqui no hace falta el criterio endurecido de notacion + campo que si
-- necesitan v_re_analysis_tag_up y v_re_analysis_advance: alli un evento mal
-- clasificado desaparecia en silencio, y aqui no puede.
SELECT
    season   = t.SEASON,
    outs     = t.OUTS_CT,
    scenario = CASE WHEN t.RUNNERS = '000' THEN 'surprise_bunt' ELSE 'squeeze' END,
    bucket   = CASE
        WHEN t.RUNNERS = '000' THEN CASE WHEN v.BAT_DEST_ID  >= 1 THEN 'success' ELSE 'fail' END
        ELSE                        CASE WHEN v.RUN3_DEST_ID >= 4 THEN 'success' ELSE 'fail' END
    END,
    n        = COUNT_BIG(*),
    mean_rv  = AVG(CAST(t.RUN_VALUE AS float)),
    sd_rv    = STDEV(CAST(t.RUN_VALUE AS float))
FROM dbo.v_retroAllEventsRegAndPost v
JOIN dbo.r24Events t
  ON t.GAME_ID = v.GAME_ID
 AND t.EVENT_ID = v.EVENT_ID
WHERE v.BAT_EVENT_FL = 'T'
  AND v.BUNT_FL = 'T'
  AND (t.RUNNERS = '000' OR (t.RUNNERS = '001' AND t.OUTS_CT < 2))
GROUP BY
    t.SEASON,
    t.OUTS_CT,
    CASE WHEN t.RUNNERS = '000' THEN 'surprise_bunt' ELSE 'squeeze' END,
    CASE
        WHEN t.RUNNERS = '000' THEN CASE WHEN v.BAT_DEST_ID  >= 1 THEN 'success' ELSE 'fail' END
        ELSE                        CASE WHEN v.RUN3_DEST_ID >= 4 THEN 'success' ELSE 'fail' END
    END;
