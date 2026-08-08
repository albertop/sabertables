# Code Review v02

## Alcance y fuentes

- Proyecto: `C:/Users/aperd/Documents/DGLv2/apps/sabertables`
- Documentacion local leida bajo `/docs`: `docs/TODO.md`
- Fuente externa consultada para nomenclatura Retrosheet: https://www.retrosheet.org/eventfile.htm
- No se reviso `_not_in_use`.
- No se modifico codigo fuente.
- No se reviso la Persona 7 (`Docker/nginx/fail2ban`) porque los archivos de esa area estan desactualizados y no representan las versiones en uso.
- No se re-listan pendientes ya anotados en `docs/TODO.md`, especialmente:
  - hardening de CSP/`unsafe-inline` e `innerHTML`
  - activacion de `TrustedHostMiddleware`
  - hallazgos ya resueltos/refutados de `code_review_v01`

## Findings

### 1. El limite de request body de 1 MB no esta aplicado en la app FastAPI

- Personas: Security Engineer, Backend Architect, QA/Reliability
- Severidad: Alta
- Ubicacion:
  - [app/main.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/main.py#L523)
- Evidencia:
  - La app se instancia con `max_request_size=1_000_000`, comentado como proteccion DoS.
  - No se ve en la app ningun middleware propio que corte por `Content-Length` o por bytes leidos.
- Riesgo:
  - El limite de body queda como una suposicion de configuracion, no como una proteccion observable en la aplicacion.
- Escenario de ataque:
  - Un cliente envia bodies grandes o muchas cargas grandes a `/api/generate-table`; aunque Pydantic rechace el JSON al final, el servidor ya gasto I/O, memoria y worker time leyendo la peticion.
- Impacto de negocio:
  - Degradacion o caida del servicio publico en una app sin autenticacion.
- Fix recomendado:
  - Implementar middleware ASGI de limite de body en la app y agregar test con payload >1 MB.

### 2. La validacion backend de temporada minima RE288 no cubre `RE288_FREQ` ni `RE288_TRANS`

- Personas: QA/Reliability, Backend Architect, Baseball/Sabermetrics
- Severidad: Media
- Ubicacion:
  - [app/main.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/main.py#L767)
  - [app/main.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/main.py#L1083)
  - [app/main.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/main.py#L1088)
  - [app/static/js/app.js](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/static/js/app.js#L1337)
- Evidencia:
  - El frontend bloquea `RE288`, `RE288_FREQ` y `RE288_TRANS` antes de 1988.
  - El backend solo aplica `RE288_MIN_SEASON` cuando `matrix_type == "RE288"`.
- Riesgo:
  - Llamadas directas a la API pueden saltarse una regla sabermetrica que la UI considera obligatoria.
- Impacto de negocio:
  - Respuestas 400 menos claras, inconsistencias API/UI, o tablas basadas en rangos sin cobertura real de conteo.
- Fix recomendado:
  - Cambiar la condicion backend a un set: `{"RE288", "RE288_FREQ", "RE288_TRANS"}` y cubrirlo con tests de API para los tres tipos.

### 3. `BREAKEVEN_POINTS` y `RE_IMPACT` envian datasets completos al browser y agregan del lado cliente

- Personas: Backend Architect, Frontend Engineer, QA/Reliability
- Severidad: Media
- Ubicacion:
  - [app/main.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/main.py#L964)
  - [app/main.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/main.py#L985)
  - [app/static/js/app.js](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/static/js/app.js#L5862)
  - [app/static/js/app.js](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/static/js/app.js#L6763)
- Evidencia:
  - El backend manda `df.to_dict(orient='records')` completo y el frontend filtra temporadas y calcula promedios ponderados.
- Riesgo:
  - Al crecer temporadas, escenarios o splits, el primer render paga todo el costo de transferencia, parseo JSON y agregacion en el hilo principal del browser.
- Impacto de negocio:
  - UX lenta en mobile, timeouts percibidos, mayor costo de ancho de banda y mas memoria por tab.
- Fix recomendado:
  - Mover el filtrado/agregacion por rango de temporadas al backend y dejar al cliente solo el estado de vista/paginacion. Si se mantiene cliente-side por interactividad, enviar solo el subconjunto del `analysis_type` y rango seleccionado.

### 4. Lazy loading global sin bloqueo de concurrencia

- Personas: Backend Architect, QA/Reliability, Python Principal Developer
- Severidad: Media
- Ubicacion:
  - [app/main.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/main.py#L239)
  - [app/main.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/main.py#L249)
- Evidencia:
  - `ensure_data_loaded()` revisa `data_store[dataset] is not None` y luego carga parquet sin lock.
- Riesgo:
  - Dos requests simultaneos al mismo dataset frio pueden cargar el mismo parquet a la vez dentro del mismo worker. En varios workers, cada proceso tambien mantiene su propia copia.
- Impacto de negocio:
  - Picos de memoria y latencia en cold start o despues de deploy, especialmente con datasets grandes como WE/WPA/LI.
- Fix recomendado:
  - Usar un lock por dataset dentro del proceso, precalentar los datasets mas pesados en startup si memoria lo permite, o mover cache a una capa explicita con control de concurrencia.

### 5. La request del frontend no tiene timeout propio

- Personas: Frontend Engineer, QA/Reliability
- Severidad: Media
- Ubicacion:
  - [app/static/js/app.js](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/static/js/app.js#L1298)
  - [app/static/js/app.js](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/static/js/app.js#L1525)
  - [app/gunicorn.conf.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/gunicorn.conf.py#L7)
- Evidencia:
  - Se usa `AbortController` para cancelar requests anteriores, pero no hay timer que aborte una request colgada.
  - Gunicorn tiene `WORKER_TIMEOUT`, pero ese limite no produce una UX controlada del lado cliente.
- Riesgo:
  - Ante red degradada o worker colgado, el boton queda en estado loading hasta que el navegador falle la conexion.
- Impacto de negocio:
  - Camino UX roto: el usuario no sabe si esperar, reintentar o cambiar parametros.
- Fix recomendado:
  - Envolver el `fetch` con timeout (por ejemplo 30-45s), abortar la request, restaurar UI y mostrar mensaje accionable.

### 6. Hay dos validadores de temporada y uno esta muerto

- Personas: Python Principal Developer, Backend Architect
- Severidad: Baja
- Ubicacion:
  - [app/main.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/main.py#L730)
  - [app/validators/data_validator.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/validators/data_validator.py#L42)
- Evidencia:
  - `app/main.py` define y usa su propio `validate_season_range`.
  - `app/validators/data_validator.py` define otro `validate_season_range`, pero no aparece usado por la app.
- Riesgo:
  - Las reglas divergen con facilidad. De hecho, la regla RE288 vive duplicada y debe cambiarse en mas de un sitio si se decide cubrir `RE288_FREQ`/`RE288_TRANS`.
- Fix recomendado:
  - Dejar un solo validador compartido y agregar casos unitarios por `matrix_type`.

### 7. El smoke test dice cubrir todos los `matrix_type`, pero excluye varios tipos parametrizados

- Personas: QA/Reliability, Backend Architect
- Severidad: Baja
- Ubicacion:
  - [tests/test_api_regression.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/tests/test_api_regression.py#L97)
  - [app/main.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/main.py#L706)
- Evidencia:
  - El test `test_todos_los_matrix_types_responden` solo cubre tipos sin parametros extra.
  - Quedan fuera de ese barrido varios contratos con parametros obligatorios (`RE288_TRANS`, `PARK_FACTORS`, `WIN_EXPECTANCY`, `RE24_TRANSITIONS`, `BREAKEVEN_POINTS`, `RE_IMPACT`, etc.).
- Riesgo:
  - Cambios en validacion o payload pueden romper tablas completas sin que el smoke test principal lo note.
- Fix recomendado:
  - Renombrar el test a "tipos simples" y agregar matriz parametrizada con payload minimo valido para cada tipo restante.

## Notas sabermetricas

- Retrosheet documenta casos que conviene mantener como fixtures de regresion: `radj` para corredor automatico en extras, `badj/padj` para ajustes de mano, `htbf` para juegos donde el home team batea primero, y el caso 2022+ donde el pitcher tambien puede figurar como DH.
- No encontre un nuevo bug sabermetrico bloqueante que no este ya trabajado en `docs/TODO.md`. La recomendacion principal es convertir esos casos Retrosheet raros en tests de invariantes si el pipeline upstream que genera `r24Events`/`v_retroAllEventsRegAndPost` esta bajo control del proyecto.

## Resumen por persona

- Python Principal Developer: findings 4, 6.
- QA/Reliability Engineer: findings 1, 2, 4, 5, 8, 9, 10.
- Frontend Engineer: findings 3, 5.
- Security Engineer: finding 1.
- Backend Architect: findings 1, 2, 3, 4, 6, 7.
- Baseball/Sabermetrics: finding 2 y notas sabermetricas.
- Persona 7 (`Docker/nginx/fail2ban`): fuera del alcance de esta version porque los archivos estan desactualizados.
