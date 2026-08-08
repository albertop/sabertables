"""
Tests unitarios de la logica de calculo de utils/.

Estos NO tocan la base de datos ni los parquets publicados: alimentan las
funciones con entradas construidas a mano y comprueban el resultado. Son los
primeros tests de esta clase en el proyecto -- hasta ahora los scripts no eran
importables, asi que solo se podian validar los datos ya generados.

Cada test comprueba una propiedad que, de romperse, produciria un numero
plausible pero incorrecto. Los errores obvios ya los atrapa el validador de
rango; lo que se busca aqui son los silenciosos.
"""
import calculate_pythag_exponents as pythag
import create_break_even_points as be
import extract_re_data as extract
import numpy as np
import pandas as pd
import pytest
import seasons

# ---------------------------------------------------------------------------
# Break-even de robo
# ---------------------------------------------------------------------------

def _re(mapping):
    """Convierte {(outs, runners): re} en el lookup que esperan las funciones."""
    return {(2024, outs, runners): value for (outs, runners), value in mapping.items()}


def test_sb_breakeven_formula():
    """
    BE = (RE_antes - RE_CS) / (RE_SB - RE_CS), con el CS un out mas tarde.

    Con estos numeros el resultado a mano es (0.9 - 0.3) / (1.2 - 0.3) = 0.667.
    """
    re_lookup = _re({
        (0, '100'): 0.9,   # antes: corredor en 1B
        (0, '010'): 1.2,   # robo con exito: corredor en 2B
        (1, '000'): 0.3,   # atrapado: bases limpias, un out mas
    })
    n_lookup = {(2024, 0, '100'): 5000}

    rows = be.calculate_sb_breakeven(re_lookup, n_lookup, [2024])
    row = next(r for r in rows if r['scenario'] == 'Runner on 1B steals 2B' and r['outs'] == 0)

    assert row['breakeven_pct'] == pytest.approx((0.9 - 0.3) / (1.2 - 0.3))
    assert row['n'] == 5000


def test_sb_breakeven_con_dos_outs_el_cs_termina_la_entrada():
    """
    Con 2 outs el atrapado es el tercer out: RE_CS = 0 por definicion, no un
    valor buscado en la matriz. Si se buscara, no existiria la entrada de 3 outs
    y la celda desapareceria en silencio.
    """
    re_lookup = _re({(2, '100'): 0.22, (2, '010'): 0.32})
    rows = be.calculate_sb_breakeven(re_lookup, {}, [2024])
    row = next(r for r in rows if r['scenario'] == 'Runner on 1B steals 2B' and r['outs'] == 2)

    assert row['breakeven_pct'] == pytest.approx(0.22 / 0.32)


def test_un_robo_que_baja_la_re_no_se_publica():
    """
    Robar con exito nunca puede bajar la RE: mismos outs, corredor en mejor base.
    Cuando la matriz dice lo contrario, la celda esta mal estimada.

    Y no es un caso de borde: BE = (RE_antes - RE_cs)/(RE_sb - RE_cs), asi que
    con denominador positivo BE > 1 equivale exactamente a RE_antes > RE_sb. Es
    la causa UNICA de los break-even por encima de 1 en las tablas de robo -- 2
    de 2 en RE24 y 391 de 391 en la tabla por conteo, sin un solo caso cruzado.
    """
    re_lookup = _re({
        (0, '101'): 2.66,   # 1B y 3B
        (0, '011'): 1.47,   # 2B y 3B: MENOS que antes, imposible
        (1, '001'): 1.20,
    })
    rows = be.calculate_sb_breakeven(re_lookup, {}, [2024])
    row = next(r for r in rows
               if r['scenario'] == 'Runners on 1B & 3B, runner on 1B steals 2B' and r['outs'] == 0)

    assert pd.isna(row['breakeven_pct'])


def test_un_robo_que_sube_la_re_si_se_publica():
    """Control del test anterior: con la misma forma pero RE coherente, sale."""
    re_lookup = _re({
        (0, '101'): 1.80,
        (0, '011'): 2.05,   # ahora si sube
        (1, '001'): 0.95,
    })
    rows = be.calculate_sb_breakeven(re_lookup, {}, [2024])
    row = next(r for r in rows
               if r['scenario'] == 'Runners on 1B & 3B, runner on 1B steals 2B' and r['outs'] == 0)

    assert row['breakeven_pct'] == pytest.approx((1.80 - 0.95) / (2.05 - 0.95))
    assert row['breakeven_pct'] < 1.0, 'con RE coherente el break-even nunca pasa de 1'


def test_la_guarda_de_monotonia_tambien_aplica_por_conteo():
    """
    Las 391 celdas afectadas estaban en la tabla POR CONTEO, no en la de RE24,
    asi que la guarda tiene que estar en las dos funciones.
    """
    re288 = {(2024, 0, '101', '3-0'): 2.66,
             (2024, 0, '011', '3-0'): 1.47,   # robar con exito baja la RE
             (2024, 1, '001', '3-0'): 1.20}
    n288 = dict.fromkeys(re288, 5000)

    rows = be.calculate_sb_breakeven_by_count(re288, n288, [2024])
    row = next(r for r in rows if r['scenario'] == 'Runners on 1B & 3B, runner on 1B steals 2B'
               and r['outs'] == 0 and r['count'] == '3-0')

    assert pd.isna(row['breakeven_pct']), (
        'con muestra de sobra pero RE incoherente el break-even se publico igual'
    )


def test_el_umbral_por_conteo_mira_la_celda_mas_pequeña():
    """
    El umbral se aplicaba solo a la celda de PARTIDA, que es la mayor de las tres
    en el 94.1% de las filas. La fragil es la del robo consumado, que ademas va
    en el denominador: habia filas con n_before=170 y n_sb=81.
    """
    re288 = {(2024, 0, '100', '0-0'): 0.90,
             (2024, 0, '010', '0-0'): 1.20,
             (2024, 1, '000', '0-0'): 0.30}
    # La celda de partida sobra de muestra; la del robo consumado no.
    n288 = {(2024, 0, '100', '0-0'): 5000,
            (2024, 0, '010', '0-0'): be.MIN_SB_BY_COUNT_N - 1,
            (2024, 1, '000', '0-0'): 5000}

    rows = be.calculate_sb_breakeven_by_count(re288, n288, [2024])
    row = next(r for r in rows if r['scenario'] == 'Runner on 1B steals 2B'
               and r['outs'] == 0 and r['count'] == '0-0')

    assert pd.isna(row['breakeven_pct'])
    assert row['n'] == 5000, 'el n publicado sigue siendo el de la celda de partida'


def test_doble_robo_pondera_el_atrapado_por_el_reparto_medido():
    """
    En el doble robo el atrapado es una mezcla: 0.863 el de delante, 0.137 el de
    atras. Ese peso se midio sobre r24Transitions y no es 0.5 ni el 0.75 que
    habia antes escrito a mano.
    """
    re_lookup = _re({
        (0, '110'): 1.6,   # antes: 1B y 2B
        (0, '011'): 2.0,   # ambos avanzan
        (0, '010'): 0.0,   # atrapan al de delante -> queda el de 1B en 2B
        (1, '010'): 1.0,
        (1, '001'): 2.0,   # atrapan al de atras -> queda el de 2B en 3B
    })
    rows = be.calculate_sb_breakeven(re_lookup, {}, [2024])
    row = next(r for r in rows if r['scenario'] == 'Double steal' and r['outs'] == 0)

    w = be.DOUBLE_STEAL_LEAD_CAUGHT_WEIGHT
    re_cs = w * 1.0 + (1 - w) * 2.0
    assert row['breakeven_pct'] == pytest.approx((1.6 - re_cs) / (2.0 - re_cs))
    assert w == pytest.approx(0.863), 'el peso medido cambio sin actualizar el test'


# ---------------------------------------------------------------------------
# Impacto de la base intencional
# ---------------------------------------------------------------------------

def test_ibb_resta_el_baseline():
    """
    run_value en las filas de evento es la RE ABSOLUTA posterior al evento, no un
    delta. El impacto de la IBB es run_value - RE(estado inicial); publicarlo
    crudo inflaba la cifra hasta 50x y forzaba el signo a ser siempre positivo.
    """
    ibb_df = pd.DataFrame([
        {'season': 2024, 'outs': 1, 'runners': '010', 'n': 100, 'run_value': 1.20},
    ])
    re_lookup = _re({(1, '010'): 0.66})

    rows = be.calculate_ibb_impact(ibb_df, [2024], re_lookup=re_lookup)

    assert len(rows) == 1
    assert rows[0]['delta_re'] == pytest.approx(1.20 - 0.66)
    assert rows[0]['delta_re_pct'] == pytest.approx((1.20 - 0.66) / 0.66)


def test_ibb_descarta_celdas_con_pocos_eventos():
    """El promedio de un punado de eventos es ruido; habia celdas con n=1."""
    ibb_df = pd.DataFrame([
        {'season': 2024, 'outs': 1, 'runners': '010', 'n': be.MIN_IBB_N - 1, 'run_value': 1.2},
        {'season': 2024, 'outs': 2, 'runners': '010', 'n': be.MIN_IBB_N, 'run_value': 0.4},
    ])
    re_lookup = _re({(1, '010'): 0.66, (2, '010'): 0.32})

    rows = be.calculate_ibb_impact(ibb_df, [2024], re_lookup=re_lookup)

    assert [r['outs'] for r in rows] == [2]


# ---------------------------------------------------------------------------
# Swing / take: modelo de tres desenlaces
# ---------------------------------------------------------------------------

def _swing_cell(**buckets):
    rows = []
    for bucket, (ev, sd, n) in buckets.items():
        rows.append({'season': 2024, 'balls': 1, 'strikes': 2, 'outs': 0,
                     'runners': '000', 'pitch_outcome': bucket,
                     'ev': ev, 'sd': sd, 'n': n})
    return pd.DataFrame(rows)


def test_swing_take_usa_tres_desenlaces():
    """
    BE = [EV_take - p_foul*EV_foul - (1-p_foul)*EV_whiff] / (EV_contact - EV_whiff)

    El foul estaba agrupado con el whiff pese a que con dos strikes son opuestos:
    el whiff es el tercer strike, el foul no resuelve nada.
    """
    df = _swing_cell(contact=(0.09, 0.5, 400), foul=(-0.01, 0.3, 400),
                     whiff=(-0.19, 0.4, 200), take=(0.02, 0.3, 500))

    rows = be.calculate_swing_take_breakeven(df)

    assert len(rows) == 1
    p_foul = 400 / (400 + 400 + 200)
    esperado = (0.02 - p_foul * -0.01 - (1 - p_foul) * -0.19) / (0.09 - -0.19)
    assert rows[0]['breakeven_pct'] == pytest.approx(esperado)


def test_swing_take_descarta_denominador_no_significativo():
    """
    El denominador debe ser significativamente POSITIVO. La guarda original era
    abs(gap) > 1e-12, y ese abs() dejaba pasar denominadores negativos que
    producian break-evens sin lectura sabermetrica.
    """
    # gap positivo pero minusculo frente a su error: no es estimable
    df = _swing_cell(contact=(0.001, 2.0, 50), foul=(-0.01, 0.3, 50),
                     whiff=(0.0, 2.0, 50), take=(0.02, 0.3, 50))

    assert be.calculate_swing_take_breakeven(df) == []


def test_swing_take_denominador_negativo_no_se_publica():
    """Contacto peor que abanicar en falso: la formula no esta definida."""
    df = _swing_cell(contact=(-0.20, 0.1, 500), foul=(-0.01, 0.1, 500),
                     whiff=(0.05, 0.1, 500), take=(0.02, 0.1, 500))

    assert be.calculate_swing_take_breakeven(df) == []


# ---------------------------------------------------------------------------
# Break-even empiricos: tag-up y avances
# ---------------------------------------------------------------------------

def test_tag_up_usa_los_fallos_como_n():
    """
    n son los FALLOS observados, no las oportunidades. Es el termino que domina
    la varianza y el peso correcto para el promedio del frontend; con las
    oportunidades totales, una celda con cientos de elevados y un solo fallo
    saldria sin marcador de confianza.
    """
    df = pd.DataFrame([
        {'season': 2024, 'outs': 1, 'runners': '001', 'direction': 'home',
         'bucket': b, 'n': n, 'mean_rv': rv, 'sd_rv': 0.1}
        for b, n, rv in [('stay', 400, -0.61), ('success', 1500, 0.12), ('fail', 23, -0.99)]
    ])

    rows = be.calculate_tagging_breakeven(df)

    assert len(rows) == 1
    assert rows[0]['n'] == 23, 'n debe ser el numero de fallos'
    assert rows[0]['breakeven_pct'] == pytest.approx((-0.61 - -0.99) / (0.12 - -0.99))


def test_tag_up_sin_fallos_no_publica():
    """Sin fallos observados el break-even no existe; no es que sea poco fiable."""
    df = pd.DataFrame([
        {'season': 2024, 'outs': 0, 'runners': '111', 'direction': 'home',
         'bucket': b, 'n': n, 'mean_rv': rv, 'sd_rv': 0.1}
        for b, n, rv in [('stay', 200, -0.7), ('success', 600, -0.2)]
    ])

    assert be.calculate_tagging_breakeven(df) == []


def test_avance_de_corredor_formula():
    df = pd.DataFrame([
        {'season': 2024, 'outs': 2, 'direction': 'second_to_home',
         'bucket': b, 'n': n, 'mean_rv': rv, 'sd_rv': 0.1}
        for b, n, rv in [('stop', 3000, 0.18), ('success', 11000, 0.90), ('fail', 1100, -0.33)]
    ])

    rows = be.calculate_advance_breakeven(df)

    assert len(rows) == 1
    assert rows[0]['scenario'] == 'Runner on 2B scores on a single'
    assert rows[0]['breakeven_pct'] == pytest.approx((0.18 - -0.33) / (0.90 - -0.33))


# ---------------------------------------------------------------------------
# Validador de rango antes de escribir
# ---------------------------------------------------------------------------

def test_el_validador_aborta_con_break_even_negativo():
    """
    Un break-even negativo implicaria que el intento mejora la RE aun fallando
    siempre. No tiene lectura sabermetrica y siempre es un artefacto.
    """
    df = pd.DataFrame({'type': ['stealing'] * 3, 'breakeven_pct': [0.7, -0.2, 0.8], 'n': [1, 1, 1]})

    with pytest.raises(ValueError, match='negativos'):
        be._validate_breakeven_column(df, 'test', group_col='type')


def test_el_validador_acepta_break_even_mayor_que_uno():
    """
    Un BE > 1 es legitimo: significa "no conviene ni al 100% de exito". Ocurre al
    robar 3B con 2 outs y, sobre todo, al decidir abanicar fuera de la zona.
    """
    df = pd.DataFrame({'type': ['stealing'] * 2, 'breakeven_pct': [0.7, 1.4], 'n': [1, 1]})

    out = be._validate_breakeven_column(df, 'test', group_col='type')

    assert out['breakeven_pct'].tolist() == [0.7, 1.4]


# ---------------------------------------------------------------------------
# Exponente pitagorico
# ---------------------------------------------------------------------------

def test_el_ajuste_recupera_el_exponente_que_genero_los_datos():
    """
    Prueba de ida y vuelta: se generan equipos cuyo win_pct sale exactamente de
    un exponente conocido, y el ajuste debe devolverlo.
    """
    exponente = 1.83
    rng = np.random.default_rng(0)
    rs = rng.integers(600, 900, 30).astype(float)
    ra = rng.integers(600, 900, 30).astype(float)
    df = pd.DataFrame({
        'runs_scored': rs,
        'runs_allowed': ra,
        'games_played': np.full(30, 162.0),
        'win_pct': rs ** exponente / (rs ** exponente + ra ** exponente),
    })

    assert pythag.fit_pythag_exponent_weighted(df) == pytest.approx(exponente, abs=1e-3)


def test_reconcile_descarta_juegos_sin_clasificar():
    """
    El view cuenta games_played como COUNT(*) pero clasifica W/L comparando
    marcadores. Un juego marcado como final sin marcador entra en el denominador
    y no en el numerador, sesgando el win_pct a la baja. Paso en 2000, con 35
    juegos que descuadraron a 26 de los 30 equipos.
    """
    df = pd.DataFrame([
        {'season': 2000, 'wins': 80.0, 'losses': 76.0, 'ties': 0.0, 'games_played': 162.0},
        {'season': 2001, 'wins': 81.0, 'losses': 81.0, 'ties': 0.0, 'games_played': 162.0},
    ])

    out = pythag.reconcile_games_played(df)

    assert out.loc[0, 'games_played'] == 156.0, 'los 6 sin clasificar salen del conteo'
    assert out.loc[0, 'win_pct'] == pytest.approx(80.0 / 156.0)
    assert out.loc[1, 'games_played'] == 162.0, 'la fila cuadrada no se toca'


def test_las_ventanas_moviles_exigen_temporadas_completas():
    """
    El gate contaba anos transcurridos, no temporadas presentes: con un hueco
    daba por completa una ventana que no lo estaba.
    """
    seasons = [2000, 2001, 2002, 2005, 2006]  # falta 2003 y 2004
    rng = np.random.default_rng(1)
    rows = []
    for s in seasons:
        for team in range(20):
            rs, ra = float(rng.integers(600, 900)), float(rng.integers(600, 900))
            wins = float(rng.integers(60, 100))
            rows.append({'season': s, 'team': str(team), 'runs_scored': rs,
                         'runs_allowed': ra, 'games_played': 162.0, 'wins': wins,
                         'losses': 162.0 - wins, 'ties': 0.0, 'win_pct': wins / 162.0})
    out = pythag.process_pythag(pd.DataFrame(rows))

    por_temporada = out.drop_duplicates('season').set_index('season')
    assert not np.isnan(por_temporada.loc[2002, 'pythag_exponent_3yr']), '2000-2002 esta completa'
    assert np.isnan(por_temporada.loc[2006, 'pythag_exponent_3yr']), '2004-2006 tiene huecos'


def test_marcar_y_permitir_lo_mismo_es_media_temporada():
    """
    RS == RA -> 0.500 sea cual sea el exponente. Es el unico punto fijo de la
    formula y el que ancla toda la escala: si esto se mueve, el exponente deja
    de ser un exponente.
    """
    for exponente in (1.0, 1.83, 2.0, 3.5):
        assert pythag.calculate_pythag_win_pct(750.0, 750.0, exponente) == pytest.approx(0.5)


def test_el_exponente_1_es_la_cuota_de_carreras():
    """Con x=1 la formula colapsa a RS/(RS+RA). Util para leer que hace x>1."""
    assert pythag.calculate_pythag_win_pct(800.0, 700.0, 1.0) == pytest.approx(800 / 1500)


def test_un_exponente_mayor_amplifica_la_ventaja():
    """
    x es la sensibilidad de las victorias al diferencial de carreras. Subirlo
    tiene que separar mas del 0.500 al equipo que marca mas, nunca acercarlo.
    """
    bajo = pythag.calculate_pythag_win_pct(800.0, 700.0, 1.5)
    alto = pythag.calculate_pythag_win_pct(800.0, 700.0, 2.5)

    assert 0.5 < bajo < alto


def test_el_ajuste_no_estima_con_un_solo_equipo():
    """
    Un punto no determina el exponente: cualquier x que pase por el reproduce el
    dato. Devolver un numero seria inventarse una estimacion.
    """
    df = pd.DataFrame([{'runs_scored': 800.0, 'runs_allowed': 700.0,
                        'games_played': 162.0, 'win_pct': 0.55}])

    assert np.isnan(pythag.fit_pythag_exponent_weighted(df))


def test_el_ajuste_descarta_las_filas_sin_carreras():
    """
    RS o RA en cero hacen indefinida la razon. El filtro va ANTES del conteo, y
    ahi es donde se nota: un equipo valido mas uno en cero son UNO, no dos, asi
    que no hay estimacion.

    (La fila en cero no podria sesgar el valor aunque entrase -- con RS=0 la
    prediccion es 0 para cualquier x, asi que suma una constante al objetivo y
    no mueve el argmin. Por eso se comprueba el conteo y no el numero.)
    """
    df = pd.DataFrame({
        'runs_scored':  [800.0, 0.0],
        'runs_allowed': [700.0, 700.0],
        'games_played': [162.0, 162.0],
        'win_pct':      [0.55, 0.30],
    })

    assert np.isnan(pythag.fit_pythag_exponent_weighted(df))


def test_el_ajuste_pesa_por_juegos_jugados():
    """
    w_i = games_played. Una temporada acortada aporta menos evidencia sobre el
    exponente que una completa, y el ajuste tiene que inclinarse al lado que mas
    juegos tiene.
    """
    def muestra(exponente, n, juegos):
        rng = np.random.default_rng(7)
        rs = rng.integers(600, 900, n).astype(float)
        ra = rng.integers(600, 900, n).astype(float)
        return pd.DataFrame({
            'runs_scored': rs, 'runs_allowed': ra,
            'games_played': np.full(n, float(juegos)),
            'win_pct': rs ** exponente / (rs ** exponente + ra ** exponente),
        })

    manda_el_largo = pd.concat([muestra(1.6, 15, 60), muestra(2.0, 15, 162)])
    manda_el_corto = pd.concat([muestra(1.6, 15, 162), muestra(2.0, 15, 60)])

    x_largo = pythag.fit_pythag_exponent_weighted(manda_el_largo)
    x_corto = pythag.fit_pythag_exponent_weighted(manda_el_corto)

    assert x_largo > x_corto, 'el grupo con mas juegos deberia arrastrar el ajuste'
    assert 1.6 < x_corto and x_largo < 2.0, 'y el resultado queda entre los dos'


# ---------------------------------------------------------------------------
# Tamano de muestra por ventana (marcador de confianza de la UI)
# ---------------------------------------------------------------------------

def _temporadas_de_equipos(rango, equipos=30):
    return pd.DataFrame([{'season': s, 'team': str(t)}
                         for s in rango for t in range(equipos)])


def test_la_n_de_cada_ventana_suma_las_temporadas_que_la_componen():
    """
    Es el n REAL de la estimacion, no el numero de temporadas: el de 3 anos se
    ajusta con tres temporadas de equipos, no con tres puntos.
    """
    out = pythag.build_window_sample_sizes(_temporadas_de_equipos(range(2000, 2026)))
    fila = out.set_index('season').loc[2025]

    assert fila['1 year_n'] == 30
    assert fila['3 years_n'] == 90
    assert fila['12 years_n'] == 360


def test_sin_ventana_completa_no_hay_n():
    """
    Misma condicion que usa el ajuste. Si publicara un n donde el exponente es
    NaN, la UI marcaria como fiable una celda vacia.
    """
    out = pythag.build_window_sample_sizes(_temporadas_de_equipos(range(2000, 2026)))
    por_temporada = out.set_index('season')

    assert por_temporada.loc[2000, '1 year_n'] == 30, 'la de 1 ano siempre esta completa'
    assert np.isnan(por_temporada.loc[2000, '3 years_n']), 'faltan 1998 y 1999'
    assert np.isnan(por_temporada.loc[2010, '12 years_n']), 'la serie empieza en 2000'
    assert por_temporada.loc[2011, '12 years_n'] == 360, '2000-2011 ya son doce'


def test_la_n_sigue_al_numero_real_de_equipos():
    """La liga tenia 15 equipos en 1901 y 30 hoy; el marcador debe reflejarlo."""
    df = pd.concat([_temporadas_de_equipos([2000, 2001], equipos=16),
                    _temporadas_de_equipos([2002], equipos=30)])

    fila = pythag.build_window_sample_sizes(df).set_index('season').loc[2002]

    assert fila['1 year_n'] == 30
    assert fila['3 years_n'] == 16 + 16 + 30


# ---------------------------------------------------------------------------
# Columnas derivadas por equipo
# ---------------------------------------------------------------------------

def _exponente_de(season):
    """Un exponente distinto por temporada para que 1yr y 3yr no coincidan."""
    return 1.6 + 0.06 * (season - 2000)


def _liga(seasons, equipos=20, semilla=3):
    """
    Liga sintetica COHERENTE: las victorias salen del diferencial de carreras
    via un exponente conocido. Con victorias aleatorias e independientes de las
    carreras el ajuste degenera al borde del intervalo y todas las ventanas
    devuelven el mismo numero, con lo que los tests dejan de distinguir nada.
    """
    rng = np.random.default_rng(semilla)
    rows = []
    for s in seasons:
        x = _exponente_de(s)
        for team in range(equipos):
            rs = float(rng.integers(600, 900))
            ra = float(rng.integers(600, 900))
            wp = rs ** x / (rs ** x + ra ** x)
            wins = round(wp * 162.0, 1)
            rows.append({'season': s, 'team': str(team),
                         'runs_scored': rs, 'runs_allowed': ra,
                         'games_played': 162.0, 'wins': wins,
                         'losses': 162.0 - wins, 'ties': 0.0, 'win_pct': wins / 162.0})
    return pd.DataFrame(rows)


def test_las_victorias_esperadas_y_las_derrotas_cubren_la_temporada():
    """
    expected_wins + expected_losses tiene que dar games_played. Es la identidad
    que hace legible la tabla: si no cierra, el win_diff no significa nada.
    """
    out = pythag.process_pythag(_liga(range(2000, 2006)))

    suma = out['expected_wins'] + out['expected_losses']
    assert np.allclose(suma, out['games_played'], atol=0.11), 'atol por el round(1) de cada lado'


def test_win_diff_es_lo_que_el_equipo_saco_de_mas():
    """
    win_diff = (W + 0.5*T) - expected_wins. Positivo = gano mas de lo que su
    diferencial de carreras predice. Los empates cuentan medio, igual que en el
    win_pct del ajuste.
    """
    df = _liga([2000, 2001])
    df.loc[0, 'ties'] = 2.0

    out = pythag.process_pythag(df)
    fila = out.loc[0]

    assert fila['win_diff'] == pytest.approx(
        round(fila['wins'] + 0.5 * 2.0 - fila['expected_wins'], 1))


def test_pythagenpat_sale_del_ritmo_de_anotacion_del_equipo():
    """
    x = ((RS+RA)/G)^0.287, por equipo y no por liga. Es la unica columna que no
    viene de un ajuste, y por eso es la mas precisa de la tabla -- de ahi que
    este exenta del marcador de confianza.
    """
    out = pythag.process_pythag(_liga([2000, 2001]))
    fila = out.loc[0]

    esperado = ((fila['runs_scored'] + fila['runs_allowed']) / fila['games_played']) ** 0.287
    assert fila['pythag_exponent_pat'] == pytest.approx(esperado)
    assert 1.5 < fila['pythag_exponent_pat'] < 2.1, 'rango historico plausible'


def test_el_win_pct_por_equipo_usa_el_exponente_de_3_anos_cuando_existe():
    """
    Prefiere el de 3 anos y cae al de 1 solo al principio de la serie, donde no
    hay ventana. El de 1 ano tiene un IC95 de +/-0.25: usarlo pudiendo usar el
    de 3 seria elegir la estimacion mas ruidosa.
    """
    out = pythag.process_pythag(_liga(range(2000, 2006))).set_index(['season', 'team'])

    primera = out.loc[(2000, '0')]
    assert np.isnan(primera['pythag_exponent_3yr']), 'en 2000 no hay ventana de 3'
    assert primera['pythag_win_pct'] == pytest.approx(pythag.calculate_pythag_win_pct(
        primera['runs_scored'], primera['runs_allowed'], primera['pythag_exponent']))

    tardia = out.loc[(2005, '0')]
    assert tardia['pythag_win_pct'] == pytest.approx(pythag.calculate_pythag_win_pct(
        tardia['runs_scored'], tardia['runs_allowed'], tardia['pythag_exponent_3yr']))


# ---------------------------------------------------------------------------
# Catalogo de extraccion
# ---------------------------------------------------------------------------

def test_los_tipos_declarados_se_aplican_y_se_comprueban():
    dataset = next(d for d in extract.DATASETS if d.key == 'events_counts')
    df = pd.DataFrame({'season': [2024], 'r24_n': [100], 'r288_n': [200]})

    out = extract._apply_dtypes(df, dataset)

    assert str(out['season'].dtype) == 'int32'
    assert str(out['r24_n'].dtype) == 'int64'


def test_falta_una_columna_esperada_y_aborta():
    """
    Si la consulta deja de devolver una columna, el fallo debe ser inmediato y
    nombrarla, no publicar un parquet incompleto.
    """
    dataset = next(d for d in extract.DATASETS if d.key == 'events_counts')
    df = pd.DataFrame({'season': [2024], 'r24_n': [100]})  # falta r288_n

    with pytest.raises(ValueError, match='r288_n'):
        extract._apply_dtypes(df, dataset)


def test_el_catalogo_no_repite_ficheros_de_salida():
    outputs = [d.output for d in extract.DATASETS]
    assert len(outputs) == len(set(outputs)), 'dos datasets escribirian el mismo fichero'


# ---------------------------------------------------------------------------
# Toque sorpresa y squeeze
# ---------------------------------------------------------------------------

def _bunt_view(**buckets):
    return pd.DataFrame([
        {'season': 2024, 'outs': 1, 'scenario': sc, 'bucket': b,
         'n': n, 'mean_rv': rv, 'sd_rv': 0.2}
        for sc, per_bucket in buckets.items()
        for b, (n, rv) in per_bucket.items()
    ])


def test_la_alternativa_al_toque_se_pondera_por_quien_toca():
    """
    Sustituye al baseline 'Standard PA' (event_cd 95), que no es un codigo de
    Retrosheet sino una agrupacion que cubre el 99.98% de los eventos: valia lo
    mismo que el promedio de la liga.

    Aqui el bateador que hace 9 de los 10 toques es el que peor batea, asi que la
    alternativa tiene que parecerse a EL, no al promedio de los dos.
    """
    effect = pd.DataFrame([
        {'season': 2024, 'batter_id': 'flojo', 'n_pa': 300, 'effect': -0.040},
        {'season': 2024, 'batter_id': 'bueno', 'n_pa': 600, 'effect': +0.030},
    ])
    weights = pd.DataFrame([
        {'season': 2024, 'outs': 1, 'scenario': 'surprise_bunt',
         'batter_id': 'flojo', 'n_bunts': 9, 'lg_rv': 0.0},
        {'season': 2024, 'outs': 1, 'scenario': 'surprise_bunt',
         'batter_id': 'bueno', 'n_bunts': 1, 'lg_rv': 0.0},
    ])

    out = be.build_bunt_swing_away(effect, weights)
    esperado = (9 * -0.040 + 1 * 0.030) / 10

    assert out.loc[(2024, 1, 'surprise_bunt'), 'swing_away'] == pytest.approx(esperado)
    assert esperado < (-0.040 + 0.030) / 2, 'sin ponderar el efecto se diluye'
    assert out.loc[(2024, 1, 'surprise_bunt'), 'effect_coverage'] == pytest.approx(1.0)


def test_los_toques_de_bateadores_sin_efecto_no_pesan_pero_se_reportan():
    """
    Un bateador con menos de 50 turnos no aparece en la vista de efectos. Sus
    toques siguen contando en el desenlace, pero no pueden pesar en la
    alternativa: la cobertura lo deja por escrito en vez de callarlo.
    """
    effect = pd.DataFrame([
        {'season': 2024, 'batter_id': 'conocido', 'n_pa': 300, 'effect': -0.02},
    ])
    weights = pd.DataFrame([
        {'season': 2024, 'outs': 1, 'scenario': 'squeeze',
         'batter_id': 'conocido', 'n_bunts': 3, 'lg_rv': 0.10},
        {'season': 2024, 'outs': 1, 'scenario': 'squeeze',
         'batter_id': 'sin_efecto', 'n_bunts': 1, 'lg_rv': 0.10},
    ])

    out = be.build_bunt_swing_away(effect, weights)

    assert out.loc[(2024, 1, 'squeeze'), 'swing_away'] == pytest.approx(0.10 - 0.02)
    assert out.loc[(2024, 1, 'squeeze'), 'effect_coverage'] == pytest.approx(3 / 4)


def test_el_squeeze_exige_fallos_observados():
    """
    Los seis break-even negativos que aparecieron tenian todos n_fail de 1 o 2,
    con hasta 19 exitos. Con un solo fallo, mean_rv_fail es un evento suelto.
    El umbral del squeeze va sobre los FALLOS, no sobre el total.
    """
    minimo = be.MIN_BUNT_N_BY_SCENARIO['squeeze']['fail']
    effect = pd.DataFrame([{'season': 2024, 'batter_id': 'b', 'n_pa': 300, 'effect': 0.0}])

    def _rows(n_fail):
        weights = pd.DataFrame([{'season': 2024, 'outs': 1, 'scenario': 'squeeze',
                                 'batter_id': 'b', 'n_bunts': 19 + n_fail, 'lg_rv': 0.0}])
        df = _bunt_view(squeeze={'success': (19, 0.30), 'fail': (n_fail, 0.22)})
        return be.calculate_bunt_breakeven(df, effect, weights)

    assert _rows(minimo - 1) == [], 'se publico una celda con menos fallos que el minimo'
    assert len(_rows(minimo)) == 1


def test_un_escenario_nuevo_sin_umbral_aborta():
    """
    Si la vista gana un escenario y nadie declara su umbral, el fallo debe ser
    inmediato: los dos que hay viven en escalas muy distintas (~460 toques por
    temporada el sorpresa, ~24 el squeeze) y heredar un umbral ajeno lo borraria
    en silencio, que es lo que paso con el squeeze al estrenarlo.
    """
    effect = pd.DataFrame([{'season': 2024, 'batter_id': 'b', 'n_pa': 300, 'effect': 0.0}])
    weights = pd.DataFrame([{'season': 2024, 'outs': 1, 'scenario': 'safety_squeeze',
                             'batter_id': 'b', 'n_bunts': 10, 'lg_rv': 0.0}])
    df = _bunt_view(safety_squeeze={'success': (8, 0.30), 'fail': (5, -0.20)})

    with pytest.raises(ValueError, match='safety_squeeze'):
        be.calculate_bunt_breakeven(df, effect, weights)


# ---------------------------------------------------------------------------
# Estirar el batazo (running)
# ---------------------------------------------------------------------------

def _stretch_rows(direction, **cubos):
    return pd.DataFrame([
        {'season': 2024, 'outs': 1, 'direction': direction, 'bucket': b,
         'n': n, 'mean_rv': rv, 'sd_rv': 0.2}
        for b, (n, rv) in cubos.items()
    ])


def test_running_usa_la_formula_de_la_familia():
    """
    BE = (Δ_stop − Δ_fail) / (Δ_success − Δ_fail), la misma que tagging y advance.
    Como RUN_VALUE es un delta, la RE inicial se cancela.
    """
    df = _stretch_rows('double', stop=(500, 0.26), success=(300, 0.43), fail=(40, -0.15))

    rows = be.calculate_running_breakeven(df)

    assert len(rows) == 1
    assert rows[0]['scenario'] == 'Single stretched to double'
    assert rows[0]['breakeven_pct'] == pytest.approx((0.26 - -0.15) / (0.43 - -0.15))


def test_running_publica_los_fallos_como_n():
    """
    Igual que tagging: la varianza la domina Δ_fail, cuyo error escala con
    1/n_fail. Con las oportunidades totales, una celda con 500 intentos y 3
    fallos saldria sin marcador de confianza.
    """
    df = _stretch_rows('triple', stop=(500, 0.20), success=(300, 0.40), fail=(3, -0.30))

    rows = be.calculate_running_breakeven(df)

    assert rows[0]['n'] == 3


def test_running_sin_los_tres_cubos_no_publica():
    """Sin fallos observados el break-even no existe; no es que sea poco fiable."""
    df = _stretch_rows('double', stop=(500, 0.26), success=(300, 0.43))

    assert be.calculate_running_breakeven(df) == []


def test_running_traduce_la_direccion_a_la_etiqueta_de_la_ui():
    """
    Las etiquetas viajan al parquet y el frontend las tiene declaradas en
    BREAKEVEN_SCENARIOS. Si dejan de casar, la tabla sale vacia -- ya paso con
    tag-up.
    """
    for direccion, etiqueta in be.LABELS_RUNNING.items():
        rows = be.calculate_running_breakeven(
            _stretch_rows(direccion, stop=(100, 0.2), success=(80, 0.4), fail=(10, -0.2))
        )
        assert rows and rows[0]['scenario'] == etiqueta, direccion


# ---------------------------------------------------------------------------
# Impacto en RE del toque de sacrificio
# ---------------------------------------------------------------------------

def test_sac_bunt_mide_lo_que_CUESTA_el_toque():
    """
    delta_re = RE(estado posterior, outs+1) - RE(estado inicial, outs). Sale
    negativo: un sacrificio con exito REGALA un out a cambio de una base, y eso
    casi siempre cuesta carreras. El subtitulo de la UI dice justo eso.
    """
    re_lookup = {(2024, 0, '100'): 0.85, (2024, 1, '010'): 0.65}
    n_lookup = {(2024, 0, '100'): 4000}

    rows = be.calculate_sac_bunt_delta_re(re_lookup, n_lookup, [2024])

    assert len(rows) == 1, 'sin RE o sin n la celda se omite, no se inventa'
    fila = rows[0]
    assert fila['label'] == 'Runner on 1B'
    assert fila['outs'] == 0
    assert fila['delta_re'] == pytest.approx(0.65 - 0.85)
    assert fila['delta_re'] < 0, 'un sacrificio con exito deberia costar RE'
    assert fila['delta_re_pct'] == pytest.approx((0.65 - 0.85) / 0.85)
    assert fila['type'] == 'sac_bunt'


def test_sac_bunt_no_se_calcula_con_dos_outs():
    """Tocar con 2 outs no tiene lectura tactica: el out acaba la entrada."""
    estados = {e for _, antes, despues in be.SAC_BUNT_SCENARIOS for e in (antes, despues)}
    re_lookup = {(2024, o, e): 0.5 for o in (0, 1, 2, 3) for e in estados}
    n_lookup = {(2024, o, e): 100 for o in (0, 1, 2, 3) for e in estados}

    rows = be.calculate_sac_bunt_delta_re(re_lookup, n_lookup, [2024])

    assert rows, 'con el lookup completo deberia producir filas'
    assert {r['outs'] for r in rows} == {0, 1}, 'no debe haber filas con 2 outs'


def test_sac_bunt_omite_la_celda_sin_frecuencia():
    """
    n viene de la matriz RE24 y es lo que decide si la fila es fiable. Sin n no
    se publica: una delta_re sin respaldo muestral es peor que no mostrar nada.
    """
    re_lookup = {(2024, 0, '100'): 0.85, (2024, 1, '010'): 0.65}

    assert be.calculate_sac_bunt_delta_re(re_lookup, {}, [2024]) == []


# ---------------------------------------------------------------------------
# Normalizador de platoon
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('entrada', ['platoon', 'PLATOON', ' true ', '1', 't', 'yes', 'y', True])
def test_platoon_reconoce_la_ventaja(entrada):
    assert be._handness_to_platoon(entrada) is True


@pytest.mark.parametrize('entrada', ['same', 'SAME', 'false', '0', 'f', 'no', 'n', False])
def test_platoon_reconoce_el_emparejamiento_igual(entrada):
    assert be._handness_to_platoon(entrada) is False


@pytest.mark.parametrize('entrada', ['X', 'L', 'R', '', None, float('nan')])
def test_un_valor_desconocido_no_se_inventa(entrada):
    """
    Devolver NA y no False: colocar un desconocido en el cubo "same" contamina
    el split de platoon con datos que nadie observo.
    """
    assert be._handness_to_platoon(entrada) is pd.NA


# ---------------------------------------------------------------------------
# Guarda de rango antes de escribir el parquet
# ---------------------------------------------------------------------------

def _be_df(valores, grupos=None):
    return pd.DataFrame({
        'breakeven_pct': valores,
        'type': grupos if grupos is not None else ['steal'] * len(valores),
    })


def test_un_breakeven_negativo_aborta_la_escritura():
    """
    Invariante duro: un break-even negativo diria que la jugada conviene con
    probabilidad de exito negativa. No es un dato ruidoso, es un signo invertido
    en el denominador. Mejor no publicar la tabla que publicarla mal.
    """
    df = _be_df([0.7, -0.2, 0.8])

    with pytest.raises(ValueError, match='negativos'):
        be._validate_breakeven_column(df, 'steal')


def test_el_abort_dice_en_que_escenario_esta_el_problema():
    """
    Sin el desglose por grupo, el mensaje obliga a rebuscar a mano cual de las
    seis familias metio el signo al reves.
    """
    df = _be_df([0.7, -0.2, -0.3], grupos=['steal', 'bunt', 'bunt'])

    with pytest.raises(ValueError, match='bunt=2'):
        be._validate_breakeven_column(df, 'todos', group_col='type')


def test_una_tabla_entera_de_nulos_no_pasa_por_valida():
    """
    Si el pipeline produce solo NaN el fallo esta aguas arriba. Escribir un
    parquet vacio deja la tabla en blanco en la web sin que nadie se entere.
    """
    with pytest.raises(ValueError, match='no hay ningun'):
        be._validate_breakeven_column(_be_df([np.nan, np.nan]), 'steal')


def test_un_breakeven_por_encima_de_1_es_legitimo_y_se_publica():
    """
    BE > 1 significa "no conviene intentarlo NUNCA": ni con 100% de exito sale
    a cuenta. Es una lectura sabermetrica valida, no un error, y tiene que
    llegar a la tabla intacta.
    """
    df = _be_df([0.7, 1.4, 2.3])

    out = be._validate_breakeven_column(df, 'steal')

    assert out['breakeven_pct'].tolist() == [0.7, 1.4, 2.3]


def test_un_valor_absurdo_aislado_se_anula_pero_no_tumba_el_pipeline():
    """
    Por encima de BREAKEVEN_SANITY_MAX ya no es una lectura, es un denominador
    casi cero. Se anula a NaN --nunca se recorta al limite, que inventaria un
    numero-- y se sigue, porque un caso suelto es casuistica.
    """
    df = _be_df([0.7] * 199 + [be.BREAKEVEN_SANITY_MAX + 1])

    out = be._validate_breakeven_column(df, 'steal')

    assert np.isnan(out['breakeven_pct'].iloc[-1]), 'el absurdo se anula'
    assert out['breakeven_pct'].iloc[:-1].notna().all(), 'el resto no se toca'


def test_el_original_no_se_muta_al_anular():
    """El validador devuelve copia: el DataFrame de entrada sigue intacto."""
    df = _be_df([0.7] * 199 + [be.BREAKEVEN_SANITY_MAX + 1])

    be._validate_breakeven_column(df, 'steal')

    assert df['breakeven_pct'].notna().all()


def test_demasiados_absurdos_dejan_de_ser_casuistica():
    """
    Si la proporcion pasa de BREAKEVEN_MAX_BAD_SHARE el problema es sistematico
    -- un filtro de muestra que no esta mordiendo, tipicamente. Anular en
    silencio ahi seria tapar el fallo y publicar una tabla llena de huecos.
    """
    n_malos = 5
    df = _be_df([0.7] * 95 + [be.BREAKEVEN_SANITY_MAX + 1] * n_malos)
    assert n_malos / 100 > be.BREAKEVEN_MAX_BAD_SHARE

    with pytest.raises(ValueError, match='abortando'):
        be._validate_breakeven_column(df, 'steal')


def test_los_nulos_no_cuentan_para_la_proporcion():
    """
    El denominador son los break-even calculados, no las filas. Contar los NaN
    diluiria la proporcion y dejaria pasar un problema sistematico.
    """
    df = _be_df([np.nan] * 900 + [0.7] * 95 + [be.BREAKEVEN_SANITY_MAX + 1] * 5)

    with pytest.raises(ValueError, match='abortando'):
        be._validate_breakeven_column(df, 'steal')


# ---------------------------------------------------------------------------
# Cubo de enfrentamiento del bateador emergente
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('entrada,esperado', [
    ('RHP vs RHB', 'RHP vs RHB'),
    ('rhp vs lhb', 'RHP vs LHB'),
    ('  LHP VS RHB  ', 'LHP vs RHB'),
    ('LHP vs LHB', 'LHP vs LHB'),
])
def test_el_enfrentamiento_se_normaliza_a_la_etiqueta_de_la_ui(entrada, esperado):
    """
    La vista SQL no garantiza mayusculas ni espacios. La etiqueta de salida es
    la que el frontend tiene declarada; si no casa, el split sale vacio.
    """
    assert be._normalize_ph_position_bucket(entrada) == esperado


@pytest.mark.parametrize('entrada', ['RHP vs SWITCH', 'RHB vs RHP', '', None])
def test_un_enfrentamiento_desconocido_no_se_coloca_en_un_cubo(entrada):
    """Cuatro combinaciones y ninguna mas; lo demas es NA, no el cubo por defecto."""
    assert be._normalize_ph_position_bucket(entrada) is pd.NA


# ---------------------------------------------------------------------------
# Swing/take por zona de Statcast
# ---------------------------------------------------------------------------

_CONTEOS = ["0-2", "1-2", "0-1", "2-2", "1-1", "0-0", "1-0", "2-1", "3-2", "2-0", "3-1", "3-0"]


def _zona(valor_por_conteo, season=2024, zone=5, split="RHP vs RHB"):
    fila = {"season": season, "zone": zone, "handedness_split": split}
    for conteo in _CONTEOS:
        fila["c_" + conteo.replace("-", "_")] = valor_por_conteo.get(conteo, 0.35)
    return pd.DataFrame([fila])


def test_la_zona_publica_los_doce_conteos():
    """
    La tabla es zona x conteo; si un conteo se cae del mapa, la columna aparece
    vacia en la web sin que nada falle.
    """
    rows = be.calculate_swing_take_zone_breakeven(_zona({}), {})

    conteos = {r["scenario"].split(" | ")[0].replace("Count ", "") for r in rows}
    assert conteos == set(_CONTEOS)


def test_la_etiqueta_de_zona_lleva_zona_y_split():
    """El frontend parte por ' | '; el orden y el separador son contrato."""
    rows = be.calculate_swing_take_zone_breakeven(_zona({}, zone=14, split="LHP vs LHB"), {})

    assert rows[0]["label"] == "Zone 14 | LHP vs LHB"
    assert rows[0]["scenario"] == "Count 0-2 | Zone 14 | LHP vs LHB"
    assert rows[0]["type"] == be.BREAKEVEN_TYPE_SWING_TAKE_ZONE


def test_un_conteo_sin_dato_se_omite_y_no_se_publica_como_cero():
    """
    NaN es "no medido"; 0.0 es "abanica siempre". Confundirlos pinta la celda
    del color de la decision mas agresiva sin tener el dato.
    """
    df = _zona({})
    df.loc[0, "c_3_0"] = np.nan

    rows = be.calculate_swing_take_zone_breakeven(df, {})

    assert not any(r["scenario"].startswith("Count 3-0 ") for r in rows)
    assert len(rows) == len(_CONTEOS) - 1


def test_un_breakeven_negativo_en_zona_se_lleva_a_cero_pero_no_se_pierde():
    """
    Con 2 strikes y lanzamiento en zona, abanicar sin conectar bate a tomar --el
    foul no cuesta nada-- y el numerador sale negativo. La DECISION es la misma
    que con 0%: abanica siempre. Se lleva a 0 porque un porcentaje negativo en la
    tabla se lee como un error; se pierde el margen, no la conclusion.
    """
    rows = be.calculate_swing_take_zone_breakeven(_zona({"0-2": -0.161}), {})
    fila = next(r for r in rows if r["scenario"].startswith("Count 0-2 "))

    assert fila["breakeven_pct"] == 0.0
    assert len(rows) == len(_CONTEOS), "la celda se conserva, no se descarta"


def test_la_n_de_zona_casa_por_temporada_zona_split_y_conteo():
    """
    Son cuatro claves. Si el lookup se arma con menos, todas las celdas de una
    zona comparten n y el marcador de confianza deja de significar nada.
    """
    n_lookup = {(2024, 5, "RHP vs RHB", "0-2"): 812}

    rows = be.calculate_swing_take_zone_breakeven(_zona({}), n_lookup)
    por_conteo = {r["scenario"].split(" | ")[0]: r["n"] for r in rows}

    assert por_conteo["Count 0-2"] == 812
    assert por_conteo["Count 3-0"] == 0, "sin muestra medida, n=0 y la UI lo marca"


def test_la_zona_exige_sus_columnas():
    """El nombre de la vista cambia; el fallo tiene que ser ruidoso."""
    df = _zona({}).drop(columns=["c_3_0"])

    with pytest.raises(ValueError, match="c_3_0"):
        be.calculate_swing_take_zone_breakeven(df, {})


# ---------------------------------------------------------------------------
# Normalizacion de la vista de TTO
# ---------------------------------------------------------------------------

def _tto(filas):
    return pd.DataFrame(filas)


def test_tto_4plus_se_pliega_sobre_tto3():
    """
    La cuarta vuelta al orden es una muestra pequeña y sesgada --solo la ven los
    abridores que van dominando--, asi que se agrupa con la tercera en vez de
    publicarse como un cubo propio que se leeria como una tendencia.
    """
    df = _tto([
        {"season": 2024, "runners": "000", "handedness_split": "RHP vs RHB",
         "tto_pass": v, "n": 100, "delta_run_value": 0.02}
        for v in ("4+", "TTO4+", "TTO-4PLUS", "4PLUS")
    ])

    out = be._normalize_tto_impact_view_df(df)

    assert set(out["tto_pass"]) == {"TTO-3"}


@pytest.mark.parametrize("entrada,esperado", [
    ("1", "TTO-1"), ("TTO2", "TTO-2"), ("tto-3", "TTO-3"), (" 2 ", "TTO-2"),
])
def test_la_vuelta_al_orden_se_normaliza(entrada, esperado):
    df = _tto([{"season": 2024, "runners": "000", "handedness_split": "RHP vs RHB",
                "tto_pass": entrada, "n": 100, "delta_run_value": 0.02}])

    assert be._normalize_tto_impact_view_df(df)["tto_pass"].iloc[0] == esperado


def test_la_primera_vuelta_es_el_cero_exacto():
    """
    TTO-1 es el baseline por definicion. Sin forzarlo, el redondeo deja un
    -0.00 que la UI pinta de rojo: el lector ve una penalizacion en la vuelta
    que es, literalmente, la referencia.
    """
    df = _tto([{"season": 2024, "runners": "000", "handedness_split": "RHP vs RHB",
                "tto_pass": "TTO-1", "n": 100, "delta_run_value": -1e-9,
                "delta_run_value_scaled": -1e-9}])

    out = be._normalize_tto_impact_view_df(df)

    assert out["delta_re"].iloc[0] == 0.0
    assert out["delta_re_pct"].iloc[0] == 0.0
    assert not np.signbit(out["delta_re"].iloc[0]), "ni siquiera -0.0"


def test_las_vueltas_siguientes_conservan_su_delta():
    """Control: forzar el cero solo debe tocar TTO-1."""
    df = _tto([{"season": 2024, "runners": "000", "handedness_split": "RHP vs RHB",
                "tto_pass": t, "n": 100, "delta_run_value": 0.031}
               for t in ("TTO-1", "TTO-2", "TTO-3")])

    out = be._normalize_tto_impact_view_df(df).set_index("tto_pass")

    assert out.loc["TTO-1", "delta_re"] == 0.0
    assert out.loc["TTO-2", "delta_re"] == pytest.approx(0.031)
    assert out.loc["TTO-3", "delta_re"] == pytest.approx(0.031)


def test_el_estado_de_corredores_se_rellena_a_tres_digitos():
    """
    SQL Server devuelve '0' donde el codigo es '000'. Sin el zfill la fila no
    casa con RUNNERS_LABELS y la tabla muestra el codigo crudo.
    """
    df = _tto([{"season": 2024, "runners": 0, "handedness_split": "RHP vs RHB",
                "tto_pass": "TTO-2", "n": 100, "delta_run_value": 0.02}])

    out = be._normalize_tto_impact_view_df(df)

    assert out["scenario"].iloc[0] == "000"
    assert out["label"].iloc[0] == be.RUNNERS_LABELS["000"]


def test_un_estado_sin_etiqueta_muestra_el_codigo_y_no_un_hueco():
    """Fallback explicito: peor que un codigo crudo es una celda vacia."""
    df = _tto([{"season": 2024, "runners": "999", "handedness_split": "RHP vs RHB",
                "tto_pass": "TTO-2", "n": 100, "delta_run_value": 0.02}])

    assert be._normalize_tto_impact_view_df(df)["label"].iloc[0] == "999"


def test_tto_exige_sus_columnas():
    df = _tto([{"season": 2024, "runners": "000", "n": 100, "delta_run_value": 0.02}])

    with pytest.raises(ValueError, match=r"handedness_split|tto_pass"):
        be._normalize_tto_impact_view_df(df)


# ---------------------------------------------------------------------------
# Normalizacion de la vista de platoon baseline
# ---------------------------------------------------------------------------

def _platoon(filas):
    return pd.DataFrame(filas)


_PLATOON_BASE = {"season": 2024, "outs": 0, "handedness_split": " RHP vs LHB ",
                 "n": 500, "delta_run_value": 0.021}


def test_el_porcentaje_de_platoon_se_toma_del_sql_si_viene():
    """
    Cuando la vista ya trae delta_run_value_scaled se usa tal cual. Recalcularlo
    aqui daria dos numeros distintos para la misma celda segun la rama.
    """
    df = _platoon([{**_PLATOON_BASE, "baseline_avg_run_value": 0.7,
                    "delta_run_value_scaled": 0.123}])

    out = be._normalize_platoon_impact_view_df(df, {})

    assert out["delta_re_pct"].iloc[0] == pytest.approx(0.123)
    assert out["delta_re_pct"].iloc[0] != pytest.approx(0.021 / 0.7), 'no lo recalcula'


def test_sin_columna_del_sql_el_porcentaje_se_calcula_sobre_el_baseline():
    df = _platoon([{**_PLATOON_BASE, "baseline_avg_run_value": 0.7}])

    out = be._normalize_platoon_impact_view_df(df, {})

    assert out["delta_re_pct"].iloc[0] == pytest.approx(0.021 / 0.7)


def test_un_baseline_exactamente_cero_no_produce_un_porcentaje():
    """
    delta/0 no tiene valor. Se suprime a NA -- la celda queda vacia en vez de
    gritar una cifra falsa -- y el delta absoluto se conserva, que es la lectura
    que si es valida.
    """
    df = _platoon([{**_PLATOON_BASE, "baseline_avg_run_value": 0.0}])

    out = be._normalize_platoon_impact_view_df(df, {})

    assert pd.isna(out["delta_re_pct"].iloc[0])
    assert out["delta_re"].iloc[0] == pytest.approx(0.021), 'el delta absoluto sobrevive'


def test_el_umbral_de_baseline_pequeno_viene_apagado_de_fabrica():
    """
    RE_IMPACT_PCT_MIN_BASELINE_ABS es 0 por defecto: la supresion por baseline
    pequeño NO esta activa salvo que se ponga por variable de entorno. Conviene
    que este escrito, porque leyendo el codigo parece un filtro siempre vivo.
    """
    assert be.RE_IMPACT_PCT_MIN_BASELINE_ABS == 0.0

    df = _platoon([{**_PLATOON_BASE, "baseline_avg_run_value": 1e-9}])
    out = be._normalize_platoon_impact_view_df(df, {})

    assert not pd.isna(out["delta_re_pct"].iloc[0]), 'apagado: el porcentaje sale igual'


def test_con_el_umbral_puesto_se_suprime_el_baseline_pequeno(monkeypatch):
    """
    Y encendido tiene que morder: un baseline por debajo del umbral produce
    porcentajes de miles por ciento a partir de ruido.
    """
    monkeypatch.setattr(be, 'RE_IMPACT_PCT_MIN_BASELINE_ABS', 0.05)

    pequeno = _platoon([{**_PLATOON_BASE, "baseline_avg_run_value": 0.01}])
    holgado = _platoon([{**_PLATOON_BASE, "baseline_avg_run_value": 0.70}])

    assert pd.isna(be._normalize_platoon_impact_view_df(pequeno, {})["delta_re_pct"].iloc[0])
    assert be._normalize_platoon_impact_view_df(holgado, {})["delta_re_pct"].iloc[0] ==         pytest.approx(0.021 / 0.70), 'el control: por encima del umbral no se toca'


def test_sin_baseline_en_la_vista_se_busca_en_la_matriz_re24():
    """
    Fallback documentado: si la vista no trae el baseline, se saca de la matriz
    por (temporada, outs, corredores). Requiere runners, y sin el aborta en vez
    de dejar la columna en blanco.
    """
    # runners=10 y no 100: con 100 el zfill no hace nada y el test no probaria
    # el relleno. SQL Server devuelve el estado como entero, asi que '010'
    # (corredor en 2B) llega como 10 y sin rellenar no casa con la matriz.
    df = _platoon([{**_PLATOON_BASE, "runners": 10}])
    re_lookup = {(2024, 0, "010"): 0.85, (2024, 0, "10"): 9.99}

    out = be._normalize_platoon_impact_view_df(df, re_lookup)

    assert out["baseline_re"].iloc[0] == pytest.approx(0.85), 'runners=10 -> "010"'
    assert out["delta_re_pct"].iloc[0] == pytest.approx(0.021 / 0.85)


def test_sin_baseline_y_sin_runners_no_se_publica_nada():
    df = _platoon([_PLATOON_BASE])

    with pytest.raises(ValueError, match="baseline_avg_run_value"):
        be._normalize_platoon_impact_view_df(df, {})


def test_el_split_de_platoon_se_limpia_para_casar_con_la_ui():
    """El espacio sobrante de la vista rompe el emparejamiento con el frontend."""
    df = _platoon([{**_PLATOON_BASE, "baseline_avg_run_value": 0.7}])

    out = be._normalize_platoon_impact_view_df(df, {})

    assert out["handedness_split"].iloc[0] == "RHP vs LHB"
    assert out["scenario"].iloc[0] == "RHP vs LHB"
    assert out["label"].iloc[0] == "RHP vs LHB"


def test_platoon_exige_sus_columnas():
    df = _platoon([{"season": 2024, "outs": 0, "n": 500}])

    with pytest.raises(ValueError, match=r"handedness_split|delta_run_value"):
        be._normalize_platoon_impact_view_df(df, {})


# ---------------------------------------------------------------------------
# Normalizacion de las vistas de bateador emergente
# ---------------------------------------------------------------------------

def _ph(filas, season=2024, outs=0):
    return pd.DataFrame([
        {"season": season, "outs": outs, "handedness_bucket": cubo,
         "n": n, "delta_run_value": delta}
        for cubo, n, delta in filas
    ])


def test_same_y_platoon_son_medias_PONDERADAS_de_sus_dos_cubos():
    """
    Same = media de (RHP vs RHB, LHP vs LHB); Platoon = media de los cruzados.
    Ponderada por n y no simple: los enfrentamientos entre diestros son varias
    veces mas frecuentes, y una media simple les daria el mismo peso que a los
    zurdo-zurdo, que son los mas raros del juego.
    """
    df = _ph([("RHP vs RHB", 900, 0.10), ("LHP vs LHB", 100, 0.50),
              ("RHP vs LHB", 400, -0.20), ("LHP vs RHB", 600, 0.30)])

    out = be._normalize_ph_view_df(df, "ph_position", "position").set_index("handedness_split")

    assert out.loc["Same", "delta_re"] == pytest.approx((900 * 0.10 + 100 * 0.50) / 1000)
    assert out.loc["Same", "delta_re"] != pytest.approx(0.30), 'la media simple daria 0.30'
    assert out.loc["Platoon", "delta_re"] == pytest.approx((400 * -0.20 + 600 * 0.30) / 1000)
    assert out.loc["Same", "n"] == 1000, 'la n del sintetico suma las dos fuentes'


def test_el_sintetico_no_cruza_temporadas_ni_outs():
    """
    Se agrupa por (temporada, outs). Si se agregase por encima, la fila de 2024
    con 0 outs llevaria dentro datos de 2023 y de 2 outs.
    """
    df = pd.concat([
        _ph([("RHP vs RHB", 100, 0.10), ("LHP vs LHB", 100, 0.10)], season=2023, outs=0),
        _ph([("RHP vs RHB", 100, 0.90), ("LHP vs LHB", 100, 0.90)], season=2024, outs=0),
        _ph([("RHP vs RHB", 100, 0.50), ("LHP vs LHB", 100, 0.50)], season=2024, outs=2),
    ])

    out = be._normalize_ph_view_df(df, "ph_position", "position")
    same = out[out["handedness_split"] == "Same"].set_index(["season", "outs"])

    assert len(same) == 3
    assert same.loc[(2024, 0), "delta_re"] == pytest.approx(0.90)
    assert same.loc[(2024, 2), "delta_re"] == pytest.approx(0.50)
    assert same.loc[(2023, 0), "delta_re"] == pytest.approx(0.10)


def test_el_sintetico_sale_aunque_falte_uno_de_los_dos_cubos():
    """
    Con un solo cubo presente, Same es ese cubo. Exigir los dos dejaria sin fila
    a las temporadas antiguas, donde faltan splits.
    """
    df = _ph([("RHP vs RHB", 300, 0.12)])

    out = be._normalize_ph_view_df(df, "ph_position", "position").set_index("handedness_split")

    assert out.loc["Same", "delta_re"] == pytest.approx(0.12)
    assert out.loc["Same", "n"] == 300
    assert "Platoon" not in out.index, 'sin cubos cruzados no se inventa la fila'


def test_un_cubo_con_n_cero_no_produce_un_sintetico():
    """Sin muestra no hay media ponderada: el divisor seria cero."""
    df = _ph([("RHP vs RHB", 0, 0.12), ("LHP vs LHB", 0, 0.30)])

    out = be._normalize_ph_view_df(df, "ph_position", "position")

    assert "Same" not in set(out["handedness_split"])


def test_un_cubo_que_no_se_reconoce_se_descarta_y_no_contamina_el_sintetico():
    """
    Un valor no mapeado (un switch hitter, una etiqueta nueva de la vista) sale
    de la tabla. Si se colara, entraria en la media ponderada de Same o Platoon
    sin que nadie sepa que representa.
    """
    df = _ph([("RHP vs RHB", 100, 0.10), ("RHP vs SWITCH", 900, 5.00)])

    out = be._normalize_ph_view_df(df, "ph_position", "position")

    # La fila se va de la tabla. No basta con mirar que no aparezca la etiqueta:
    # sin el filtro el cubo se convierte en NA y la fila SIGUE ahi, con su n de
    # 900 y su delta de 5.00, como una celda sin nombre.
    assert out["handedness_split"].notna().all(), 'quedo una fila sin cubo'
    assert set(out["handedness_split"]) == {"RHP vs RHB", "Same"}
    assert out["n"].sum() == 100 + 100, 'los 900 del intruso no entran por ningun lado'

    same = out.set_index("handedness_split").loc["Same"]
    assert same["delta_re"] == pytest.approx(0.10), 'el intruso no pesa en la media'
    assert same["n"] == 100


def test_los_cubos_salen_en_el_orden_declarado():
    """
    El orden es contrato con la UI: los cuatro reales primero y los dos
    sinteticos al final. Ordenar alfabeticamente pondria 'Platoon' y 'Same'
    en medio de los reales.
    """
    df = _ph([("LHP vs RHB", 100, 0.1), ("RHP vs RHB", 100, 0.2),
              ("LHP vs LHB", 100, 0.3), ("RHP vs LHB", 100, 0.4)])

    out = be._normalize_ph_view_df(df, "ph_position", "position")

    assert out["handedness_split"].tolist() == be.PH_POSITION_BUCKET_ORDER


def test_la_misma_funcion_sirve_a_las_dos_vistas_de_ph():
    """
    Reemplazo de jugador de posicion y de lanzador tienen la misma forma; el
    type es lo unico que cambia. Eran dos funciones de 45 lineas identicas.
    """
    df = _ph([("RHP vs RHB", 100, 0.1)])

    for impact_type in ("ph_position", "ph_pitcher"):
        out = be._normalize_ph_view_df(df, impact_type, "x")
        assert set(out["type"]) == {impact_type}


def test_ph_exige_sus_columnas():
    df = pd.DataFrame([{"season": 2024, "outs": 0, "n": 100}])

    with pytest.raises(ValueError, match=r"handedness_bucket|delta_run_value"):
        be._normalize_ph_view_df(df, "ph_position", "position")


# ---------------------------------------------------------------------------
# Bucketing de colas de Win Expectancy (CR-04-03)
# ---------------------------------------------------------------------------

def _we_filas(diferenciales, season=2024, team=1, inn=1, outs=0, runners="000"):
    return pd.DataFrame([
        {"SEASON": season, "INN_CT": inn, "BAT_HOME_ID": team, "OUTS_CT": outs,
         "RUNNERS": runners, "RUNS_DIFF": d, "n": n, "WE": we}
        for d, n, we in diferenciales
    ])


def _we(df, desde=2024, hasta=2024, team=1):
    from app.services import table_generator as tg
    return tg.generate_win_expectancy_table({"win_expectancy": df}, desde, hasta, team)


# La tabla publica la WE en porcentaje, no en probabilidad.
_PCT = 100


def test_we_no_saca_columnas_fuera_de_la_escala():
    """
    WPA y LI recortan a +-4; Win Expectancy solo etiquetaba. Una vista que
    empezara a emitir -5 le sacaria a WE columnas '-5'/'+5' que las otras dos no
    tienen, y las tres tablas dejarian de ser comparables.
    """
    df = _we_filas([(-6, 10, 0.05), (-5, 20, 0.08), (-4, 30, 0.12),
                    (0, 100, 0.50), (4, 30, 0.88), (5, 20, 0.92), (6, 10, 0.95)])

    headers = _we(df)["headers"]

    assert "-5" not in headers and "+5" not in headers
    assert headers == ["Inning", "Outs", "Runners", "-4+", "Tie", "+4+"]


def test_las_colas_se_pliegan_ponderadas_por_n_y_no_se_pierde_ninguna():
    """
    El pivot usa aggfunc="first": si las filas plegadas llegaran sin agregar, se
    quedaria con UNA y tiraria las otras -- su probabilidad y su muestra. La cola
    '-4+' tiene que ser la media ponderada de -6, -5 y -4.
    """
    df = _we_filas([(-6, 10, 0.05), (-5, 20, 0.08), (-4, 30, 0.12), (0, 100, 0.50)])

    fila = _we(df)["rows"][0]
    esperado = (10 * 0.05 + 20 * 0.08 + 30 * 0.12) / 60

    assert fila["-4+"] == pytest.approx(esperado * _PCT)
    assert fila["-4+"] != pytest.approx(0.05 * _PCT), 'se quedo con la primera y tiro las otras'
    assert fila["-4+"] != pytest.approx(0.12 * _PCT), 'ni con la del propio -4'


def test_la_muestra_de_la_cola_suma_las_tres_celdas():
    """
    El n alimenta el marcador de fiabilidad. Si solo llevara el del -4, la cola
    saldria marcada como menos fiable de lo que es.
    """
    df = _we_filas([(-6, 10, 0.05), (-5, 20, 0.08), (-4, 30, 0.12), (0, 100, 0.50)])

    fila = _we(df)["rows"][0]

    assert fila["-4+_n"] == 60


def test_una_sola_temporada_tambien_pliega():
    """
    La rama de temporada unica tomaba las filas tal cual, sin agregar. Correcta
    mientras cada celda fuera una sola fila; con el clip puesto deja de serlo.
    """
    df = _we_filas([(-5, 40, 0.10), (-4, 60, 0.20)], season=2024)

    fila = _we(df, 2024, 2024)["rows"][0]

    assert fila["-4+"] == pytest.approx((40 * 0.10 + 60 * 0.20) / 100 * _PCT)
    assert fila["-4+_n"] == 100


def test_el_interior_de_la_escala_no_se_toca():
    """Control: el clip solo debe morder en las colas."""
    df = _we_filas([(-3, 10, 0.20), (-1, 10, 0.40), (0, 10, 0.50), (2, 10, 0.65)])

    fila = _we(df)["rows"][0]

    assert fila["-3"] == pytest.approx(0.20 * _PCT)
    assert fila["-1"] == pytest.approx(0.40 * _PCT)
    assert fila["Tie"] == pytest.approx(0.50 * _PCT)
    assert fila["+2"] == pytest.approx(0.65 * _PCT)


# ---------------------------------------------------------------------------
# Columnas de tres caracteres (CR-06-01)
# ---------------------------------------------------------------------------

def test_un_estado_de_corredores_con_basura_aborta_en_vez_de_recortarse():
    """
    Era `astype("string").str[:3]`: un `100X` de origen se publicaba como `100`,
    un estado base-out valido en apariencia que entra en denominadores y medias
    ponderadas de media docena de tablas sin que nada lo delate.

    El estado base-out es la unidad atomica de casi todo el sitio, asi que es
    preferible no publicar a publicar una corrupcion que parece un dato.
    """
    df = pd.DataFrame({'runners': ['100X', '010']})

    with pytest.raises(ValueError, match='tres caracteres'):
        extract._normalizar_string3(df, 'runners', 're24_values')


def test_el_mensaje_dice_que_valores_son_los_malos():
    """Sin los valores, el aviso obliga a rebuscar a mano en la vista."""
    df = pd.DataFrame({'runners': ['100X', '010', 'basura']})

    with pytest.raises(ValueError, match=r"100X|basura"):
        extract._normalizar_string3(df, 'runners', 're24_values')


def test_los_espacios_sobrantes_si_se_limpian():
    """
    Limpiar no es lo mismo que recortar: quitar espacios preserva el valor, y
    cortar a tres caracteres lo inventa.
    """
    df = pd.DataFrame({'runners': [' 100 ', '010']})

    assert extract._normalizar_string3(df, 'runners', 'x').tolist() == ['100', '010']


def test_los_conteos_tambien_pasan_por_la_validacion():
    """
    La marca la comparten dos columnas con alfabetos distintos --corredores y
    conteo--, y la longitud es lo que tienen en comun.
    """
    ok = pd.DataFrame({'count': ['0-0', '3-2']})
    assert extract._normalizar_string3(ok, 'count', 'x').tolist() == ['0-0', '3-2']

    with pytest.raises(ValueError, match='tres caracteres'):
        extract._normalizar_string3(pd.DataFrame({'count': ['10-2']}), 'count', 'x')


def test_los_nulos_no_disparan_la_validacion():
    """Un NaN es ausencia de dato, no un valor corrupto."""
    df = pd.DataFrame({'runners': pd.array(['100', None], dtype='string')})

    assert extract._normalizar_string3(df, 'runners', 'x').isna().sum() == 1


# ---------------------------------------------------------------------------
# Marca de commit del lote (CR-06-06)
# ---------------------------------------------------------------------------

def test_una_extraccion_completa_sin_marca_de_commit_no_se_promueve(tmp_path):
    """
    last_updated.json es lo que la web ensena como fecha de los datos. Si por
    una regresion dejara de escribirse, el lote se promovia igual y quedaban
    datos nuevos con una marca de frescura vieja.
    """
    staging, final = tmp_path / 'stage', tmp_path / 'final'
    staging.mkdir()
    final.mkdir()
    (staging / 'x.parquet').write_bytes(b'x' * 10)

    with pytest.raises(RuntimeError, match='last_updated'):
        extract._promote_staging(staging, final, ['x.parquet'], completa=True)

    assert not (final / 'x.parquet').exists(), 'app/data tiene que quedar intacto'


def test_una_extraccion_parcial_no_exige_la_marca(tmp_path):
    """
    Con --dataset no se reescribe a proposito: seguiria describiendo un lote que
    ya no es el que hay.
    """
    staging, final = tmp_path / 'stage', tmp_path / 'final'
    staging.mkdir()
    final.mkdir()
    (staging / 'x.parquet').write_bytes(b'x' * 10)

    extract._promote_staging(staging, final, ['x.parquet'], completa=False)

    assert (final / 'x.parquet').exists()


# ---------------------------------------------------------------------------
# Escritura de los parquets del pitagorico
# ---------------------------------------------------------------------------

def _liga_procesada(seasons_rango=range(2000, 2013), equipos=20):
    """Una liga sintetica coherente, ya pasada por process_pythag."""
    df = _liga(seasons_rango, equipos=equipos)
    return pythag.process_pythag(df)


def _ip(seasons_rango=range(2000, 2013)):
    return pd.DataFrame({"season": list(seasons_rango),
                         "Total_IP": [43000.0] * len(list(seasons_rango))})


def test_el_parquet_del_pitagorico_tiene_una_fila_por_temporada(tmp_path):
    """
    La tabla de la web es una fila por temporada. Si la escritura dejara una por
    equipo, saldrian treinta filas identicas por anio.
    """
    df = _liga_procesada()

    salida = pythag.save_pythag_data(df, df_team=df, data_dir=tmp_path)

    escrito = pd.read_parquet(tmp_path / "pythag_exponents.parquet")
    assert len(escrito) == escrito["season"].nunique()
    assert len(escrito) == len(salida)


def test_las_columnas_publicadas_son_las_que_espera_la_web(tmp_path):
    """
    Los nombres viajan tal cual a las cabeceras de la tabla. Si cambian sin que
    cambie el frontend, la columna sale vacia.
    """
    df = _liga_procesada()

    pythag.save_pythag_data(df, df_team=df, data_dir=tmp_path)

    columnas = set(pd.read_parquet(tmp_path / "pythag_exponents.parquet").columns)
    assert {"season", "1 year", "3 years", "12 years", "Pythagenpat"} <= columnas


def test_cada_ventana_lleva_su_tamano_de_muestra(tmp_path):
    """
    El `_n` alimenta el marcador de fiabilidad. Sin el, la columna de un anio se
    presenta con el mismo peso visual que la de doce, y tiene un intervalo de
    mas o menos 0.25 sobre valores que van de 1.6 a 2.0.
    """
    df = _liga_procesada()

    pythag.save_pythag_data(df, df_team=df, data_dir=tmp_path)

    escrito = pd.read_parquet(tmp_path / "pythag_exponents.parquet")
    for col in ("1 year", "3 years", "12 years"):
        assert f"{col}_n" in escrito.columns, f"falta el n de {col}"


def test_la_temporada_se_escribe_como_entero(tmp_path):
    """Un float en season sale como 2024.0 en la tabla."""
    df = _liga_procesada()

    pythag.save_pythag_data(df, df_team=df, data_dir=tmp_path)

    escrito = pd.read_parquet(tmp_path / "pythag_exponents.parquet")
    assert escrito["season"].dtype == "int32"
    assert escrito["1 year_n"].dtype == "Int32", "el n admite nulos: ventana incompleta"


def test_el_pythagenpat_publicado_es_el_promedio_de_la_liga(tmp_path):
    """
    Se calcula por equipo --((RS+RA)/G)^0.287-- y la tabla publica una fila por
    temporada, asi que hay que promediar. Coger el del primer equipo daria un
    numero que no representa a nadie.
    """
    df = _liga_procesada()

    pythag.save_pythag_data(df, df_team=df, data_dir=tmp_path)

    escrito = pd.read_parquet(tmp_path / "pythag_exponents.parquet").set_index("season")
    esperado = df[df.season == 2005]["pythag_exponent_pat"].mean()
    assert escrito.loc[2005, "Pythagenpat"] == pytest.approx(esperado, abs=1e-5)


def test_runs_per_win_se_escribe_con_sus_columnas(tmp_path):
    df = _liga_procesada()
    exponentes = pythag.save_pythag_data(df, df_team=df, data_dir=tmp_path)

    pythag.save_rpw_data(exponentes, df, _ip(), data_dir=tmp_path)

    escrito = pd.read_parquet(tmp_path / "runs_per_win.parquet")
    columnas = set(escrito.columns)
    assert {"season", "1 year", "Pythagenpat", "FanGraphs"} <= columnas
    assert escrito["season"].dtype == "int32"


def test_las_carreras_por_victoria_caen_en_el_rango_conocido(tmp_path):
    """
    Historicamente van de 8 a 11 carreras por victoria. Un valor fuera de ahi
    indica que el ritmo de anotacion o el exponente entraron mal, y es un numero
    que nadie mira dos veces si sale plausible.
    """
    df = _liga_procesada()
    exponentes = pythag.save_pythag_data(df, df_team=df, data_dir=tmp_path)

    pythag.save_rpw_data(exponentes, df, _ip(), data_dir=tmp_path)

    escrito = pd.read_parquet(tmp_path / "runs_per_win.parquet")
    valores = escrito["1 year"].dropna()
    assert len(valores) > 0
    assert valores.between(6.0, 14.0).all(), valores.describe()


def test_fangraphs_necesita_las_entradas_lanzadas(tmp_path):
    """
    La formula de FanGraphs es 9 * (RS_liga / IP_liga) * 1.5 + 3. Sin IP no se
    puede calcular, y publicarla a cero seria peor que no publicarla.
    """
    df = _liga_procesada()
    exponentes = pythag.save_pythag_data(df, df_team=df, data_dir=tmp_path)

    pythag.save_rpw_data(exponentes, df, _ip(), data_dir=tmp_path)

    escrito = pd.read_parquet(tmp_path / "runs_per_win.parquet")
    assert escrito["FanGraphs"].notna().any()
    assert escrito["FanGraphs"].dropna().between(6.0, 14.0).all()


# ---------------------------------------------------------------------------
# El orquestador del pitagorico (run) y su linea de comandos
# ---------------------------------------------------------------------------

@pytest.fixture
def pythag_sin_base(monkeypatch, tmp_path):
    """
    Sustituye la consulta a SQL Server por dos DataFrames y redirige la
    escritura a un directorio temporal. No se abre ninguna conexion.
    """
    equipos = _liga(range(2000, 2013))
    ip = pd.DataFrame({"season": sorted(equipos.season.unique()), "Total_IP": 43000.0})
    consultado = []

    def falso_read_sql(sql, _engine):
        consultado.append(sql)
        return (ip.copy() if "mlbPitching" in sql else equipos.copy())

    monkeypatch.setattr(pythag.pd, "read_sql", falso_read_sql)
    monkeypatch.setattr(pythag, "get_connection", lambda: object())
    monkeypatch.setattr(pythag.db, "dispose_engine", lambda: None)

    escrituras = {}
    for nombre in ("save_pythag_data", "save_rpw_data"):
        original = getattr(pythag, nombre)

        def envoltorio(*a, _o=original, _n=nombre, **k):
            escrituras[_n] = True
            k["data_dir"] = tmp_path
            return _o(*a, **k)

        monkeypatch.setattr(pythag, nombre, envoltorio)

    return consultado, escrituras, tmp_path


def test_run_consulta_calcula_y_escribe(pythag_sin_base):
    consultado, escrituras, destino = pythag_sin_base

    pythag.run()

    assert len(consultado) == 2, "una consulta para equipos y otra para entradas"
    assert escrituras == {"save_pythag_data": True, "save_rpw_data": True}
    assert (destino / "pythag_exponents.parquet").exists()
    assert (destino / "runs_per_win.parquet").exists()


def test_run_acota_las_consultas_al_rango(pythag_sin_base):
    """
    Los marcadores tienen que salir resueltos. Un `{SEASON_MAX}` que llegue a
    SQL sin sustituir es un error de sintaxis en plena ejecucion anual.
    """
    consultado, _, _ = pythag_sin_base

    pythag.run()

    for sql in consultado:
        assert "{SEASON" not in sql
        assert f"BETWEEN {seasons.MIN_SEASON} AND {seasons.MAX_SEASON}" in sql


def test_run_recorta_lo_que_venga_fuera_de_rango(monkeypatch, tmp_path):
    """
    Filtro de garantia: si la vista devolviera temporadas fuera del rango --como
    hacia swing/take con la temporada en curso-- no deben llegar al parquet.
    """
    equipos = _liga(range(2000, 2013))
    intrusa = _liga([1899])
    con_intrusa = pd.concat([equipos, intrusa], ignore_index=True)
    ip = pd.DataFrame({"season": sorted(con_intrusa.season.unique()), "Total_IP": 43000.0})

    monkeypatch.setattr(pythag.pd, "read_sql",
                        lambda sql, _e: (ip.copy() if "mlbPitching" in sql else con_intrusa.copy()))
    monkeypatch.setattr(pythag, "get_connection", lambda: object())
    monkeypatch.setattr(pythag.db, "dispose_engine", lambda: None)
    for nombre in ("save_pythag_data", "save_rpw_data"):
        original = getattr(pythag, nombre)
        monkeypatch.setattr(pythag, nombre,
                            lambda *a, _o=original, **k: _o(*a, **{**k, "data_dir": tmp_path}))

    pythag.run()

    escrito = pd.read_parquet(tmp_path / "pythag_exponents.parquet")
    assert escrito["season"].min() >= seasons.MIN_SEASON


def test_la_conexion_se_cierra_aunque_run_termine(pythag_sin_base, monkeypatch):
    cerrado = []
    monkeypatch.setattr(pythag.db, "dispose_engine", lambda: cerrado.append(1))

    pythag.run()

    assert cerrado, "el pool quedaria abierto tras el pipeline"


def test_el_cli_del_pitagorico_arranca_run(monkeypatch):
    llamado = []
    monkeypatch.setattr(pythag, "run", lambda: llamado.append(1))

    assert pythag._cli([]) == 0
    assert llamado == [1]


def test_el_cli_acepta_las_tres_fechas(monkeypatch):
    visto = []
    monkeypatch.setattr(pythag, "run", lambda: visto.append(seasons.rango()))

    pythag._cli(["--min-season", "1930", "--max-season", "2020"])

    assert visto == [(1930, 2020)]


def test_el_pitagorico_usa_la_conexion_compartida(monkeypatch):
    """
    Un envoltorio de una linea, pero importa que sea ESE: los tres scripts del
    pipeline tienen que compartir el engine de db.py. Cuando cada uno abria el
    suyo, la lista de servidores del .env no se honraba igual en todos.
    """
    centinela = object()
    monkeypatch.setattr(pythag.db, "get_engine", lambda: centinela)

    assert pythag.get_connection() is centinela
