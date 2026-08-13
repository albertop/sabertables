# Plan: Missed Calls en RE Impact Analysis

**Estado: propuesta. Nada aplicado.**

Ver 8.9 en [TODO.md](TODO.md) para la medición previa completa.

## Qué se publica

Un análisis más dentro de RE Impact: **cuántas carreras mueve un canto
equivocado**, por conteo y estado de bases. Filas por escenario, columnas por
outs, igual que el resto — pero con una diferencia que decide la forma de la
tabla.

**El eje que importa es el CONTEO, no las bases.** Medido en 2025: 0,146 carreras
por fallo con dos strikes contra ~0,00 en 2-0 y 3-0. El mismo error vale diez
veces más según cuándo ocurra, y esa es la historia que la tabla cuenta. Las bases
importan menos.

Eso rompe el molde de los demás análisis de RE Impact, que van por estado de
bases. Es la primera decisión de diseño y hay que tomarla antes de escribir nada.

## Paso 0 — Decidir la forma de la tabla

Tres opciones, y no es obvia:

- **12 filas de conteo × 3 columnas de outs.** La más fiel a lo medido. Se sale
  del molde: ninguna otra tabla de RE Impact tiene filas de conteo.
- **8 estados de bases × 3 outs**, con el conteo agregado. Encaja con el molde y
  con `escenariosPorEstadoDeBases`, pero **esconde el hallazgo principal**.
- **Las dos, con el selector de `re_impact_type` distinguiendo** `missed_calls` de
  `missed_calls_by_count`. Más trabajo y más superficie que mantener.

Sin esto resuelto, todo lo demás es prematuro. **Es una decisión del usuario.**

## Paso 1 — Los tests, antes que el código

La regla de la casa, y aquí hay tres que importan:

1. **Que la tabla se pinte** con filas y números, en navegador. Es el que faltó
   cuando las tres tablas de carreras salieron en blanco.
2. **Que los dos brazos sumen lo medido**: 18.682 strikes cantados bola y 12.157
   bolas cantadas strike en 2025. Si el pipeline cambia esos números sin que nadie
   lo pida, algo se rompió aguas arriba.
3. **Que la población sea SOLO temporada regular.** `game_type = 'R'`. En 2025
   hay 14.245 lanzamientos de postemporada —`D`, `F`, `L`, `W`—, un 2% del total.
   No es catastrófico, pero es **otra población**: en octubre ni los árbitros ni
   los bateadores son una muestra de la temporada. Es el mismo criterio que ya
   aplica `v_wrc_league_inputs`, y es exactamente el tipo de filtro que se cae en
   un refactor sin que nada avise.
4. **Que ningún lanzamiento se pierda en silencio.** La primera medición devolvió
   892 fallos en vez de 30.839 porque el formato del conteo en `re288_values` es
   `0-0` con guion y yo buscaba `00`. Solo sobrevivieron los cantos terminales, que
   van por otra rama. **No falló nada: devolvió un número plausible y equivocado.**
   El test tiene que exigir cero casillas sin encontrar.

## Paso 2 — La vista

`24_v_re_analysis_missed_calls.sql`, uniendo `scastPitch` con `scastPitchValues`
por `(game_pk, at_bat_number, pitch_number)`.

Devuelve la rejilla contada —temporada × bases × outs × conteo × canto real ×
canto de reglamento × n— y **no valora nada**. La valoración vive en los parquets
de RE288 y RE24 y se aplica en Python, como en GIDP y en todo lo demás.

Población: `game_type = 'R'`, `description IN ('called_strike','ball')`,
coordenadas no nulas.

## Paso 3 — El cálculo

`calculate_missed_calls_impact()` en `create_break_even_points.py`, con el patrón
de `calculate_gidp_impact`:

    impacto = valor(canto real) - valor(canto de reglamento)

donde `valor` sale de RE288 para los cantos que continúan el turno, y de RE24 para
los que lo terminan —tercer strike y cuarta bola—. **Esa costura entre dos
matrices es la parte frágil y necesita su propio test.**

## Paso 4 — Frontend

El tipo `missed_calls` en las seis cadenas de `app.js`. Si la forma elegida en el
paso 0 es por estado de bases, entra en `ESCENARIOS_POR_ESTADO_DE_BASES` y no hay
que tocar seis sitios. Si es por conteo, hace falta un camino nuevo.

Y el subtítulo, que aquí **no es cosmético**: tiene que decir que «correcto»
significa zona de reglamento, no la zona que los árbitros aplican de verdad. Sin
eso, la tabla se lee como una acusación al árbitro en vez de como lo que es.

## Lo que NO hay que hacer

- **No llamarlo framing.** En cuanto se atribuye al receptor hacen falta controles
  por lanzador, árbitro y conteo, y vuelve el sesgo de selección. Ese fue el
  motivo de cambiar de enfoque.
- No mezclarlo con el suelo de 1910 del resto del sitio: aquí es **2008**.
- No callar los lanzamientos sin coordenadas: 12.308 en 2008.
