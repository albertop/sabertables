# Plan: el orden de las columnas de conteo (1-0 y 2-1)

**Estado: CERRADO el 13 ago 2026 — se deja como está.**

Decisión del usuario tras ver la medición. El orden actual se mantiene: es una
convención de lectura correcta y estable, y la medición demuestra que no existe un
orden por valor único al que cambiarse.

Este documento se conserva porque la pregunta volverá a surgir, y aquí está la
respuesta medida: **el orden se invierte según los corredores**, así que cualquier
secuencia fija estará mal para parte de la tabla. No es un defecto pendiente.

## Lo medido, en RE288 completo

1989-2025, 888 casillas de estado base-out por temporada.

### El par que yo señalé primero era ruido

`2-2` contra `0-1`: `2-2` es menor en el **50,5%** de las casillas, diferencia
media **−0,0027** carreras, mediana **−0,0003**. Por décadas oscila entre 45,8% y
62,5% sin tendencia. **Están empatados y el orden actual es tan bueno como
cualquier otro.** Lo presenté como anomalía tras mirar una sola casilla de una
sola temporada; fue un error de método.

### El par que sí tiene señal, y la sorpresa

`2-1` contra `1-0`: `2-1` es menor en el **56,1%**, media **−0,0117**. Débil en
conjunto... pero **el promedio esconde que el orden SE INVIERTE según los
corredores**:

| bases | veces que `2-1` < `1-0` | diferencia media |
|---|---|---|
| `000` | **33,3%** | +0,005 |
| `100` | 45,9% | +0,002 |
| `111` | 45,0% | +0,010 |
| `110` | 55,9% | −0,004 |
| `001` | 61,3% | −0,007 |
| `101` | 65,8% | −0,032 |
| `010` | **70,3%** | −0,014 |
| `011` | **71,2%** | −0,054 |

**Con las bases vacías, `1-0` vale menos que `2-1` — el orden actual es correcto.
Con corredores en posición de anotar, se invierte.**

Tiene sentido: con un corredor en segunda, el strike gastado en `2-1` pesa más
porque cada out acerca el final de la entrada y la carrera está a un sencillo. Con
las bases vacías, la bola de más importa más que el strike perdido.

## La consecuencia, que es lo importante

**La premisa del cambio no se sostiene.** Se pedía intercambiar dos columnas
suponiendo que existe un orden por valor único, y **no existe**: el orden depende
de la fila. Cualquier secuencia fija estará mal para parte de la tabla.

Las 24 filas no comparten un mismo gradiente.

## Las tres salidas

### A. No tocar nada — RECOMENDADA

El orden actual es una **convención de lectura**, no una afirmación sobre valores:
del conteo más favorable al lanzador al más favorable al bateador, con el primer
lanzamiento en el centro. Como convención es correcta, estable y ya está aprendida
por quien usa el sitio.

Cambiar dos columnas por una diferencia media de 0,0117 carreras —que además se
invierte en cinco de los ocho estados de bases— mueve una tabla en producción sin
mejorar nada.

### B. Ordenar por bolas y strikes, no por valor

Renunciar al gradiente y ordenar por una regla mecánica y evidente: por strikes y
dentro de cada grupo por bolas. Se defiende sola y no depende de ninguna medición.
El precio es perder la lectura de izquierda a derecha como gradiente.

### C. Intercambiar `1-0` y `2-1`

Lo pedido. **Solo mejora la lectura con corredores en posición de anotar y la
empeora con las bases vacías**, que es la fila más consultada. Si se hace, hay que
hacerlo en `sort_order_count` de la vista de RE288, no en el frontend, para que
todas las tablas que comparten el formato lo hereden.

## Si se elige C, los pasos

1. **Test primero**: fijar el orden esperado como contrato en un test estático,
   comprobando que se pone rojo con el orden viejo.
2. Cambiar `sort_order_count` en la vista SQL de RE288 — **un solo sitio**.
3. Regenerar `re288_values.parquet` y verificar que **ningún valor cambia**: es una
   reordenación, no un recálculo. Comparar celda a celda por clave.
4. Comprobar en el navegador RE288, RE288 Transitions, RE288 Frequencies y Missed
   Calls: las cuatro comparten el formato y las cuatro tienen que moverse juntas.
5. Revisar que las descargas CSV y JSON salgan en el orden nuevo.

## Lo que NO hay que hacer

Cambiarlo solo en `app.js` para Missed Calls. El orden vive en `sort_order_count`
y lo comparten cuatro tablas; tocarlo en un sitio las descuadra entre sí.
