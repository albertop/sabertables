# Code Review v03 — DUPLICADO DE LA v02, SIN CONTENIDO PROPIO

Esta revisión no aportó nada. Se anula y se deja la nota en su sitio para que
nadie la busque pensando que falta.

## Por qué

Los **siete títulos de hallazgo son idénticos carácter a carácter** a los de
`code_review_v02.md`, con la misma redacción, las mismas severidades y los mismos
números de línea.

Y se generó contra una vista **anterior** del proyecto: sus enlaces apuntaban a
`apps/run_expectancy`, una carpeta que ya no existe —el proyecto se renombró a
`apps/sabertables`— pese a que el fichero está fechado después del cambio.

## Lo que ya estaba corregido cuando se escribió

Tres de los siete se habían arreglado **antes** de que la v03 se generase:

| hallazgo | estado real en ese momento |
|---|---|
| #1 el límite de 1 MB no está aplicado | `MaxBodySizeMiddleware` ya registrado; probado, 1.1 MB → HTTP 413 |
| #2 la regla RE288 no cubre `RE288_FREQ` ni `RE288_TRANS` | ya unificada en `RE288_MATRIX_TYPES` |
| #6 dos validadores de temporada, uno muerto | el duplicado ya estaba eliminado |

El #2 además decía que el frontend bloquea *"antes de 1988"*, cuando el suelo ya
era **1989** (ver 6.8 en `docs/TODO.md`).

## Los otros cuatro

`#3` payload en cliente, `#4` lazy loading sin lock, `#5` fetch sin timeout y
`#7` cobertura del smoke test están anotados como **6.4 a 6.7** en
`docs/TODO.md`, con el análisis hecho: el `#3` es diseño deliberado —la
agregación en cliente hace instantáneo el selector de temporadas— y el `#7` está
mal descrito, porque el test no dice cubrir todos los tipos: su docstring dice
*"los que no necesitan parámetros extra"*.

## Si se relanza

Que sea contra el estado actual: ruta `apps/sabertables`, 128 tests, y con los
tres arreglos dentro. El texto original está en
`_not_in_use/reviews/code_review_v03_original.md`.
