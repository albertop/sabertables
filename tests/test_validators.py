"""
Los dos validadores que usa la aplicacion.

`validate_dataframe_schema` corre al arrancar contra cada parquet: es el contrato
entre lo que escribe el pipeline y lo que espera la web. Ya atrapo una vez que
`re288_frequencies` sirviera la columna `frequency` mientras la app exigia
`pa_final_count_share`, con ese endpoint roto sin que nadie lo supiera.

`sanitize_error_message` decide que se le cuenta a un desconocido cuando algo
falla en produccion.
"""
import pandas as pd
import pytest
from app.validators.data_validator import sanitize_error_message, validate_dataframe_schema


# ---------------------------------------------------------------------------
# El contrato de esquema
# ---------------------------------------------------------------------------

def _df():
    return pd.DataFrame({"season": [2024], "runners": ["100"], "n": [10]})


def test_un_esquema_correcto_pasa():
    """El contrato es lanzar o no lanzar; en exito no devuelve nada."""
    validate_dataframe_schema(_df(), "RE24", ["season", "runners", "n"])


def test_sobrar_columnas_no_es_un_problema():
    """
    El pipeline puede anadir una columna sin romper la web. Exigir igualdad
    exacta convertiria cada anadido en una caida.
    """
    validate_dataframe_schema(_df(), "RE24", ["season"])


def test_falta_una_columna_y_el_arranque_se_detiene():
    """
    Es el caso real: el parquet dejo de traer la columna que la app pedia y el
    endpoint quedo roto en silencio. Mejor no arrancar que servir una tabla que
    no existe.
    """
    with pytest.raises(ValueError, match="Missing required columns"):
        validate_dataframe_schema(_df(), "RE24", ["season", "no_existe"])


def test_el_error_dice_QUE_columna_falta():
    """Sin el nombre, hay que ir a comparar el parquet con el codigo a mano."""
    with pytest.raises(ValueError, match="pa_final_count_share"):
        validate_dataframe_schema(_df(), "RE288 Frequencies", ["pa_final_count_share"])


def test_el_error_dice_de_QUE_dataset_se_trata():
    """Arrancan veintiuna validaciones; sin el nombre no se sabe cual reviento."""
    with pytest.raises(ValueError, match="RE288 Frequencies"):
        validate_dataframe_schema(_df(), "RE288 Frequencies", ["falta"])


def test_un_nulo_en_una_columna_obligatoria_tambien_detiene():
    """
    Una celda vacia en `season` o en `n` no da error al leer el parquet: da una
    tabla con huecos que parecen ausencia de juego.
    """
    df = pd.DataFrame({"season": [2024, None], "n": [1, 2]})

    with pytest.raises(ValueError, match="NaN"):
        validate_dataframe_schema(df, "RE24", ["season", "n"])


def test_el_error_de_nulos_dice_en_que_columna():
    df = pd.DataFrame({"season": [2024, 2025], "n": [1, None]})

    with pytest.raises(ValueError, match="n"):
        validate_dataframe_schema(df, "RE24", ["season", "n"])


def test_solo_se_miran_los_nulos_de_las_columnas_exigidas():
    """
    Una columna opcional con huecos es normal --un break-even que no se pudo
    calcular es NaN a proposito-- y no debe impedir el arranque.
    """
    df = pd.DataFrame({"season": [2024], "opcional": [None]})

    validate_dataframe_schema(df, "BREAKEVEN", ["season"])


# ---------------------------------------------------------------------------
# Que se le cuenta a un desconocido
# ---------------------------------------------------------------------------

def test_fuera_de_produccion_el_mensaje_va_entero():
    """En desarrollo se quiere la ruta y la linea; es lo que sirve para arreglar."""
    msg = r'File "C:\proyecto\app\main.py", line 42: falta la columna n'

    assert sanitize_error_message(msg, is_production=False) == msg


def test_por_defecto_no_se_sanea():
    """El defecto es el modo desarrollo: sanear de mas oculta lo que hace falta."""
    assert sanitize_error_message("C:\\ruta\\secreta.py", ) == "C:\\ruta\\secreta.py"


def test_en_produccion_desaparece_la_ruta_de_windows():
    """
    Una ruta absoluta le dice a un desconocido como esta montado el servidor y
    con que usuario corre.
    """
    salida = sanitize_error_message(r"Error en C:\Users\aperd\proyecto\app\main.py", True)

    assert "aperd" not in salida
    assert "[PATH]" in salida


def test_en_produccion_desaparece_la_ruta_de_unix():
    salida = sanitize_error_message("no se pudo leer /srv/app/data/re24_values.parquet", True)

    assert "/srv/app/data" not in salida
    assert "[FILE]" in salida


def test_en_produccion_desaparecen_fichero_y_linea_del_traceback():
    salida = sanitize_error_message('File "/app/main.py", line 1337: boom', True)

    assert "line 1337" not in salida
    assert "[REDACTED]" in salida


def test_en_produccion_desaparecen_los_nombres_de_columna():
    """
    Los nombres de columna describen el modelo de datos. No es un secreto, pero
    tampoco hace falta regalarlo en un mensaje de error.
    """
    salida = sanitize_error_message("Missing required columns: pa_final_count_share", True)

    assert "pa_final_count_share" not in salida


def test_lo_saneado_sigue_diciendo_que_paso():
    """
    Un mensaje que se queda en "error" no le sirve a nadie. La idea es quitar
    las rutas, no el motivo.
    """
    salida = sanitize_error_message(r'No data found in C:\app\data\re24.parquet', True)

    assert "No data found" in salida
