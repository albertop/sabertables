"""
El orquestador del pipeline.

Los cuatro pasos se sustituyen por funciones de mentira: aqui no se extrae nada
ni se toca la base. Lo que se prueba es el ORDEN y que un paso que falla detenga
lo que viene detras -- si no, un fallo en la extraccion dejaria que los
break-even se calculasen sobre los parquets del anio pasado sin avisar.
"""
import cli
import pytest
import run_all
import seasons


@pytest.fixture(autouse=True)
def _log():
    cli.setup_logging(quiet=True)


@pytest.fixture(autouse=True)
def _restaurar_rango():
    previo = (seasons.MIN_SEASON, seasons.MIN_PER_PITCH_SEASON, seasons.MAX_SEASON)
    yield
    (seasons.MIN_SEASON, seasons.MIN_PER_PITCH_SEASON, seasons.MAX_SEASON) = previo


@pytest.fixture
def pasos(monkeypatch):
    """
    Sustituye los cuatro pasos y devuelve la lista de los que se ejecutaron.

    Devuelve tambien un diccionario para hacer fallar el que se quiera.
    """
    import calculate_pythag_exponents
    import create_break_even_points
    import extract_re_data
    import sync_sql_views

    ejecutados = []
    codigos = {}

    def falso(nombre):
        def _fn(*a, **k):
            ejecutados.append(nombre)
            return codigos.get(nombre, 0)
        return _fn

    monkeypatch.setattr(sync_sql_views, "main", falso("vistas"))
    monkeypatch.setattr(extract_re_data, "extract_and_save_data", falso("extraccion"))
    monkeypatch.setattr(calculate_pythag_exponents, "run", falso("pythag"))
    monkeypatch.setattr(create_break_even_points, "main", falso("breakeven"))
    return ejecutados, codigos


def test_los_cuatro_pasos_corren_en_orden(pasos):
    """
    El orden no es decorativo: los break-even leen los parquets que escribe la
    extraccion, y el pitagorico se apoya en las vistas.
    """
    ejecutados, _ = pasos

    assert run_all.main([]) == 0
    assert ejecutados == ["vistas", "extraccion", "pythag", "breakeven"]


def test_skip_views_salta_solo_las_vistas(pasos):
    ejecutados, _ = pasos

    run_all.main(["--skip-views"])

    assert ejecutados == ["extraccion", "pythag", "breakeven"]


def test_apply_views_pide_aplicar_en_vez_de_comprobar(monkeypatch):
    """
    Por defecto solo COMPRUEBA. Aplicar modifica la base, asi que tiene que
    pedirse a proposito.
    """
    import calculate_pythag_exponents
    import create_break_even_points
    import extract_re_data
    import sync_sql_views

    recibido = []
    monkeypatch.setattr(sync_sql_views, "main", lambda modo: (recibido.append(modo), 0)[1])
    monkeypatch.setattr(extract_re_data, "extract_and_save_data", lambda: 0)
    monkeypatch.setattr(calculate_pythag_exponents, "run", lambda: 0)
    monkeypatch.setattr(create_break_even_points, "main", lambda: 0)

    run_all.main([])
    assert recibido == [["--check"]]

    recibido.clear()
    run_all.main(["--apply-views"])
    assert recibido == [["--apply"]]


def test_un_paso_que_falla_detiene_el_pipeline(pasos):
    """
    Lo importante es lo que NO pasa: si la extraccion falla y el pipeline
    siguiera, los break-even se calcularian sobre los parquets del anio pasado y
    el resultado pareceria correcto.
    """
    ejecutados, codigos = pasos
    codigos["extraccion"] = 3

    assert run_all.main([]) == 3
    assert ejecutados == ["vistas", "extraccion"], "no debe seguir tras el fallo"


def test_las_vistas_con_deriva_tambien_detienen_el_pipeline(pasos):
    """
    check devuelve 1 cuando el fichero y la base difieren. Seguir seria calcular
    sobre unas vistas que no son las del repositorio.
    """
    ejecutados, codigos = pasos
    codigos["vistas"] = 1

    assert run_all.main([]) == 1
    assert ejecutados == ["vistas"]


def test_un_paso_que_no_devuelve_codigo_no_se_toma_por_fallo(monkeypatch, pasos):
    """
    extract_and_save_data devuelve None, no 0. Tratar eso como error abortaria
    el pipeline siempre.
    """
    import extract_re_data

    ejecutados, _ = pasos
    monkeypatch.setattr(extract_re_data, "extract_and_save_data",
                        lambda: (ejecutados.append("extraccion"), None)[1])

    assert run_all.main([]) == 0
    assert len(ejecutados) == 4


def test_el_rango_de_temporadas_se_fija_antes_de_los_pasos(pasos, monkeypatch):
    """
    run_all llama a las funciones directamente, no a los _cli de cada script,
    asi que el enganche de las tres fechas que ellos tienen NO se dispara por
    esta via. Tiene que estar aqui.
    """
    vistos = []

    import extract_re_data
    monkeypatch.setattr(extract_re_data, "extract_and_save_data",
                        lambda: vistos.append(seasons.rango()))

    run_all.main(["--max-season", "2024", "--min-season", "1920"])

    assert vistos == [(1920, 2024)]


def test_un_rango_invalido_para_antes_de_tocar_nada(pasos):
    """Descubrirlo a los veinte minutos de consultas no sirve de nada."""
    ejecutados, _ = pasos

    with pytest.raises(seasons.RangoDeTemporadasInvalido):
        run_all.main(["--max-season", "1950"])

    assert ejecutados == [], "no deberia haber arrancado ningun paso"
