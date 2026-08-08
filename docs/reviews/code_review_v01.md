# Code Review v01

## Alcance
- Proyecto: `C:/Users/aperd/Documents/DGLv2/apps/sabertables`
- Revisión solicitada: desde 7 perspectivas (`Python`, `QA/Reliability`, `Frontend`, `Security`, `Backend Architecture`, `Baseball/Sabermetrics`, `Docker+nginx+fail2ban`)
- No se ejecutaron cambios de código, solo análisis.
- Incidentes ya presentes como `PENDIENTE` en `docs/TODO.md` no fueron re-listados como nuevos hallazgos.

## Hallazgos consolidados

### 1) `END` duplicado en la generación de estados RE24
- Severidad: Alta
- Tipo: Correctitud de datos / Riesgo funcional
- Ubicación:
  - [app/services/table_generator.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/services/table_generator.py#L1733)
- Descripción:
  - En `generate_re24_transitions_table`, el estado `END` se inserta dos veces en `state_order`.
  - Esto puede crear columnas duplicadas en la tabla de transiciones y distorsionar los cálculos de métricas (por ejemplo, sumas y ordenamientos de estados finales).

### 2) `unsafe-inline` en CSP + scripts/estilos embebidos que dependen de datos del backend
- Severidad: Alta
- Tipo: Seguridad (XSS / hardening insuficiente)
- Ubicaciones:
  - [app/main.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/main.py#L560)
  - [app/templates/index.html](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/templates/index.html#L10)
  - [app/templates/index.html](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/templates/index.html#L13)
  - [app/templates/index.html](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/templates/index.html#L133)
- Descripción:
  - Se permite explícitamente `unsafe-inline` en la política CSP mientras la plantilla incluye scripts y scripts de configuración inline, además de inyección de JSON en el HTML.
  - Esto reduce la protección real de CSP y aumenta el impacto si una fuente de XSS existe (o llega por datos no confiables).

### 3) Uso extensivo de `innerHTML` con datos del servidor en el frontend
- Severidad: Alta
- Tipo: Seguridad (DOM XSS potencial)
- Ubicación:
  - [app/static/js/app.js](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/static/js/app.js#L1934)
  - [app/static/js/app.js](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/static/js/app.js#L1972)
- Descripción:
  - Se renderizan títulos y filas usando `innerHTML` con valores provenientes de respuestas de API y estructuras de estado del backend.
  - Sin sanitización adicional, esto amplifica riesgo de inyección si cualquier cadena no se purifica antes del render.

### 4) Confianza de proxies demasiado amplia
- Severidad: Alta
- Tipo: Seguridad/Confiabilidad operacional
- Ubicaciones:
  - [app/main.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/main.py#L90)
  - [app/main.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/main.py#L622)
  - [app/main.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/main.py#L508)
- Descripción:
  - La configuración de `ProxyHeadersMiddleware` puede terminar confiando todos los proxies (`*`) por defecto si no se setea `PROXY_TRUSTED_IPS`.
  - Combinado con lógica que depende de IP/rate-limit, esto permite spoofing de cabeceras y degradación de control de abuso en escenarios no intencionados.

### 5) Verificación de `Origin`/`Referer` parcial (solo endpoint específico)
- Severidad: Media
- Tipo: Seguridad de exposición CSRF/origen
- Ubicaciones:
  - [app/main.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/main.py#L745)
  - [app/main.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/main.py#L821)
- Descripción:
  - El middleware de verificación (`verify_origin`) solo se aplica al endpoint `/api/download-stats`.
  - Los demás endpoints sensibles de generación/cálculo no tienen esa misma protección explícita por origen, reduciendo consistencia del modelo de threat.

### 6) Mutación de DataFrames en utilidad de promedio ponderado
- Severidad: Media
- Tipo: Confiabilidad de datos / efectos colaterales
- Ubicación:
  - [app/services/table_generator.py](C:/Users/aperd/Documents/DGLv2/apps/sabertables/app/services/table_generator.py#L44)
- Descripción:
  - `calculate_weighted_mean` añade columnas temporales al DataFrame de entrada (`weighted_value`).
- Impacto:
  - Puede introducir estado compartido entre operaciones y efectos laterales difíciles de rastrear al reusar el mismo objeto DataFrame en pipelines más amplios.

## Hallazgos por persona (sin duplicación)

### Python
- Prioridad: `#1` (END duplicado), `#6` (mutación en función de agregación), `#2` (sin cambios de arquitectura mayores).

### QA / Reliability
- Prioridad: `#1` (impacto de consistencia de salida), `#7` (protecciones operativas incompletas de despliegue), `#4` (proxy trust), `#5` (cobertura incompleta de verificación de origen).

### Frontend
- Prioridad: `#3` (innerHTML con datos de servidor), `#2` (CSP efectiva debilitada).

### Security
- Prioridad: `#2`, `#3`, `#4`, `#5`.

### Backend Architecture
- Prioridad: `#4`, `#5`, `#1` en pipeline de tabla.

### Baseball / Sabermetrics
- Prioridad: `#1` por impacto directo en precisión estadística de transiciones RE24; `#6` por estabilidad de cómputo reutilizable.

### Docker + nginx + fail2ban
- No se reportan hallazgos nuevos en esta revisión para esta perspectiva, ya que los archivos de configuración de nginx/fail2ban no están sincronizados con las versiones en uso.

## Riesgos residuales sin evidencia suficiente para bloquear
- Sin pruebas de integración y carga, no se confirmó si los problemas de rendimiento/latencia o timeout en `/api/generate-table` aparecen en producción o solo con cargas atípicas.
- Parte de la validación de input/output numérico (rangos, nulos y atípicos) requiere revisar más fixtures de datos realistas para confirmar impacto exacto.
