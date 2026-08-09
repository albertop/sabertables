# Plan: Base Running incompleto y rombos ausentes

**Estado: propuesta. Nada de esto está aplicado.**

## Los tres problemas, medidos

### 1. Base Running tiene 2 filas en vez de 5

Medido en los parquets:

| tabla | escenarios |
|---|---|
| **Break-Even / Base Running** | Single stretched to double · Double stretched to triple · Runner on 1B to 3B on a single · Runner on 2B scores on a single · Runner on 1B scores on a double |
| **RE Impact / Base Running** | Single stretched to double · Double stretched to triple |

**Causa.** En Break-Even, `running` se arma sumando **dos** vistas:

```
running_rows = load_stretch_...() + load_advance_...()
```

Al hacer la versión en carreras solo enganché la de **stretch**. Las tres filas
que faltan son las de `v_re_analysis_advance` —avanzar una base de más en un
batazo ajeno—, y nunca llegué a llamarla.

### 2. Base Running no dibuja el rombo

`running_runs` usa el texto como escenario, no un estado de bases. En estirar el
batazo eso es correcto —el que corre es el bateador, no hay corredores previos—,
pero **avanzar una base de más SÍ tiene estado**: hay un corredor en primera o en
segunda cuando otro batea.

Break-Even / Base Running sí pinta rombos, así que esa tabla obtiene el estado de
algún sitio. **No sé de dónde, y es lo primero que hay que averiguar** — no lo
supongo, lo miro.

### 3. Rompí el test que cuadra las dos tablas

Al cambiar `scenario` a estado de bases en `re_impact`, dejó de cruzar con
`breakeven_points`, que sigue con el texto. Dos tests fallan ahora mismo:

```
test_el_break_even_se_reconstruye_desde_las_carreras[steal_runs-stealing]
test_el_break_even_se_reconstruye_desde_las_carreras[tagging_runs-tagging]
```

No es un fallo nuevo de datos: es que la clave del cruce ya no existe. Pero deja
sin vigilancia el invariante `BE = coste / (ganancia + coste)`, que es el que
detectó el fallo del doble robo.

## Lo que propongo

### Paso 0 — HECHO. Y cambia el resto del plan.

Break-Even **no saca el estado de bases del parquet**: lo tiene en una tabla en
el frontend, `BREAKEVEN_SCENARIOS` (`app/static/js/app.js:5666`), con una entrada
por escenario:

```js
running: [
  { key: 'Single stretched to double',      runners: '100', ... },
  { key: 'Double stretched to triple',      runners: '010', ... },
  { key: 'Runner on 1B to 3B on a single',  runners: '110', ... },
  { key: 'Runner on 2B scores on a single', runners: '101', ... },
  { key: 'Runner on 1B scores on a double', runners: '011', ... },
]
```

Esa tabla hace **dos** cosas: da el `runners` para el rombo y **define el
conjunto de filas** — por eso Break-Even enseña cinco.

**Consecuencia: mi cambio de `scenario` a estado de bases sobra y hay que
deshacerlo.** El mapeo ya existe, está en un solo sitio y es el que usa la tabla
que funciona. Duplicarlo en el pipeline fue reinventar la rueda otra vez.

### Paso 1 — Enganchar la vista de avance

`load_advance_rows_from_db()` que devuelva las dos familias de una consulta,
igual que se hizo con stretch y tag-up, y sumar sus filas de carreras a
`running_runs`. Con eso Base Running pasa de 2 a 5 filas y cuadra con
Break-Even.

### Paso 2 — Deshacer mi cambio de `scenario` y reusar el mapeo que ya existe

- En el pipeline, `scenario` vuelve a ser el **texto**, igual que en Break-Even.
  Eso restaura de paso el cruce entre las dos tablas (paso 3 desaparece).
- En el frontend, los tres tipos `*_runs` leen `runners` de
  `BREAKEVEN_SCENARIOS`, exactamente como hace Break-Even. Cero mapeos nuevos.

### Paso 3 — Ya no hace falta

Al volver `scenario` al texto, el cruce con `breakeven_points` funciona solo y
los dos tests de la identidad vuelven a pasar sin tocarlos.

### Paso 4 — Tests, antes de tocar el código

- Que **cada análisis tiene las MISMAS filas en las dos tablas**. Es el test que
  habría cazado el 5 contra 2 sin que tú lo vieras.
- Que el rombo se dibuja en los análisis con estado de bases —ya existe—, con
  `running_runs` incluido tras el paso 1.
- Restaurar el cruce de la identidad con la clave nueva.

### Paso 5 — Regenerar, reconstruir y verificar en el navegador

Y esta vez comparo **las dos tablas lado a lado** antes de decirte nada.

## Lo que NO propongo

No tocar Break-Even. Funciona y es la referencia contra la que se compara.

## Por qué se me escapó

Mis tests comprueban que cada tabla es coherente **consigo misma**. Ninguno
compara una tabla con la otra, y estas dos son dos vistas del mismo cálculo: si
una tiene cinco escenarios y la otra dos, una de las dos está incompleta. Ese
test es el paso 4 y es el que faltaba desde el principio.
