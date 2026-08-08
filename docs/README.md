# SaberTables

Tablas de sabermetría de las Grandes Ligas, calculadas sobre datos reales de
jugada a jugada. En producción: **[sabertables.com](https://www.sabertables.com)**.

## Los documentos

| documento | para qué |
|---|---|
| **[OPERACION_ANUAL.md](OPERACION_ANUAL.md)** | **Cómo se actualiza el sitio cada año y cómo se construye la imagen.** Es el que hay que seguir paso a paso una vez al año. |
| **[REGLAS_DESARROLLO.md](REGLAS_DESARROLLO.md)** | **Cómo se escribe código aquí:** el ciclo TDD y las cuatro reglas que este proyecto añade. Aplica igual a personas y a asistentes de IA. |
| [DESARROLLO.md](DESARROLLO.md) | Levantar el proyecto en local, correr los tests, el contenedor de prueba. |
| [ANALISIS_CANDIDATOS.md](ANALISIS_CANDIDATOS.md) | Catálogo de análisis propuestos y no hechos, con su estado. |
| [TODO.md](TODO.md) | Defectos, mejoras y decisiones tomadas. Es el registro largo: cada punto lleva lo que se midió y por qué se decidió lo que se decidió. |
| [reviews/](reviews/) | Revisiones externas de código, con el veredicto de cada hallazgo. |

## Qué hace

Publica **18 tipos de tabla** sobre más de un siglo de temporadas: matrices de
run expectancy (RE24 y RE288 por conteo), win expectancy, WPA, leverage index,
pesos lineales, coeficientes de wOBA, constantes de FIP, factores de parque,
exponentes pitagóricos, carreras por victoria, puntos de equilibrio (robo,
tag-up, estirar el batazo, toque, swing/take) e impacto en RE de varias
decisiones tácticas.

Las fuentes son **Retrosheet** (eventos jugada a jugada) y **Statcast**
(lanzamiento a lanzamiento).

## Cómo está montado

```
SQL Server ──► pipeline (utils/) ──► parquets (app/data/) ──► FastAPI (app/) ──► navegador
   vistas         una vez al año        13,8 MB total          en Docker
```

La separación importa: **la web no toca la base de datos**. El pipeline se
ejecuta una o dos veces al año contra SQL Server y deja ficheros parquet; la
aplicación solo los lee. Por eso el sitio responde rápido y por eso una caída de
la base no afecta a producción.

### El pipeline (`utils/`)

| fichero | qué hace |
|---|---|
| `run_all.py` | Orquesta todo en orden. **Es el punto de entrada.** |
| `sync_sql_views.py` | Comprueba (o aplica) el DDL de las vistas de `utils/sql/`. |
| `extract_re_data.py` | Extrae los 16 datasets base y escribe sus parquets. |
| `calculate_pythag_exponents.py` | Ajusta los exponentes pitagóricos y carreras por victoria. |
| `create_break_even_points.py` | Puntos de equilibrio e impacto en RE. Es el grueso del cálculo sabermétrico. |
| `seasons.py` | **Las tres fechas que acotan toda la extracción.** Fuente única. |
| `cli.py`, `db.py` | Argumentos y logging comunes; conexión a la base. |
| `build_og_image.py` | Regenera la imagen de tarjeta social a partir del logo. |
| `sql/` | DDL de las vistas, versionado en el repositorio. |

### La aplicación (`app/`)

FastAPI servido por gunicorn con workers de uvicorn. `main.py` tiene las rutas,
la seguridad (CSP con nonce, validación de Host y Origin, límite de cuerpo,
rate limit) y la carga perezosa de los parquets con lock por dataset.
`services/table_generator.py` arma cada tabla. El frontend es una sola página
con `static/js/app.js`.

## Las tres fechas

Todo lo que se extrae está acotado por tres valores, y viven en **un solo sitio**,
`utils/seasons.py`:

| variable | valor | qué acota |
|---|---|---|
| `MIN_SEASON` | 1910 | primera temporada con eventos en Retrosheet |
| `MIN_PER_PITCH_SEASON` | 1989 | primera con detalle de lanzamiento (conteo, zona) |
| `MAX_SEASON` | 2025 | última temporada **cerrada** |

Se cambian por `.env` o por línea de comandos (`--min-season`,
`--min-per-pitch-season`, `--max-season`). **Subir el techo cada invierno es un
solo cambio**, y es el paso 1 de la operación anual.

Hay dos suelos porque Retrosheet publica los eventos desde 1910 pero el detalle
de cada lanzamiento no existe hasta 1989. Mezclarlos daría tablas con dos décadas
de celdas vacías que parecen ausencia de juego y son ausencia de dato.

Dos vistas son **más estrictas** que ese rango, a propósito y por motivos que no
son el rango general:

- `v_re_analysis_stretch` y `v_re_analysis_advance` arrancan en 1989 por
  cobertura de **localización del batazo** —95,8% ese año frente al 89,9% de 1988
  y el 45% de 1987—. Coincide el año con el suelo de conteo y no el motivo, así
  que no cuelgan de `MIN_PER_PITCH_SEASON`.
- `v_re_analysis_ph_pitchers` termina en **2021**, último año en que los
  lanzadores batearon con regularidad antes del bateador designado universal.

## Estructura del repositorio

```
app/            la aplicación web (es lo único que entra en la imagen Docker)
  data/         los parquets publicados
  static/       js, css, imágenes, robots.txt, sitemap.xml
  templates/    index.html
  services/     generación de tablas
utils/          el pipeline y el DDL de las vistas
tests/          327 tests
docs/           esto
docker_images/  los .tar generados (no sube a git ni a la imagen)
_not_in_use/    lo retirado que no se quiso borrar (no sube a git)
```

## Calidad

```bash
python -m pytest tests/ -q && python -m ruff check . && python -m mypy .
```

**327 tests.** Se trabaja con TDD, y con cuatro reglas más que este proyecto
añade —verificación por mutación, medir antes de escribir el test, validar toda
corrección contra los datos, y cubrir lo que falla en silencio—. Están en
**[REGLAS_DESARROLLO.md](REGLAS_DESARROLLO.md)**, cada una con la cicatriz que
la explica.

Ha valido la pena: así se encontró un `UnboundLocalError` latente en el cálculo
de estirar el batazo, y así se descubrió que cuatro tests que estaban en verde
pasaban por la razón equivocada.
