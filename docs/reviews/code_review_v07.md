# code_review_v07

## Alcance
- Revisión del proyecto con foco en pruebas automatizadas y su cobertura de regresiones.
- No se realizaron cambios de código, solo revisión.
- Se omitieron explícitamente hallazgos listados como pendientes en `docs/TODO.md` según la revisión ejecutada previamente.

## Hallazgos por persona

### 1. Python Principal Developer

- [MEDIO] `tests/test_data_invariants.py` tiene demasiados caminos que se `skipean` por ausencia de datos de entrada (`pytest.skip` en múltiples pruebas), lo que permite que una ejecución de CI sin artefactos generados salga “verde” con cobertura de calidad muy reducida.
  - Archivo: [tests/test_data_invariants.py](C:\Users\aperd\Documents\DGLv2\apps\sabertables\tests\test_data_invariants.py)
  - Referencias concretas: líneas `71`, `110`, `125`, `157`, `253`, `337`, `342`, `374`, `384`, `421`, `471`, `506`, `749`.
  - Riesgo: regresa a verde aunque fallos reales en invariantes de dataset no se ejecuten en escenarios de entorno limpio.
  - Acción recomendada: reemplazar `skip` por fixtures locales que fabrican datos sintéticos mínimos y mantener la suite ejecutable sin dependencias externas pesadas.

- [BAJO] La suite mezcla validaciones de contrato de retorno (`status_code`) con pocas comprobaciones de estructura de error en varios casos, especialmente cuando se esperan respuestas de validación.
  - Archivo: [tests/test_main_paths.py](C:\Users\aperd\Documents\DGLv2\apps\sabertables\tests\test_main_paths.py)
  - Referencias concretas: líneas de negativas de validación como `183`, `197`, `228`, `299`, `738`, `746`, `759`.
  - Riesgo: regresiones silenciosas en el formato de payload de error no detectadas por la regresión principal.
  - Acción recomendada: agregar esquemas JSON (`schema`) y asserts estructurados de `error`/`message` para los 400/422/503 en lugar de solo `status_code`.

### 2. QA / Reliability

- [ALTO] En tests de entorno se detectan cambios de configuración global que pueden filtrar estado entre casos. Las pruebas de producción se apoyan en mutaciones de entorno en caliente con efecto persistente sobre pruebas posteriores (comentarios en ambos módulos), lo que puede hacer la suite dependiente del orden.
  - Archivos: [tests/test_api_regression.py](C:\Users\aperd\Documents\DGLv2\apps\sabertables\tests\test_api_regression.py), [tests/test_main_paths.py](C:\Users\aperd\Documents\DGLv2\apps\sabertables\tests\test_main_paths.py)
  - Referencias: líneas `322` y `628`.
  - Riesgo: falsos positivos en ejecuciones paralelas o cuando cambia el orden de tests.
  - Acción recomendada: evitar estado global persistente (usar fixtures de contexto que restauren siempre variables y objetos del `app`), y añadir prueba explícita de aislamiento por caso.

- [MEDIO] Falta cobertura cruzada de casos de carga concurrente: hoy solo hay una prueba puntual de lectura en paralelo de dataset, pero no se extiende a los endpoints de mayor costo (p. ej. `RE24`, `RE288_TRANS`, `BREAKEVEN_POINTS`).
  - Archivo: [tests/test_api_regression.py](C:\Users\aperd\Documents\DGLv2\apps\sabertables\tests\test_api_regression.py:398)
  - Riesgo: regresiones de condiciones de carrera en rutas más pesadas no detectadas.
  - Acción recomendada: parametrizar esa prueba crítica por tipo de tabla y validar que el control de lazy load se mantiene por dataset.

### 3. Frontend

- [MEDIO] La validación de front-end está centrada en “salud visual mínima” (H1, keywords, OG tags) pero no cubre interacción de botones por tipo de tabla ni rutas de descarga enlazadas al estado dinámico, aunque sí hay invariantes parciales.
  - Archivos: [tests/test_api_regression.py](C:\Users\aperd\Documents\DGLv2\apps\sabertables\tests\test_api_regression.py), [tests/test_data_invariants.py](C:\Users\aperd\Documents\DGLv2\apps\sabertables\tests\test_data_invariants.py)
  - Referencias: `1020`, `1038`, `1179`, `1187` y `901`-`926`.
  - Riesgo: una regresión de JS que rompa el flujo de descargas o filtros por tipo puede pasar si no altera el HTML estático cubierto.
  - Acción recomendada: añadir pruebas de integración mínima del árbol JS para rutas críticas de descarga/filtros con datasets mínimos.

### 4. Seguridad

- [MEDIO] La superficie de headers de origen aceptados/rechazados está bien enfocada en casos `origin`/`referer`, pero falta cubrir patrones de proxy mixtos (cabeceras múltiples, listas separadas por coma, valores mixtos con espacios) que en operación real suelen aparecer más a menudo.
  - Archivo: [tests/test_main_paths.py](C:\Users\aperd\Documents\DGLv2\apps\sabertables\tests\test_main_paths.py), [tests/test_api_regression.py](C:\Users\aperd\Documents\DGLv2\apps\sabertables\tests\test_api_regression.py)
  - Referencias: pruebas de origen en `669`, `670`, `785`, `797`, `825`, `837`.
  - Riesgo: posibilidad de bypass de validación si la normalización de headers no coincide con variantes de proxy.
  - Acción recomendada: parametrizar casos de cabeceras ambiguas en ambas suites y unificar la lógica de validación a una función testeable.

### 5. Backend Architect

- [BAJO] En pruebas de ejecución de pipeline (`tests/test_run_all.py`) se cubre orden y parada por fallo temprano, pero no hay verificación de idempotencia de reintento parcial (ejecución con paso previo exitoso y `--skip-*` o `--apply-*` combinados tras fallo intermedio).
  - Archivo: [tests/test_run_all.py](C:\Users\aperd\Documents\DGLv2\apps\sabertables\tests\test_run_all.py)
  - Referencias: `55`, `66`, `74`, `98`, `111`, `123`, `138`.
  - Riesgo: en incidentes reales, un paso parcial puede dejar estado intermedio no recuperable en siguiente corrida si no se valida ese camino.
  - Acción recomendada: agregar pruebas de recuperación (rerun con estado parcial y flags combinados) y aserciones de limpieza/lock.

### 6. Baseball / Sabermetrics

- [MEDIO] `tests/test_break_even_io.py` y `tests/test_calculations.py` cubren rutas principales de persistencia y filtros, pero quedan sin testear casos de esquina donde el `n` de muestra es extremadamente alto/irregular en series históricas largas por combinación de dataset + tipo + temporada.
  - Archivos: [tests/test_break_even_io.py](C:\Users\aperd\Documents\DGLv2\apps\sabertables\tests\test_break_even_io.py), [tests/test_calculations.py](C:\Users\aperd\Documents\DGLv2\apps\sabertables\tests\test_calculations.py)
  - Referencias: `607`, `1242`, `1521`, `1846`, `1849`.
  - Riesgo: regresiones en métricas de sesgo o estabilidad numérica que solo aparecen en temporadas muy largas o recortadas.
  - Acción recomendada: añadir casos extremos de recuentos y longitudes de ventana con entradas sintéticas controladas.

### 7. Docker / nginx / infra

- [BAJO] Existe validación puntual de `Dockerfile` en invariantes, pero no hay prueba de que la configuración de runtime (workers, límites de petición, logging de nginx/proxy) esté armonizada entre archivo de configuración de app y configuración de despliegue.
  - Archivo: [tests/test_api_regression.py](C:\Users\aperd\Documents\DGLv2\apps\sabertables\tests\test_api_regression.py), [tests/test_data_invariants.py](C:\Users\aperd\Documents\DGLv2\apps\sabertables\tests\test_data_invariants.py)
  - Referencias: `1044`-`1081`, `1050`-`1059`.
  - Riesgo: falsas expectativas de comportamiento (p. ej., número de workers, timeout, redirecciones) que solo fallan en entorno de despliegue.
  - Acción recomendada: agregar pruebas de contrato de configuración de despliegue usando plantillas mínimas o fixtures de configuración leída por ambos niveles.

## Nota de deduplicación
- No se re-listan como nuevos hallazgos elementos ya marcados en `docs/TODO.md` como pendientes conocidos.
- Hallazgos previos de revisiones pasadas sobre `PROXY_TRUSTED_IPS`, CSP, límites de payload y orden de temporadas quedaron cubiertos en revisiones anteriores y no se repiten aquí con el mismo alcance.
