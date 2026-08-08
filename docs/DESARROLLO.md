# Desarrollo

Cómo trabajar en el proyecto en local. Para la actualización anual del sitio, ver
[OPERACION_ANUAL.md](OPERACION_ANUAL.md).

## Levantar la aplicación

Solo hace falta Python; los datos ya están en `app/data/` como parquets.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r app/requirements.txt -c constraints.txt
pip install -r requirements-dev.txt
```

```bash
uvicorn app.main:app --reload --port 8000
```

**Ojo con el `.env`:** trae `ENVIRONMENT=production`. Con eso puesto, la
aplicación fuerza el redirect a HTTPS y valida el `Host`, así que una petición a
`localhost` acaba en un 307 hacia `www.sabertables.com` y parece que no funciona.
Para trabajar en local, `ENVIRONMENT=development`.

Python local: 3.14. La imagen de producción usa 3.13.

## Los tests

```bash
python -m pytest tests/ -q
```

**668 tests**, con **100% de cobertura** y sin una sola exclusión:

| fichero | qué cubre |
|---|---|
| `test_calculations.py` | La lógica de cálculo de `utils/`, con entradas construidas a mano. |
| `test_data_invariants.py` | Los parquets generados: rangos, esquemas y el **contrato con la aplicación**. |
| `test_api_regression.py` | Rutas, seguridad, paginación, descargas y SEO. |
| `test_main_paths.py` | Rutas de error de la aplicación y lo que solo corre en producción. |
| `test_table_generator.py` | Generación de tablas: multi-temporada y rutas de error. |
| `test_break_even_io.py` | La frontera con SQL Server y la escritura de los parquets. |
| `test_extract_re_data.py` | Tipado, staging y promoción del lote. |
| `test_seasons.py` | El rango de temporadas y que ningún camino se lo salte. |
| `test_db.py`, `test_validators.py`, `test_run_all.py`, `test_sync_sql_views.py` | Conexión, validadores y orquestación. |
| `test_asset_version.py` | El cache-buster derivado del hash de los assets. |

Ninguno abre una conexión a SQL Server: la frontera se prueba sustituyendo
`_read_sql_view` y `pd.read_sql`, que son los dos puntos por los que pasa todo.

### Cómo se escriben los tests aquí

Dos reglas que vienen de haberse quemado:

**1. Validar contra los datos antes de dar una corrección por cerrada.** No basta
con que el test pase; hay que comprobar qué dice el dato real. Más de una
"corrección" resultó no cambiar ningún número, y más de un hallazgo resultó ser
correcto y ya estar cubierto aguas arriba.

**2. Verificar cada test por mutación.** Se rompe el código a propósito y se
comprueba que el test lo detecta. Un test que pasa igual con el código roto no
prueba nada. Esto ha encontrado tests que pasaban por la razón equivocada —por
ejemplo uno que se ejecutaba fuera del arranque de la aplicación y validaba el
respaldo en vez de la derivación—, y un `UnboundLocalError` latente en el cálculo
de estirar el batazo.

Cosas que cubren los tests porque **fallan en silencio**: la paginación de las
once tablas (se rompió entera una vez y nadie se enteró hasta usar el sitio), los
botones de descarga, las dimensiones declaradas del `og:image` frente al PNG
real, y que ningún año de configuración vuelva a escribirse a mano.

## Estilo y tipos

```bash
python -m ruff check .
python -m mypy .
```

Los dos están configurados en `pyproject.toml`, con los ignores **documentados**.
Dos trampas de mypy que costaron encontrar y están escritas allí:

- Los módulos de `utils/` se referencian por su **nombre suelto** (`run_all`), no
  como `utils.run_all`, porque `utils/` no tiene `__init__.py`.
- Un módulo **no puede aparecer en dos `[[tool.mypy.overrides]]`**: mypy descarta
  la configuración entera y deja de silenciar nada, sin decir por qué.

## Cobertura

```bash
python -m pytest tests/ -q --cov=app --cov=utils --cov-report=term
```

**100%**, los dieciséis módulos, sin ningún `pragma: no cover`.

Llegar ahí destapó **ocho trozos de código que no podían ejecutarse nunca** y se
borraron en vez de escribirles tests: tres funciones sin ningún uso, una lista de
tipos duplicada dentro de un endpoint que Pydantic ya validaba, un validador de
rango que las cotas del campo hacían imposible, y seis comprobaciones de columnas
que un `reindex` anterior ya garantizaba. Perseguir el número sirvió sobre todo
para eso.

## El contenedor de prueba

```powershell
.\utils\rebuild-local-test.ps1
```

O a mano:

```bash
docker stop sabertables-local-test; docker rm sabertables-local-test
docker build -t sabertables:local-test .
docker run -d --name sabertables-local-test -e ENVIRONMENT=development -p 3001:8000 sabertables:local-test
docker logs -f sabertables-local-test
```

Queda en `http://localhost:3001`. La imagen ocupa unos 708 MB en disco; el
`.tar` que genera el script de despliegue son ~165 MB, porque las capas van
comprimidas.

Detalles de la imagen y del despliegue, en [OPERACION_ANUAL.md](OPERACION_ANUAL.md).

## Ejecutar solo una parte del pipeline

Todos los scripts aceptan los argumentos comunes (`-v`, `-q`, `--log-file`) y las
tres fechas (`--min-season`, `--min-per-pitch-season`, `--max-season`).

```bash
python utils/extract_re_data.py --dataset re24_values -v
python utils/calculate_pythag_exponents.py
python utils/create_break_even_points.py
python utils/sync_sql_views.py --check      # o --apply
```

`run_all.py` los llama a todos en orden. **Ojo:** los llama como funciones, no
por línea de comandos, así que las tres fechas se fijan una sola vez en su
`main` y valen para los tres.

## Los assets del frontend

`app/static/js/app.js` y `styles.css` se sirven con un cache-buster derivado del
**hash SHA-256** de los propios ficheros, cacheado por mtime y tamaño. No hay que
subir ningún número a mano: cuando se olvidaba, el navegador seguía sirviendo la
versión anterior.

La imagen de tarjeta social se regenera con:

```bash
python utils/build_og_image.py
```

Pillow hace falta **solo** para eso; no entra en la imagen de producción.

## Dónde no escribir años

`utils/seasons.py` es la única fuente de verdad de `MIN_SEASON`,
`MIN_PER_PITCH_SEASON` y `MAX_SEASON`. Hay un test que barre el repositorio y
falla si aparece uno de esos años en código efectivo fuera de ahí.

La **aplicación no puede importar** `utils/seasons.py` —el Dockerfile solo copia
`app/`—, así que **deriva del dato**: el suelo de RE288 sale del `min(season)` del
parquet, y el rango que se muestra en la página y en las meta tags sale del de
re24. Eso es mejor que copiar el número: no hay dos sitios que puedan discrepar.

Hay tres excepciones permitidas, cada una con su motivo escrito en el propio test:
el respaldo de `app/config.py` (que nunca gana al derivado) y las dos vistas SQL
con su suelo de cobertura de localización del batazo.
