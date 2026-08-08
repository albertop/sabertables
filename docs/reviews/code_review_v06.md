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
- Codigo actual de validacion, carga, pipeline derivado, template principal y configuracion SQL.
- Retrosheet event files: https://www.retrosheet.org/eventfile.htm

## Resumen ejecutivo

Esta es una revision nueva de v06, actualizada sobre el mismo archivo. Se evito copiar el contenido anterior y se buscaron riesgos adicionales en contratos de datos, pipeline, seguridad operativa y accesibilidad.

| ID | Severidad | Persona principal | Resumen |
| --- | --- | --- | --- |
| CR-06-01 | Media | Backend Architect | `DGL_SQL_SERVERS` se parsea separando por coma, lo que rompe servidores SQL con puerto `host,1433`. |
| CR-06-02 | Media | QA / Reliability Engineer | `validate_dataframe_schema()` permite DataFrames vacios aunque tengan columnas correctas. |
| CR-06-03 | Media | Baseball/Sabermetrics | El pipeline derivado normaliza estados de corredores con `zfill(3)` sin validar dominio canonico. |
| CR-06-04 | Media | Backend Architect | Las consultas largas de SQL no tienen timeout de sentencia; solo existe timeout de conexion. |
| CR-06-05 | Baja | Frontend Engineer | Las tablas generadas no exponen `caption`/`aria-labelledby`, reduciendo accesibilidad para lectores de pantalla. |
| CR-06-06 | Baja | Security Engineer | La sanitizacion de errores de produccion redacting paths, pero puede seguir filtrando SQL/ODBC/server details. |
| CR-06-07 | Baja | Python Principal Developer | Existe un validador de weighted mean, pero el patron de calculo no queda obligado por contrato reutilizable. |

## Persona 1 - Python Principal Developer

### CR-06-07 - El validador de weighted mean no esta integrado como contrato reusable

Severidad: Baja

Archivos:

- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\validators\data_validator.py:92`

Hallazgo:

`validate_weighted_mean_inputs()` valida casos importantes para promedios ponderados, como `n <= 0`, infinitos y `NaN`. Sin embargo, al vivir como helper independiente, no fuerza que todos los calculos ponderados del proyecto lo usen antes de dividir por pesos.

Riesgo:

Si una consulta o parquet futuro introduce pesos en cero o negativos, el fallo podria aparecer como `inf`, `NaN`, tabla vacia o resultado silenciosamente incorrecto, dependiendo del calculo consumidor. El riesgo es bajo hoy si los invariantes de datos ya lo cubren, pero el helper sugiere una regla de negocio que deberia estar integrada al patron de calculo.

Recomendacion:

Crear una funcion unica tipo `weighted_mean(df, value_col, weight_col="n")` que primero llame al validador y luego calcule. Evitar que cada generador implemente su propia division ponderada. Agregar tests negativos con `n=0`, `n<0` y `NaN`.

Personas relacionadas: QA / Reliability Engineer.

## Persona 2 - QA / Reliability Engineer

### CR-06-02 - `validate_dataframe_schema()` permite datasets vacios

Severidad: Media

Archivos:

- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\validators\data_validator.py:40`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\validators\data_validator.py:57`

Hallazgo:

`validate_dataframe_schema()` verifica columnas requeridas y nulos en esas columnas, pero no valida que el DataFrame tenga filas. Un DataFrame vacio con el schema correcto pasa la validacion.

Riesgo:

Una extraccion SQL que devuelva cero filas, un parquet vacio o un filtro accidentalmente demasiado restrictivo puede pasar el check inicial. El error entonces aparece mas tarde como tabla vacia, 400 generico, excepcion en pivots/aggregations o datos incompletos publicados.

Recomendacion:

Agregar una opcion `allow_empty: bool = False` al validador. Para datasets criticos, fallar con mensaje claro si `df.empty`. Para tablas donde vacio es valido, exigir que el caller lo declare explicitamente.

Personas relacionadas: Backend Architect, Python Principal Developer.

## Persona 3 - Frontend Engineer

### CR-06-05 - Las tablas no tienen `caption` ni relacion accesible con sus titulos

Severidad: Baja

Archivos:

- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\templates\index.html:228`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\templates\index.html:246`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\templates\index.html:607`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\templates\index.html:960`

Hallazgo:

El macro `table_container()` y varias tablas especificas renderizan el titulo visual fuera del elemento `<table>`, pero la tabla no incluye `<caption>` ni `aria-labelledby` apuntando a ese titulo. Visualmente funciona, pero el nombre accesible de la tabla queda pobre para lectores de pantalla.

Riesgo:

Usuarios que navegan con lector de pantalla o por landmarks pueden encontrar varias tablas sin contexto semantico claro, especialmente porque la pagina contiene muchas matrices similares y contenedores que aparecen/desaparecen dinamicamente.

Recomendacion:

Agregar `aria-labelledby="{{ title_id }}"` al `<table>` del macro y equivalentes para tablas custom. Si se prefiere semantica HTML pura, usar `<caption class="visually-hidden">...</caption>` sincronizado con el titulo visible.

Personas relacionadas: QA / Reliability Engineer.

## Persona 4 - Security Engineer

### CR-06-06 - Sanitizacion de errores incompleta para detalles SQL/ODBC

Severidad: Baja

Archivos:

- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\validators\data_validator.py:11`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\validators\data_validator.py:20`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\app\main.py:1467`

Hallazgo:

`sanitize_error_message()` elimina rutas de archivos, line numbers y algunos nombres de columnas, pero no normaliza otros detalles sensibles comunes en errores de produccion: nombres de servidor SQL, driver ODBC, fragmentos de query, DSN, nombres de tablas/vistas o mensajes del proveedor de base de datos.

Attack scenario:

Un atacante provoca errores de API con combinaciones invalidas o carga anomala y observa el mensaje devuelto. Si una excepcion interna atraviesa el sanitizador, puede revelar detalles de infraestructura o modelo de datos.

Exploitation method:

Forzar errores que contengan excepciones de SQLAlchemy/PyODBC, rutas no cubiertas por regex o nombres internos de datasets. El sanitizador actual es regex-based y solo cubre algunos patrones.

Business impact:

Exposicion de informacion tecnica que facilita reconocimiento: nombres de vistas, columnas, drivers, rutas parciales o estructura interna. No es explotacion directa, pero debilita defensa en profundidad.

Recommended fix:

En produccion, devolver mensajes genericos por categoria y registrar el detalle completo solo en logs internos. Mantener allowlist de errores seguros para el usuario. Para excepciones no esperadas, responder algo tipo “Internal server error” con request id, no el string sanitizado de la excepcion.

Personas relacionadas: Backend Architect, QA / Reliability Engineer.

## Persona 5 - Backend Architect

### CR-06-01 - `DGL_SQL_SERVERS` no puede representar correctamente `host,port`

Severidad: Media

Archivos:

- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\utils\db.py:18`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\utils\db.py:42`

Hallazgo:

`_configured_servers()` parsea `DGL_SQL_SERVERS` con `raw.split(",")`. En SQL Server/ODBC, el puerto tambien se expresa con coma, por ejemplo `tcp:db.example.com,1433`. Ese valor se parte accidentalmente en dos servidores: `tcp:db.example.com` y `1433`.

Riesgo:

Una configuracion perfectamente normal para SQL Server remoto puede fallar o, peor, probar un servidor incorrecto antes de fallar. Tambien hace dificil documentar un formato robusto para multiples servidores con puerto.

Recomendacion:

Cambiar el separador de lista a punto y coma, JSON o variable repetible. Por ejemplo: `DGL_SQL_SERVERS=tcp:db1.example.com,1433;tcp:db2.example.com,1433`. Alternativamente, soportar comillas/escaping, aunque JSON suele ser menos ambiguo.

Personas relacionadas: QA / Reliability Engineer, Security Engineer.

### CR-06-04 - Consultas SQL largas sin timeout de sentencia

Severidad: Media

Archivos:

- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\utils\db.py:92`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\utils\create_break_even_points.py:1665`

Hallazgo:

`DGL_SQL_TIMEOUT` se usa como timeout de conexion, pero las consultas largas del pipeline se ejecutan con `pd.read_sql_query(...)` sin timeout de sentencia visible. Si una vista queda bloqueada, cambia el plan de ejecucion o entra en espera por locks, el proceso puede quedarse colgado indefinidamente.

Riesgo:

Jobs manuales o programados de extraccion pueden consumir tiempo operacional sin fallar rapido. Tambien puede bloquear publicaciones de datos nuevos y hacer dificil distinguir entre “consulta lenta” y “pipeline muerto”.

Recomendacion:

Agregar timeout de query/statement en la conexion ODBC o ejecutar `SET LOCK_TIMEOUT`/opciones equivalentes al abrir conexion. Envolver cada dataset con medicion de tiempo y error especifico por vista. Si una consulta supera el SLA, fallar el staging completo para no publicar lotes incompletos.

Personas relacionadas: QA / Reliability Engineer.

## Persona 6 - Baseball / Sabermetrics Expert

### CR-06-03 - Estados de corredores normalizados con `zfill(3)` sin validar dominio

Severidad: Media

Archivos:

- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\utils\create_break_even_points.py:897`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\utils\create_break_even_points.py:1187`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\utils\create_break_even_points.py:1848`
- `C:\Users\aperd\Documents\DGLv2\apps\sabertables\utils\create_break_even_points.py:1931`

Hallazgo:

El pipeline derivado convierte `runners` con `astype(str).str.zfill(3)` en varias rutas. Eso ayuda si SQL devuelve `1` y se necesita `001`, pero tambien puede transformar valores inesperados en cadenas que parecen codigos de estado sin validar que pertenezcan al dominio canonico.

Riesgo sabermetrico:

Los estados de corredores son la llave de RE24/RE288. Un estado mal normalizado contamina lookup, escenarios de tag-up, swing/take, platoon y TTO impact. Como Retrosheet usa codificacion precisa de eventos y estados derivados, es mejor detectar un estado invalido que publicarlo como si fuera valido.

Recomendacion:

Despues de normalizar whitespace/formato, validar contra el set canonico: `000`, `100`, `010`, `001`, `110`, `101`, `011`, `111`, y solo permitir `AVG` en datasets donde este explicitamente documentado. Si aparece otro valor, abortar la generacion derivada con muestra de filas afectadas.

Fuente Retrosheet: https://www.retrosheet.org/eventfile.htm

Personas relacionadas: QA / Reliability Engineer, Python Principal Developer.

## Items no repetidos por estar ya cubiertos

No se duplicaron temas ya marcados como pendientes en `docs/TODO.md`, incluyendo CSP/`innerHTML`, `TrustedHostMiddleware`, limite de body, RE288 minimo 1989, locks de lazy loading, timeout base, payload BREAKEVEN y smoke tests.

Tampoco se reutilizaron como hallazgos principales los issues de v05: guard de `Referer`, bucketing de Win Expectancy, validacion de `average`, lower bounds runtime y timeout temprano del frontend.

## Validacion

No se ejecutaron tests ni comandos de validacion. El encargo fue review-only y no cambios de codigo fuente.
