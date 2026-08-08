# Code Review v04 - SaberTables

Fecha: 2026-08-08

## Alcance aplicado

Este review usa como raiz del proyecto `C:\Users\aperd\Documents\DGLv2\apps\sabertables`.

Exclusiones solicitadas:

- No se revisan nginx ni fail2ban.
- No se incluye Persona 7.
- No se revisa `_not_in_use`.
- No se repiten issues que ya estan en `docs/TODO.md` como pendientes.
- No se hicieron cambios de codigo fuente; este archivo es el unico entregable.

Fuentes revisadas antes del analisis:

- `docs/TODO.md`
- Codigo actual de `app/`, `utils/`, tests y configuracion Python relevante.
- Nomenclatura oficial de Retrosheet event files: https://www.retrosheet.org/eventfile.htm

## Resumen ejecutivo

Se encontraron 5 hallazgos nuevos no cubiertos como pendientes en `docs/TODO.md`.

| ID | Severidad | Persona principal | Resumen |
| --- | --- | --- | --- |
| CR-04-01 | Media | Frontend Engineer | El timeout/AbortController se inicializa antes de validaciones tempranas y puede quedar vivo sin `finally`. |
| CR-04-02 | Media | Security Engineer | `verify_origin` acepta `Referer` por prefijo y puede relajar el guard cuando `Origin` falla. |
| CR-04-03 | Media | Baseball/Sabermetrics | Win Expectancy no agrupa `RUNS_DIFF` en colas `-4+`/`+4+` como WPA/LI. |
| CR-04-04 | Baja | Backend Architect | `average` para Park Factors no esta validado como contrato API `1/3/5`. |
| CR-04-05 | Baja | Python Principal Developer | Dependencias runtime estan expresadas solo con lower bounds, sin lock/reproducibilidad fuerte. |

## Persona 1 - Python Principal Developer

### CR-04-05 - Dependencias runtime con lower bounds abiertos

Severidad: Baja

Archivos:

- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\requirements.txt:1`

Hallazgo:

El archivo de dependencias runtime usa solamente restricciones tipo `>=` para componentes criticos como FastAPI, Uvicorn, Gunicorn, Pandas, PyArrow, SQLAlchemy y ORJSON. Esto deja que builds futuros incorporen versiones mayores sin revision explicita.

Riesgo:

Un deploy o rebuild puede cambiar comportamiento de validacion, serializacion, parseo Parquet, middleware ASGI o compatibilidad de drivers sin que haya cambios en el repo. En una app orientada a tablas estadisticas, una regresion silenciosa de Pandas/PyArrow puede afectar resultados o performance.

Recomendacion:

Usar un lock file o constraints file para runtime, por ejemplo `requirements.txt` generado desde `requirements.in`, `uv.lock`, `pip-tools`, Poetry o equivalente. Mantener ventanas de upgrade deliberadas y probadas en CI.

Personas relacionadas: QA / Reliability Engineer, Security Engineer, Backend Architect.

## Persona 2 - QA / Reliability Engineer

### CR-04-01 - El timeout del frontend puede quedar vivo tras validaciones tempranas

Severidad: Media

Archivos:

- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\static\js\app.js:1329`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\static\js\app.js:1351`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\static\js\app.js:1381`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\static\js\app.js:1893`

Hallazgo:

`handleGenerateTable()` crea el `AbortController` y programa `setTimeout()` antes de varias validaciones que pueden hacer `return` antes de entrar al bloque `try/finally`. Por ejemplo, si `fromSeason > toSeason` o si una tabla RE288 se solicita antes de 1989, la funcion retorna antes del `finally` que limpia el timeout.

Riesgo:

Cada validacion fallida puede dejar timers pendientes durante 45 segundos. Cuando esos timers disparan, abortan un controller ya obsoleto y modifican estado como `abortadoPorTimeout`. Normalmente no rompe el flujo feliz, pero introduce fugas pequenas, estado residual y comportamiento dificil de reproducir si el usuario corrige rapido y vuelve a intentar.

Recomendacion:

Mover la creacion del `AbortController` y del `setTimeout()` despues de todas las validaciones sincrónicas que pueden retornar. Alternativamente, envolver toda la funcion despues del `isLoading = true` en un `try/finally` unico que garantice limpieza incluso en retornos tempranos.

Personas relacionadas: Frontend Engineer.

## Persona 3 - Frontend Engineer

### CR-04-01 - El timeout del frontend puede quedar vivo tras validaciones tempranas

Este hallazgo se documenta una sola vez en la seccion de QA porque el impacto principal es confiabilidad de UI. Desde frontend, la misma correccion recomendada aplica: no iniciar recursos abortables hasta que la request vaya a ejecutarse, o garantizar cleanup con `finally` para todos los retornos.

## Persona 4 - Security Engineer

### CR-04-02 - `verify_origin` acepta `Referer` por prefijo y relaja el guard si `Origin` falla

Severidad: Media

Archivos:

- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\main.py:985`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\main.py:998`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\main.py:1006`

Hallazgo:

`verify_origin()` calcula `origin_ok` con match exacto contra `ALLOWED_ORIGINS`, pero calcula `referer_ok` con `referer.startswith(allowed)`. Ademas, acepta la request si cualquiera de los dos es verdadero. Eso significa que un `Origin` invalido podria quedar compensado por un `Referer` con prefijo parecido al dominio permitido.

Riesgo:

El guard de origen es una defensa anti-abuso/costo, no una autenticacion fuerte. Aun asi, el uso de `startswith()` para URLs es fragil porque dominios como `https://sabertables.com.ejemplo.invalid/...` empiezan con `https://sabertables.com`. Tambien es mas seguro rechazar cuando `Origin` esta presente y no coincide, en vez de permitir que `Referer` lo salve.

Recomendacion:

Parsear `Referer` con `urllib.parse.urlparse()` y comparar exactamente `scheme://netloc` contra los origenes permitidos. Si `Origin` existe y no esta permitido, rechazar sin fallback a `Referer`. Usar `Referer` solo como fallback cuando `Origin` falta.

Personas relacionadas: Backend Architect, QA / Reliability Engineer.

## Persona 5 - Backend Architect

### CR-04-04 - `average` de Park Factors no esta validado como contrato API

Severidad: Baja

Archivos:

- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\main.py:901`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\main.py:921`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\main.py:1332`

Hallazgo:

El modelo `TableRequest` declara `average: int | None`, pero el comentario y el comportamiento esperado para Park Factors son ventanas `1`, `3` o `5`. La ruta solo verifica que `average is not None`; cualquier entero llega al generador y falla indirectamente como “no data”.

Riesgo:

El contrato real de la API no queda expresado en Pydantic ni en OpenAPI. Clientes externos pueden enviar valores invalidos y recibir errores de datos en vez de errores de validacion. Tambien dificulta tests negativos precisos.

Recomendacion:

Modelar `average` como `Literal[1, 3, 5] | None` o agregar un validator especifico que solo aplique cuando `matrix_type == "PARK_FACTORS"`. Devolver 422/400 con mensaje de contrato, no “no data”.

Personas relacionadas: QA / Reliability Engineer, Frontend Engineer.

## Persona 6 - Baseball / Sabermetrics Expert

### CR-04-03 - Win Expectancy no agrupa colas de diferencial de carreras como WPA/LI

Severidad: Media

Archivos:

- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\services\table_generator.py:1133`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\services\table_generator.py:1137`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\services\table_generator.py:1313`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\services\table_generator.py:1488`

Hallazgo:

`generate_win_expectancy_table()` pivotea directamente por `RUNS_DIFF` y solo etiqueta `-4` como `-4+` y `4` como `+4+`. En cambio, `generate_wpa_by_inning_table()` y `generate_li_by_inning_table()` hacen `clip(lower=-4, upper=4)` antes de agrupar.

Riesgo:

Si existen estados con diferencial menor a -4 o mayor a +4, Win Expectancy puede producir columnas adicionales como `-5`, `-6`, `5` o `6`, o dejar la cola mal agregada. Eso hace que WE sea inconsistente con WPA/LI y puede distorsionar la lectura sabermetrica de estados extremos.

Recomendacion:

Aplicar el mismo bucketing antes del calculo de WE:

```python
df["RUNS_DIFF_BUCKET"] = df["RUNS_DIFF"].clip(lower=-4, upper=4)
```

Luego agrupar/pivotear por el bucket y etiquetar `-4`/`4` como colas. Agregar test con diferenciales `-5` y `5` para asegurar que se agregan en `-4+` y `+4+`.

Personas relacionadas: Backend Architect, QA / Reliability Engineer.

## Nota Retrosheet

La referencia oficial de Retrosheet confirma que los event files son registros lineales separados por comas y que existen casos especiales relevantes para parsers historicos: juegos donde el home batea primero, registros `radj` para corredores colocados por regla de desempate desde 2020, y el caso desde 2022 donde un pitcher puede aparecer tambien como DH. No vi un hallazgo nuevo suficientemente accionable en el codigo revisado que no dependa de archivos upstream fuera de este alcance, pero estas reglas deberian mantenerse como fixtures/invariantes si el proyecto incorpora parsing Retrosheet directamente.

Fuente: https://www.retrosheet.org/eventfile.htm

## Items no repetidos por estar ya cubiertos en `docs/TODO.md`

No se duplicaron los siguientes temas porque ya aparecen resueltos, descartados o pendientes como hardening en `docs/TODO.md`:

- CSP, `unsafe-inline`, nonce de scripts e `innerHTML` como hardening pendiente.
- `TrustedHostMiddleware` como defense-in-depth pendiente.
- Limite de request body.
- RE288 y temporada minima 1989.
- Validadores duplicados de temporadas.
- Locks de carga lazy de datasets.
- Timeout base del frontend.
- Filtrado de payload BREAKEVEN.
- Smoke tests existentes para rutas principales.
- Proxy trusted IPs ya documentado en reviews previos.

## Validacion

No se ejecutaron tests ni comandos de validacion. El encargo fue review-only y no cambios de codigo fuente.
