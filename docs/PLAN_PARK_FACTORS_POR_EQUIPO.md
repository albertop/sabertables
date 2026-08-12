# Plan: factores de parque por equipo sin tocar producción

**Estado: propuesta. El rollback ya está hecho.**

## Qué pasó

Cambié `dbo.retroParkFactors` para añadirle `HOME_TEAM_ID`, que es lo que hace
falta para el wRC+. La tabla pasó de 7.524 a 7.737 filas.

Eso habría roto la tabla de Park Factors de la aplicación **en el siguiente
pipeline**. No por duplicados simples, sino por un **producto cartesiano**: la
app ya cruza con `ballparks`, que tiene una fila por equipo, así que un estadio
compartido pasaba de dos filas a cuatro, y dos de ellas emparejaban a un equipo
con el factor del otro. Medido: 1914 de 17 a 21 filas, 1964 de 20 a 22. 2025 no
se veía afectado.

Revertido. La tabla está otra vez en 7.524 filas y la consulta del pipeline
devuelve exactamente los mismos valores que el parquet en producción —diferencia
numérica máxima 0,0—.

## El error de diseño, que es el que hay que no repetir

**Modifiqué en sitio una tabla de la que depende producción.** Lo que se quería
—factores por equipo— es información *nueva*, y la información nueva no tiene por
qué entrar deformando la que ya funciona.

## El plan

La regla que lo gobierna: **`retroParkFactors` y `parkFactors.parquet` no se
tocan.** Ni una columna. Si al terminar el plan esos dos objetos son distintos de
como están hoy, el plan se hizo mal.

### Paso 1 — Tabla nueva, nombre nuevo — HECHO (12 ago 2026)

`dbo.retroParkFactorsByTeam` creada y **vacía**; se llena en el paso 2. El DDL
está en `retrosheet/scripts/retroParkFactorsByTeam_ddl.sql` —fuera de este
repositorio, como el resto de DDL de tablas base—.

Comprobado justo después de crearla: `retroParkFactors` sigue con 7.524 filas y
sus diez columnas, la consulta del pipeline devuelve lo mismo sin un duplicado, y
los 6 tests del paso 4 siguen verdes.

La clave es `(PARK_ID, SEASON, HOME_TEAM_ID, SEASONS_AVG)` —el equipo dentro, que
es lo que permite las dos filas de un estadio compartido— y un `CHECK` exige
`SEASONS_EFF <= SEASONS_AVG`.

### Paso 2 — El script R escribe DOS ficheros — HECHO (12 ago 2026)

Hecho en `retrosheet/scripts/15_generate_park_factors.R`, y la tabla nueva ya está
cargada con sus 7.737 filas.

**El heredado sale idéntico: 52.668 de 52.668 celdas.** La diferencia de 7,7×10⁻⁸
que aparecía al comparar no era un cambio de cálculo — la columna `PF_R` en SQL
Server es `real`, o sea `float32`, así que lo guardado ya venía redondeado a 32
bits y el CSV lo trae en 64. Al comparar en la misma precisión, coinciden todas.

**Y el por-equipo tiene los MISMOS valores que el heredado, diferencia máxima
0,0.** Las dos filas de Fenway 1914 —Red Sox y Braves— llevan el mismo `PF_R` de
0,9555, que es lo correcto: el estadio era uno. Lo que cambia entre equipos no
sería el parque sino su calendario de carretera.

Clave del diseño: las medias móviles se calculan sobre **una** fila por parque y
temporada —la de más juegos locales, igual que el script original— y el equipo se
pega después con un join. Si la serie llevara dos filas en las temporadas de
estadio compartido, las ventanas promediarían sobre más filas y todos los valores
multi-año de ese parque cambiarían.

Verificado tras la carga: `retroParkFactors` sigue en 7.524 filas con sus diez
columnas, la consulta del pipeline devuelve lo mismo sin duplicados, y los tests
del paso 4 siguen verdes. **No se recargó**: sus valores ya eran idénticos.

`park_factors_v2.R` ya calcula todo lo que hace falta. Solo tiene que emitir:

- `retroParkFactors.csv` — **idéntico al de hoy**, mismas columnas y mismas filas.
- `retroParkFactorsByTeam.csv` — el nuevo, con el equipo y la ventana efectiva.

Y la comprobación que cierra el paso: el primero tiene que salir **byte a byte
igual** al que produce el script actual. Si no, no se sigue.

### Paso 3 — Parquet nuevo, consulta nueva

`QUERY_PARK_FACTORS` no se toca. Se añade `QUERY_PARK_FACTORS_BY_TEAM` y su
`parkFactorsByTeam.parquet`. Dos rutas separadas que no se cruzan.

### Paso 4 — La prueba que habría cazado esto sola

Un test sobre la salida del pipeline, no sobre la base:

- cero duplicados en `(PARK_ID, SEASON, SEASONS)` en `parkFactors.parquet`;
- y **el mismo número de filas por temporada que hoy**, comparado contra una
  instantánea guardada.

Este segundo es el que importa. El fallo no fue que hubiera duplicados —la app ya
repite parques compartidos a propósito—, fue que el número de filas cambió. Solo
contar filas por temporada lo detecta.

Se añade **antes** de tocar nada, y se comprueba que se pone rojo con la tabla
nueva, que ya sabemos que es el caso.

### Paso 5 — El wRC+ consume el parquet nuevo

Con el enlace equipo-parque disponible, la vista `24_v_park_factor_by_team_season`
deja de hacer falta: el dato llega ya resuelto desde el script R.

### Paso 6 — Unificar: NO, o al menos no ahora

Tentador dejar una sola tabla. No compensa: la única ganancia son 213 filas
duplicadas de espacio, y el coste es volver a poner producción en el camino de un
cambio de esquema.

## Lo que NO hay que hacer

- Regenerar `parkFactors.parquet` mientras el paso 4 no esté verde.
- Cambiar `QUERY_PARK_FACTORS`.
- Añadir columnas a `retroParkFactors`.

## Copia de seguridad — ya no existe (12 ago 2026)

`dbo.retroParkFactors_bak_20260812` se borró tras comprobar que era **idéntica**
a la tabla actual: mismas columnas, mismas claves, diferencia 0,0. No protegía
nada que la tabla viva no tuviera ya.

Y si hubiera que rehacerla, el camino existe: `retroParkFactors.csv` regenera
esos valores exactos, verificado celda a celda (52.668 de 52.668).
