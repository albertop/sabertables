# Operación anual: actualizar el sitio y construir la imagen

Este es el documento que hay que seguir **una vez al año**, cuando la temporada
cierra y las fuentes publican los datos completos. Sustituye a
`utils/procedure.txt`, que tenía los seis pasos en una línea cada uno; aquí está
lo mismo con lo que hace falta saber para que no salga mal.

**Tiempo total:** entre 40 minutos y una hora y media, casi todo esperando a que
corran las consultas. La parte que exige atención son 10 minutos.

---

## Antes de empezar: cuándo se puede

**No se ejecuta en cuanto termina la Serie Mundial.** Hay que esperar a que las
fuentes publiquen la temporada completa:

- **Retrosheet** publica los ficheros de eventos de una temporada meses después
  de que acabe, normalmente en la primavera siguiente. De ahí sale casi todo.
- **Statcast** publica **en vivo**, durante la temporada.

Esa diferencia es la razón de que exista `MAX_SEASON`, y no es teórica: llegó a
publicarse una temporada **a medias** —302 filas de swing/take por zona, con la
temporada en curso hasta agosto— presentada en la tabla igual que un año entero y
sin ninguna marca. Entraba por la única puerta que no tenía tope. Ver 4.9 en
[TODO.md](TODO.md).

**La comprobación:** que los datos de la temporada que vas a publicar estén
cargados y completos en SQL Server. Si Retrosheet aún no ha publicado, no subas
`MAX_SEASON`.

### Qué hace falta tener a mano

- Acceso a **SQL Server** con las tablas de Retrosheet y Statcast cargadas.
- **Docker Desktop** corriendo en la máquina de trabajo (Windows).
- Acceso **SSH al VPS** y a **Portainer** (`https://docker.prdm0.com`).
- El `.env` de la raíz del proyecto, con la conexión a la base.

---

## Paso 1 — Subir la temporada máxima

**Es el único cambio de configuración de todo el proceso, y es el primero.**

En el `.env` de la raíz:

```
MAX_SEASON=2026
```

Las tres fechas viven en `utils/seasons.py` y esa es la **única fuente de
verdad**. No hay que buscar ningún año en las consultas ni en las vistas: hay un
test que falla si alguien vuelve a escribir uno a mano.

Los otros dos (`MIN_SEASON=1910`, `MIN_PER_PITCH_SEASON=1989`) no se tocan salvo
que cambie la disponibilidad de las fuentes.

> **Si prefieres no tocar el `.env`**, los tres se pueden pasar por línea de
> comandos en el paso siguiente: `--max-season 2026`. El `.env` es mejor porque
> queda escrito.

---

## Paso 2 — Ejecutar el pipeline

```bash
python utils/run_all.py --log-file logs/pipeline-2026.log
```

Eso hace, en orden: comprobar el DDL de las vistas, extraer los 16 datasets,
ajustar los exponentes pitagóricos y calcular los puntos de equilibrio e impacto
en RE.

**Lo primero que imprime es el rango**, y conviene mirarlo antes de irse a por un
café:

```
Rango de temporadas: 1910-2026 (desde 1989 para lo que necesita conteo)
```

Si ahí no pone el año nuevo, para: el `.env` no se está leyendo.

### Qué esperar

- **Entre 30 y 60 minutos.** Las consultas de swing/take trabajan sobre unos 12,8
  millones de filas a nivel de lanzamiento; la agregación ocurre en el servidor,
  pero aun así tarda.
- Es normal que parezca colgado un buen rato en una sola vista. Si quieres que
  falle antes en vez de esperar, `PH_QUERY_TIMEOUT_SECONDS` en el `.env` pone un
  `LOCK_TIMEOUT`.

### Lo que hay que leer en el log

El pipeline avisa de cosas que **no** lo detienen y que conviene mirar:

| aviso | qué significa |
|---|---|
| `N filas fuera de AAAA-AAAA descartadas después de la consulta` | Una consulta o vista no está acotando y el filtro de garantía tuvo que recortar. No es fatal, pero hay que mirar cuál. |
| `filas equipo-temporada con juegos sin clasificar` | Juegos marcados como finales sin marcador. Pasó en 2000, con 35 juegos. El conteo se recalcula solo; si aparece en una temporada nueva, merece un vistazo. |
| `break-even por encima de 25` | Celdas con denominador casi cero. Se anulan a NaN y se sigue, salvo que sean más del 1%, en cuyo caso **aborta** — y eso sí es un problema real de muestra. |
| `celdas con break-even negativo llevadas a 0` | Esperado en swing/take con dos strikes. Ver 2.4 en [TODO.md](TODO.md). |

**Si aborta**, no deja los parquets a medias: se escriben en un directorio de
paso y se promueven al final. Los datos anteriores siguen intactos.

---

## Paso 3 — Comprobar lo que salió

**No te saltes esto.** Es donde se detecta que una temporada entró a medias o que
una vista dejó de devolver lo mismo.

```bash
python -m pytest tests/ -q
```

Los 318 tests incluyen invariantes sobre los parquets ya generados: el contrato
de esquema entre los datos y la aplicación, los rangos de cada columna y el
rango de temporadas.

Y una mirada al rango real de lo que se acaba de escribir:

```bash
python -c "import pandas as pd, glob, os; [print(f'{os.path.basename(f):38s} {int(pd.read_parquet(f, columns=[\"season\"])[\"season\"].min())}-{int(pd.read_parquet(f, columns=[\"season\"])[\"season\"].max())}') for f in sorted(glob.glob('app/data/*.parquet')) if 'season' in pd.read_parquet(f).columns]"
```

Lo que debe salir: **todas** las tablas terminando en `MAX_SEASON`. Las de RE288
y robo por conteo empiezan en 1989; el resto en 1910. Si una tabla se pasa del
techo, algo no está acotando.

---

## Paso 4 — Construir la imagen

```powershell
.\utils\create_docker_image.ps1
```

Pide la versión en formato `x.y.z` y valida el formato antes de construir —ese
valor acaba a la vez en la etiqueta de la imagen y en el nombre del fichero, así
que un espacio rompe una de las dos cosas cuando ya has esperado el build—.
También comprueba que Docker responde en vez de preguntártelo.

**La convención es `1.<última temporada de datos>.N`.** Ojo: el número del medio
**no es el año de compilación**. La `1.2025.33` se construyó en abril de **2026**;
el 2025 es la última temporada que llevan los datos. El tercero es un contador que
sigue subiendo aunque cambie la temporada. No es versión semántica.

Así que tras `1.2025.33` viene `1.2025.34` mientras los datos lleguen a 2025, y
pasará a `1.2026.N` cuando el pipeline publique la temporada 2026.

```powershell
.\utils\create_docker_image.ps1 -Version 1.2025.34
```

Sin `-Version` pregunta. El parámetro existe porque con `Read-Host` a secas el
script solo funcionaba con una persona delante: cualquier ejecución no
interactiva fallaba con *"PowerShell is in NonInteractive mode"*.

El script construye y hace `docker save` a
`docker_images/sabertables-<version>.tar`. **El `.tar` ronda los 165 MB** —las
capas van comprimidas— y la imagen desempaquetada ocupa unos **708 MB**, que es lo
que reporta `docker images`.

### Qué lleva la imagen

- `python:3.13-slim`, construcción en dos etapas.
- Corre como `appuser`, no como root.
- **Sin pip ni setuptools** en la imagen final: se quitan a propósito para
  reducir superficie.
- Las dependencias se instalan con `-c constraints.txt`, que fija versiones
  **exactas**. `requirements.txt` lleva mínimos; `constraints.txt` manda.
- Solo entra `app/`. Ni `utils/`, ni `tests/`, ni `docs/`, ni el `.env`.

> **El `.env` NO entra en la imagen.** Por eso `docker-compose.yml` declara las
> variables en `environment:`. Cuando no lo hacía, en producción todo caía a los
> valores por defecto del código y `ENVIRONMENT` valía `development` — o sea que
> no se aplicaba ni el redirect a HTTPS, ni HSTS, ni la verificación de origen,
> porque las tres están condicionadas a `== 'production'`.

---

## Paso 5 — Probar la imagen en local

Antes de subir nada al VPS:

```powershell
.\utils\rebuild-local-test.ps1
```

O a mano:

```bash
docker stop sabertables-local-test; docker rm sabertables-local-test
docker build -t sabertables:local-test .
docker run -d --name sabertables-local-test -e ENVIRONMENT=development -p 3001:8000 sabertables:local-test
```

Y comprobar:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3001/healthz
```

`/healthz` es una sonda de **preparación** real, no un "estoy vivo": devuelve
**503** con la lista de problemas si falta algún parquet esencial o si un dataset
no cargó. Un 200 significa que la aplicación puede servir tablas.

Luego abre `http://localhost:3001` y mira **con los ojos**:

- Que el selector de temporadas llegue al año nuevo.
- Que una tabla de cada familia pinte —RE24, Win Expectancy, Break Even Points—.
- Que **la paginación funcione**. Esto se pide expresamente porque una vez se
  rompió en las once tablas a la vez, en silencio, por un cambio de seguridad
  que invalidó los `onclick` en línea. Hay tests que lo cubren desde entonces,
  pero míralo igual.
- Que los botones de descarga bajen algo.

---

## Paso 6 — Llevar la imagen al VPS

Copia el `.tar` a `~/images` del servidor:

```bash
scp docker_images/sabertables-1.2026.1.tar usuario@servidor:~/images/
```

Y en el VPS:

```bash
./deploy_sabertables_image.sh
```

Pide la versión y hace `docker load`. Al terminar, la imagen
`sabertables:1.2026.1` está disponible en el servidor —pero **todavía no corre
nada nuevo**—.

---

## Paso 7 — Cambiar la versión en Portainer

Entra a **https://docker.prdm0.com** y edita el stack de `sabertables.com`:
cambia la etiqueta de la imagen a la versión nueva y redespliega.

**Aquí es donde el sitio cambia de verdad.** Los pasos anteriores no tocan
producción.

> **`docker-compose.yml` del repositorio es una plantilla, no lo que corre.** La
> versión real se cambia en Portainer, así que ese fichero se desactualiza solo.
> Conviene subir la etiqueta ahí también al publicar, aunque solo sea para que no
> engañe a quien lo lea — llegó a decir `1.2025.1` con `docker_images/` ya en la
> 33.

### Comprobación final, en producción

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://www.sabertables.com/healthz
```

Y en el sitio: que el selector llegue al año nuevo y que una tabla pinte.

---

## Cosas que conviene saber antes de que muerdan

### El puerto está atado a 127.0.0.1

`ports: "127.0.0.1:3001:8000"`, y **no** `"3001:8000"`. Con la segunda forma el
puerto queda publicado en todas las interfaces: si 3001 es alcanzable desde
fuera, se llega a la aplicación **saltándose nginx**, y con ello el TLS y todo lo
que viva allí.

Es seguro porque nginx corre en el **host** (`/etc/nginx/sites-available/`), no
en otro contenedor. **Si algún día nginx se contenedoriza, esto hay que
revertirlo** y conectarlos por una red de Docker.

### Dos workers, no cuatro

Los workers **no comparten memoria**: cada uno carga su propia copia de los
parquets. Medido: 427 MB con cuatro workers tras pedir tres tablas pesadas, 226
MB con dos. Y no se pierde concurrencia, porque `worker_class` es
`UvicornWorker`, asíncrono — cuatro workers no dan cuatro veces la capacidad, dan
cuatro copias.

Ojo: el valor por defecto **en el código** (`app/main.py`) sigue siendo 4. Lo que
manda es la variable de entorno, que el compose pone a 2.

### Sin límite de memoria, a propósito

Había `limits.memory` bajo `deploy:`, que **solo lo respeta Docker Swarm** — con
`docker compose up` se ignora. De ahí que el contenedor llegara a 516 MB con un
"límite" de 512 sin que nadie lo matara: el límite no existía, solo lo parecía.

Se quitó en vez de convertirlo en `mem_limit` porque las temporadas crecen y un
techo fijo se convierte en una caída sorpresa dentro de unos años. El crecimiento
es lento: 0,12 MB de datos por temporada, unos 4 MB de memoria al año contando
las dos copias. El consumo lo dominan Python, pandas y pyarrow, no los datos.

### Los logs

En producción **no se escriben ficheros**: todo va a stderr y lo rota Docker con
el driver `json-file`, 10 MB por fichero y 3 ficheros. No hay nada que rotar a
mano. En desarrollo sí hay ficheros, y los rota loguru a medianoche con retención
y compresión.

### `API_REQUIRE_ORIGIN`

Exige una cabecera `Origin` o `Referer` conocida en los endpoints que generan
tablas. Sube el coste de usar la API desde fuera del sitio; no lo impide. **La
primera vez que se despliegue con esto activo, vigila que no dé 403 a tráfico
legítimo.**

---

## Si algo sale mal

| síntoma | dónde mirar |
|---|---|
| El pipeline dice `Rango de temporadas` con el año viejo | El `.env` no se está leyendo. Comprueba que estás en la raíz del proyecto. |
| Una tabla sale vacía en la web | Mira si su parquet se escribió y si tiene filas. Un parquet vacío deja la tabla en blanco **sin ningún error**. |
| `/healthz` devuelve 503 | La respuesta trae la lista de `problems`: dice qué fichero falta. |
| El contenedor arranca y muere | `docker logs sabertables.com`. Suele ser un parquet ausente o un `.env` incompleto. |
| Una consulta no termina nunca | Ha pasado. `PH_QUERY_TIMEOUT_SECONDS` hace que falle antes en vez de esperar indefinidamente. |
| Los tests fallan tras el pipeline | Léelos: los invariantes están para eso. Un fallo aquí es preferible a publicarlo. |

**Para volver atrás:** en Portainer, cambia la etiqueta a la versión anterior y
redespliega. Las imágenes viejas siguen cargadas en el servidor. Los parquets del
repositorio son la única copia de los datos generados, así que si quieres poder
volver a los del año pasado, guárdalos antes de ejecutar el pipeline.

---

## Resumen para quien ya lo ha hecho antes

```
1. .env → MAX_SEASON=<año nuevo>
2. python utils/run_all.py --log-file logs/pipeline-<año>.log     (30-60 min)
3. python -m pytest tests/ -q                                      + mirar rangos
4. .\utils\create_docker_image.ps1                                 versión 1.<año>.N
5. .\utils\rebuild-local-test.ps1  →  http://localhost:3001        mirar con los ojos
6. scp del .tar a ~/images  →  ./deploy_sabertables_image.sh
7. Portainer → cambiar etiqueta del stack → redesplegar
8. curl https://www.sabertables.com/healthz
```
