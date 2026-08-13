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

## Paso 0 — DECIDIDO: la forma es la de RE288

Rejilla de **24 filas × 12 columnas de conteo**, calcada de la tabla de RE288.
Mis tres opciones anteriores sobraban: la casa ya tiene resuelto exactamente este
problema —enseñar conteo y estado base-out a la vez— y la solución está en
`renderRE288Page`.

La estructura, tal cual:

| | |
|---|---|
| Filas | **8 estados de bases × 3 outs = 24** |
| Columnas | El rombo, la etiqueta, `Outs`, y **las 12 de conteo** (`count_columns`) |
| Rombo y etiqueta | `rowspan="3"`, una vez por grupo de estado de bases |
| Separador | Borde grueso cada 3 filas, al cerrar cada grupo |
| Columnas fijas | El rombo y la etiqueta van `sticky-col`: con 12 columnas de conteo la tabla se desplaza en horizontal y hay que saber en qué fila estás |

**Por qué las 24 filas, y por qué el rombo.** Porque **el eje de bases pesa MÁS
que el del conteo**, cosa que no supe hasta medirla:

| | rango del coste por canto |
|---|---|
| entre estados de bases, dentro de cada conteo | **0,2076 carreras** |
| entre conteos, colapsando las bases | 0,1684 carreras |

El caso más claro es 2-2: de **0,082 a 0,502 carreras** según las bases, seis
veces. Colapsar ese eje publicaría 0,1296 para los ocho estados y escondería el
hallazgo más grande de los dos. Y tiene sentido físico: con las bases llenas una
cuarta bola mal cantada empuja una carrera; con las bases vacías solo cambia un
conteo.

El rombo va porque hace legible ese eje, que es el idioma del sitio. No va de
adorno ni por copiar RE288.

**Aviso sobre cómo llegué aquí.** Puse las 24 filas y el rombo copiando RE288 por
inercia, sin medir si el eje de bases aportaba algo —el usuario había pedido 12
columnas de conteo y 3 de outs, o sea 3 filas—. La justificación de arriba
existe porque él preguntó *"¿y por qué vas a poner el rombo?"*. La conclusión
resultó ser la correcta; el camino no lo era.

Y de paso desaparece medio paso 4: el renderizador, la paginación por grupos de 3
y el manejo de `count_columns` ya existen. Lo que NO hay que hacer es escribirlo
de nuevo a mano, que es justo el error que cometí con el heatmap de wRC
Components.

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

## Paso 1-bis — DECIDIDO: parquet propio

`missed_calls.parquet`, no dentro de `re_impact.parquet`.

Ahí caben 6 filas por temporada —sac_bunt— y Missed Calls trae 8 estados × 3 outs
× 12 conteos × 2 brazos: **576 por temporada, ~10.000 en la serie**. Y una
dimensión que ese esquema no tiene, el conteo.

Esquema: `season, runners, outs, count, tipo, n, delta_re`.

Es la lección de los factores de parque de esta misma sesión: **la información
nueva no entra deformando la que ya funciona**. Si Missed Calls sale mal, los
otros seis análisis ni se enteran. El precio es un dataset y un endpoint más.

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
