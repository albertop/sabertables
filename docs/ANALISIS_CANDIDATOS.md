# Análisis candidatos

Catálogo de análisis propuestos y todavía no publicados. Viene de
`utils/re_analysis.txt`, que era una lista sin estado; aquí cada uno lleva qué
haría falta y si es viable con los datos que hay.

**Esto no es una lista de tareas.** Son ideas con su triaje. Lo que sí está
comprometido vive en la sección 8 de [TODO.md](TODO.md).

La regla era que no se hacía ningún análisis nuevo hasta poner la versión actual
en producción. **Se cumplió: la `1.2025.37` está en producción desde el 9 de
agosto de 2026**, con los tres análisis en carreras y GIDP dentro. El siguiente
candidato se puede empezar.

## Ya publicado

Lo que la lista original proponía y hoy existe, para que nadie lo vuelva a
proponer creyendo que falta:

| propuesta | dónde está |
|---|---|
| Swing/take por conteo y zona | `swing-take` y `swing-take-zone`, con el modelo de tres desenlaces (ver 2.4 en TODO) |
| Times-through-order | `tto` en RE Impact, con las vueltas 1-3 y la 4+ plegada sobre la 3 |
| Intentional walks | `ibb` en RE Impact |
| Pinch-hitting | `ph_position` y `ph_pitcher` |
| Avanzar una base de más | `running`: 1B→3B en sencillo, 2B→home, 1B→home en doble |
| Toque para hit | `bunt-for-hit`, con el toque sorpresa y el squeeze (ver 2.2 en TODO) |

## Comprometido en el TODO

`8.1` break-even del intento de pickoff · `8.2` break-even de avance en rodado ·
`8.3` swing/take por conteo · `8.5` evaluación del corredor emergente.

Y `8.4`, umbral de selección de lanzamiento, marcado **no viable** con los datos
actuales.

## Pendientes de triar

Los que quedan de la lista original. La columna de viabilidad es una estimación
a partir de lo que ya se sabe de las fuentes, no una medición.

### Decisión en el primer lanzamiento

Abanicar o tomar en 0-0, por zona y perfil de bateador y lanzador.
Comparar desenlaces de swing contra desenlaces de take.

**Viable.** Es un caso particular de lo que ya hace `swing-take-zone`, acotado al
conteo 0-0. El trabajo sería de presentación más que de cálculo.

### Protección con dos strikes

Enfoque de contacto contra swing completo con dos strikes, por clase de zona,
tipo de lanzamiento y tramo de velocidad.

**Viable con matices.** El tipo de lanzamiento y la velocidad solo existen en la
era Statcast —los datos de swing/take arrancan en 2008—, así que sería una tabla
con un suelo muy posterior al del resto del sitio. Eso hay que decirlo en la UI o
se lee como si faltaran datos.

### Impuesto de tomar un strike en zona

Cuánto cuesta en carreras tomar un strike en zonas bateables, por conteo y zona.

**Viable, y probablemente el más interesante de los que quedan.** Es una lectura
directa de lo que ya se calcula: el break-even de swing/take por zona ya separa
tomar de abanicar. Lo que añade es presentarlo como coste absoluto en vez de como
umbral de contacto necesario.

### Impacto de perseguir fuera de zona

Cuándo perseguir duele menos, o incluso ayuda. Swing fuera de zona contra take
fuera de zona.

**Viable.** Mismo motor que el anterior, con las celdas fuera de zona. Nota
sabermétrica ya medida: **el 100% de los break-even negativos cae dentro de la
zona**, 0% fuera. Fuera de zona el break-even se comporta como cabría esperar.

### Calidad de contacto

Si "buscar daño" bate a "poner la bola en juego" en cada conteo, con perfiles de
velocidad de salida y ángulo de lanzamiento.

**Dudoso.** Requiere clasificar a los bateadores por arquetipo, y eso mete una
decisión de modelado —dónde se corta— que no tiene respuesta objetiva. Además
arrastra el problema de siempre de los splits por jugador: muestras pequeñas que
piden encogimiento bayesiano.

### Emparejamiento por tipo de lanzamiento

Cómo cambia el valor esperado según la familia del lanzamiento en el mismo
contexto. "Mejor y peor lanzamiento" por situación.

**Viable solo en la era Statcast.** Mismo problema de suelo que la protección con
dos strikes.

### Emparejamiento del bullpen

Valor de usar un especialista: relevista nuevo contra el lanzador actual, por
bolsillo de lateralidad y leverage.

**Complicado.** El problema no son los datos sino el sesgo de selección: el
mánager saca al especialista **precisamente** cuando cree que va a funcionar, así
que la comparación cruda mide la decisión del mánager y no el efecto del cambio.
Hace falta un baseline construido con cuidado, como el que se acabó usando en
bunt-for-hit.

### Explotar el posicionamiento defensivo

Batear a las zonas desocupadas contra tirar de la banda habitual.

**Complicado por otro motivo:** la regla que limita los shifts entró en 2023, así
que el régimen cambia a mitad de la serie. Sería una tabla de dos eras que hay
que separar, como pasa con el bateador designado universal en `ph_pitcher`.

### Conversión de conteos

Valor de llegar a conteos favorables según el plan de lanzamiento.

**Viable**, y encaja con la maquinaria de RE288 que ya existe: la matriz por
conteo tiene precisamente el valor de cada estado de conteo. Lo que falta es la
transición entre conteos, que es lo que mide `re288_transitions`.

### Riesgo de error contra probabilidad de out

Jugadas o tiros agresivos contra la opción segura, por tipo de batazo, velocidad
del corredor y calidad del brazo.

**Dudoso con los datos actuales.** La velocidad del corredor y la calidad del
brazo no están en Retrosheet, y en Statcast solo desde hace pocos años. Sin esas
dos, el análisis mide el promedio de situaciones muy distintas.

## Candidatos de jugada, no de lanzamiento (9 ago 2026)

La lista original venía toda del lado del lanzamiento. Estos salen del otro lado:
sucesos del juego que mueven corredores y que hoy no se miden en ninguna tabla.
Van con el mismo triaje.

**`GIDP` fue el primero, y está publicado** en la `1.2025.37`. Ver 8.7 en [TODO.md](TODO.md).

### Con los datos de evento que ya se extraen

| candidato | qué mediría | viabilidad |
|---|---|---|
| **Doble matanza (GIDP)** | Coste en carreras de rodar a doble play frente a un out normal, por estado de bases | **PUBLICADO** como Double Play. `DP_FL`, completo desde 1910 |
| **Wild pitch y passed ball** | Carreras que regala cada uno, por separado | Viable. Banderas propias por evento |
| **Balk** | Igual, muestra pequeña pero limpia | Viable |
| **Pickoff** | Coste de que te agarren fuera | Viable, y hoy **no se cuenta en ninguna parte**: `RUN1_PK_FL` se excluye del CS para no inflarlo, y ahí muere |
| **Error defensivo** | Carreras que añade el error, por estado de bases | Viable |
| **Elección defensiva** | Ídem | Viable |
| **Sacrifice fly** | Hoy vive dentro de tag-up como jugada, no como desenlace propio | Viable |
| **Indiferencia defensiva** | El robo que no es robo | Viable. Hoy se mezcla o se descarta según la vista |
| **Toque para hit en carreras** | `bunt-for-hit` existe solo como umbral | Barato: el patrón de los tres `*_runs` ya está |
| **Robo por conteo en carreras** | `sb_by_count` existe solo como umbral | Barato, ídem |

### Hacen falta vistas nuevas

| candidato | qué mediría | viabilidad |
|---|---|---|
| **Corredor fantasma** | Valor real de empezar en segunda en extra innings | Viable y **es un experimento natural limpio**: la regla entra en 2020 y la serie llega a 2025 |
| **Relevista con corredores heredados** | Impacto de cambiar de lanzador según el estado de bases | Arrastra el mismo sesgo de selección que el emparejamiento del bullpen |
| **Tipo de batazo por estado de bases** | Rodado / línea / elevado | Viable desde 1989, que ya es el suelo de cobertura por lanzamiento |
| **Swing/take en carreras** | Hoy solo existe como umbral | Viable |
| **Turno en el orden al bate** | El hueco del noveno | Cruza con el bateador designado universal de 2022, que ya está marcado |

### Break-Even

| candidato | qué mediría | viabilidad |
|---|---|---|
| **Umbrales en probabilidad de victoria** | Robar con dos outs en el noveno de un juego de una carrera no tiene el mismo umbral que en el segundo inning, y **en carreras eso no se ve** | El mejor de la lista. Reusa Win Expectancy y WPA, que ya están calculadas |
| **Hit and run** | Cuánto tiene que funcionar para pagar el rodado convertido en doble play y el corredor expuesto | Viable, y encaja con GIDP |
| **Toque de sacrificio** | Existe como impacto, no como umbral | Viable |
| **Base por bolas intencional** | Cuánto peor tiene que ser el siguiente bateador | Viable |
| **Bateador emergente** | Cuánto mejor tiene que ser para pagar el out esperado | Ya comprometido como `8.5` |
| **Robo de home** y **doble robo retrasado** | | Dudoso por muestra; hay que medirla antes de prometer nada |
| **Swing en 3-0** | | Viable |
| **Umbral de robo por inning y marcador** | No solo por conteo | Es el de probabilidad de victoria, acotado |

### Descartados de entrada

Framing del receptor, defensas corridas, infield in, y cualquier cosa que dependa
de posicionamiento. Retrosheet no lo trae, y lo que saldría sería una estimación
disfrazada de medición.

## Lo que tienen en común los que quedan

Casi todos los viables son **variaciones de presentación sobre swing/take por
zona**, que ya está calculado. Los que traen algo genuinamente nuevo —bullpen,
posicionamiento— son los que tienen un problema de diseño estadístico serio
detrás: sesgo de selección o un cambio de reglas a mitad de la serie.

Eso sugiere que el próximo trabajo con más rendimiento no es un análisis nuevo,
sino sacarle más partido al que ya existe.
