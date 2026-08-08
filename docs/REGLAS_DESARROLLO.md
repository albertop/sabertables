# Reglas de desarrollo

Cómo se escribe código en este proyecto. Aplica tanto a quien trabaje a mano como
a un asistente de IA: **son las mismas reglas**.

## El ciclo

**Rojo → Verde → Refactor.**

1. **Rojo.** Escribe un test que verifique el comportamiento que quieres, y
   **ejecútalo para verlo fallar**. Si pasa a la primera, el test no prueba lo
   que crees.
2. **Verde.** Escribe el mínimo código de producción que lo haga pasar. Nada más.
3. **Refactor.** Limpia con los tests en verde. Si se ponen en rojo, deshaz.

### Las tres reglas obligatorias

1. **Nunca código de producción antes que su test.** Y el test tiene que haberse
   visto fallar.
2. **Un requisito pequeño cada vez.** No resuelvas el problema entero de una
   tacada. Un ciclo, un comportamiento.
3. **Refactor al final, no durante.** Mejorar la forma y cambiar el
   comportamiento son dos pasos distintos, nunca el mismo commit mental.

---

## Las cuatro reglas que este proyecto añade

Las de arriba son el TDD de manual. Estas salen de errores concretos cometidos
aquí, y cada una tiene una cicatriz detrás.

### 4. Ver fallar el test no basta: hay que verificarlo por mutación

Un test que pasa no prueba nada hasta que has comprobado que **falla cuando el
código está roto de la forma que ese test debería detectar**.

El procedimiento: rompe el código a propósito —invierte una condición, cambia un
signo, quita un filtro—, ejecuta solo ese test, y confirma que se pone rojo.
Luego restaura.

**Por qué está aquí.** De 43 mutaciones aplicadas en una sesión, **cuatro tests
pasaron mudos**, y ninguno era un caso raro:

- Un test del filtro de carreras en cero pasaba porque el filtro *no puede*
  cambiar el resultado —con RS=0 la predicción es 0 para cualquier exponente, así
  que suma una constante al objetivo y no mueve el argmin—. Lo único observable
  era el conteo.
- Un test del relleno a tres dígitos usaba `100`, que ya tiene tres dígitos.
- Un test de un valor no reconocido comprobaba que la etiqueta no aparecía,
  cuando lo que pasaba es que se convertía en `NA` y **la fila seguía ahí**.
- Un test de la aplicación se ejecutaba fuera del arranque, así que validaba el
  respaldo y no el valor derivado.

Los cuatro estaban en verde. Los cuatro eran inútiles.

### 5. En cálculo sobre datos, mide antes de escribir el test

El TDD de manual asume que conoces la respuesta esperada. En análisis de datos
muchas veces **no la conoces**: el valor correcto es lo que digan los datos.

Ahí el ciclo se adapta, no se abandona:

1. **Mide primero.** Consulta el dato real y averigua qué pasa de verdad.
2. **Escribe el test con el número medido** y la razón por la que ese número es
   el correcto.
3. Sigue con verde y refactor como siempre.

**Por qué está aquí.** Un revisor externo señaló que Win Expectancy no recortaba
las colas del diferencial de carreras. Era cierto en el código. Al medir: **0
filas de 450.488** fuera de rango, porque el recorte ya lo hacía la vista SQL. El
defecto era latente, no activo — y saberlo cambió la corrección, porque poner el
recorte a secas habría *introducido* un fallo peor.

### 6. Una corrección no se cierra sin validar contra los datos

Que los tests pasen no significa que la corrección haga lo que crees. Antes de
darla por cerrada:

- **Compara la salida antes y después.** Si dices que no cambia nada, demuéstralo.
- **Valida en las dos direcciones**: que lo que debía cambiar cambió, y que lo que
  no debía cambiar sigue igual.

**Por qué está aquí.** Al añadir el recorte de colas a Win Expectancy hubo que
reorganizar la agregación. La afirmación "no cambia ningún número" se comprobó
celda a celda en siete rangos distintos —temporadas sueltas y rangos largos, local
y visitante— y salió **idéntica bit a bit**. Sin esa comprobación era una
suposición.

### 7. Lo que falla en silencio necesita test aunque sea aburrido

Un fallo que revienta se arregla solo, porque alguien lo ve. Los que hay que
cubrir son los que **no dan error**:

- Una tabla que sale vacía en la web.
- Un parquet que se escribe con cero filas.
- Un `og:image` que declara unas dimensiones y el PNG tiene otras.
- Una paginación que deja de responder al clic.

**Por qué está aquí.** Un cambio de seguridad —añadir un nonce a la política de
contenido— invalidó todos los `onclick` en línea y **rompió la paginación de las
once tablas a la vez**. Nada falló, nada se registró en ningún log. Se descubrió
usando el sitio.

---

## Cuándo NO aplica el test primero

Hay tres casos, y conviene reconocerlos para no fingir que se sigue el ciclo:

- **Exploración.** Consultar datos para entender qué pasa no es código de
  producción. Explora, saca la conclusión, y **entonces** empieza el ciclo con lo
  que has aprendido.
- **Vistas SQL.** No se pueden ejecutar sin la base delante. Se prueban por el
  contrato: el test verifica lo que la función de Python hace con lo que la vista
  devuelve, y el DDL vive versionado en `utils/sql/`.
- **Cosas que se miran con los ojos.** Que un heatmap tenga los colores en el
  sentido correcto, o que una tarjeta social se vea bien. Ahí el test cubre lo
  verificable —el sentido de la escala, las dimensiones del fichero— y la vista
  la pones tú.

En los tres, **dilo**. "Esto no lo cubre ningún test y por qué" es información
útil; fingir cobertura no.

---

## Cómo se escribe un test aquí

**El nombre dice el comportamiento, no la función.** `test_un_robo_que_baja_la_re_no_se_publica`,
no `test_calculate_sb_breakeven_2`.

**El docstring dice por qué importa**, no qué hace el código. Lo que se busca es
la consecuencia de que se rompa:

```python
def test_los_nulos_no_cuentan_para_la_proporcion():
    """
    El denominador son los break-even calculados, no las filas. Contar los NaN
    diluiría la proporción y dejaría pasar un problema sistemático.
    """
```

**Un comportamiento por test.** Si el nombre necesita un "y", son dos.

**Cada test con su control.** Si uno comprueba que algo se descarta, otro
comprueba que lo válido sobrevive. Un filtro que lo tira todo pasa el primero.

---

## Antes de dar algo por terminado

```bash
python -m pytest tests/ -q && python -m ruff check . && python -m mypy .
```

Los tres en verde. Y para cualquier cosa que toque datos publicados, la
comparación antes/después de la regla 6.

---

## Cómo se reporta

- **Si algo no se pudo verificar, dilo.** "Los cambios en las consultas SQL están
  verificados por sustitución de marcadores y por los tests de Python, no contra
  la base" es una frase honesta y necesaria.
- **Si un test pasó por la razón equivocada, cuéntalo.** Es información sobre la
  calidad de la suite.
- **Si un hallazgo resulta no ser un problema, dilo con el dato.** "0 filas de
  450.488" cierra la discusión; "creo que no aplica" no.
- **No hace falta narrar cada corrección menor.** Un error que no cambia nada
  para quien lee se corrige y se sigue.
