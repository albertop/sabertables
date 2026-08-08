# Code Review v05 — DUPLICADO DE LA v04, SIN CONTENIDO PROPIO

Esta revisión no aportó nada. Se anula y se deja la nota en su sitio para que
nadie la busque pensando que falta. **Es el segundo caso igual**: la v03 fue un
duplicado de la v02.

## Por qué

Los **seis títulos de hallazgo son idénticos carácter a carácter** a los de
`code_review_v04.md`, con las mismas severidades, las mismas personas y los
mismos números de línea. Neutralizando el prefijo del ID (`CR-04` → `CR-05`),
los dos ficheros difieren en **28 líneas de 200**, y ninguna cambia un hallazgo:

- el título (`v04` → `v05`);
- un acento corregido (`sincrónicas` → `sincronicas`);
- CR-05-02 reescribe «Riesgo / Recomendación» como «Attack scenario /
  Exploitation method / Business impact / Recommended fix» — el mismo contenido
  con formato de informe de seguridad;
- la nota de Retrosheet gana dos apuntes (conteos `??` antes de 1988, caracteres
  de incertidumbre).

## Los cinco ya estaban corregidos cuando se escribió

Verificado en el código, no en el TODO:

| hallazgo | estado real | dónde |
|---|---|---|
| CR-05-01 timer antes de las validaciones | el `setTimeout` se crea **dentro** del `try` (línea 1409) y después de todos los `return` tempranos; el `finally` hace `clearTimeout` | `app/static/js/app.js:1594` |
| CR-05-02 `Referer` por prefijo | `_origen_de()` compara `scheme://netloc` EXACTO, y si `Origin` viene y no está permitido se rechaza **sin** rescate por `Referer` | `app/main.py:1101`, `:1134` |
| CR-05-03 WE sin `clip` de colas | `clip(lower=-4, upper=4)` aplicado, igual que WPA y LI | `app/services/table_generator.py:1155` |
| CR-05-04 `average` sin contrato | `average: Literal[1, 3, 5] \| None` | `app/main.py:1015` |
| CR-05-05 dependencias sin fijar | `constraints.txt` con las versiones exactas, y el Dockerfile instala con `-c` | `constraints.txt`, `Dockerfile:19` |

Cuatro de los cinco estaban resueltos **días antes**. El quinto, CR-05-03, se
cerró el mismo 8 de agosto (ver 6.9 en `docs/TODO.md`) — y ahí conviene una
precisión que el revisor no podía tener: **no producía ningún número
incorrecto**. El parquet trae `RUNS_DIFF` en `[-4, 4]`, 0 filas de 450.488 fuera
de rango, porque para WE el recorte ya lo hacía la vista SQL. Era fragilidad
latente, no un fallo activo. Y poner el `clip` a secas habría dejado un fallo
peor: el pivot usa `aggfunc="first"`, así que las filas plegadas habrían perdido
su probabilidad y su muestra.

## Si se relanza

Que sea contra el estado actual: **318 tests**, ruff y mypy limpios, y las cinco
correcciones dentro. Y conviene mirar antes qué hay en `docs/TODO.md`, que era
justamente una de las exclusiones declaradas del encargo.

Lo que sí falta por revisar y ninguna de las cinco versiones ha tocado:

- El rango de temporadas de la extracción y su parametrización (`utils/seasons.py`),
  que es de hoy.
- El SEO más allá de las meta tags — la SPA no tiene URLs indexables por tabla.
- La cobertura de `create_break_even_points.py`, hoy en 65%, y lo que haría falta
  para probar la frontera con SQL Server.
