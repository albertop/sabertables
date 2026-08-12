
CREATE OR ALTER VIEW [dbo].[v_mlb_team_season_pythag_input]
AS
WITH TotalInnings as (
SELECT [season]
      ,sum([IP]) as Total_IP
  FROM [dbo].[mlbPitching]
  where team<>'XXX' and team is not null and team<>''
  group by season
),
Games AS (
    SELECT
        [season],
        [isTie],
        CASE WHEN [reverseHomeAwayStatus] = 0 THEN teamAwayId ELSE teamHomeId END AS teamAwayId,
        CASE WHEN [reverseHomeAwayStatus] = 0 THEN teamAwayScore ELSE teamHomeScore END AS teamAwayScore,
        CASE WHEN [reverseHomeAwayStatus] = 0 THEN teamHomeScore ELSE teamAwayScore END AS runsAllowedAway,
        CASE WHEN [reverseHomeAwayStatus] = 0 THEN teamHomeId ELSE teamAwayId END AS teamHomeId,
        CASE WHEN [reverseHomeAwayStatus] = 0 THEN teamHomeScore ELSE teamAwayScore END AS teamHomeScore,
        CASE WHEN [reverseHomeAwayStatus] = 0 THEN teamAwayScore ELSE teamHomeScore END AS runsAllowedHome
    FROM [dbo].[mlbGames] g
    WHERE gameType = 'R'
      -- 'FT' = Final Tied. Antes solo entraba 'F', asi que los 776 juegos
      -- empatados de temporada regular quedaban fuera POR COMPLETO: ni victoria,
      -- ni derrota, ni empate, ni sus carreras. La consecuencia era que la
      -- columna ties valia siempre 0 y la logica de medias victorias de mas
      -- abajo -- (wins + 0.5*ties) -- nunca se ejecutaba. El codigo decia
      -- tratarlos y no los trataba.
      --
      -- Son 97 temporadas afectadas, con 30 empates en 1907 y 29 en 1904. Un
      -- equipo con 5 empates en una temporada de 154 juegos veia excluido el 3%
      -- de sus partidos.
      --
      -- Incluirlos es coherente: un empate aporta RS = RA exactamente, o sea que
      -- empuja la razon de carreras hacia 1 y el win_pct hacia .500 a la vez.
      -- Los dos lados del ajuste se mueven juntos.
      --
      -- Efecto medido en el exponente: |dif| media 0.0034, maxima 0.0322 (1902),
      -- sesgo -0.0029. Es el 3% del error estandar del propio ajuste (0.126), o
      -- sea despreciable. Verificado con control: las 28 temporadas sin empates
      -- dan diferencia exactamente 0.
      -- SE FILTRA POR MARCADOR, NO POR CODIGO DE ESTADO.
      --
      -- Antes era statusCode IN ('F','FT'), y dejaba fuera 176 partidos que SI se
      -- jugaron, repartidos en 42 temporadas y con 1.333 carreras entre todos:
      -- 'FR' (final por lluvia), 'FO', 'FG' y 'RV'. Son juegos oficiales, con
      -- ganador y perdedor, que cuentan para la clasificacion. En 2025 son cinco,
      -- tres de ellos de los White Sox, cuya temporada se calculaba con 159
      -- juegos en vez de 162.
      --
      -- Es el MISMO fallo que ya se corrigio con 'FT' para los empates: se anadio
      -- ese codigo y no se comprobo si faltaban otros. Filtrar por marcador cierra
      -- la familia entera y aguanta codigos nuevos.
      --
      -- Los cancelados siguen fuera, que es lo correcto: 184 partidos sin
      -- marcador --'CR', 'CE', 'CI', 'CA'-- incluidos los 120 de 2001 y los 41 de
      -- 2000, que nunca se disputaron.
      AND teamAwayScore IS NOT NULL
      AND teamHomeScore IS NOT NULL
),
TeamSeason AS (
    SELECT
        season,
        team,
        COUNT(*) AS games_played,
        SUM(wins) AS wins,
        SUM(losses) AS losses,
        SUM(ties) AS ties,
        SUM(runs_scored) AS runs_scored,
        SUM(runs_allowed) AS runs_allowed
    FROM (
        SELECT
            season,
            teamAwayId AS team,
            CASE WHEN isTie = 1 THEN 0 WHEN teamAwayScore > teamHomeScore THEN 1 ELSE 0 END AS wins,
            CASE WHEN isTie = 1 THEN 0 WHEN teamAwayScore < teamHomeScore THEN 1 ELSE 0 END AS losses,
            CASE WHEN isTie = 1 THEN 1 ELSE 0 END AS ties,
            teamAwayScore AS runs_scored,
            runsAllowedAway AS runs_allowed
        FROM Games

        UNION ALL

        SELECT
            season,
            teamHomeId AS team,
            CASE WHEN isTie = 1 THEN 0 WHEN teamHomeScore > teamAwayScore THEN 1 ELSE 0 END AS wins,
            CASE WHEN isTie = 1 THEN 0 WHEN teamHomeScore < teamAwayScore THEN 1 ELSE 0 END AS losses,
            CASE WHEN isTie = 1 THEN 1 ELSE 0 END AS ties,
            teamHomeScore AS runs_scored,
            runsAllowedHome AS runs_allowed
        FROM Games
    ) x
    GROUP BY season, team
)
SELECT
    ts.season,
    ts.team,
    mt.team_full_name,
    ts.games_played,
    ts.wins,
    ts.losses,
    ts.ties,
    ts.runs_scored,
    ts.runs_allowed,
    CAST(ts.wins + 0.5 * ts.ties AS float) / NULLIF(ts.games_played, 0) AS win_pct
FROM TeamSeason ts
-- LEFT JOIN, no INNER. Este join solo aporta team_full_name, una columna de
-- presentacion, y estaba DESCARTANDO temporadas-equipo enteras del ajuste
-- pitagorico: 92 en total, y no al azar sino dos franquicias completas --
-- Brooklyn (57 temporadas, 1901-1957) y Montreal (35, 1969-2003). Recuperarlas
-- mueve el exponente 0.019 de media y hasta 0.146, el 15% de un error estandar:
-- 1994 pasa de 1.597 a 1.679 al reincorporar a Montreal, que ese ano tenia el
-- mejor record del beisbol.
--
-- sport_id = 1 restringe a Grandes Ligas. mlbTeams tambien contiene equipos de
-- ligas menores, y eso causaba dos problemas:
--   1) Falsos positivos. En mlbTeams 'MON' son los Montgomery Biscuits y 'BRO'
--      los Brooklyn Cyclones. La temporada 2004 de Montreal ya venia etiquetada
--      como "Montgomery Biscuits".
--   2) Duplicados. Habia 532 pares (season, team_code) con mas de una fila, que
--      duplicaban 28 temporadas-equipo en la salida: esos equipos entraban dos
--      o tres veces en el ajuste. Con sport_id = 1 quedan 0 pares duplicados.
--
-- El mapeo cubre los dos codigos donde mlbGames y mlbTeams no coinciden. Se
-- comprobo que team_abbreviation NO sirve como clave alternativa: dejaria sin
-- casar el 43% de los juegos-equipo, frente al 3.4% de team_code.
LEFT JOIN (
    SELECT season, team_code, team_full_name
    FROM dbo.mlbTeams
    WHERE sport_id = 1
) mt
    ON mt.season = ts.season
   AND mt.team_code = CASE ts.team
                          WHEN 'BRO' THEN 'BKL'   -- Brooklyn: Superbas / Robins / Dodgers
                          WHEN 'MON' THEN 'MTL'   -- Montreal Expos
                          ELSE ts.team
                      END;
