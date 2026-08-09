# Pendientes

Estado tras la revisión de sabermetría, estadística y Python de agosto de 2026.
Los P0, P1, P2 y P3 de esa revisión están cerrados y validados; lo que queda es
esto.

Orden: primero lo que bloquea reconstruir o confiar en el pipeline, después las
decisiones de modelado, y al final la higiene.

**El proyecto ya está en git** (`github.com/albertop/sabertables`, privado) desde
el 8 de agosto de 2026. Varios puntos de abajo se escribieron cuando no lo
estaba, y hablan de mover cosas a `_not_in_use/` en vez de borrarlas o de
validar un refactor regenerando el pipeline entero. **Eso era la falta de red de
seguridad, no una norma**: hoy borrar es reversible. Se dejan como registro de
cómo se trabajó entonces.

Para reconstruir todo en orden:

```bash
python utils/run_all.py
```

---

## 1. Bloqueante de reproducibilidad

### 1.1 ~~Las vistas SQL no tienen DDL en el repositorio~~ — RESUELTO

Las **14** vistas del pipeline están ahora en `utils/sql/`, con prefijo numérico
que marca el orden de creación. Resultaron ser 14 y no 9: las vistas finales se
apoyan en intermedias (`v_re_analysis_platoon_baseline`,
`v_re_analysis_tto_base_*`, `v_re_analysis_swing_take_zone_aggr_handedness`) que
no aparecen en el código Python. El orden se resolvió con
`sys.sql_expression_dependencies`, no a ojo.

```bash
python utils/sync_sql_views.py --check    # compara ficheros vs base (por defecto)
python utils/sync_sql_views.py --apply    # crea o actualiza las vistas
python utils/sync_sql_views.py --dump     # regenera los ficheros desde la base
```

`--check` verifica que las 14 coinciden con la base y devuelve código 1 si hay
deriva. Probado en ambos sentidos: detecta una alteración artificial y pasa
limpio al restaurarla.

`--apply` se ejecutó contra la base y se verificó que no cambió nada:

- las 14 vistas se actualizaron sin error
- `--check` posterior sigue dando 14 iguales, 0 con deriva
- re-volcar produce ficheros byte-idénticos, o sea que las definiciones vivas
  no se movieron
- el pipeline completo (`02` y `03`) produce salidas **idénticas**:
  `breakeven_points`, `re_impact`, `sb_breakeven_by_count`, `pythag_exponents`
  y `runs_per_win` bit a bit; `ph_impact_source` dentro de la tolerancia float32
  conocida (ver 4.2)

Relacionado, y fuera de este punto: `01` consulta ~19 tablas base (`r24Values`,
`r24Events`, `retroGamesReg`, `mlbGames`…) que presumiblemente construye otro
proceso. Conviene documentar cuál es.

### 1.2 ~~No hay guard de orden entre `01` y `03`~~ — RESUELTO

`03` escribe ahora `app/data/derived_from.json` con el sha256 de las entradas
que consumió (`re24_values.parquet`, `re288_values.parquet`) y la lista de
salidas que calculó a partir de ellas. Con eso hay tres barreras:

1. **`01` avisa al terminar** si la extracción dejó obsoletas las salidas de
   `03`, con el nombre de los ficheros afectados y el comando a ejecutar.
2. **`tests/test_data_invariants.py`** falla si los hashes registrados no
   coinciden con los ficheros actuales — la comprobación dura.
3. El manifiesto queda como registro de procedencia consultable.

Verificado que las dos entradas son **estables bit a bit** entre corridas de
`01`, así que el hash no produce falsas alarmas. Los tres caminos del aviso
(cambió / no cambió / no hay manifiesto) están probados, y el test se comprobó
en ambos sentidos: detecta una entrada alterada y pasa limpio con los datos
coherentes.

Queda fuera: `02` no depende de `01` (lee sus datos directamente de SQL), así
que no necesita manifiesto.

---

## 2. Decisiones de modelado

Estas no son errores de código: el cálculo hace lo que dice hacer. Son preguntas
sobre qué debería significar el análisis, y por eso necesitan criterio del dueño
del proyecto.

### 2.1 ~~`running` y `tagging` no miden lo que dicen medir~~ — RESUELTO (agosto 2026)

Ambas familias se calculaban con **transiciones teóricas sobre la matriz RE24,
sin mirar un solo evento**. La coincidencia numérica con `stealing` era el
síntoma: la matriz solo conoce estados base-out, así que si la terna
(inicio → éxito → fallo) es la misma, el número es el mismo.

(Este punto llegó a cerrarse por error como "no es un defecto", razonando que la
aritmética era correcta dados los estados. Ese era el criterio equivocado: la
pregunta es si mide lo que dice medir.)

Ahora las **11 filas** —5 de `running` y 6 de `tagging`— salen de eventos reales
y ninguna coincide con `stealing`.

#### Vistas nuevas

| vista | qué mide |
|---|---|
| `15_v_re_analysis_tag_up` | tag-up a home y a tercera tras elevado atrapado |
| `16_v_re_analysis_stretch` | el bateador estirando su propio batazo |
| `17_v_re_analysis_advance` | un corredor ya en base tomando una base de más |

Como `RUN_VALUE` es un delta, la RE inicial se cancela:

```
BE = (Δ_stop − Δ_fail) / (Δ_success − Δ_fail)
```

#### Criterio endurecido, tras leer la especificación de Retrosheet

Se exigen **la notación y el campo de destino a la vez**, porque cada fuente por
separado cuela casos mal clasificados:

- **El texto solo**: la `X` no implica out. Un error dentro del paréntesis
  **anula** el out — *"BX2(7E4) means the batter reached second safely"* — así
  que `3XH(5E2)` es un corredor a salvo.
- **El campo solo**: `DEST_ID = 0` es "out", pero incluye 7,943 corredores
  puestos out en forzados, doble plays y atrapados fuera de base. Solo 8,951 de
  16,894 llevan `3XH`.

Los códigos de destino no están en `eventfile.htm`; se determinaron contra la
notación sobre 400k+ eventos: **0 = out, 1/2/3 = base alcanzada,
4/5/6/7 = anotó**.

Otras inferencias validadas contra la spec: el primer carácter del `EVENT_TX` es
el fildeador (`8/F78`), y `/F` (elevado) es un modificador distinto de `/FL`
(foul).

#### Confundidores que hubo que eliminar

**Otros corredores.** En "doble a triple con 2 outs y corredor en 1B" el
break-even salía **0.025** — absurdo. La causa: cuando ponen out al bateador en
tercera, el corredor de delante ya anotó en el **97%** de los casos (283 de 292),
frente a la mitad cuando el bateador se para. Los cubos diferían en lo que hizo
*otro* corredor. Condicionando por su destino daba **0.891**.

Solución: `stretch` se limita a **bases limpias** y `advance` a **un solo
corredor**, con el destino del bateador fijado al modal.

**Localización del batazo.** El éxito al estirar no es observable —un sencillo
estirado con éxito se anota como **doble**, indistinguible de uno limpio— así que
se condiciona por localización y trayectoria, restringido a zonas donde estirar
es una **decisión viva**: tasa de éxito entre 10% y 75% con n≥500. Fuera de esa
banda no hay decisión (rodado al hueco del shortstop: 0.8%; línea a la raya:
68.4%).

Por eso `running` arranca en **1989**, cuando la localización alcanza el 95.8% de
cobertura (89.9% en 1988, 45% en 1987, 12% antes de 1986).

#### `n` = fallos observados

No las oportunidades. Es el tamaño de muestra que manda —la varianza la domina
`Δ_fail`, cuyo error escala con `1/n_fail`— y es el peso correcto para el
promedio del frontend. Con oportunidades totales, una celda con 245 elevados y
**un solo fallo** saldría sin marcador de confianza.

#### Resultado

`running`, agregado 1989-2025:

| escenario | 0 outs | 1 out | 2 outs |
|---|---|---|---|
| Single stretched to double | 0.724 | 0.734 | 0.708 |
| Double stretched to triple | 0.774 | 0.686 | 0.890 |
| Runner on 1B to 3B on a single | 0.768 | 0.737 | 0.888 |
| Runner on 2B scores on a single | 0.945 | 0.732 | 0.416 |
| Runner on 1B scores on a double | 0.916 | 0.792 | 0.444 |

`tagging`:

| | 0 outs | 1 out |
|---|---|---|
| a home | ~0.71 | ~0.42 |
| a tercera | ~0.69 | ~0.88 |

Dos ejes que la versión teórica no podía producir:

- **Anotar se abre con los outs** (0.95 → 0.42). Con 0 outs mandas al corredor
  solo si es casi seguro; quedan dos oportunidades de traerlo.
- **Avanzar a tercera se cierra con los outs** (0.74 → 0.89). Con 2 outs, estar
  en tercera apenas mejora sobre segunda: anotas con un sencillo desde ambas.

Ese segundo patrón aparece por **tres caminos de datos independientes** —tag-up,
doble→triple y 1B→3B— lo que da confianza en que es señal.

Estirar el propio batazo a doble no depende de los outs (~0.72 constante):
el bateador es el único corredor y no hay a quién adelantar.

#### Frontend

`BREAKEVEN_SCENARIOS` en `app.js` lista los escenarios a mano, así que hubo que
actualizarlo — al no hacerlo, la tabla de `tagging` salió **completamente
vacía**. De ahí salió el test de contrato de escenarios (ver "Hecho en la
revisión").

El campo `runners` de esa lista alimenta el diagrama de bases y muestra el
**punto de decisión** —dónde está el corredor cuando decide— no el estado previo
al lanzamiento.

---

### 2.1-ter (histórico) Por qué coincidían los números

La coincidencia es real —20 de 36 celdas comparten valor con otra— pero es una
**consecuencia matemática de la métrica**, no un error. Las tres familias aplican
la misma fórmula sobre la matriz RE24, que solo conoce estados base-out y no
puede saber cómo se llegó a ellos:

```
BE = (RE_base − RE_fallo) / (bonus + RE_éxito − RE_fallo)
```

Si la terna (inicio → éxito → fallo) coincide, el resultado coincide. "Estirar un
sencillo a doble" es `100 → 010`, fallo `000`: la misma terna que "robar 2B desde
1B". Y `tagging` sale desplazada un out porque el elevado ya sumó el out, así que
la fórmula se evalúa en `o+1`.

Leído al derecho: *la pregunta "¿qué tasa de éxito justifica avanzar de 1B a 2B
arriesgando un out?" tiene la misma aritmética la robes o la tomes tras un
elevado*. Eso es un resultado, no un fallo.

**Corrección de la revisión original:** este punto se anotó como defecto y se
llegó a proponer añadir escenarios multi-corredor argumentando que `tagging`
"solo cubre el 20% de su dominio". Ese encuadre era incorrecto: se midió la
frecuencia de situaciones de elevado de sacrificio, pero `tagging` no es un
análisis de SF — modela la decisión de un corredor concreto, y para eso sus tres
escenarios son correctos.

**Opción futura, no correctiva.** Existe una forma distinta —y probablemente
mejor— de plantear estos análisis: medirlos con **eventos reales** en vez de con
transiciones teóricas. Se verificó que el dato está disponible:

- `v_retroAllEventsRegAndPost` tiene `H_CD` (tipo de hit), `BAT_DEST_ID`
  (destino del bateador, 0 = out) y `EVENT_TX` (jugada cruda).
- Intentos fallidos de estirar, observables directamente: `H_CD=1` con
  `BAT_DEST_ID=0` son **6,485** desde 2000 (`S9/L89S.BX2(96)` = sencillo,
  bateador out en segunda); en dobles son 1,533.
- Los elevados de sacrificio están en `r24Events.SF`, con su `RUN_VALUE`.
- El join `r24Events` ↔ `v_retroAllEventsRegAndPost` por `GAME_ID + EVENT_ID`
  funciona sin pérdida (0 sin casar sobre 192,317 eventos de 2024).

Daría números distintos a los teóricos, porque un sencillo estirable no es un
sencillo cualquiera: es uno bateado a un hueco, que además adelanta más a los
otros corredores. Pero es **una funcionalidad nueva, no una corrección**.

Caveat si se construye: el éxito no es directamente observable. Un sencillo
estirado con éxito se anota como **doble**, indistinguible de un doble limpio.
Solo el fallo se ve.

---

### 2.1-bis (histórico) El detalle de las coincidencias

Verificado sobre los datos publicados, valores idénticos:

```
stealing  Runner on 1B steals 2B      0 outs   0.697919   n=9826
running   Single stretched to double  0 outs   0.697919   n=9826

stealing  Runner on 2B steals 3B      0 outs   0.878547   n=2891
running   Double stretched to triple  0 outs   0.878547   n=2891

stealing  Runner on 1B steals 2B      1 out    0.733696
tagging   Runner on 1B tags up to 2B  0 outs   0.733696
```

Los dos primeros escenarios de `RUNNING_SCENARIOS` tienen exactamente las mismas
transiciones de estado que los dos primeros de `SB_SCENARIOS`, y toda la familia
`tagging` es `stealing` desplazada un out. Son la misma fórmula de dos parámetros
presentada tres veces: 6 de las 12 filas de `running` y las 6 de `tagging`.

Sabermétricamente no deberían coincidir. Estirar un sencillo a doble es una
decisión tomada **durante** la jugada, con el corredor ya en movimiento y con una
distribución de resultados distinta a la de un fallo de robo. Pero tal como está
modelado —estado inicial `100`, éxito `010`, fallo `000` + 1 out— es literalmente
la misma aritmética.

Resuelto como no-defecto; ver arriba. Se conserva el detalle porque documenta
por qué coinciden, que no es evidente al mirar la tabla.

### 2.1-quater `running` coincide con `stealing`, y es inevitable — INVESTIGADO (8 ago 2026)

Al cerrar el 2.1 se escribió que las 11 filas nuevas salen de eventos reales y
**"ninguna coincide con `stealing`"**. Eso es falso para los dos escenarios de
estirado, y conviene dejarlo claro.

Medido sobre `single_to_double`, 111 celdas (temporada, outs), comparando la
media observada de cada cubo contra la transición de la matriz RE24:

| cubo | \|diferencia\| mediana | idénticas a 1e-6 |
|---|---|---|
| `stop` | **0.00000** | **111/111** |
| `fail` | **0.00000** | **111/111** |
| `success` | 0.00276 | 12/111 |

**No es un fallo de implementación: el escenario es degenerado.** Con bases
limpias cada desenlace es un único estado base-out:

- `stop` → el bateador a salvo en 1B, estado `100` con los mismos outs
- `fail` → el bateador out, estado `000` con un out más

Todos los eventos de esos cubos valen exactamente lo mismo, así que su media *es*
el valor de la matriz. El condicionamiento por zonas de decisión viva cambia
**cuántos** eventos entran —y por eso `n` sí difiere— pero no puede cambiar cuánto
vale cada uno.

Y el álgebra lo remata. Como `RUN_VALUE` es un delta, la RE inicial se cancela:

```
BE_estirado = [RE(1B,o) − RE(000,o+1)] / [RE(2B,o) − RE(000,o+1)]
BE_robo     = [RE(1B,o) − RE(000,o+1)] / [RE(2B,o) − RE(000,o+1)]
```

Idénticas. La coincidencia estaba forzada desde el principio.

**El 2.1 no quedó a medias.** La vista existe, el criterio endurecido de
Retrosheet está aplicado, y `advance` y `tag_up` sí miden algo que la matriz no
sabe: ahí los desenlaces **no** son degenerados —hay otros corredores y entran
carreras—, así que las medias observadas no coinciden con ninguna transición.

Lo que cambió en los escenarios de estirado fue la **procedencia** del número y su
`n` (fallos observados en vez de oportunidades teóricas), no el número.

**Decidido (8 ago 2026): no se dice en la UI.** Se llegó a poner una nota en el
pie de la tabla y se retiró. Dos razones:

- Salía en **toda** la familia `running`, pero solo aplica a 2 de sus 5 filas —
  los dos escenarios de estirado. Los tres de avance de corredor no coinciden con
  nada.
- Nadie mira las dos tablas a la vez, así que explicaba una coincidencia que el
  visitante no iba a notar, en un pie que ya carga la leyenda de fiabilidad.

Queda documentado **aquí**, que es donde le sirve a quien mantenga el código.
Medido: correlación 0.9973 y 12 empates exactos de 111.


### 2.2 ~~El baseline de bunt-for-hit es prácticamente cero~~ — RESUELTO (8 ago 2026)

**`event_cd = 95` no es un código de Retrosheet.** Lo fabrica
[extract_re_data.py:735](../utils/extract_re_data.py) agrupando los códigos
`2,3,14,16,18,19,20,21,22,23`, o sea el turno que acaba de forma normal. Cubre el
99.98% de los eventos, así que el baseline valía lo mismo que el promedio general.

**Y ojo con la conclusión fácil:** cambiarlo por "la media de la liga en el mismo
estado" no habría arreglado nada, porque `RUN_VALUE` es un delta y esa media vale
~0.000 por construcción. Lo que estaba mal es que la alternativa se tomaba del
bateador **promedio**, y quien toca no lo es. **El ajuste por bateador no es un
refinamiento: es el arreglo.**

#### La intención no está anotada, así que se aísla con el estado de corredores

| universo | por qué es limpio |
|---|---|
| **Bases limpias** → toque sorpresa | No hay a quién sacrificar: **0 de 24,810** llevan `SH_FL='T'` |
| **Solo corredor en 3B**, <2 outs → squeeze | Sin otro corredor no hay duda sobre a quién se adelanta |

El filtro anterior (`bunt_fl='T' AND sh_fl<>'T'`) no sirve: **un sacrificio
fallido no se anota como SH**, así que caía en el cubo del toque para hit. Con
corredor en 1B y 0 outs, 21,451 de 29,313 toques son sacrificios.

#### Tres cubos, todos observados

```
BE = (Δ_swing_away − Δ_fail) / (Δ_success − Δ_fail)
```

`swing_away` = qué habrían valido esos turnos si **esos mismos bateadores**
hubieran bateado: `liga(celda) + Σ(toques_b · efecto_b) / Σ(toques_b)`. La
ponderación importa — medido 1989-2025 con bases limpias, la liga vale ~0.000,
los bateadores que tocan −0.001 a −0.004 sin ponderar y **−0.007 a −0.011**
ponderando. Los que más tocan son los que peor batean.

#### Resultado publicado, serie completa 1910-2025

| escenario | 0 outs | 1 out | 2 outs |
|---|---|---|---|
| Bunt for a hit | 0.364 | 0.364 | 0.405 |
| Squeeze | 0.842 •• | 0.633 | — |

Contra lo que había antes: 4 escenarios por estado de corredores, BE ponderado
global **0.537**, rango 0.214–1.230. Ahora **0.379**, rango 0.295–1.125.

Serie **completa desde 1910**: a diferencia de `running`, esto no depende de la
localización del batazo, y `BUNT_FL` está presente en todas las décadas.

#### Tres cosas que no se pueden hacer, dichas y no escondidas

- **No se puede separar suicide de safety.** Se buscó la huella del suicide —el
  corredor colgado en un ponche o un foul— y hay **1 caso en todo el corpus**:
  los 400 `K/BF` con corredor en tercera lo dejan en la base.
- **Dentro del mismo bateador sigue habiendo selección**: se toca cuando la
  tercera base está echada atrás. No es observable, y va en la nota de la UI.
- El **squeeze con 0 outs** son 20 intentos en 3 temporadas. Sale con el marcador
  `••` de baja confianza, que es para lo que está.

#### Dos defectos que aparecieron por el camino

**El umbral único borraba el squeeze entero.** `MIN_BUNT_FOR_HIT_N = 50` está
pensado para una jugada con ~460 intentos por temporada; el squeeze tiene ~24.
Ahora el umbral es por escenario, y un escenario nuevo sin umbral declarado
**aborta** en vez de desaparecer en silencio.

**Y el umbral estaba sobre la celda equivocada.** Aparecieron 6 break-even
negativos —un fallo mejor que no intentarlo, imposible— y los seis tenían
`n_fail` de 1 o 2 con hasta 19 éxitos. Con un solo fallo, `mean_rv_fail` es el
delta de un evento suelto. Mismo diagnóstico que en tag-up y que en el robo por
conteo. Barrido del mínimo de fallos en el squeeze:

| n_fail ≥ | celdas | intentos | BE<0 | BE ponderado |
|---|---|---|---|---|
| 1 | 147 | 2,816 | 6 | 0.6059 |
| 2 | 120 | 2,587 | 3 | 0.6326 |
| **4** | **70** | **1,882** | **0** | **0.6350** |
| 5 | 47 | 1,403 | 0 | 0.6380 |

Se eligió 4: limpia todos los negativos conservando el 67% de los intentos, y el
break-even ponderado apenas se mueve en todo el barrido (0.606 → 0.640), o sea
que la guarda quita ruido y no señal.

#### Rendimiento: por qué son tres vistas y no una

La primera versión hacía todo en una sentencia y **no terminaba en 20 minutos**.
Medido pieza a pieza: la agregación de liga tarda 2.4s, el efecto por bateador
8s, el universo de toques 3.1s — pero al unirlas en la misma sentencia el
optimizador re-expande el recorrido de los 15.5M turnos varias veces. Separadas
en `18_v_re_analysis_bunt`, `19_..._batter_effect` y `20_..._weights` tardan
**3.6s + 9.3s + 6.0s**, y el ensamblado lo hace pandas sobre 48k × 31k filas.

También se cambió el filtro del baseline: excluir sacrificios con `r24Events.SH`
(0.6s) en vez de un anti-join contra `BUNT_FL` en la vista de retro, que es una
unión y no se puede indexar por ese filtro. `SH` coincide **exactamente** con
`SH_FL` sobre los toques: 181,525 con 1/T y 120,891 con 0/F, sin un caso cruzado.

#### Validación

Ensamblado contra la medición a mano independiente (1989-2025): surprise 0 outs
0.342 vs 0.356, 1 out 0.345 vs 0.351, 2 outs 0.375 vs 0.383, squeeze 1 out 0.640
vs 0.625 — la diferencia es la esperada por medir el efecto del bateador sobre
todos los estados en vez de solo el de la celda. Cuatro tests unitarios nuevos
(la ponderación, la cobertura cuando falta el efecto, el mínimo de fallos, y el
escenario sin umbral). Comprobado en el contenedor: dos filas, el `••` aparece
solo en el squeeze de 0 outs, la celda de 2 outs sale `—`, y el diagrama del
squeeze pinta tercera en naranja `#ec8013`, idéntico a la referencia `001`.
112 tests en verde.

### 2.2-bis (histórico) El diagnóstico original

`calculate_bunt_for_hit_breakeven` compara el toque contra el "Standard PA"
(`event_cd = 95`). Medido en 2025, estado 0 outs / bases limpias:

```
Standard PA (95):   0.500289
All Events  (-1):   0.500446
delta:             -0.000157
```

El bucket 95 cubre el **99.98%** de los eventos, así que el baseline es
indistinguible del promedio general y la fórmula se reduce a
`BE = −EV_fallo / (EV_éxito − EV_fallo)`, es decir el break-even contra
"resultado neutro en RE", no contra una alternativa real.

Además hay un confundidor: quien toca para hit es un bateador flojo, no el
promedio de la liga, así que la alternativa está sobreestimada.

**Decisión:** ¿el baseline debería excluir los propios toques, condicionarse a
bateadores comparables, o está bien tal cual y solo hay que documentar qué
significa exactamente el número?

### 2.3 ~~`delta_re` mezcla medidas observadas y teóricas~~ — RESUELTO (agosto 2026)

**Decisión: corregir el texto, no la estructura de datos.**

Se descartaron la columna `measure_type` y la idea de publicar teórico +
observado + brecha para IBB. El diagnóstico original sobredimensionaba el
problema por no haber mirado antes lo que la UI ya hacía:

- **La fiabilidad ya estaba resuelta.** `RELIABILITY_DEFAULT_CONFIG` en `app.js`
  combina umbrales absolutos de n (`strictMinGoodN: 30`, `strictMinMediumN: 10`)
  con cuantiles relativos a los datos en pantalla, marca las celdas (`●` media,
  `••` baja confianza) y además las atenúa visualmente. El IBB de 0.483 con n=41
  ya salía marcado como estimación de baja confianza, con su leyenda al pie.
- **Cinco de los seis subtítulos ya eran correctos.** `ibb`, `ph_position`,
  `ph_pitcher`, `platoon_baseline` y `tto` son medidas observadas, y decir
  "usually" o "tends to" las describe bien.

El único desajuste real era `sac_bunt`, que decía *"How many runs your team
usually gains or loses when trying a sacrifice bunt"* siendo un valor que:

1. no es un promedio — es la resta de dos casillas de la matriz RE24, exacto;
2. está condicionado a que el toque salga, así que no incluye toques fallidos,
   toques que acaban en hit ni los que acaban en doble play.

Como su `n` es la frecuencia del estado base-outs (2,518 a 12,687) nunca dispara
un marcador, con lo que el lector veía un número sin advertencia descrito como
promedio empírico.

**Cambio aplicado**, en `app/static/js/app.js` (diccionario de subtítulos de
RE Impact y subtítulo de la tabla dedicada):

> How many runs your team loses when executing a successful sacrifice bunt.

Sale el "usually" —que era falso— y entra "successful", que marca la condición.

Nota factual: 3 de las 696 filas de `sac_bunt` son positivas (máximo +0.014;
1916, 1943 y 1958, todas con corredores en 1B y 2B y 0 outs), así que "loses" no
es literalmente universal. En 2025 son 0 de 6, y en total el 0.4%.

---

### 2.3-bis (histórico) `delta_re` mezcla medidas observadas y teóricas bajo el mismo nombre

En `re_impact.parquet` conviven dos cosas distintas en la misma columna:

| tipo | naturaleza | qué mide |
|---|---|---|
| `sac_bunt` | **teórica** | transición pura sobre la matriz RE24 |
| `ibb`, `ph_position`, `ph_pitcher`, `tto`, `platoon_baseline` | **observada** | promedio real de eventos, con sesgo de selección |

Ejemplo concreto: "IBB con corredor en 2B y 1 out" da **+0.549** observado, pero
la transición teórica pura (`010 → 110`, mismos outs) es **+0.292**. La diferencia
no es error: refleja que la base intencional se concede selectivamente y que el
valor observado incluye lo que realmente pasó el resto de la entrada.

Ninguna de las dos está mal, y la distinción sigue siendo cierta: se conserva
aquí porque es el contexto que hace falta para entender por qué el subtítulo de
`sac_bunt` tuvo que redactarse distinto al de los demás.

Lo que resultó no ser cierto es que el lector no tuviera forma de juzgar el
número: la señal de fiabilidad ya cubría la parte de ruido de muestreo. Ver
arriba la decisión.

### 2.4 ~~En swing/take, los foules cuentan como "miss"~~ — RESUELTO (agosto 2026)

**Decisión: modelo de tres desenlaces, y unificar las dos familias.**

Al investigarlo apareció que el problema era mayor de lo anotado: las dos
familias de swing/take usaban definiciones **opuestas**.

| vista | `foul` iba con |
|---|---|
| `v_re_analysis_swing_take` (por conteo) | `miss` |
| `v_re_analysis_swing_take_zone` (por zona) | **contacto** (`is_contact = 1`) |

Las dos tablas de la app decían "swing/take break-even" y respondían preguntas
distintas. Ninguna de las dos agrupaciones sirve, porque con dos strikes el foul
no se parece a ninguno de los otros dos:

| conteo | EV contacto | EV whiff | EV foul |
|---|---|---|---|
| 0-0 | +0.048 | −0.041 | −0.041 |
| 1-1 | +0.049 | −0.064 | −0.064 |
| **0-2** | +0.102 | **−0.166** | **−0.009** |
| **3-2** | −0.006 | **−0.324** | **−0.014** |

Con menos de dos strikes foul y whiff son indistinguibles. Con dos strikes
difieren 20-23×: el whiff es el tercer strike, el foul no resuelve nada. Y el
foul es el **39-41% de los swings**.

**Cambio aplicado.** Las dos vistas exponen ahora cuatro desenlaces
(`contact` / `foul` / `whiff` / `take`) y `03` resuelve el break-even con tres,
manteniendo fija la tasa de foul observada:

```
p_c* = [EV_take − p_f·EV_foul − (1−p_f)·EV_whiff] / (EV_contact − EV_whiff)
```

El denominador pasa a ser el recorrido completo entre bola en juego y abanico en
falso, que es grande y estable; el anterior se degeneraba en conteos de bateador
y disparaba el resultado.

Supuesto declarado en el docstring: al variar el contacto, la tasa de foul se
mantiene y lo que cambia es el whiff. La alternativa —mantener la proporción
foul:whiff— es lo que hacía el código anterior sin declararlo.

**Validación.** El efecto se concentra exactamente donde debía:

| strikes | efecto medio en el BE |
|---|---|
| 0 | +0.011 |
| 1 | +0.011 |
| **2** | **−0.225** (hasta −0.65 en 3-2) |

Que los conteos de menos de dos strikes **no se muevan** era la comprobación
clave: ahí agrupar daba igual, así que un cambio grande habría delatado un
error. Máximo global: 9.844 → 3.663 por conteo, 7.009 → 4.605 por zona.

**Hallazgo secundario: los break-even negativos de la tabla por zona pasaron a
significar algo.** Suben de 245 a 1,380 celdas y el peor de −0.14 a −0.51, y el
100% cae **dentro** de la zona (0% fuera). Con dos strikes en zona son el 55.8%,
con mediana −0.161.

No es ruido: con dos strikes, tomar un lanzamiento en zona es strike cantado
tres, mientras que un swing que nunca conecta todavía produce foules parte del
tiempo. El valor de "abanicar sin llegar a conectar" supera al de tomar. Se
siguen llevando a 0 porque la decisión es la misma —"abanica siempre"— y un
porcentaje negativo se lee como error en la tabla, pero el comentario del código
ya no dice que sean ruido.

Señal resultante, mediana del break-even:

| strikes | fuera de zona | en zona |
|---|---|---|
| 0 | 0.740 | 0.067 |
| 1 | 1.009 | 0.136 |
| 2 | 0.783 | 0.000 |

En zona, abanica. Fuera, no persigas. Con dos strikes, protege el plato.

**Coste:** se publican 3,461 celdas por conteo en vez de 3,548 — exigir las
cuatro categorías con n≥20 elimina celdas que antes pasaban porque foul y whiff
sumaban juntos. El conteo **3-0 desaparece**: casi nadie abanica ahí, así que no
hay muestra para separar los tres desenlaces. Antes se publicaba un número que
no era estimable.

**Efecto colateral documentado:** `v_re_analysis_swing_take_zone_aggr_handedness`
y `v_re_analysis_swing_take_zone_final_handedness` resultaron ser **código
muerto** — nadie del pipeline las consulta. El nombre de la segunda aparecía solo
como etiqueta de log en `03`, y encima mentía: esa consulta lee la vista base y
hace su propio agregado. La etiqueta está corregida; ver 4.7 para las vistas.

---

### 2.4-bis (histórico) La clasificación original

Salió al volcar el DDL. `v_re_analysis_swing_take` clasifica en el bucket `miss`
no solo los abanicos en falso sino también `foul`, `foul_tip`, `foul_bunt`,
`foul_pitchout`, `bunt_foul_tip` y `swinging_pitchout`.

Es defendible —un foul es "abanicó y no puso la bola en juego"— pero cambia el
número de forma no trivial, porque un foul y un abanico en falso **no cuestan lo
mismo**: con menos de dos strikes ambos suman un strike, pero con dos strikes el
foul es neutro y el abanico es el tercer strike. Meterlos en el mismo bucket
hace `EV_miss` menos negativo de lo que sería solo con abanicos, y eso **baja**
el break-even.

Dado que el foul es ~35-40% de los lanzamientos en conteos de dos strikes, no es
un detalle menor.

Confirmado por el dueño del proyecto: **se coló al construir la vista**, no era
la clasificación buscada. Ver arriba la resolución.

### 2.5 ~~Los 779 juegos empatados quedan fuera del ajuste pitagórico~~ — RESUELTO (agosto 2026)

**Decisión: incluirlos** (`statusCode IN ('F','FT')` en el view).

El argumento no fue el número sino la coherencia: el código decía
`wins + 0.5*ties` con `ties` valiendo siempre 0. Ahora la lógica de medias
victorias sí se ejecuta, en 762 filas de 97 temporadas.

Incluirlos es sabermétricamente coherente porque un empate aporta RS = RA
exactamente: empuja la razón de carreras hacia 1 y el `win_pct` hacia .500 a la
vez, así que los dos lados del ajuste se mueven juntos.

Efecto medido: |dif| media **0.0034**, máxima 0.0322 (1902), sesgo −0.0029. Es
el **3% del error estándar** del propio ajuste. Validado con control: las 28
temporadas sin empates dan diferencia **exactamente 0**.

(Un intento previo de medirlo incluyendo también `FG` y `FO` salió contaminado:
esos códigos contienen 8 juegos **decididos**, no solo empates, y el control lo
delató al dar diferencia no nula en temporadas sin empates.)

---

### 2.5-bis Dos franquicias enteras faltaban en el ajuste — RESUELTO

Apareció al validar lo anterior, y resultó **5 veces más grave** que los empates.

El `INNER JOIN` contra `mlbTeams` existía solo para traer `team_full_name`, una
columna de presentación, y descartaba filas. `mlbTeams` contiene también equipos
de ligas menores y usa códigos distintos a `mlbGames`:

| en `mlbGames` | en `mlbTeams` | temporadas perdidas |
|---|---|---|
| `BRO` Brooklyn | `BKL` | **57** (1901-1957) |
| `MON` Montreal | `MTL` | **35** (1969-2003) |

Tres defectos en el mismo join:

1. **92 temporadas-equipo descartadas** — Brooklyn y Montreal no existían en el
   ajuste, en ninguna de sus temporadas.
2. **Falsos positivos.** En `mlbTeams`, `MON` son los *Montgomery Biscuits* y
   `BRO` los *Brooklyn Cyclones*. La temporada 2004 de Montreal venía etiquetada
   como "Montgomery Biscuits".
3. **Duplicados.** 532 pares (season, team_code) tenían más de una fila,
   duplicando 28 temporadas-equipo en la salida: esos equipos entraban dos o
   tres veces en el ajuste.

**Arreglo:** `LEFT JOIN` sobre `mlbTeams` filtrado a `sport_id = 1` (Grandes
Ligas), con mapeo explícito de los dos códigos. `sport_id = 1` por sí solo lleva
los pares duplicados de 532 a 0 y elimina los falsos positivos.

Se comprobó que `team_abbreviation` **no** sirve como clave alternativa pese a
valer 'BRO' y 'MON': dejaría sin casar el 43% de los juegos-equipo frente al
3.4% de `team_code`.

Resultado: **2,706 filas** (antes 2,643), **0 duplicados** (antes 28), **0 sin
nombre**, y los nombres salen correctos por época — "Brooklyn Robins" en 1916,
"Brooklyn Dodgers" en 1941.

Efecto en el exponente: |dif| media **0.0190**, máxima **0.1456** — el **15% de
un error estándar**. Casos concretos: 1994 pasa de 1.597 a **1.679** al
reincorporar a Montreal, que ese año tenía el mejor récord del béisbol cuando la
huelga canceló la temporada; 1953 sube +0.071 al recuperar al Brooklyn campeón.

Efecto acumulado de 2.5 y 2.5-bis sobre la columna de 1 año: |dif| media 0.0176
sobre las 125 temporadas, máxima 0.1595 (1994) y −0.1434 (1918).

---

### 2.5-ter (histórico) El diagnóstico original de los empates

`v_mlb_team_season_pythag_input` filtra `statusCode = 'F'`, pero los juegos
empatados tienen `statusCode = 'FT'`. La consecuencia es que **su propia lógica
de empates es código muerto**:

- `ties` vale **siempre 0** en el view
- el término `0.5 * ties` de `win_pct` nunca se activa
- el `adjusted_wins = wins + 0.5 * ties` de `02` es siempre solo `wins`

Son **779 juegos en 97 temporadas**, y en la era temprana no son marginales:

| temporada | empates |
|---|---|
| 1904 | 29 |
| 1907 | 31 |
| 1910 | 26 |

Un equipo con 5 empates en una temporada de 154 juegos ve excluido ~3% de sus
partidos del ajuste. Los empates tampoco entran en `games_played`, así que el
denominador también encoge: no es solo que no se cuenten como media victoria, es
que desaparecen.

(Nota de honestidad: al validar el punto de `win_pct` en la revisión inicial se
comprobó que `win_pct == (wins + 0.5*ties)/G` con diferencia máxima 0 y se dio
por bueno el tratamiento de empates. La igualdad era cierta pero **vacía**: se
cumplía porque `ties` era 0 en todas las filas. Un resultado así de limpio
debería haber levantado sospecha.)

Se resolvió incluyéndolos; ver arriba. Se conserva esta sección porque la nota
de honestidad de arriba explica por qué el problema pasó desapercibido en la
primera revisión.

---

## 3. Estadística no expuesta al usuario

### 3.1 ~~El exponente pitagórico de 1 año no es informativo por sí solo~~ — RESUELTO (agosto 2026)

**Decisión: reutilizar la señal de fiabilidad que la app ya tenía**, en vez de
publicar bandas de confianza o retirar la columna.

`02` emite ahora una columna `<nombre>_n` por cada columna de valor, con las
**equipo-temporadas que alimentaron ese ajuste** — que es el tamaño de muestra
real de la regresión: 30 para un año, 361 para doce, 15 en 1901. La UI las pasa
a `buildReliabilityStats` y renderiza el marcador con el mismo mecanismo que
Linear Weights y las demás tablas.

El gradiente resultante sale del dato, sin umbrales inventados, y reproduce el
error estándar medido por bootstrap:

| ventana | n (2025) | SE | marcador |
|---|---|---|---|
| 1 año | 30 | 0.126 | `••` baja |
| 2 años | 60 | 0.091 | `●` media |
| 3 años | 90 | 0.076 | limpio |
| 12 años | 361 | 0.040 | limpio |

En pantalla, temporada 2025:

```
1.6275 ••   1.7133 ●   1.7050   1.7204   1.7663   1.7477   1.7477   1.8718 ••
```

Con tooltip por celda (`n = 30 | 25th percentile`) y la leyenda al pie.

Aplicado también a **Runs Per Win**, que deriva de los mismos exponentes y
hereda su incertidumbre. `Pythagenpat` y `FanGraphs` salen marcados `••` con
razón: ambos se calculan sobre una sola temporada, con la misma muestra de 30
equipos que la columna de un año.

Las series tempranas se degradan solas: en 1930, con 15 equipos, también quedan
marcadas las columnas de 2 y 3 años. Reparto global: 192 celdas bajas, 203
medias, 446 limpias.

Los valores publicados **no cambiaron**; solo se añadió la señal.

---

### 3.1-bis (histórico) Por qué el ajuste de 1 año es tan ruidoso

Bootstrap sobre equipos, B=300:

| ventana | SE | IC95 |
|---|---|---|
| **1 año** | **0.126** | **±0.25** |
| 2 años | 0.091 | ±0.18 |
| 3 años | 0.076 | ±0.15 |
| 5 años | 0.060 | ±0.12 |
| 8 años | 0.049 | ±0.10 |
| 10 años | 0.045 | ±0.09 |
| 12 años | 0.040 | ±0.08 |

Un intervalo de ±0.25 sobre valores que van de 1.6 a 2.0. La caída aparente de
1.796 (2024) a 1.627 (2025) da **z = 0.95**: es ruido, no un cambio en cómo se
convierten carreras en victorias. La causa es estructural — se estima un
parámetro con un punto por equipo, y la mediana histórica es de 20 equipos por
temporada (mínimo 15 en 1901).

Está documentado en el docstring de `fit_pythag_exponent_weighted`. Se conserva
aquí porque es la medición que justifica los umbrales de la señal de fiabilidad
descrita arriba.

### 3.2 ~~Sesgos de selección sin advertencia en la UI~~ — RESUELTO (7 ago 2026)

Cuatro análisis publicados tenían un confundidor conocido que el número no
refleja. No es ruido muestral —más temporadas no lo corrigen—, así que el
marcador de fiabilidad no servía: hacía falta decirlo.

**Hecho:** `METHOD_NOTES` en `app.js`, que `setBreakevenFooterText` renderiza
como una línea `Note: …` entre el recuento de eventos y la leyenda de
fiabilidad. Las cuatro notas, verificadas en el navegador:

| Análisis | Nota |
|---|---|
| swing/take | El valor del *take* se mide solo sobre lanzamientos que el bateador eligió no batear, sesgados hacia bolas. Eso sube el lado del take y con él el break-even. |
| swing/take por zona | Lo mismo, añadiendo que separar por zona lo reduce pero no lo elimina. |
| robo por conteo | El conteo no es independiente del duelo: ya lleva información sobre bateador y lanzador. |
| bunt-for-hit | La alternativa es un turno promedio de la liga, no el de este bateador (ver 2.2). |

`stealing`, `running` y `tagging` no llevan nota: no tienen sesgo declarado, y el
pie sale igual que antes.

**Validación.** Cinco mutaciones dirigidas contra el test nuevo —no pasar la
clave en robo por conteo, en swing/take por zona y en la tabla general, vaciar
una nota y borrarla entera— y las cinco lo hacen fallar. La primera versión del
test dejaba pasar tres de ellas porque buscaba el literal en cualquier punto de
la llamada, y `'sb_by_count'` ya aparecía dentro del segundo argumento; ahora
parte los argumentos por las comas de primer nivel y exige el tercero.

### 3.2-bis Win Expectancy, WPA y Leverage Index mezclaban dos equipos — RESUELTO (7 ago 2026)

El subtítulo decía *"Positive (+) numbers in the headers mean away team is ahead,
Negative (−) means away team is behind"*, que explica lo único obvio del asunto y
calla lo que de verdad confunde: **la tabla habla de dos equipos a la vez.**

| Elemento | ¿De quién habla? |
|---|---|
| Selector `Away / Home` | del equipo que **batea** (por eso las filas dicen "Top" o "Bottom") |
| Cabeceras `−4+ … +4+` | de la ventaja del equipo que **batea** |
| El valor de la celda | siempre del equipo de **casa** |

Con *Away* seleccionado los dos apuntan en direcciones opuestas. Verificado
contra los datos, no supuesto — 2025, 1ª entrada, 0 outs, bases limpias:

```
Away batea:   Tie → 52.79%    +1 → 42.26%    +4 → 15.75%
Home batea:   Tie → 57.71%    +1 → 68.02%    +4 → 90.40%
```

Es decir, `RUNS_DIFF` está medido **desde el equipo que batea**, y por eso con el
away bateando la fila baja de izquierda a derecha.

**Hecho:** `buildRunDiffHeaderHTML` monta la cabecera en dos filas —`Inning`,
`Outs` y `Runners` con `rowspan=2`, y encima de las columnas de diferencial un
grupo que dice `Away team's lead` o `Home team's lead` según el selector—, y el
subtítulo se queda en una sola frase: *"Every cell is the HOME team's chance of
winning the game from this state."* El signo se explica donde está el signo.

**Validación.** Comprobado en el contenedor local para los seis casos (tres
tablas × Away/Home): la etiqueta del grupo sigue al selector, el `colspan` es 9,
las tres columnas fijas llevan `rowspan=2`, el cuerpo tiene 12 celdas y las
anchuras cuadran (95+59+83 fijas, 853 de grupo = 9 × 95). Ninguna cabecera era
`sticky`, así que la segunda fila no rompe el desplazamiento.

### 3.2-ter "Average leverage is 1.0" era cierto del juego y falso de la tabla — RESUELTO (7 ago 2026)

El subtítulo de Leverage Index lo afirmaba como constante. Medido:

| | 2025 |
|---|---|
| Ponderado por cuántas veces ocurre cada estado | **1.0164** |
| Media de las celdas que se ven en pantalla | **1.5664** |
| Mediana de las celdas | 1.2489 |
| Celdas por encima de 1.0 | **58.9%** |

La normalización de aguas arriba es correcta —sobre las 116 temporadas la media
ponderada es 1.0080 y **102 de 116** caen en [0.98, 1.02]—, pero quien mira la
tabla ve mayoría de valores por encima de 1 y la frase le parece falsa. Los
estados frecuentes son los aburridos.

Además el corte por equipo que hace la UI **no es neutro**: el local batea
último, así que sus turnos deciden más. 2025: away 0.9953, home 1.0384, y el home
sale más alto todas las temporadas.

**Hecho:** `computeWeightedLeverage(data)` calcula `sum(LI·n)/sum(n)` sobre la
selección real —la misma ponderación que ya usa break-even al agregar
temporadas—, y el subtítulo pasa a *"An average plate appearance rates 1.0 —
weighted by how often each state actually happens, Away batters average 0.995
across these seasons."* Las filas ya traen el `n` por celda (los `<header>_n` que
alimentan los marcadores de fiabilidad), así que no hace falta nada del servidor.
Se calcula sobre `data.rows`, que es toda la selección: la paginación solo
recorta al pintar.

**Validación.** Los cuatro cortes probados en el contenedor cuadran con el
cálculo independiente desde el parquet: 2025 away 0.995 / home 1.038, 2020-2025
away 0.991 / home 1.036. Dos tests nuevos: uno fija la parte que sí es cierta (la
media ponderada global en [0.97, 1.04] y ≥85% de temporadas en [0.98, 1.02]), y
otro que el número se calcule en vez de afirmarse. Mutaciones probadas: volver a
`const avg = 1.0` y restaurar la frase vieja; ambas detectadas. La primera
versión del segundo test dejaba pasar la constante, porque buscaba
`computeWeightedLeverage(data)` y eso ya casa con la línea de `function`.

### 3.3 ~~Seis filas de `sb_breakeven_by_count` con BE > 2~~ — RESUELTO (7 ago 2026)

Estaba mal planteado por dos lados.

**Primero, `BE > 2` no es una categoría.** La línea es **1.0**: por encima de eso
ya dice "ni lo intentes", y da igual que sea 1.5, 2 o 3.18.

**Segundo, y esto es lo importante: en un break-even de ROBO, `BE > 1` no
significa "no conviene". Significa que la celda está mal estimada.**

`BE = (RE_antes − RE_cs)/(RE_sb − RE_cs)`, así que con denominador positivo
`BE > 1` equivale exactamente a `RE_antes > RE_sb` — o sea, a que robar con éxito
*baje* la esperanza de carreras. Eso es imposible: mismos outs, corredor en mejor
base. Ejemplo, 2020 conteo 3-0 con 0 outs y corredores en 1B y 3B:

```
RE(1B y 3B) = 2.6591    RE(2B y 3B) = 1.4737    ganancia = −1.19    n_sb = 19
```

La correspondencia es **exacta y sin un solo caso cruzado**, en las dos tablas:

```
                 robar baja la RE
BE > 1        No      Si          tabla RE24:      2 de 2
No          1390       0          tabla por conteo: 391 de 391
Si             0       2
```

Ojo: esto **no** vale para swing/take, donde `BE > 1` sí es legítimo porque la
fórmula es otra (467 y 577 filas, intactas).

**Subir `MIN_SB_BY_COUNT_N` no era la solución, medido:**

| umbral sobre `n_before` | publicadas | BE > 1 |
|---|---|---|
| 30 (el que había) | 5,168 (100%) | 389 |
| 100 | 4,986 (96.5%) | — |
| 300 | 4,235 (**81.9%**) | 0 |

Había que llegar a 300 y perder el 18% de la tabla. Y las filas malas no eran
todas de muestra pequeña: 271, 170, 121, 105.

**Hecho, dos cambios:**

1. **Guarda de monotonía** (`_steal_gain_is_possible`): si la matriz dice que
   robar con éxito baja la RE, la celda no se publica. En las dos funciones,
   `calculate_sb_breakeven` y `calculate_sb_breakeven_by_count`.
2. **El umbral pasa a mirar la celda más pequeña de las tres**, no solo la de
   partida. La de partida es la mayor en el **94.1%** de las filas (mediana 879
   frente a 374 del mínimo); la frágil es la del robo consumado, que además va en
   el denominador — hay filas con `n_before=170` y `n_sb=81`, o 44 y 19. El
   umbral sigue en 30 y configurable por `MIN_SB_BY_COUNT_N`, pero ahora muerde
   donde tiene que morder. Con la guarda puesta no hace falta subirlo: el máximo
   publicado es 0.9997.

Resultado: `stealing` 1,390 filas máximo **0.992**, `sb_by_count` 4,752 filas
máximo **0.9997**, **cero** por encima de 1 en ambas.

**Validación.** Cuatro tests unitarios (celda incoherente se descarta, celda
coherente se publica y queda bajo 1, la guarda está en las dos funciones, el
umbral mira el mínimo) y tres mutaciones dirigidas, las tres detectadas.

**Alternativa considerada y descartada: `run_value_smooth`.** La columna existe
en `r288Values`, la calcula
`scripts/runExpectancy288/sql/07_run_value_smooth.sql` y es un encogimiento
empírico-Bayes del ratio V/L hacia la media multianual (K=4000 por validación
cruzada, −24% de error fuera de muestra). Con ella la monotonía **no se viola ni
una vez** (0 de 5,168) y se publicarían 5,126 celdas (96.2%) en vez de 4,752.
Se descarta porque el site muestra la matriz RE288 con `run_value` **crudo**: el
break-even dejaría de cuadrar con la tabla de al lado, y movería el 44.4% de los
valores más de 0.05. Cambiar las dos cosas a la vez es otra conversación.

### 3.4 Pythagenpat y FanGraphs salían marcadas como poco fiables — RESUELTO (8 ago 2026)

Llevaban `••` de baja confianza. Son las **dos columnas más precisas de la tabla**.

El marcador mide el tamaño de muestra de un **ajuste**: las columnas de 1, 2, 3…
años salen de mínimos cuadrados sobre equipos-temporada, y con más puntos el
exponente se estima mejor. Esas dos no ajustan nada:

- **Pythagenpat** = `((RS+RA)/G)^0.287` por equipo, promediado por temporada.
- **FanGraphs** = `9·(Total_RS/Total_IP)·1.5+3`, una razón de totales de liga
  sobre ~2,430 juegos. Ni siquiera usa el exponente ajustado; heredaba
  `FanGraphs_n = 1 year_n` solo porque había que ponerle algún `n`.

Con `n` = 30 equipos caían en el cuantil bajo y se marcaban.

**Medido** con bootstrap remuestreando equipos, B=400, las 125 temporadas:

| columna | SE mediana | SE máx |
|---|---|---|
| **Pythagenpat** | **0.0080** | 0.0190 |
| exponente de 1 año | 0.1264 | 0.2559 |
| exponente de 12 años | 0.040 | — |

El ajuste de 1 año es **16× más ruidoso**, y Pythagenpat es **5× más precisa que
la columna de 12 años**. Contrastado además con el SE analítico de la media
ponderada, que correlaciona **0.9933** con el bootstrap.

**Hecho:** `RELIABILITY_EXEMPT_COLUMNS` en `app.js`. No llevan marcador y además
salen del cálculo de los umbrales — su `n`=30 arrastraba el cuantil hacia abajo y
con ello la escala de las demás columnas.

**Se descartó ensanchar la ventana a 3 años**, que era la propuesta inicial.
Medido: quitaría 0.0034 de ruido (SE 0.0080 → 0.0046) e introduciría un sesgo
mediano de **0.0153**, p90 0.0408, máximo 0.0702. **El sesgo es 4.5× el ruido que
quita**, y en el 79% de las temporadas supera al SE actual. Los peores casos son
las transiciones de era (1920-21, 1970), que es exactamente lo que Pythagenpat
existe para seguir: el entorno va de 1.7304 (1908) a 1.9932 (1930), y una media
móvil lo persigue con retraso. El problema era el marcador, no la ventana.

**Validación.** Comprobado en el contenedor sobre 25 filas de las dos tablas: en
2025, `1 Year 1.6275••` y `2 Years 1.7133●` conservan el suyo, y Pythagenpat
(1.8718 / 9.51) y FanGraphs (9.76) salen limpias.


---

## 4. Higiene y estructura

### 4.1 Los scripts no son importables — RESUELTO (7 ago 2026)

Empezaban por dígito (`00_`…`03_`), que no es un identificador Python válido, así
que no había ni un test unitario de la lógica de cálculo: los 59 tests validaban
los **datos publicados**, no las funciones que los producen.

**Hecho:**

- Renombrados a `sync_sql_views.py`, `extract_re_data.py`,
  `calculate_pythag_exponents.py`, `create_break_even_points.py`.
- El orden que codificaba el prefijo numérico vive ahora en `utils/run_all.py`
  (`--skip-views`, `--apply-views`; por defecto las vistas van en `--check`,
  porque aplicar DDL modifica la base).
- `tests/conftest.py` pone `utils/` en `sys.path`.
- `tests/test_calculations.py`: **19 tests unitarios** con entradas construidas a
  mano y resultado conocido, sin tocar la base ni los parquets.

**Bug encontrado por los propios tests.** En `calculate_swing_take_breakeven`,
`gap` y `p_foul` se calculaban **antes** de los filtros. Si los filtros vaciaban
`pivot`, asignar una Serie no vacía a un DataFrame vacío no lo deja vacío: pandas
adopta el índice de la Serie y reaparece una fila entera de NaN, que reventaba
más abajo en `astype(int)` con un `IntCastingNaNError` sin relación aparente.
Corregido moviendo el cálculo detrás del filtro. `breakeven_points.parquet` sale
byte a byte idéntico: el caso solo se daba con cero celdas publicables.

**Validación.** Cada test se comprobó contra la lógica rota, no solo contra la
buena: siete mutaciones dirigidas (quitar el baseline de la IBB, volver al peso
0.75 del doble robo, restaurar el `abs() > 1e-12` del denominador, contar años
transcurridos en vez de temporadas presentes, usar oportunidades en vez de
fallos como `n`, desactivar la guarda de negativos, rechazar BE > 1) y las siete
hacen fallar al test que las vigila. Pipeline completo re-ejecutado tras el
renombrado: 21 de los 25 ficheros de `app/data` byte a byte idénticos; los cuatro
que cambian son `derived_from.json` (nombre del productor + timestamp),
`last_updated.json` y los dos parquets no reproducibles de 4.2. 78 tests en verde.

### 4.2 `win_expectancy.parquet` y `ph_impact_source.parquet` no son reproducibles byte a byte — NO HAY NADA QUE ARREGLAR

Los `AVG()` de SQL Server se calculan en paralelo y la suma en punto flotante no
es asociativa, así que el orden de combinación de las sumas parciales varía entre
ejecuciones. Medido: la misma consulta dos veces seguidas devuelve 227,396 filas
con `WPA` distinto, todas a nivel 1e-16.

Impacto analítico **cero** (en `win_expectancy` sobrevive 1 fila de 450,488 al
cast a float32, con una diferencia de 1 ULP sobre un WE de 0.0005). Está
documentado junto a `QUERY_WIN_EXPECTANCY`.

**No hay nada que arreglar**, pero al comparar dos lotes hay que usar
`np.allclose(rtol=1e-6)` y no igualdad exacta. Se anota para que nadie lo
persiga como si fuera un bug — ya pasó una vez durante la revisión.

### 4.3 ~~Calidad de datos aguas arriba: 35 filas fantasma en la temporada 2000~~ — RESUELTO EN ORIGEN

`dbo.mlbGames` tiene 35 juegos de 2000 marcados como finales (`statusCode='F'`)
con el marcador en NULL. Descuadran a 26 de los 30 equipos y hacían que el
`win_pct` de la liga sumara 14.801 en vez de 15.0.

**Diagnóstico cerrado (agosto 2026):** no son juegos a los que les falte el
marcador. Son filas que no corresponden a ningún partido jugado.

Evidencia, contrastando un extracto de mlb.com descargado en ese momento contra
Retrosheet:

| | |
|---|---|
| CSV de mlb.com, temporada regular **con** marcador | **2,430** |
| Temporada completa teórica (30 × 162 / 2) | **2,430** |
| Filas extra sin marcador con `statusCode='F'` | 35 |
| Esas 35 en `retroGamesReg` (fuente independiente) | **0** |

MLB tiene exactamente los 2,430 juegos de una temporada completa, todos con
marcador. Las 35 sobran, y Retrosheet no conoce ninguna. Son de la misma
naturaleza que las 6 `CO` (canceladas) y las 4 `DR` (lluvia) que sí vienen bien
etiquetadas — pero la API las devuelve como `'F'` sin `statusReason` ni fecha de
reprogramación o reanudación.

Rellenar el marcador sería inventar partidos.

**RESUELTO en origen.** Las 35 filas se marcaron con `statusCode = 'CO'`, el
mismo código que ya usaban las 6 canceladas de esa temporada. Ahora cualquier
consumidor de `mlbGames` las excluye, no solo este pipeline.

Verificaciones tras el cambio:

- el view pasa de 69 juegos sin clasificar a **0**, y `games_played == W+L+T`
- `reconcile_games_played` ya **no imprime aviso**: la defensa quedó en no-op,
  que es la señal de que está arreglado aguas arriba
- **los exponentes no cambiaron en ninguna de las 125 temporadas**, o sea que el
  arreglo en la base y la defensa en Python producen lo mismo y se validan
  mutuamente
- ninguna otra temporada presenta el patrón
- respaldo de las 35 filas completas antes de tocar nada

La defensa de `reconcile_games_played` se deja puesta a propósito: protege igual
si la API vuelve a devolver filas así en el futuro.

### 4.4 ~~El selector de temporadas no hace nada en cuatro tablas~~ — ERA FALSO

Cuatro endpoints devuelven la serie histórica completa e **ignoran el rango de
temporadas por diseño** (hay comentarios explícitos en `app/main.py`):

| tabla | línea | devuelve |
|---|---|---|
| `WOBA_COEFFS` | [main.py:844](../app/main.py) | 116 temporadas |
| `FIP_CONST` | [main.py:855](../app/main.py) | 116 temporadas |
| `PYTHAG_EXPONENTS` | [main.py:867](../app/main.py) | 125 temporadas |
| `RUNS_PER_WIN` | [main.py:880](../app/main.py) | 125 temporadas |

El comportamiento es correcto: son series por temporada, no agregados sobre un
rango.

**ERA FALSO — la UI ya lo comunica.** Se anotó aquí diciendo que los desplegables
*From Season* / *To Season* seguían visibles y activos. No lo están:
[app.js:557](../app/static/js/app.js) mete `d-none` a los dos contenedores para
exactamente esos cuatro tipos. Comprobado en el contenedor local, con `RE24` de
control:

| tabla | From Season | To Season |
|---|---|---|
| RE24 (control) | visible y activo | visible y activo |
| `WOBA_COEFFS` | **oculto** | **oculto** |
| `FIP_CONST` | **oculto** | **oculto** |
| `PYTHAG_EXPONENTS` | **oculto** | **oculto** |
| `RUNS_PER_WIN` | **oculto** | **oculto** |

La confusión durante la revisión venía de otro sitio: al cambiar de tipo de tabla
sin recargar, la selección anterior seguía en pantalla un instante. No es que los
selectores estuvieran activos.

Queda cubierto por `test_las_tablas_de_serie_historica_ocultan_el_selector_de_temporadas`
para que no se vuelva a anotar como pendiente.

### 4.5 ~~Sin paginación en las tablas de serie histórica~~ — ERA FALSO

Anotado por error: sí tienen paginación (`renderPythagExponentsPagination`,
`renderRunsPerWinPagination` y equivalentes). El control está más abajo en la
página, fuera del recorte que se inspeccionó al anotarlo.

Se deja constancia para que nadie vuelva a "arreglar" algo que funciona.

### 4.6 ~~El cache-buster de los assets es manual~~ — RESUELTO (7 ago 2026)

`asset_version` está escrito a mano en
[app/templates/index.html:3](../app/templates/index.html) y hay que subirlo en
cada cambio de `app.js` o `styles.css`. Si no se sube, los navegadores con caché
siguen sirviendo la versión anterior.

Ocurrió durante la revisión: se cambiaron dos veces los textos de `app.js` y se
añadieron los marcadores de fiabilidad **sin tocar la versión**, así que un
usuario con caché no habría visto ninguno de los tres cambios. Y verificar con
`curl` sobre el fichero servido daba verde igualmente, porque el problema no era
el fichero sino que el navegador no lo pedía.

**Hecho (7 ago 2026):** `asset_version()` en `app/main.py` devuelve los 12
primeros caracteres del SHA-256 de `app.js` + `styles.css`, y la plantilla lo
recibe por contexto. El valor se recalcula solo cuando cambia el mtime o el
tamaño de algún fichero, así que en producción son dos `stat()` por petición y en
desarrollo el navegador recoge la edición sin reiniciar el servidor. Si falta un
asset devuelve `"0"` en vez de tumbar la portada con un 500.

**Validación.** Cuatro tests en `tests/test_asset_version.py`: la portada sirve
el valor calculado, editar cualquiera de los dos ficheros lo mueve, volver al
contenido original lo devuelve —es un hash, no un contador—, y el literal ya no
está en la plantilla. Comprobado también contra el contenedor local: sirve
`app.js?v=33b8601ae8c9`. Regresión probada: volver a poner
`{% set asset_version = '1.0.52' %}` hace fallar dos de los cuatro.

### 4.7 ~~Dos vistas SQL son código muerto~~ — RESUELTO (7 ago 2026)

`v_re_analysis_swing_take_zone_aggr_handedness` y
`v_re_analysis_swing_take_zone_final_handedness` no las consulta nadie del
pipeline. Se descubrió al unificar swing/take (2.4): `03` lee la vista **base**
(`v_re_analysis_swing_take_zone`) y hace su propio agregado en línea. El nombre
de `_final_handedness` aparecía solo como etiqueta de log, describiendo una
consulta que no era la que se ejecutaba. La etiqueta ya está corregida.

Las dos vistas siguen existiendo en la base y siguen versionadas en `utils/sql/`
(10 y 11), calculando el break-even **con la fórmula vieja de dos desenlaces** y
con el foul agrupado como contacto.

**Hecho (7 ago 2026): eliminadas.** Antes de tocar nada se comprobó la
dependencia real con `sys.sql_expression_dependencies`: a `_aggr_handedness` solo
la usaba `_final_handedness`, y a `_final_handedness` no la usaba nadie. Se
soltaron en ese orden. El DDL queda en `_not_in_use/sql_vistas_muertas/` por si
alguna vez hay que recrearlas.

**Validación.** `sync_sql_views.py --check` da 15 vistas, 0 con deriva, 0
ausentes. Pipeline completo re-ejecutado: `breakeven_points.parquet`,
`re_impact.parquet` y `sb_breakeven_by_count.parquet` salen **byte a byte
idénticos**, que es la prueba de que efectivamente estaban muertas.

### 4.8 ~~Columnas sin tipo declarado~~ — RESUELTO (7 ago 2026)

El catálogo avisaba por log de columnas publicadas sin castear. Al medirlas
resultaron ser otras de las anotadas aquí: `re24_values.prob` (float64) y
**`win_expectancy.n`** (int64) — no `ph_values.n`, que ni siquiera sale del
catálogo actual (`ph_values.parquet` es un resto de 25 MB que no produce ni
consume nadie; candidato a `_not_in_use`, no tocado).

**Hecho:** `prob` → `float32`, igual que `run_value` y `percentage`; `n` →
`int32`, igual que el resto de recuentos.

**Y `ph_values.parquet` fuera.** Salió al medir esto. No lo produce el catálogo
actual ni ningún otro script, y no lo lee nadie: la única mención en todo
`run_expectancy` era esta nota. La prueba definitiva es la fecha — `2026-03-31`,
mientras el resto de `app/data` se regenera en cada ejecución. Era un resto de
marzo que el `COPY app/ /app/app/` del Dockerfile metía en cada imagen. Movido a
`_not_in_use/data_huerfana/`: **`app/data` pasa de 37.8 a 13.3 MB**, y los tests
siguen en verde.

**Validación.** Regenerado el pipeline: en `re24_values` solo cambia `prob`, con
un máximo de **2.98e-08** en valor absoluto —exactamente la pérdida predicha del
cast, siete órdenes por debajo de lo que muestra la UI— y el fichero baja de 0.91
a 0.71 MB. En `win_expectancy`, `n` conserva todos sus valores y lo único que se
mueve es 1 celda de `WE` y 1 de `WPA` a nivel 1e-11, que es el 4.2 conocido.

Además ya no es una decisión que se pueda olvidar: `test_ninguna_columna_se_publica_sin_tipo_declarado`
recorre el catálogo y falla si un parquet publica una columna sin tipo. Probado
quitando cada uno de los dos tipos: ambos casos lo hacen fallar.

---

### 4.9 La extracción no tenía rango de temporadas — RESUELTO (8 ago 2026)

**El síntoma.** `breakeven_points.parquet` traía **302 filas de 2026**, la
temporada en curso, con 304.334 lanzamientos y **solo** en `swing_take_zone`.
Ninguna de las otras 18 tablas tenía 2026.

**La causa, medida.** No había un rango; había cinco criterios distintos
conviviendo:

| criterio | dónde | problema |
|---|---|---|
| `>= (SELECT MIN(season) FROM dbo.retroGamesReg)` | 4 consultas | el rango publicado cambiaba solo al cargar temporadas nuevas |
| `>= 1989` a mano | RE288 | duplicado, sin nombre |
| `BETWEEN r24_min AND r24_max` | events_counts | correcto para alinearse con la matriz, pero sin techo propio |
| `season_cap = max(seasons)` del parquet | swing/take | dato mandando sobre configuración |
| `YEAR(game_date)` **sin ninguna cota** | swing/take **por zona** | por aquí entraba la temporada en curso |

Statcast publica en vivo; Retrosheet publica al cerrar el año. Por eso la
temporada a medias entraba por una sola puerta —y se presentaba en la tabla
igual que un año entero, sin ninguna marca.

**La solución.** `utils/seasons.py`, un único sitio con las tres fechas:

    MIN_SEASON            1910   suelo general
    MIN_PER_PITCH_SEASON  1989   suelo de lo que necesita conteo o zona
    MAX_SEASON            2025   techo, común a todo

Se configuran por `.env` o por línea de comandos (`--min-season`,
`--min-per-pitch-season`, `--max-season`), y las opciones viven en el parser
**compartido**, así que un script nuevo las hereda sin hacer nada.

**Dos capas, a propósito.** El filtro va en el SQL *y* otra vez sobre el
DataFrame ya cargado. No es redundancia inútil: el de SQL está para no traerse un
siglo de filas y tirarlas; el de Python es **la garantía**, y es lo que hace que
una vista rehecha sin cota o una consulta nueva que olvide el marcador se topen
igual con el rango. Es exactamente el fallo que hubo. Los dos puntos de paso son
`_extract_one()` y `_read_sql_view()`, por donde entran los 16 datasets y todas
las vistas.

**Lo que NO se tocó, y por qué.** Una vista puede ser más estricta que el rango:

- `_stretch` y `_advance` arrancan en 1989 por su cuenta. **No es el suelo de
  conteo aunque el año coincida:** es cuando la localización del batazo alcanza
  el 95,8% de cobertura, frente al 89,9% de 1988 y el 45% de 1987. Es una
  restricción de calidad de *ese* análisis, medida y anotada en la propia vista,
  así que no cuelga de `MIN_PER_PITCH_SEASON` —si ese parámetro se moviera,
  estas dos no deben seguirlo—.
- `_ph_pitchers` termina en 2021, último año en que los lanzadores batearon con
  regularidad antes del DH universal de 2022. Ya documentado en la vista.

**Decisión tomada sobre el pitagórico.** Publicaba desde **1901**, nueve
temporadas por debajo de `MIN_SEASON`, porque sale de clasificaciones y no de
Retrosheet. Se acota igualmente a 1910 para que todas las tablas digan el mismo
rango —que es además el que anuncia la portada—. Reversible: se baja `MIN_SEASON`
y se rehace, no hay nada más que tocar.

**19 tests**, verificados por mutación. No comprueban que cada consulta lleve su
`WHERE` —eso se olvida— sino que **ningún camino se salta el rango**: que todos
los datasets con temporada declaran `season_col`, que los tres de conteo están
marcados, que no queda ni un marcador `{SEASON_MAX}` sin sustituir (llegaría a
SQL como error de sintaxis en plena extracción anual), que ninguna consulta
vuelve a derivar el suelo de `retroGamesReg`, que el tope de swing/take ya no
sale del parquet, y que un rango invertido revienta **antes** de conectar y no a
los veinte minutos de consultas.

**Pendiente al ejecutar:** rehacer el pipeline. Hasta entonces
`breakeven_points.parquet` sigue con sus 302 filas de 2026, y el
`temporalCoverage` de los datos estructurados —que ya declara `1901/2025`— no
cuadra con lo servido.

---

## 5. Revisión externa (docs/reviews/code_review_v01.md)

Seis hallazgos. **Tres no se sostuvieron y uno era real.** Cada uno se contrastó
contra el código antes de aceptarlo.

| # | afirmación | veredicto |
|---|---|---|
| 1 | `END` duplicado en `state_order` | **FALSO** |
| 2 | `unsafe-inline` en CSP | cierto, severidad menor |
| 3 | `innerHTML` con datos del servidor | cierto en la letra, sin vía de explotación |
| 4 | proxies de confianza `*` | **CIERTO — el único grave** |
| 5 | `verify_origin` solo en un endpoint | **rechazado** |
| 6 | mutación en `calculate_weighted_mean` | cierto pero latente |

### 5.1 `PROXY_TRUSTED_IPS = "*"` permitía saltarse el rate limit — RESUELTO (8 ago 2026)

`ProxyHeadersMiddleware` se montaba con `trusted_hosts=["*"]`, o sea aceptando
`X-Forwarded-For` de cualquiera. Como el rate limit de slowapi se indexa por IP
remota (`get_remote_address`), bastaba con mandar una cabecera distinta en cada
petición para **saltárselo entero** — y de paso envenenar los logs de acceso y lo
que vea fail2ban.

**Hecho:** el defecto pasa a loopback más los tres rangos privados de RFC 1918
(`127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`), que es donde vive un
proxy inverso propio — nginx en Docker sale por 172.16/12. **Un despliegue detrás
de proxy sigue funcionando sin tocar nada**; lo que deja de funcionar es falsear
la cabecera desde fuera. Comprobado que `uvicorn 0.52` acepta CIDR además de IPs
sueltas (`_TrustedHosts`). Si algún día el proxy es una CDN o un balanceador
gestionado, hay que listar sus rangos publicados en `PROXY_TRUSTED_IPS` — no `*`.
Y si alguien vuelve a poner `*`, arranca con un aviso explícito en el log.

### 5.2 `calculate_weighted_mean` mutaba el DataFrame del llamante — RESUELTO (8 ago 2026)

Añadía la columna `weighted_value` al DataFrame de entrada. **Latente, no vivo:**
los siete llamantes reciben el suyo de `filter_and_validate_data`, que ya devuelve
`.copy()`, así que la caché de `data_store` no se estaba ensuciando. Arreglado con
un `df = df.copy()` porque es una línea y quita el pie de banco.

### 5.3 Por qué se rechazó el hallazgo #1

`state_order.append("END")` aparece **exactamente una vez** en todo
`table_generator.py` ([línea 1735](../app/services/table_generator.py)), igual que
`OFF`. No hay duplicado ni columnas repetidas. Estaba clasificado como severidad
Alta con impacto en la precisión de RE24.

### 5.4-bis `innerHTML` con datos del servidor — DECIDIDO: se deja como está (8 ago 2026)

La parte de CSP de este punto **está resuelta** (ver 5.4, nonce en `script-src`).
Esta es la otra mitad, y **se acepta a conciencia**, no por olvido.

**El riesgo teórico.** El frontend pinta las tablas con `innerHTML` usando datos de
la API. Si alguna cadena llevara HTML dentro, el navegador lo ejecutaría.

**Por qué hoy no hay vía de entrada.** Dos cortes independientes, cualquiera de
los dos basta:

- **La entrada.** Los **12 campos** de `TableRequest` son enteros acotados o
  cadenas con `pattern` de enum cerrado. Desde hoy también `average`, que era
  `int` suelto (CR-04-04). **No hay ni un campo de texto libre**: un atacante no
  tiene dónde escribir la carga.
- **La salida.** Lo que se pinta no viene de la petición, sale de los parquets que
  genera el propio pipeline. Etiquetas como `Runner on 1B steals 2B` se escriben
  en `create_break_even_points.py`. Para inyectar habría que comprometer SQL
  Server primero, y entonces el XSS es el menor de los problemas.

**Y aunque entrara:** no hay autenticación ni cookies de sesión. No hay sesión que
robar ni acción que ejecutar en nombre de nadie; se quedaría en desfigurar la
página al visitante afectado.

**El coste de arreglarlo** es cambiar los ~86 sitios donde se construye HTML por
`textContent` o escapado explícito — reescribir el renderizado de todas las
tablas. Es el mismo cálculo que con los `style=` de la CSP: mucho trabajo para
cerrar una puerta a la que no llega ningún pasillo.

**LA CONDICIÓN QUE INVALIDA ESTA DECISIÓN.** Es lo único que hay que recordar de
todo este punto:

> El día que entre contenido que **no** venga de los parquets propios —nombres de
> jugador de una API externa, comentarios, un buscador que devuelva el término
> escrito, cualquier campo de texto libre— este razonamiento deja de valer y esto
> pasa a ser lo primero de la lista.

El escapado debe tratarse como **requisito previo** de cualquier feature que traiga
texto de fuera, no como deuda a saldar después. Aplica también a los análisis
nuevos de la sección 8 si alguno llega a mostrar nombres de jugador.

### 5.5 Por qué se rechazó #5

`/api/download-stats` es de **lectura** y devuelve estadísticas internas de
descargas; `verify_origin` ahí no es protección CSRF sino anti-scraping. Los demás
endpoints no están "desprotegidos": **son la API pública del producto**, sin
estado ni autenticación que proteger. La asimetría es intencionada.

### 5.6 Open redirect en la aplicación de HTTPS — RESUELTO (8 ago 2026)

Empezó como "la validación de cabecera `Host` está inactiva" —`TrustedHostMiddleware`
se importaba y nunca se registraba— y al mirarlo de cerca resultó ser algo
concreto y explotable, no una formalidad.

`HTTPSEnforcementMiddleware` construía el destino del 308 con la cabecera que
mandara el cliente:

```python
host = request.headers.get('x-forwarded-host') or request.headers.get('host') or request.url.netloc
return RedirectResponse(url=f"https://{host}{request.url.path}", status_code=308)
```

`x-forwarded-host` va **primero** y no la filtra nadie: `ProxyHeadersMiddleware`
gestiona `X-Forwarded-For` y `X-Forwarded-Proto`, no esa. Una petición HTTP con
`X-Forwarded-Host: evil.com` devolvía un **308 permanente a `https://evil.com/…`**
con método y query preservados. Al ser permanente, navegador y cachés se lo quedan.

Solo se registra con `ENVIRONMENT == 'production'`, o sea justo donde importa. El
`canonical`, `og:url` y `twitter:url` de la plantilla están escritos a mano, así
que este era el **único** punto que derivaba una URL de la petición.

**Hecho:** `ALLOWED_REDIRECT_HOSTS`, derivada de `ALLOWED_ORIGINS` —que ya
declaraba los hosts reales, y tenerlos en dos sitios es exactamente como la regla
de RE288 acabó duplicada y divergente—. Si el host pedido no está en la lista se
redirige a `CANONICAL_HOST` y se registra un aviso, porque es señal de ataque.
Hosts admitidos: `sabertables.com`, `www.sabertables.com`, `localhost:8000`,
`127.0.0.1:8000`.

**Validación.** Tres tests: el ataque por `x-forwarded-host`, el mismo por `host`,
y un control con host legítimo que sí debe conservarse. Mutación probada
(`if True: host = solicitado`): fallan los dos de ataque y el control sigue
pasando, que es lo correcto.

**`TrustedHostMiddleware` activado (8 ago 2026).** Lista confirmada contra el VPS:
`sabertables.com`, `www.sabertables.com`, `localhost`, `127.0.0.1`. El nginx real
usa `proxy_set_header Host $host`, o sea que pasa el Host original sin reescribir,
así que llegan los dominios tal cual. `localhost` es obligatorio — el healthcheck
del contenedor llama a `http://localhost:8000/healthz/`. Configurable por
`ALLOWED_HOSTS`. Starlette compara sin puerto, así que no hay que listarlos.

**El orden resultó ser el detalle que importaba.** `add_middleware` ejecuta al
**revés** de como se añade. Puesto donde parecía natural, corría *después* del
redirect a HTTPS: un `Host: evil.test` recibía un 308 en vez de un 400 — la
cabecera se usaba antes de validarla, que es justo lo que esto viene a impedir.
Ahora va inmediatamente después de `ProxyHeaders`, que debe seguir siendo el
primero para normalizar `X-Forwarded-*`.

**Y no sustituye al arreglo del redirect:** `TrustedHostMiddleware` solo mira
`Host`, **no** `X-Forwarded-Host`. Hacen falta las dos defensas.

Verificado en un contenedor de producción: `sabertables.com`, `www.` y `localhost`
dan 200, `evil.test` da **400**, y el healthcheck de Docker reporta `healthy`.

**Y esto no lo tapa nginx.** Si tiene un `server` por defecto que rechaza hosts
desconocidos, queda enmascarado en el borde — pero enmascarado no es arreglado, y
su configuración real vive fuera del repo.

### 5.5-bis `verify_origin` extendido y endurecido — RESUELTO (8 ago 2026)

Solo protegía `/api/download-stats`. Ahora también `/api/generate-table`, que es
el endpoint que hace el trabajo. Se apaga con `API_REQUIRE_ORIGIN=false` para
curl, Postman y los tests; por defecto se activa solo en producción.

**Y se corrigieron dos defectos del propio guard** (CR-04-02 de la revisión v04):

- `referer.startswith(allowed)` aceptaba `https://sabertables.com.evil.test/`,
  porque empieza igual. Basta registrar un dominio que tenga el tuyo de prefijo.
  Ahora se parsea con `urlparse` y se compara `scheme://netloc` **exacto**.
- Bastaba con que casara **una** de las dos cabeceras, así que un `Origin`
  inválido quedaba rescatado por el `Referer` — la más fiable subordinada a la más
  débil. Ahora `Origin` manda: si viene y no está permitido, se rechaza sin más.

Verificado en contenedor: prefijo → 403, `Origin` malo con `Referer` bueno → 403,
`Referer` legítimo sin `Origin` → 200, `Origin` legítimo → 200.

**Lo que esto NO hace, y conviene tenerlo claro:** no impide el uso de la API
desde fuera. `Origin` y `Referer` las pone el cliente y se falsifican con curl en
dos minutos. **Es imposible hacer una API pública de uso exclusivo del frontend**
— el frontend corre en el navegador del visitante, así que todo lo que puede
enviar se puede reproducir. Esto sube el coste; quien cierra de verdad es el rate
limit por IP (que ahora sí funciona, tras arreglar `PROXY_TRUSTED_IPS='*'`) y una
capa tipo Cloudflare delante.

### 5.7 `/healthz` era una sonda falsa — RESUELTO (8 ago 2026)

Devolvía `200` **siempre** mientras el proceso viviera, sin mirar los datos. Si
faltaba un parquet o se corrompía, todas las tablas daban 500 y el monitor seguía
en verde. Era liveness disfrazada de readiness, que es lo que su propio docstring
decía ser.

**Hecho:** comprueba que estén `re24_values.parquet`, `innings.json` y
`last_updated.json`, y **abre** el parquet grande — uno truncado existe y pesa, así
que comprobar la presencia no lo detecta. Si algo falla, `503` con la lista de
problemas. Añadido `Cache-Control: no-store` (con una CDN delante, un `200`
guardado taparía una caída) y `datetime.utcnow()` → `datetime.now(UTC)`, que ruff
marcó como `UP017`.

**Verificado en el contenedor quitando un parquet en caliente:** sano 200 → sin
datos **503** → restaurado 200.

**Para Uptime Kuma:** URL `https://sabertables.com/healthz` **sin barra final** —
`/healthz/` devuelve 307 y Kuma puede tratarlo como caída. Aceptar solo `200-299`.
El `HEALTHCHECK` del Dockerfile también se corrigió a la ruta sin barra.

**Efecto en el despliegue:** ahora Docker marca el contenedor como no sano si
faltan los datos. Es lo correcto, pero un despliegue con el volumen mal montado ya
no arranca en verde.

### 5.8 Higiene del Dockerfile y del compose — RESUELTO (8 ago 2026)

- **`WORKERS` 4 → 2** en el Dockerfile, que resultó ser de donde salía el 4 que
  se veía en el VPS (no del script de despliegue). Medido: 427 MB con 4 workers
  tras pedir tres tablas pesadas, **226 MB con 2**. No se pierde concurrencia:
  `worker_class` es `UvicornWorker`, asíncrono.
- **pip y setuptools fuera de la imagen final.** Había un `pip install --upgrade
  pip` que costaba **18.1 MB** de capa para una herramienta que en ejecución no se
  usa. Quitarlo elimina `CVE-2025-8869` y `CVE-2026-1703` de raíz en vez de
  perseguir versiones, y reduce superficie. Imagen **726 → 708 MB**.
  Contrapartida: ya no se puede instalar nada dentro del contenedor en caliente.
- **Límites de memoria retirados del compose.** Estaban bajo `deploy.resources`,
  que **solo lo respeta Docker Swarm** — con `docker compose up` se ignora. De ahí
  que el contenedor llegara a 516 MB con un "límite" de 512 sin que nadie lo
  matara: el límite no existía, solo lo parecía.
- **`environment:` explícito en el compose**, porque `.env` no entra en la imagen.

**Las dos cosas que quedaban señaladas aquí ya están hechas** (se dejan escritas
porque el razonamiento sigue valiendo):

- La etiqueta del compose decía `sabertables:1.2025.1` con `docker_images/` ya en
  **1.2025.33**. Actualizada. Sigue siendo plantilla —la versión real se cambia en
  Portainer— así que se desactualizará otra vez; conviene subirla al publicar.
- `ports: "3001:8000"` publicaba en **todas** las interfaces: con el 3001
  alcanzable desde fuera se llegaba a la app **saltándose nginx**, y con ello el
  TLS y todo lo que viva allí. Atado a `127.0.0.1:3001:8000`, que es seguro
  porque nginx corre en el **host** y no en otro contenedor. Si algún día nginx
  se contenedoriza, hay que revertirlo y conectarlos por una red de Docker.


---

## 6. Revisión externa v02 (docs/reviews/code_review_v02.md)

Siete hallazgos, todos contrastados contra el código. **Seis ciertos, uno mal
descrito.** Tres corregidos.

### 6.1 El límite de 1 MB del cuerpo no existía — RESUELTO (8 ago 2026)

Peor de lo que decía el revisor. `app = FastAPI(..., max_request_size=1_000_000)`
con el comentario *"prevent DoS attacks"*: **ese parámetro no existe en FastAPI**.
Cae en `**extra` y acaba en `app.extra`, un diccionario que va al esquema OpenAPI
y no corta nada. Verificado: `'max_request_size' in inspect.signature(FastAPI.__init__).parameters`
→ `False`. La protección se leía en el código y no estaba en ninguna parte.

**Hecho:** `MaxBodySizeMiddleware`, ASGI puro. Corta por `Content-Length` —donde
se rechaza sin leer un byte— y además cuenta lo recibido, porque `Content-Length`
se puede omitir con `Transfer-Encoding: chunked`. Configurable por
`MAX_REQUEST_BODY_BYTES`.

**Es ASGI puro y no `BaseHTTPMiddleware` por una razón medida:** la primera
versión usaba `BaseHTTPMiddleware` y leía `request.stream()` para contar bytes.
Eso deja al endpoint sin cuerpo y **rompió 13 tests** con `422 Field required`.
Envolviendo `receive` los bytes se cuentan al pasar y el endpoint los recibe igual.

Verificado en el contenedor: un cuerpo de 1.1 MB devuelve **HTTP 413**.

### 6.2 El suelo de temporada de RE288 solo cubría un tipo — RESUELTO (8 ago 2026)

`main.py` comprobaba `matrix_type == "RE288"`, pero el frontend bloquea los tres
antes de 1988. Una llamada directa a la API con `RE288_FREQ` y `from_season=1958`
se saltaba una regla que la UI considera obligatoria.

**Hecho:** `RE288_MATRIX_TYPES` en `app/config.py`, y el mensaje de error ahora
nombra el tipo real. Test parametrizado sobre los tres.

**El test nació débil y hubo que apretarlo:** comprobaba solo `status_code == 400`,
y un 400 de *"no hay datos para ese rango"* lo satisfacía igual — daba verde con
el `== "RE288"` incompleto. Ahora exige además que el detalle mencione el año.

### 6.3 Dos validadores de rango, uno muerto — RESUELTO (8 ago 2026)

`app/validators/data_validator.py` definía un `validate_season_range` que no
llamaba nadie; el vivo está en `app/main.py`. **La consecuencia no era teórica:**
la regla de RE288 estaba duplicada y **las dos copias tenían el mismo
`== "RE288"` incompleto**. Eliminada la copia muerta.

### 6.4 ~~Lazy loading sin lock~~ — RESUELTO (8 ago 2026)

`ensure_data_loaded()` comprobaba "¿está cargado?" y leía el parquet sin cerrojo.
FastAPI ejecuta los endpoints síncronos en un **pool de hilos**, así que dos
peticiones al mismo dataset frío entraban a la vez y lo leían las dos: doble
lectura de disco y doble pico de memoria en el arranque en frío, justo con los
más pesados (WE, WPA, LI).

**Hecho:** cerrojo **por dataset** —no global, para que cargar `win_expectancy`
no bloquee a quien pide `re24`— con doble comprobación. La segunda comprobación
no es adorno: sin ella el cerrojo solo *serializaría* la doble carga en vez de
evitarla. El cuerpo largo de `if/elif` se movió intacto a `_load_dataset()`.

**Validación.** Test con **ocho hilos saliendo de una barrera** contra un dataset
frío, con un `sleep` que ensancha la ventana de carrera: el parquet se lee una
sola vez. Dos mutaciones, las dos detectadas — quitar el cerrojo, y dejar el
cerrojo pero sin la segunda comprobación.

`ensure_data_loaded()` comprueba `data_store[dataset] is not None` y carga el
parquet sin cerrojo. Dos peticiones simultáneas al mismo dataset frío pueden
leerlo a la vez dentro del mismo worker. No corrompe nada —la asignación al dict
es atómica bajo el GIL— pero duplica lectura y memoria en el arranque en frío.

**Qué hacer:** un lock por dataset, o precalentar los pesados (WE/WPA/LI) al
arrancar.

### 6.5 ~~El fetch del frontend no tiene timeout~~ — RESUELTO (8 ago 2026)

Había `AbortController` para cancelar la petición **anterior**, pero ningún
temporizador para una colgada: con red degradada o un worker atascado el botón se
quedaba en *loading* hasta que el navegador decidiera fallar.

**El detalle que había que resolver:** el `catch` trataba **todo** `AbortError`
como intencionado y volvía en silencio. Un timeout que solo abortara habría
dejado al usuario sin mensaje — exactamente el síntoma que se quería quitar. Por
eso hay una bandera que distingue "lo canceló una petición más nueva" (silencio,
es lo esperado) de "se agotó el tiempo" (mensaje accionable).

**45 s**, holgado sobre el peor caso real medido (`BREAKEVEN_POINTS`, ~2.7 MB) y
por debajo del `WORKER_TIMEOUT` de gunicorn, así que el aviso lo da la UI antes de
que el servidor corte por su cuenta. Verificado que llega al contenedor.

Hay `AbortController` para cancelar la petición anterior, pero ningún temporizador
que aborte una colgada. Con red degradada el botón se queda en *loading* hasta que
falle el navegador. **Qué hacer:** envolver el `fetch` con 30-45s, abortar,
restaurar la UI y dar un mensaje accionable.

### 6.6 ~~El payload de BREAKEVEN_POINTS~~ — RESUELTO (8 ago 2026)

Medido, que es como se zanja:

| endpoint | crudo | gzip |
|---|---|---|
| **BREAKEVEN_POINTS** | **2,693 KB** | **288 KB** |
| RE_IMPACT/tto | 981 KB | 122 KB |
| RE_IMPACT/sac_bunt | 148 KB | 27 KB |
| WIN_EXPECTANCY (la más pesada de las normales) | 89 KB | 26 KB |
| RE24 | 2 KB | 1 KB |

`BREAKEVEN_POINTS` pesa **30× la tabla normal más grande**. La preocupación es
real en magnitud. Pero su receta —*"mover el filtrado/agregación al backend"*—
es el arreglo equivocado: la agregación en cliente es lo que hace **instantáneo**
mover el selector de temporadas, y las temporadas tienen que venir todas para eso.

**El desperdicio no son las temporadas, son los tipos.** Desglosado:

```
table_data      12,309 filas  2,055 KB   <- los SEIS tipos de break-even a la vez
by_count_data    5,328 filas    878 KB   <- solo hace falta si eliges sb_by_count
```

Eligiendo "Stolen Bases" (1,390 filas) te bajas las 12,309 de los seis tipos. El
selector tiene 7 opciones y descarga las 7 siempre. `RE_IMPACT` **ya** filtra por
tipo en el servidor (de ahí que `sac_bunt` pese 148 KB y `tto` 981 KB); break-even
es el que no.

**Hecho:** `breakeven_type` viaja al servidor y este manda solo ese tipo,
conservando todas las temporadas. `by_count_data` solo se envía si se pide.

**No cuesta nada en interacción**, y esto es lo que lo decidió: cambiar de tipo en
la UI ya llama a `hideTableContainers()` y obliga a pedir de nuevo — igual que
`RE_IMPACT`. Así que ese 90% se bajaba y se tiraba **cada vez**, sin ahorro en caché.

| tipo | gzip antes | gzip ahora |
|---|---|---|
| **stealing** (por defecto) | 288 KB | **24 KB** (−92%) |
| tagging | 288 KB | 4 KB (−98%) |
| swing-take-zone (el peor) | 288 KB | 101 KB (−65%) |

**Validación.** 8 tests: cada tipo recibe solo el suyo, `by_count_data` vacío
salvo cuando se pide, las listas de temporadas no encogen, y un cliente que no
mande `breakeven_type` sigue recibiéndolo todo. Aprobado visualmente por el dueño.

### 6.7 ~~Cobertura del smoke test~~ — RESUELTO (8 ago 2026)

El revisor decía que el test *"dice cubrir todos los matrix_type"*. **No lo
decía:** su docstring era explícito —*"los que no necesitan parámetros extra"*— y
la lista se llamaba `simple`. Pero el fondo era cierto: **9 de los 18** tipos sin
barrido.

**Hecho:** `MATRIX_TYPES_CON_PARAMETROS` con el payload **mínimo** válido de los
nueve que faltaban (`RE288_TRANS`, `LINEAR_WEIGHTS`, `WIN_EXPECTANCY`,
`WIN_PROBABILITY_ADDED`, `LEVERAGE_INDEX`, `PARK_FACTORS`, `RE24_TRANSITIONS`,
`BREAKEVEN_POINTS`, `RE_IMPACT`). Mínimo y no típico a propósito: si el contrato
gana un parámetro obligatorio, el test se entera antes que el usuario.

Y un tercer test cierra el agujero de raíz: **compara los dos barridos contra el
patrón de `matrix_type` en `app/main.py`**. Añadir un tipo nuevo sin cobertura
falla, que es justo como `R24_FREQUENCIES` se rompió sin que nadie lo viera.

**El barrido encontró algo al primer intento:** `RE24_TRANSITIONS` exige también
`event_cd`, que no estaba en mi payload mínimo. No era un defecto del código —era
mío— pero demuestra que el test hace su trabajo. Mutación probada: sacar un tipo
del barrido lo detecta.

El revisor dice que el test *"dice cubrir todos los matrix_type"*. **No lo dice:**
su docstring es explícito —*"Este recorre todos los que no necesitan parámetros
extra"*— y la lista se llama `simple`. Pero el fondo es cierto: **9 de 18 tipos**
no tienen barrido de humo (`RE288_TRANS`, `PARK_FACTORS`, `WIN_EXPECTANCY`,
`RE24_TRANSITIONS`, `BREAKEVEN_POINTS`, `RE_IMPACT`…).

**Qué hacer:** matriz parametrizada con el payload mínimo válido de cada uno.

### 6.8 El suelo de temporada de RE288 era 1988 y debe ser 1989 — RESUELTO (8 ago 2026)

No era solo una etiqueta: había datos publicados de más.

| parquet | temporada mínima | filas de 1988 |
|---|---|---|
| `re288_values` | 1989 | 0 |
| `re288_transitions` | 1989 | 0 |
| `sb_breakeven_by_count` | 1989 | 0 |
| **`re288_frequencies`** | **1988** | **288** |

`re288_frequencies` publicaba una temporada de 1988 —**288 filas, exactamente los
288 estados**— sin contrapartida en ninguna otra tabla RE288, y la UI dejaba
seleccionarla.

**El 1988 no correspondía a ninguna fuente.** Comprobado contra la base:
`r24Events_PitchCount` llega hasta **1910** y `r288Values` arranca en **1989**. El
`where t2.SEASON>=1988` de la consulta de frecuencias era un corte arbitrario.

**Hecho:** 1989 en la extracción, en `RE288_MIN_SEASON` (backend y frontend), en
los mensajes de error y en los comentarios. De paso, la validación del frontend
repetía el año a mano tres veces en tres líneas seguidas teniendo la constante
declarada arriba; ahora la usa.

**No se tocó** `16_v_re_analysis_stretch.sql:29`, que menciona 1988 al decir
*"89.9% en 1988, 45% en 1987"*: eso es la cobertura medida del batazo, el dato que
justifica que `running` empiece en 1989. Es un hecho histórico, no un marcador.

**Validación.** Regenerado el pipeline completo: `re288_frequencies` pasa de
10,944 a 10,656 filas, exactamente las 288 de menos. Test de invariante nuevo
sobre los cuatro parquets RE288, comprobado en los dos sentidos: **falla con los
datos viejos** y solo en el fichero afectado.


---

### 6.9 Win Expectancy no agrupaba las colas de diferencial como WPA y LI — RESUELTO (8 ago 2026)

Hallazgo CR-04-03 de la revisión v04. `generate_win_expectancy_table()` pivotaba
directamente por `RUNS_DIFF` y solo **etiquetaba** `-4` como `-4+` y `4` como
`+4+`. WPA y LI sí hacen `clip(lower=-4, upper=4)` antes de agrupar.

**Validado antes de tocar nada: hoy no producía ni un número incorrecto.** El
parquet trae `RUNS_DIFF` en `[-4, 4]` —**0 filas de 450,488** fuera del rango—
porque para WE el recorte ya lo hace la vista SQL. El defecto era latente, no
activo: si la vista empezara a emitir `-5`, WE sacaría columnas `-5`/`+5` que WPA
y LI no tienen y las tres tablas dejarían de ser comparables.

**Lo que costó más que el clip.** Poner el `clip` a secas habría dejado un fallo
peor que el que arregla. El pivot usa `aggfunc="first"`, y la rama de temporada
única tomaba las filas **tal cual, sin agregar** —correcto solo mientras cada
celda fuera una sola fila (verificado: 0 duplicados por
season/team/inn/outs/runners/runs_diff). Con el clip puesto, un `-5` y un `-4` se
pliegan sobre el mismo `-4`: `first` se habría quedado con una y **tirado la
otra**, su probabilidad y su muestra. Así que la agregación ponderada por `n`
pasa a hacerse **siempre**, también con una sola temporada.

**Comprobado que no cambia nada hoy:** salida idéntica bit a bit en siete casos
—2024 home, 2024 away, 1990 home, 2025 away, 2000-2010, 1990-2025 y 2020-2025—.
Con un grupo de una fila, `sum(we*n)/sum(n) == we`.

Cinco tests nuevos, verificados por mutación, con los diferenciales `-5` y `+5`
que pedía el revisor: que no aparezcan columnas fuera de escala, que la cola sea
la media **ponderada** de las celdas plegadas, que su `n` las **sume** (alimenta
el marcador de fiabilidad), que la rama de temporada única también pliegue, y que
el interior de la escala no se toque.

---
### 6.10 Revisión externa v05 — DUPLICADO de la v04, sin contenido propio (8 ago 2026)

**Segunda vez que pasa.** La v03 ya fue un duplicado de la v02, y ahora la v05
repite la v04: los **seis títulos son idénticos carácter a carácter**, con las
mismas severidades, las mismas personas y los mismos números de línea.
Neutralizando el prefijo del ID, los dos ficheros difieren en **28 líneas de
200**, y ninguna cambia un hallazgo — el título, un acento, CR-05-02 reescrito
con formato de informe de seguridad, y dos apuntes más en la nota de Retrosheet.

**Los cinco ya estaban corregidos**, verificado en el código y no en el TODO:

| hallazgo | estado | dónde |
|---|---|---|
| CR-05-01 timer antes de las validaciones | el `setTimeout` se crea dentro del `try` y después de todos los `return`; el `finally` lo limpia | `app/static/js/app.js:1594` |
| CR-05-02 `Referer` por prefijo | comparación exacta de `scheme://netloc`, y `Origin` inválido rechaza sin rescate por `Referer` | `app/main.py:1101`, `:1134` |
| CR-05-03 WE sin `clip` de colas | aplicado, igual que WPA y LI | `app/services/table_generator.py:1155` |
| CR-05-04 `average` sin contrato | `Literal[1, 3, 5] \| None` | `app/main.py:1015` |
| CR-05-05 dependencias sin fijar | `constraints.txt` y el Dockerfile instalando con `-c` | `constraints.txt`, `Dockerfile:19` |

Cuatro estaban resueltos días antes. El quinto se cerró el mismo día (ver 6.9),
con una precisión que el revisor no podía tener: **no producía ningún número
incorrecto**, porque el recorte ya lo hacía la vista SQL —0 filas de 450.488
fuera de rango—. Era fragilidad latente.

**Qué hacer distinto si se relanza.** Las cinco versiones han encontrado 12
hallazgos únicos en total y han repetido dos tandas enteras. Conviene apuntar la
próxima a lo que ninguna ha mirado: el rango de temporadas de la extracción
(`utils/seasons.py`), el SEO más allá de las meta tags, y la frontera con SQL
Server que hoy deja `create_break_even_points.py` en 65% de cobertura.

El texto original está en `_not_in_use/reviews/code_review_v05_original.md`.

---
## 6-bis. Revisión externa v06 (docs/reviews/code_review_v06.md)

**El primero de los seis que aporta hallazgos nuevos.** Ninguno repetido, y apunta
donde ninguno había mirado: el pipeline. Seis hallazgos, **cinco ciertos**,
ninguno causando fallo hoy.

### 6.11 `.str[:3]` truncaba los estados de corredores en vez de validarlos — RESUELTO

`_apply_dtypes()` hacía `astype("string").str[:3]`, así que un `100X` de origen
—una columna renombrada, un JOIN con basura pegada— se publicaba como `100`: un
estado base-out **válido en apariencia**, que entra en denominadores y medias
ponderadas de media docena de tablas sin que nada lo delate.

Verificado en los datos actuales: **8 estados canónicos y 12 conteos, todos de
longitud 3**. No había corrupción; era riesgo latente. Ahora se limpian espacios
y se valida la longitud, y si algo no encaja **aborta** con los valores
culpables. No se valida contra un conjunto canónico porque la marca la comparten
dos columnas con alfabetos distintos —corredores y conteo—; la longitud es lo
que tienen en común y es lo que atrapa el caso real.

### 6.12 El frontend suponía JSON en todos los errores HTTP — RESUELTO

`if (!response.ok) { await response.json(); }` sin mirar el `content-type`.
Cuando el error no viene de la aplicación sino de una capa intermedia —un 502 de
nginx, un 413— el cuerpo es HTML, el parseo lanza y todo acababa en el catch
genérico: el usuario leía *"Failed to generate table"* mientras la respuesta
traía justo la información que hacía falta.

Demostrado en las dos direcciones con un 502 fabricado: el código antiguo lanza
`SyntaxError`; el nuevo devuelve *"502 Bad Gateway: … nginx/1.24.0"*. Se limpian
las etiquetas y se corta a 200 caracteres, para no volcar una página entera ni
inyectar HTML ajeno en el hueco del mensaje.

### 6.13 `last_updated.json` no era obligatorio al promover — RESUELTO

El docstring decía que es la marca de commit del lote, pero el bucle hacía
`if not source.exists(): continue`. Si por una regresión dejara de escribirse, el
lote se promovía igual y quedaban **datos nuevos con una marca de frescura
vieja**, que es justo lo que la web enseña al usuario. Ahora en extracción
completa es obligatorio y aborta sin tocar `app/data`; en parcial (`--dataset`)
sigue explícitamente fuera, porque describiría un lote que ya no es el que hay.

### 6.14 `TrustServerCertificate=yes` fijo — RESUELTO parcialmente

Cierto en la forma; **el escenario descrito no aplica**. `DGL_SQL_SERVER` no está
definido, o sea localhost, y la autenticación es Windows (`Trusted_Connection`):
no hay usuario ni contraseña en la cadena ODBC, así que no hay credenciales que
filtrar, y el MITM contra localhost no es una amenaza real.

Lo que sí es cierto: si algún día se apunta a un host remoto, la validación TLS
deja de hacerse **sin que nadie se entere**. Ahora es configurable con
`DGL_SQL_TRUST_CERT=no`, el defecto sigue en `yes` para no romper la instalación
local (certificado autofirmado), y **el modo efectivo se imprime en el log de
conexión** — un "sin validar" que no se ve es justo como esto llegó a estar fijo.

### 6.15 Enteros de entorno sin validación — ACEPTADO, valor bajo

Cierto, pero el patrón bien hecho ya existe en `seasons.validar()`. Se aplicaría
solo a los pocos numéricos que quedan (`MAX_REQUEST_BODY_BYTES`,
`DGL_SQL_TIMEOUT`). Un traceback en el arranque por una variable mal puesta es
ruidoso, que es lo que se quiere; la mejora es de mensaje, no de comportamiento.

### 6.16 Un endpoint con muchos contratos — NO SE ACEPTA como defecto

Describe la arquitectura correctamente y **no señala ningún fallo**. La propuesta
de un registro de tipos con metadatos por tabla tiene sentido, pero es una
refactorización grande sobre código que funciona y está cubierto por tests. Se
anota como nota de diseño para cuando se añada la próxima familia de tablas.

---
## 7. Infraestructura

### 7.1 Mover el proyecto a git y GitHub — RESUELTO (8 ago 2026)

En `https://github.com/albertop/sabertables`, **privado**. Un commit,
112 ficheros, 17,1 MB.

**Las tres decisiones que este punto pedía tomar:**

| decisión | resultado |
|---|---|
| Qué no se sube | `.env`, `.venv/`, `docker_images/`, `_not_in_use/` y las cachés de mypy, ruff y pytest |
| Público o privado | **Privado** |
| `app/data/` | **Sí se versiona** — al revés de lo que este punto daba por hecho |

**Por qué se cambió de opinión con `app/data/`.** Este documento decía que los
parquets quedaban fuera, y añadía: *"sin acceso a la base un clon no puede
regenerarlos, así que hay que documentarlo"*. Al llegar aquí, esa misma frase era
el argumento contrario: si un clon no puede regenerarlos, razón de más para
versionarlos. Son 14 MB, hacen que un clon arranque sin tocar SQL Server, y —lo
que más pesa— **eran la única copia de los datos generados**. No había ninguna
copia de seguridad: si se perdía el disco, se perdían.

El pipeline corre una o dos veces al año, así que el historial crece unos 28 MB
anuales. Nada para lo que GitHub admite.

**Lo que se preparó antes del primer commit:**

- **`.gitignore` ampliado** con `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`,
  `.coverage`, `htmlcov/`, `logs/` y `*.log`. `.mypy_cache` son **63 MB** que,
  sin esa línea, entran en el primer commit y **ya no salen del historial** sin
  reescribirlo entero.
- **`.env.example`** con las 11 claves reales más las de base de datos. Se
  verificó que no falta ninguna de las que lee el código.
- **`.gitattributes`** para los finales de línea, con `*.ps1` en CRLF.

**Comprobado que no hay secretos**, y no dando por buena la lectura del
`.gitignore`: `git check-ignore` confirma que `.env` está fuera, y tras el push
se verificó contra el remoto que de ese fichero solo está `.env.example`. Un
barrido del repositorio no encontró claves privadas, tokens ni contraseñas. La
conexión a SQL Server va por autenticación integrada de Windows.

**Lo que este punto encargaba al terminar**, y queda hecho: se retira de este
documento la regla de mover a `_not_in_use/` en vez de borrar. Con historial,
borrar es reversible. Las menciones a `_not_in_use/` que quedan en puntos
anteriores son **registro de lo que se hizo entonces**, no instrucciones
vigentes.

`_not_in_use/` no se subió: son 25 MB que viven solo en el disco. Se puede
vaciar cuando quieras.

### 7.2 Evaluar el SEO del sitio — AUDITADO Y CORREGIDO; lo que queda, PRIORIDAD MUY BAJA (decidido el 8 ago 2026)

**Lo que estaba peor de lo que parecía, y nadie podía verlo.** `og:image` declaraba
1200×630 apuntando a `logo.png`, que es **1024×1024 — cuadrado**. Con
`summary_large_image`, X y LinkedIn recortan al centro en 2:1, y sobre un logo
circular eso corta justo por el texto **"SABERTABLES.COM"**: cada enlace
compartido perdía el nombre del sitio. No se detectó antes porque **esa imagen no
se pinta en ninguna página** —el único `<img>` de la portada es el botón de Buy Me
A Coffee, que viene de un CDN—; `logo.png` solo lo bajan los rastreadores de
Facebook, X, LinkedIn, WhatsApp y Slack.

Se generó `og-image.png`, 1200×630 de verdad (255 KB), con el logo recortado en
círculo y centrado sobre el mismo azul marino. Reproducible con
`python utils/build_og_image.py`; Pillow es dependencia **solo de desarrollo**.
Pegar el cuadrado entero dejaba una costura visible —el logo tiene viñeteado, así
que su fondo junto al círculo (29,33,45) no es el de la esquina (31,35,47)— y por
eso se recorta el círculo con máscara antialiaseada.

**Lo demás que se corrigió:**

- **`robots.txt` tenía dos fuentes:** el fichero `app/static/robots.txt` y una
  cadena escrita a mano en la ruta de `main.py`. Editar el fichero no cambiaba
  nada. Nadie lo habría notado hasta que las dos dijeran cosas distintas —un
  `Disallow` puesto en el fichero que el sitio nunca llegaría a anunciar—. Ahora
  la ruta sirve el fichero, como ya hacía el sitemap.
- **Datos estructurados de tipo `Dataset`.** Solo había un `WebApplication`, que
  describe la herramienta pero no lo que aquí se publica. Google indexa conjuntos
  de datos por un canal aparte (Dataset Search) que solo mira ese tipo.
- **Favicon duplicado** (dos `<link rel="icon">` al mismo `.ico`).

**Lo que ya estaba bien** —y contradice lo que decía la nota anterior de este
punto—: la portada **sí** sirve ~6.000 caracteres de texto indexable sin
JavaScript, tiene un solo `h1`, 3 `h2` y 16 `h3`, `alt` en las imágenes, `title`
de 67 caracteres, `description` de 143, `lang="en"`, canonical, y `/robots.txt` y
`/sitemap.xml` responden 200 desde la raíz.

**Diez tests de regresión**, verificados por mutación. Todo lo cubierto falla en
silencio: comprueban que las dimensiones declaradas del `og:image` coinciden con
las del PNG real (leyendo la cabecera IHDR), que la proporción es apaisada si la
tarjeta es la grande, que `og:image` y `twitter:image` no divergen, que el JSON-LD
parsea, que existe el bloque `Dataset`, que `robots.txt` sale del fichero, que el
sitemap que anuncia responde, que no hay favicons repetidos, que la portada no se
queda sin texto indexable y que solo hay un `h1`.

### Lo que queda — APARCADO con prioridad muy baja

**Decisión del 8 de agosto de 2026: no se resuelve ahora.** Queda anotado con el
análisis hecho para no tener que rehacerlo cuando se retome.

**El problema.** Es una SPA con **una sola URL**. Los 18 tipos de tabla, las 116
temporadas y cada combinación de filtros viven todos en
`https://www.sabertables.com/`. Al elegir una tabla, el JavaScript pide los datos
y repinta; la barra de direcciones no cambia.

Tres consecuencias, y solo la primera es evidente:

1. **No hay nada que indexar salvo la portada.** Google guarda una entrada por
   URL. Con una sola URL hay exactamente una página en el índice.
2. **No se puede enlazar.** Nadie puede citar "la matriz RE24 de 2024" desde un
   foro; solo la portada y "elige RE24 en el desplegable". Eso corta la vía por
   la que este tipo de sitio suele crecer.
3. **No se puede volver.** El botón atrás sale del sitio, y recargar pierde la
   tabla.

**Conviene no confundirse:** el problema **no** es que Google no ejecute
JavaScript —lo ejecuta—. Es que no hay URLs distintas que indexar. Aunque
renderizara todo perfectamente, seguiría habiendo una página.

**Las tres salidas, de menos a más coste:**

| opción | qué arregla | coste |
|---|---|---|
| **Rutas en el cliente** (`/re24/2024`) con `history.pushState` | enlazado y botón atrás del todo; SEO a medias —Google las indexa si están en el sitemap, pero tarda más porque exige ejecutar JS— | solo frontend |
| **Prerenderizado** en el pipeline anual, servido a los rastreadores | SEO completo | decidir cuántas de las 2.088 combinaciones (18 × 116) merecen página |
| **Renderizado en servidor** | lo mejor | reescribir la capa de presentación, que hoy vive entera en `app.js` |

**Recomendación cuando se retome:** rutas en el cliente primero. Arregla lo que
hoy es un problema de usabilidad real con el menor cambio, y deja el terreno
listo —una vez existan las URLs, prerenderizar es añadir un paso al pipeline, no
rediseñar nada—. Encaja especialmente bien aquí porque **los datos solo cambian
una vez al año**: no hay nada dinámico que prerenderizar mal.

**Ojo antes de servir contenido indexable desde la API:** el `verify_origin` de
5.5 bloquearía a los rastreadores, que no mandan `Origin`. Hoy no aplica porque
no hay nada indexable ahí; el día que lo haya, hay que excluir esa ruta del guard.

**Y una inconsistencia menor de texto:** la portada dice "1910 to 2025", pero el
pitagórico y runs-per-win llegan a **1901**. No es incorrecto para el grueso de
las tablas, pero se queda corto. Cambiar copy visible es decisión de producto.

---

### 7.3 Lista de despliegue — VERIFICADA (8 ago 2026)

**Workers por defecto.** El Dockerfile y el compose ya ponían `WORKERS=2`, pero
el defecto **en el código** seguía en 4, en dos sitios: `app/gunicorn.conf.py`
—el que manda en producción— y `app/main.py`. Un arranque sin esas variables se
llevaba cuatro copias de los parquets en memoria sin que nada lo dijera. Los dos
a 2, con test que lo fija.

**`API_REQUIRE_ORIGIN` no bloquea tráfico legítimo.** Verificado con el guard
encendido, los ocho casos:

| caso | resultado |
|---|---|
| navegador con `Origin` correcto | 200 |
| dominio sin `www` | 200 |
| solo `Referer`, sin `Origin` | 200 |
| `Referer` con ruta y query | 200 |
| sin cabeceras (curl, bot, monitor) | 403 |
| `Origin` de otro sitio | 403 |
| `Referer` que empieza igual (`...sabertables.com.evil.invalid`) | 403 |
| `Origin` malo + `Referer` bueno | 403 |

Y las rutas que **no** deben exigir origen responden 200 sin cabeceras: `/`,
`/healthz`, `/robots.txt`, `/sitemap.xml` y los estáticos. Lo de `/healthz`
importa para Uptime Kuma, que no manda `Origin`.

`ALLOWED_ORIGINS` está en el código —no en el entorno— y cubre los dos dominios
reales más localhost. La app no fija `Referrer-Policy`, así que vale la del
navegador (`strict-origin-when-cross-origin`), que sí manda el `Referer` en
peticiones del mismo origen. Y el `fetch` del frontend va a una ruta relativa, o
sea POST del mismo origen: el navegador manda `Origin` igualmente.

**Lo único que no se pudo comprobar desde aquí es nginx.** Si algún día añadiera
`Referrer-Policy: no-referrer` o filtrara `Origin`, el guard empezaría a dar 403
a tráfico bueno.

### 7.4 `DOCKER.md` retirado (8 ago 2026)

524 líneas contradiciendo a la documentación nueva: decía `python:3.12` (la
imagen usa 3.13), nombraba `run-expectancy` **27 veces** (el proyecto se llama
sabertables desde el rename), daba 450-500 MB de tamaño (pesa 708) y mostraba
`-p 3001:8000` sin el `127.0.0.1`. No mencionaba `constraints.txt`, ni que pip se
quita de la imagen, ni las tres fechas.

Movido a `_not_in_use/docs/` con la nota de por qué. Lo operativo vive ahora en
`docs/OPERACION_ANUAL.md` y `docs/DESARROLLO.md`.

---
### 7.3 El canonical apuntaba a `www` y producción sirve el apex — RESUELTO (8 ago 2026)

**Medido en producción**, no en local. Los cuatro puntos de entrada acaban en el
mismo sitio, con la ruta conservada:

```
http://sabertables.com/        301 -> https://sabertables.com/
http://www.sabertables.com/    301 -> https://sabertables.com/
https://www.sabertables.com/   301 -> https://sabertables.com/
https://sabertables.com/       200
```

nginx ya había decidido que el bueno es el **apex**. La aplicación decía lo
contrario en once sitios: `canonical`, `og:url`, `twitter:url`, `og:image`,
`twitter:image`, cuatro URLs de los datos estructurados, el `<loc>` del sitemap y
la línea `Sitemap:` de robots.txt. Más `CANONICAL_HOST`.

**Se cedió del lado de la aplicación** porque nginx no hay que tocarlo: cambiar
la redirección en el VPS habría sido más trabajo y más riesgo para el mismo
resultado. Certificado comprobado: cubre `sabertables.com` **y**
`www.sabertables.com` (válido hasta el 28 de octubre de 2026), así que la
redirección de www sigue funcionando.

`www` **se sigue aceptando** como `Host`. No ser el canónico no es lo mismo que
ser rechazado: si algún día nginx dejara de redirigir, la aplicación no debe
responder 400 a un dominio que es suyo.

**Por qué no lo vio la auditoría de SEO (7.2).** Se hizo contra el contenedor
local, donde no hay nginx: `localhost` responde 200 y la redirección no existe.
Auditar SEO sin mirar el host real de producción deja fuera justo la capa que
decide cuál es la URL buena.

**Cinco tests**, verificados por mutación. Dos de ellos pasaron mudos al primer
intento y los dos eran fallos del test, no del código:

- Uno leía `ALLOWED_HOSTS`, que `conftest.py` fija por entorno: estaba validando
  su propia fixture. Ahora comprueba también `DEFAULT_ALLOWED_HOSTS`, que es lo
  que se lleva un despliegue sin la variable.
- Otro hacía `in` sobre esa cadena, y `sabertables.com` es **subcadena** de
  `www.sabertables.com`: un defecto que solo tuviera www habría pasado. Ahora se
  parte por comas.

Y un tercero fallaba por la razón equivocada: buscaba `//www.` en todo el
sitemap, y lo encontraba en los **namespaces XML** (`http://www.sitemaps.org/...`),
que son estándar. Ahora mira solo los `<loc>`.

**Al desplegar:** la etiqueta nueva lleva estos cambios. Comprobar después que
`curl -sI https://sabertables.com/ | grep -i canonical` —o el HTML— ya no
menciona www.

---

## 10. Llevar robo, avance y tag-up a RE Impact, en carreras (9 ago 2026)

**Petición.** AÑADIR los tres análisis —Stolen Bases, Base Running y Tag-up— a
**RE Impact Analysis**, expresados en **carreras**.

**No se quita nada.** Los tres siguen igual en Break-Even Points, con su
porcentaje. Son dos lecturas del mismo número y las dos tienen público: "¿qué
tasa de éxito necesito?" y "¿cuántas carreras me juego?". La segunda es la que
falta.

### 10.1 Por qué merece la pena

**El break-even tira la magnitud.** BE responde "¿qué tasa de éxito necesito?" y
es adimensional: dos celdas con BE = 0.70 pueden estar arriesgando 0,05 carreras
o 0,5. La tabla actual no distingue una decisión que casi da igual de una que
decide el partido. La vista en carreras devuelve justo eso.

**No es un análisis nuevo, es la otra mitad del que ya hay.** Los dos salen de
los mismos tres números:

    ganancia = RE_exito  − RE_quedarse
    coste    = RE_quedarse − RE_fallo
    BE       = coste / (ganancia + coste)

O sea que BE ya está calculado *a partir de* las carreras; simplemente no se
publican. No hay que medir nada nuevo.

**Efecto secundario bueno: los colores se unifican.** El heatmap de RE Impact usa
azul para ganancia y rojo para pérdida; el de break-even va al revés, porque un
BE alto es malo. Presentarlo en carreras hace que la regla sea la misma en las
dos familias, que es una de las cosas que más confunde hoy.

### 10.2 La asimetría que condiciona el diseño

**Los tres análisis NO están en la misma situación**, y esto es lo que hay que
decidir antes de escribir código:

| análisis | ¿tiene tasa de éxito observada? |
|---|---|
| **Base Running** | **Sí.** La vista `_stretch` trae los cubos `stop`/`success`/`fail` con su `n` cada uno |
| **Tag-up** | **Sí.** Igual, con `stay`/`success`/`fail` |
| **Stolen Bases** | **No.** Su BE se calcula solo con la matriz RE24; el `n` publicado es la frecuencia del estado de partida, no intentos de robo |

Con tasa observada se puede publicar lo que de verdad interesa: el **valor
esperado neto de intentarlo**, `p·ganancia − (1−p)·coste`, que es directamente
comparable con el `delta_re` del toque de sacrificio y de la base intencional.

Sin ella —el robo— solo se pueden publicar ganancia y coste por separado. Hay
dos salidas:

1. **Publicar el robo solo con ganancia y coste**, y las otras dos además con el
   neto. Barato, pero la tabla queda desigual.
2. **Añadir los conteos reales de SB y CS**, que están en Retrosheet
   (`event_cd` 4 = robo, 6 = atrapado). Una vista nueva y los tres quedan
   iguales. Es lo que haría, pero es trabajo extra en SQL.

### 10.3 Dos avisos

- **El `n` no se puede copiar tal cual.** Los break-even publican `n_fail`, los
  fallos observados, porque es el término que domina la varianza. Una tabla en
  carreras quiere los **intentos totales** como tamaño de muestra. Son
  denominadores distintos y mezclarlos daría marcadores de fiabilidad que
  describen otra cosa.
- **El signo.** RE Impact usa negativo = cuesta carreras (el sacrificio sale
  negativo). Ganancia y coste deben seguir esa convención o las dos familias se
  leerán al revés.

### 10.4 Plan

**Paso 1 — decidir el alcance del robo.** Opción 1 (rápida, tabla desigual) u
opción 2 (vista nueva con SB/CS, los tres iguales). Es lo único que bloquea.

**Paso 2 — pipeline.** En `create_break_even_points.py`, junto al cálculo de cada
break-even que ya existe, emitir además filas de impacto con
`type` = `steal_runs` / `running_runs` / `tagging_runs` y las columnas
`delta_re` (el neto, o la ganancia si no hay tasa), más ganancia y coste. Se
escriben en `re_impact.parquet`, que ya tiene el esquema y las columnas
opcionales.

**Paso 3 — backend.** Añadir los tres a `analysis_type` de `RE_IMPACT`. El
patrón de Pydantic ya enumera los tipos; hay un test que lo vigila.

**Paso 4 — frontend.** Tres entradas nuevas en el selector de RE Impact, con su
nota metodológica. El heatmap no necesita nada: la regla de color de RE Impact ya
es la correcta para carreras.

**Paso 5 — tests.** La identidad `BE = coste / (ganancia + coste)` es un
invariante precioso: **las dos tablas tienen que cuadrar**. Un test que recalcule
el BE desde las carreras publicadas y lo compare con el de `breakeven_points`
detecta cualquier divergencia entre las dos vistas del mismo número.

**Qué NO cambia:** Break-Even Points se queda exactamente como está, con sus
seis tipos. Esto solo añade.

---
## 9. Revisiones externas v06 y v07 — validadas el 8 ago 2026

**Las dos son revisiones nuevas de verdad**, a diferencia de la v03 y la v05, que
fueron duplicados. La v06 mira el código de producción; la v07 mira **la propia
suite de tests**, y está escrita contra el estado de ahora.

### 9.1 Veredictos de la v06

| hallazgo | veredicto |
|---|---|
| **CR-06-01** `DGL_SQL_SERVERS` parte `host,1433` | **CONFIRMADO y CORREGIDO** |
| **CR-06-02** un DataFrame vacío pasa la validación de esquema | **CONFIRMADO**, pendiente |
| **CR-06-03** `zfill(3)` sin validar el dominio de `runners` | **CONFIRMADO**, pendiente |
| **CR-06-04** sin timeout de sentencia | **PARCIAL**, ver abajo |
| **CR-06-05** las tablas no tienen `caption` ni `aria-labelledby` | **CONFIRMADO**, pendiente |
| **CR-06-06** la sanitización no cubre detalles SQL/ODBC | **PREMISA FALSA** |
| **CR-06-07** la media ponderada no está centralizada | **CONFIRMADO, y más de lo que dice** |

**CR-06-01, corregido.** Medido: `tcp:db.example.com,1433` se parseaba como
`['tcp:db.example.com', '1433']`, o sea que se intentaría conectar a un servidor
llamado `1433` antes de fallar. Hoy no se nota porque la conexión va a
localhost. El separador de lista pasa a punto y coma, con la coma aceptada por
compatibilidad cuando ningún trozo es solo dígitos.

**CR-06-06, premisa falsa.** El escenario de ataque describe fugas de
SQLAlchemy/PyODBC, driver o DSN. **La aplicación no toca SQL Server**: no hay ni
una referencia a pyodbc o sqlalchemy en `app/`; solo lee parquets. Esos detalles
no pueden filtrarse porque no existen en ese proceso. La recomendación genérica
—mensaje genérico más un id de petición— sigue teniendo sentido como defensa en
profundidad, pero el riesgo descrito no aplica.

**CR-06-07, acierta más de lo que su autor sabía.** Cita
`validate_weighted_mean_inputs`, que **ya no existe**: se borró hoy por tener
cero usos. Pero el fondo es correcto y peor de lo que dice: hay **cuatro
divisiones ponderadas escritas a mano** fuera de `calculate_weighted_mean`
—Win Expectancy, WPA, Leverage Index y las transiciones RE24—, cada una con su
propio `sum(x*n)/sum(n)`. Cuatro sitios donde arreglar un fallo exige acordarse
de los otros tres.

**CR-06-04, parcial.** `PH_QUERY_TIMEOUT_SECONDS` ya pone un `LOCK_TIMEOUT`, que
cubre esperas por bloqueo pero no una consulta legítimamente lenta. Y la
extracción de `01` no tiene ningún timeout. Ahora bien: **el pipeline tarda ocho
minutos y corre una vez al año**; un timeout de sentencia mal calibrado abortaría
una consulta que solo era lenta. Lo que el revisor pide al final —que un fallo
tumbe el lote entero sin publicar nada— **ya ocurre**: la excepción descarta el
staging y `app/data` queda intacto.

### 9.2 Veredictos de la v07

Revisa la suite, no el código. Dos afirmaciones se midieron:

**[ALTO] «la suite depende del orden».** **No se reproduce.** Ejecutada entera y
con los ficheros en otro orden, 671 tests en verde en ambos casos. El riesgo que
describe es real como categoría —y ya mordió dos veces en esta sesión— pero está
cubierto: hay una fixture en `conftest.py` que restaura las tres fechas de
temporada después de cada test, y otra que restaura el módulo recargado en modo
producción. Se deja como **vigilancia**, no como defecto abierto.

**[MEDIO] «demasiados `pytest.skip` por falta de datos».** Hay **14** en
`test_data_invariants.py`, y hoy **se salta cero**. El escenario que teme —CI en
limpio, todo verde sin ejecutar nada— **no se da**, porque `app/data/` está
versionado en git: un clon nuevo trae los parquets. Solo saltarían si alguien los
borrara. Riesgo bajo, pero conviene que la suite **falle** en vez de saltar si
los datos no están donde deberían.

Los demás hallazgos de la v07 son ampliaciones de cobertura razonables y ninguno
señala un fallo: estructura de los errores 4xx además del código, concurrencia en
más de un dataset, cabeceras de proxy ambiguas, reintento parcial del pipeline y
contrato entre la configuración de la app y la del despliegue.

### 9.3 Plan de trabajo — EJECUTADO (8 ago 2026)

Los cinco puntos hechos. 685 tests, 100% de cobertura, todo verificado por
mutación.

| | resultado |
|---|---|
| CR-06-01 servidor con puerto | separador a punto y coma, coma por compatibilidad |
| CR-06-02 dataset vacío | `allow_empty=False` en la validación de arranque |
| CR-06-03 estados de corredores | validados contra los ocho canónicos en los cuatro sitios |
| CR-06-05 accesibilidad | `aria-labelledby` en las catorce tablas |
| CR-06-07 media ponderada | cuatro copias unificadas en `media_ponderada_por_grupo` |
| v07 `pytest.skip` | siete convertidos en fallo; siete se quedan |

**Lo que hubo que decidir por el camino:**

- **CR-06-03 cambia el comportamiento**: antes un estado de corredores
  desconocido se mostraba crudo en la tabla; ahora aborta la generación. Había un
  test que fijaba lo anterior y se reescribió con el motivo. Enseñar `999`
  avisaba a quien mirara esa fila, pero no de las demás: un estado inventado no
  se queda en su celda, contamina el lookup de RE24 y RE288.
- **CR-06-07 verificado contra producción**, que corre el código anterior: Win
  Expectancy, WPA y Leverage Index de 2020-2025 salen fila por fila idénticos.
- **`media_ponderada_por_grupo` se queda sin anotación de tipo de retorno**. Con
  ella, mypy estrecha la inferencia y da por inalcanzables las ramas que producen
  las cabeceras `-4+`, `Tie` y `+4+`, que sí se alcanzan. Sale más barato que
  tres `type: ignore`.
- **De los 14 `pytest.skip` solo se convirtieron 7.** Los otros siete se saltan
  por *falta de filas de una porción concreta* —un tipo de break-even sin datos
  en cierta temporada—, que es condicional del dato y legítimo. Los convertidos
  son los que se saltaban por *fichero ausente*, y esos sí: los parquets están
  versionados, así que su ausencia significa que algo se rompió. Comprobado
  escondiendo un parquet: la suite se pone **roja** en vez de verde con saltos.

### 9.4 Plan original

**Primero, lo que es un defecto y se arregla solo:**

1. **CR-06-02** — `allow_empty=False` en `validate_dataframe_schema`. Un parquet
   con el esquema correcto y **cero filas** pasa hoy la validación de arranque y
   publica una tabla en blanco. La promoción del staging rechaza ficheros de cero
   **bytes**, no de cero **filas**, así que este hueco es real.
2. **CR-06-03** — validar `runners` contra el conjunto canónico
   (`000`,`100`,`010`,`001`,`110`,`101`,`011`,`111`) después del `zfill(3)`, en
   los cuatro sitios. Hoy los datos están limpios —comprobado, solo esos ocho
   valores—, así que es prevención. Ojo: `re_impact.scenario` contiene además
   etiquetas de platoon, así que la validación va sobre la columna `runners`, no
   sobre `scenario`.
3. **CR-06-07** — unificar las cuatro divisiones ponderadas en
   `calculate_weighted_mean`. Es refactor con los tests ya puestos, así que la
   red de seguridad existe.

**Después, lo que mejora sin arreglar nada roto:**

4. **CR-06-05** — `aria-labelledby` en las tablas. Accesibilidad; barato.
5. **v07** — que `test_data_invariants` falle en vez de saltar cuando faltan los
   parquets, y aserciones sobre el cuerpo de los errores 4xx, no solo el código.

**Lo que NO se hará, y por qué:**

- **CR-06-04**, timeout de sentencia: el pipeline corre una vez al año y tarda
  ocho minutos; un techo mal puesto abortaría una consulta legítima. Lo que pide
  el revisor al final ya ocurre.
- **CR-06-06**: la premisa no aplica; la aplicación no habla con la base.
- El resto de la v07: ampliaciones de cobertura sobre una suite que ya está al
  **100%** con verificación por mutación. Se anotan, no se priorizan.

---
## 8. Análisis nuevos (features)

**Esta sección es distinta de las anteriores.** Las secciones 1 a 7 son defectos y
mejoras de lo que ya existe; aquí van **análisis que no existen todavía**. No se
mezclan a propósito: un defecto hay que arreglarlo, un análisis nuevo se decide si
se quiere.

Vienen de dos ficheros sueltos que no referenciaba nadie —`utils/break-evien.txt`
y `utils/todo.txt`— más la parte no construida de
`utils/new_analysis_data_requirements.txt`. Los tres están en `_not_in_use/notas/`.

### 8.0 De esas listas, lo que YA está hecho

Se deja escrito para que nadie lo vuelva a proponer creyendo que falta:

| idea en las notas | dónde está hoy |
|---|---|
| Intentional walks | publicado como `ibb` en RE Impact (y con el `delta_re` corregido, P0-1) |
| Pinch-hitting | publicado como `ph_position` y `ph_pitcher` |
| Trying for an extra base (1B→3B en sencillo, 2B→home, 1B→home en doble) | `v_re_analysis_advance`, publicado en `running` — **son exactamente esos tres** |
| Bunt-for-hit attempt | resuelto hoy (2.2), con el toque sorpresa y el squeeze |
| Swing/take por conteo | `swing-take` y `swing-take-zone`, con el modelo de tres desenlaces |

La nota de bunt-for-hit decía *"si el fichero de eventos distingue bunt hit / bunt
out con suficiente limpieza; si no, lo dejaría fuera"*. La respuesta resultó ser
que sí, pero **no por donde se esperaba**: no hace falta distinguir la intención en
el `EVENT_TX`, se aísla con el estado de corredores (ver 2.2).

### 8.1 Break-even del intento de pickoff

De `todo.txt`: *"no puedes observar el lead del corredor, pero sí la clase de
desenlace"* — pickoff con éxito, error en el pickoff, y las secuencias donde se
combina con un caught stealing. Permite valorar el riesgo global de un entorno de
corrido agresivo, aunque **no** la decisión concreta del corredor en ese momento.

#### Diseño esbozado

El más limpio de los que quedan. Los desenlaces son explícitos —corredor
borrado / sin efecto / error con avance— y el baseline es "no hacer nada desde el
estado original", que encaja con el resto de la familia sin inventar nada.

**Cuidado con lo aprendido en 2.1-quater:** con un solo corredor y sin más
variables, los tres desenlaces pueden ser estados base-out deterministas, y
entonces el resultado coincidiría con una transición de la matriz por
construcción. Conviene comprobarlo **antes** de construir la vista, no después.

### 8.2 Break-even de avance en rodado

Corredor en 3B, 1 out, rodado al shortstop o a segunda. Como decía la nota
original: el problema no son las matemáticas, es construir un baseline de
"quedarse" fiable **para el mismo contexto de batazo**.

Es el mismo problema que se resolvió en `v_re_analysis_advance` con las zonas de
decisión viva (localización y trayectoria con tasa de éxito entre 10% y 75% y
n≥500), así que hay camino hecho.

### 8.3 Swing/take por conteo

Anotado como "uno de los análisis RE288 más naturales, pero necesitas desenlaces
de swing/take a nivel de lanzamiento".

**Esto ya está hecho**, y conviene que la nota no siga sugiriendo lo contrario:
existe `v_re_analysis_swing_take` con el modelo de tres desenlaces (contacto /
foul / abanico) y su versión por zona, publicadas como `swing-take` y
`swing-take-zone`. Lo que queda de esa idea es la nota metodológica sobre el
sesgo del *take*, ya en la UI (ver 3.2).

### 8.4 Umbral de selección de lanzamiento — NO VIABLE con los datos actuales

La nota original ya lo decía: pide Statcast o PITCHf/x, y los eventos de
Retrosheet no llegan. Se conserva para que nadie lo vuelva a proponer sin saber
que ya se descartó por falta de datos.

### 8.5 Evaluación del corredor emergente (pinch-run) — PENDIENTE, con especificación escrita

Venía de `utils/new_analysis_data_requirements.txt`, un documento que especificaba
**tres** análisis. Dos ya están publicados —`ibb` y los dos de bateador emergente
(`ph_position`, `ph_pitcher`)—, así que el fichero estaba obsoleto en dos tercios.
Este es el que **nunca se construyó**; se rescata para que la especificación no se
pierda con el fichero.

Una fila por sustitución de corredor emergente:

| campo | tipo | qué es |
|---|---|---|
| `season` | int | temporada |
| `outs_at_sub` | int | outs en el momento de la sustitución |
| `runners_at_sub` | str | estado de bases en ese momento |
| `runs_scored_rest_of_inning` | int | carreras desde la sustitución hasta el final de la entrada |

```
Valor = carreras_resto_de_entrada − RE(estado en la sustitución)
```

Agregado por (base ocupada, outs), para ver dónde compensa correr por alguien.

**Dos avisos, de cosas aprendidas después de escribir esa especificación:**

- **El baseline no puede ser la liga.** Es el mismo defecto que tenía el toque
  (2.2): a quien sustituyen es un corredor lento, no el promedio. Hace falta un
  ajuste por corredor equivalente al `swing_away` del toque, o el número dirá que
  el emergente es maravilloso cuando parte de lo que mide es *a quién quitó*.
- **Cuidado con la degeneración de 2.1-quater.** Si los desenlaces acaban siendo
  estados base-out deterministas, el resultado coincidirá con una transición de la
  matriz por construcción. Comprobarlo antes de construir la vista.

---

### 8.6 Subir la cobertura de pruebas al 95% o más

Hoy: **67%** global con 278 tests (`pytest --cov=app --cov=utils`).

| módulo | cobertura | statements sin cubrir |
|---|---|---|
| `app/services/table_generator.py` | 87% | 73 |
| `app/main.py` | 84% | 122 |
| `utils/create_break_even_points.py` | 65% | 308 |
| `utils/calculate_pythag_exponents.py` | 54% | 78 |

**Lo que queda ya no es lógica pura.** Los 308 de `create_break_even_points.py`
son `main()` (118 líneas de orquestación), los `load_*_from_db` —una por vista—,
los `save_*` que escriben parquets, el manifiesto y el `_cli`. En
`calculate_pythag_exponents.py` es lo mismo: `get_connection`, los dos `save_*`,
`run` y `_cli`.

Para pasar de ahí hay que decidir **cómo se prueba la frontera con SQL Server**,
y son dos caminos distintos:

1. **Fixtures de DataFrame** que imiten la salida de cada vista, inyectados
   saltándose la capa de conexión. Barato, pero prueba el cálculo, no el
   contrato con la vista —que es justo donde ya nos mordió una vez
   (`frequency` vs `pa_final_count_share`).
2. **Una base de prueba** con un subconjunto de temporadas. Prueba el contrato
   de verdad, pero es infraestructura que hoy no existe y ata los tests a tener
   SQL Server delante.

Sin esa decisión, el 95% no se alcanza —y buena parte de lo que falta son
líneas de I/O cuyo valor de prueba es bajo. Puede que el objetivo correcto sea
95% **de la lógica de cálculo** y no del fichero entero.

Lo pura-lógica ya está cubierto y verificado por mutación: el break-even de las
seis familias, el ajuste del exponente pitagórico y sus ventanas móviles, la
guarda de rango previa al parquet, el break-even por zona de Statcast, y los
tres normalizadores de vista (platoon, TTO y bateador emergente).

**Nota de por qué vale la pena.** El último empujón de cobertura (los tests de
`calculate_running_breakeven`) destapó un `UnboundLocalError` real: `rows = []`
estaba indentado dentro del `if len(pivot):`, así que si ninguna celda tenía los
tres desenlaces observados la función reventaba en vez de devolver `[]`. Nunca
saltó en producción porque siempre hay datos, pero es exactamente la clase de
fallo que aparece el año que una vista cambia. Se arregló al encontrarlo.

## Hecho en la revisión (para contexto)

Cerrado y validado: el `delta_re` de IBB (publicaba RE absoluta, inflado hasta
50×), los break-even de swing/take (34% y 43% fuera de rango, hasta ±5,600), la
escritura atómica de `01`, la conexión unificada por `.env` (donde el nombre de
servidor **nunca** se había honrado por una cadena ODBC con `Server=` duplicado),
el peso del doble robo medido (0.75 → 0.863), la temporada 2000 del pitagórico,
el refactor table-driven de `01`, logging con `--log-file` y `argparse` con
`--dataset`, y `tests/test_data_invariants.py` con 51 tests que incluyen el
contrato de esquema con la app —que detectó que `re288_frequencies` llevaba
tiempo sirviendo la columna `frequency` mientras `app/main.py` exigía
`pa_final_count_share`, dejando ese endpoint roto.
