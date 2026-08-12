# Plan: dejar wRC Components como las demás tablas

**Estado: propuesta. Nada aplicado.**

## Qué está mal, mirado en pantalla

La tabla funciona y los números son correctos, pero **la presentación me la
inventé** en vez de copiar la de al lado. Medido en el navegador:

| defecto | cómo está | cómo está en las demás |
|---|---|---|
| Subtítulo | Un párrafo con las tres fórmulas seguidas | Una frase que explica qué estás viendo |
| Cabeceras | Sin clase, fondo transparente | `class="bg-primary text-center"` |
| Ordenación | No hay | `data-accion` + `data-clave` y un icono `⇅ ▲ ▼` |
| Heatmap | No hay | Interruptor arriba a la derecha, **encendido por defecto** |
| Paginación | 116 filas de golpe, **5.060 px de alto** | 12 filas por página |

## Lo que propongo

### Paso 1 — Los tests, antes de tocar la tabla

Cinco, en `test_render_navegador.py`, y **cada uno tiene que ponerse rojo hoy**:

1. **Las cabeceras llevan el fondo del sitio.** Que todo `th` tenga `bg-primary`.
   Es lo que hace que se lean como cabeceras y no como una fila más.
2. **Se puede ordenar.** Que los `th` traigan `data-accion` y `data-clave`, y que
   al pulsar uno **cambie el orden de las filas de verdad** — no solo que aparezca
   el icono. Comprobar el valor de la primera fila antes y después.
3. **Hay interruptor de heatmap y arranca encendido.** Que exista y que
   `checked === true` sin tocarlo.
4. **El heatmap pinta.** Que con él encendido haya celdas con color de fondo, y
   que al apagarlo desaparezcan. Que el interruptor exista no prueba que haga algo.
5. **Pagina.** Que se pinten 12 filas y no 116, que haya controles, y que al pasar
   de página **cambie la temporada de la primera fila**.

El 2, el 4 y el 5 comprueban el EFECTO, no la presencia del control. Es la
lección del botón de descarga: existía y no bajaba nada.

### Paso 2 — El subtítulo

Al estilo de los que ya hay —*"wOBA coefficients measure the OBP-scaled offensive
value of each batting event, derived from its average run value"*—: una frase que
dice qué estás viendo, no un volcado de fórmulas.

Propuesta, dos líneas:

> League-wide constants a researcher needs to compute wRAA, wRC and wRC+ for any
> batter, from 1910 to 2025.
> Regular season only. Park factors for the wRC+ adjustment are published in the
> Park Factors table.

Las fórmulas **no van en el subtítulo**. Si las quieres visibles, su sitio es la
documentación o un desplegable, no la cabecera de la tabla.

### Paso 3 — Cabeceras con fondo y ordenación

Copiar el patrón de `renderWOBAHeaders`: `class="bg-primary text-center"`,
`data-accion="sortWRCComponents"`, `data-clave` con el nombre de la columna, y el
icono según la columna y el sentido activos.

### Paso 4 — Heatmap

Registrar `wrc-components-heatmap-toggle` en la lista de interruptores
(`app.js:574`) y añadirlo al encabezado, dentro del `div.d-flex` que ya puse.
**Encendido por defecto**, como pediste.

Una decisión que quiero que veas antes de codificar: **el heatmap tiene que ser
por columna, no por tabla**. Las escalas no son comparables entre sí —`lgwOBA`
vale 0,31 y `League PA` vale 182.838—; con una escala común, todo saldría del
mismo color menos las dos columnas de conteos.

### Paso 5 — Paginación

Un `wrcComponentsPagination` calcado del de wOBA: 12 filas por página,
`sortColumn: 'season'`, `sortDirection: 'desc'`. Con 116 temporadas son 10
páginas.

### Paso 6 — Verificar en el navegador

Los cinco tests en verde, y mirarlo yo al lado de wOBA Coefficients antes de
decir que está.

## Lo que NO propongo

Tocar los datos ni el endpoint. Los números son correctos y están validados: wRC+
da 100,0 clavado para el bateador de liga. Esto es solo presentación.
