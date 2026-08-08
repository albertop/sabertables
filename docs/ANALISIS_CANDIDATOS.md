# Análisis candidatos

Catálogo de análisis propuestos y todavía no publicados. Viene de
`utils/re_analysis.txt`, que era una lista sin estado; aquí cada uno lleva qué
haría falta y si es viable con los datos que hay.

**Esto no es una lista de tareas.** Son ideas con su triaje. Lo que sí está
comprometido vive en la sección 8 de [TODO.md](TODO.md), y la regla vigente es
que **no se hace ningún análisis nuevo hasta poner la versión actual en
producción**.

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

## Lo que tienen en común los que quedan

Casi todos los viables son **variaciones de presentación sobre swing/take por
zona**, que ya está calculado. Los que traen algo genuinamente nuevo —bullpen,
posicionamiento— son los que tienen un problema de diseño estadístico serio
detrás: sesgo de selección o un cambio de reglas a mitad de la serie.

Eso sugiere que el próximo trabajo con más rendimiento no es un análisis nuevo,
sino sacarle más partido al que ya existe.
