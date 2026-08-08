# Code Review v06 - SaberTables

Fecha: 2026-08-08

## Alcance aplicado

Raiz revisada: `C:\Users\aperd\Documents\DGLv2\apps\sabertables`.

Exclusiones aplicadas:

- No se revisan nginx ni fail2ban.
- No se incluye Persona 7.
- No se revisa `_not_in_use`.
- No se repiten issues marcados como pendientes en `docs/TODO.md`.
- No se hicieron cambios al codigo fuente; este archivo es el unico entregable.

Referencias usadas:

- `docs/TODO.md`
- Codigo actual de `app/`, `utils/` y configuracion Python relevante.
- Retrosheet event files: https://www.retrosheet.org/eventfile.htm

## Resumen ejecutivo

Este v06 evita repetir el lote reportado en v05 y se enfoca en pipeline, confiabilidad de datos, contrato API y robustez de errores.

| ID | Severidad | Persona principal | Resumen |
| --- | --- | --- | --- |
| CR-06-01 | Media | QA / Reliability Engineer | `_apply_dtypes()` trunca campos `STRING3` con `.str[:3]` en vez de validar el valor original. |
| CR-06-02 | Media | Security Engineer | `utils/db.py` fuerza `TrustServerCertificate=yes` para SQL Server. |
| CR-06-03 | Baja | Frontend Engineer | El frontend asume que todo error HTTP trae JSON y pierde mensajes utiles si llega HTML/texto. |
| CR-06-04 | Baja | Backend Architect | `/api/generate-table` concentra muchos contratos incompatibles en un solo endpoint. |
| CR-06-05 | Baja | Python Principal Developer | Enteros de configuracion leidos desde env pueden tumbar el proceso en import/startup sin mensaje claro. |
| CR-06-06 | Baja | QA / Reliability Engineer | La promocion de staging no exige `last_updated.json` como archivo obligatorio del lote completo. |

## Persona 1 - Python Principal Developer

### CR-06-05 - Configuracion numerica desde env sin validacion defensiva

Severidad: Baja

Archivos:

- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\main.py`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\utils\db.py`

Hallazgo:

Hay valores numericos de configuracion que se convierten con `int(os.getenv(...))`, como `MAX_REQUEST_BODY_BYTES` en la app y `DGL_SQL_TIMEOUT` en los scripts de SQL. Si la variable existe pero viene vacia, con espacios, con texto o con un valor fuera de rango razonable, el proceso puede fallar en arranque o durante conexion con un traceback generico.

Riesgo:

Un error simple de configuracion puede impedir que la app levante o que el pipeline extraiga datos. En operaciones, esto se ve como fallo de runtime en vez de como configuracion invalida y accionable.

Recomendacion:

Centralizar parsing de env en helpers tipo `get_int_env(name, default, min_value, max_value)` que produzcan mensajes claros. Para la app web, validar al startup y loggear el valor efectivo. Para scripts, fallar con `SystemExit` legible si el valor no es valido.

Personas relacionadas: Backend Architect, QA / Reliability Engineer.

## Persona 2 - QA / Reliability Engineer

### CR-06-01 - `_apply_dtypes()` trunca `STRING3` antes de validar

Severidad: Media

Archivos:

- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\utils\extract_re_data.py`

Hallazgo:

En `_apply_dtypes()`, los campos declarados como `STRING3` se convierten con `astype("string").str[:3]`. Eso normaliza cualquier valor largo recortandolo a tres caracteres. Para campos como `runners`, un dato fuente corrupto como `100X`, `111-extra` o un valor con basura al final podria convertirse en un estado aparentemente valido.

Riesgo:

Es un riesgo de corrupcion silenciosa: el pipeline publica parquet limpio en apariencia, pero pierde evidencia de que el origen produjo un estado invalido. En tablas de run expectancy, un estado de corredores mal clasificado afecta denominadores, pesos y promedios.

Recomendacion:

Validar antes de transformar. Para estados de corredores, exigir exactamente `000`, `100`, `010`, `001`, `110`, `101`, `011`, `111` o valores explicitamente permitidos como `AVG` cuando aplique. Si se quiere limpiar whitespace, hacer `.str.strip()` y luego validar longitud/patron; no truncar.

Personas relacionadas: Python Principal Developer, Baseball/Sabermetrics Expert.

### CR-06-06 - `last_updated.json` no es obligatorio en la promocion de staging

Severidad: Baja

Archivos:

- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\utils\extract_re_data.py`

Hallazgo:

`_promote_staging()` valida que existan los archivos esperados del dataset, pero `last_updated.json` se agrega al orden de promocion como opcional: si no existe, se salta. El comentario dice que `last_updated.json` funciona como marca de commit del lote completo, pero la funcion no lo exige.

Riesgo:

Si por una regresion futura se omite o falla la escritura de `last_updated.json` sin abortar antes, el pipeline podria promover datos nuevos sin la marca que la app usa para comunicar frescura. El fallo seria raro, pero la semantica del commit marker queda menos fuerte que lo documentado.

Recomendacion:

Separar los modos `full` y `partial`. En extracciones completas, incluir `last_updated.json` dentro de la lista obligatoria antes de promover. En extracciones parciales, mantenerlo explicitamente fuera y loggear que no se actualiza.

Personas relacionadas: Backend Architect.

## Persona 3 - Frontend Engineer

### CR-06-03 - El frontend asume JSON en todos los errores HTTP

Severidad: Baja

Archivos:

- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\static\js\app.js`

Hallazgo:

En el flujo de `fetch('/api/generate-table')`, cuando `response.ok` es falso, el frontend intenta leer `await response.json()` directamente. Si el error viene de una capa intermedia, de un proxy, de un middleware externo o de una respuesta HTML/text/plain, esa lectura lanza excepcion y se cae al `catch` generico.

Riesgo:

El usuario pierde mensajes diagnosticos utiles. En incidentes reales, 413, 502, 503 o errores HTML pueden mostrarse como un “Failed to generate table” generico, aunque la respuesta tuviera informacion accionable.

Recomendacion:

Leer primero `content-type`. Si incluye `application/json`, parsear JSON. Si no, usar `await response.text()` con un limite razonable de caracteres y mostrar un mensaje seguro. Mantener siempre fallback generico para no exponer HTML completo.

Personas relacionadas: QA / Reliability Engineer.

## Persona 4 - Security Engineer

### CR-06-02 - SQL Server usa `TrustServerCertificate=yes` de forma fija

Severidad: Media

Archivos:

- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\utils\db.py`

Hallazgo:

La cadena ODBC construida por `_build_url()` incluye `TrustServerCertificate=yes` siempre. Eso evita que el cliente valide correctamente el certificado TLS del SQL Server.

Attack scenario:

Si el pipeline se ejecuta contra un SQL Server remoto o en una red no totalmente confiable, un atacante con posicion de red podria interceptar la conexion y presentar un certificado no confiable que igual seria aceptado por el cliente.

Exploitation method:

El atacante realiza MITM entre la maquina que corre `utils/extract_re_data.py` y el servidor SQL. Como el cliente confia en cualquier certificado presentado, la proteccion TLS pierde autenticidad. Si se usa `DGL_SQL_USER` y `DGL_SQL_PASSWORD`, el riesgo aumenta por posible exposicion de credenciales o manipulacion de resultados.

Business impact:

Riesgo de fuga de credenciales de base de datos, extraccion de datos sensibles o publicacion de parquets manipulados que luego la app serviria como fuente de verdad estadistica.

Recommended fix:

Hacer `TrustServerCertificate` configurable y default seguro para entornos remotos. Para desarrollo local puede permitirse por env, pero en CI/produccion deberia usarse validacion TLS real con certificado confiable. Loggear el modo efectivo sin imprimir secretos.

Personas relacionadas: Backend Architect, QA / Reliability Engineer.

## Persona 5 - Backend Architect

### CR-06-04 - Un solo endpoint concentra contratos de muchas tablas

Severidad: Baja

Archivos:

- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\main.py`

Hallazgo:

`/api/generate-table` recibe un `matrix_type` y luego decide dentro del handler que campos aplican para cada tabla. Algunas tablas usan temporada, otras no; unas usan `event_cd`, otras `inning`, otras `average`, otras `breakeven_type`. El contrato real queda repartido entre Pydantic, validaciones condicionales y ramas del handler.

Riesgo:

A medida que se agregan tablas, aumenta la probabilidad de contract mismatches: campos requeridos solo para un tipo, validaciones olvidadas, errores “no data” que deberian ser 422/400 de contrato, y tests que tienen que entender demasiadas combinaciones.

Recomendacion:

Introducir un registro de matrix types con metadata por tabla: parametros requeridos, parametros prohibidos, dataset requerido, funcion generadora, cacheabilidad y mensaje de error. Otra opcion es separar endpoints por familia (`/api/re24`, `/api/re288`, `/api/park-factors`, etc.) si se busca un contrato REST mas explicito.

Personas relacionadas: QA / Reliability Engineer, Frontend Engineer.

## Persona 6 - Baseball / Sabermetrics Expert

### CR-06-01 - Validar estados de corredores antes de normalizarlos

Este hallazgo se reporta una sola vez en QA porque el problema tecnico esta en el pipeline. Desde sabermetria, el punto importante es que los estados base-out son la unidad atomica de muchas tablas del proyecto. Retrosheet documenta formatos de evento con casos especiales historicos y modernos; por eso es preferible fallar duro ante estados inesperados en vez de convertirlos a algo que parece valido.

Recomendacion especifica de dominio:

Agregar invariantes de dominio para `runners`, `outs`, `count`, `inning`, `RUNS_DIFF` y codigos de evento agregados. Para `runners`, no truncar; validar contra el set canonico antes de publicar parquet.

Fuente Retrosheet: https://www.retrosheet.org/eventfile.htm

## Items no repetidos por estar ya cubiertos en `docs/TODO.md` o v05

No se duplicaron temas ya marcados como pendientes en `docs/TODO.md`, incluyendo CSP/`innerHTML`, `TrustedHostMiddleware`, limite de body, RE288 minimo 1989, locks de lazy loading, timeout base, payload BREAKEVEN y smoke tests. Tampoco se reutilizo como hallazgo principal el lote de v05: guard de `Referer`, bucketing de Win Expectancy, validacion de `average`, lower bounds runtime y timeout temprano del frontend.

## Validacion

No se ejecutaron tests ni comandos de validacion. El encargo fue review-only y no cambios de codigo fuente.
