// MLB Run Expectancy Tables - Client-side JavaScript

// Reference to the generate button
let generateBtn = null;

// Global heatmap state - shared across all tables
let globalHeatmapEnabled = false;

// Global storage for current table data
let currentTableData = {
    re24: null,
    re24_probabilities: null,
    re288: null,
    linear_weights: null
};

// Loading state tracker
let isLoading = false;

// Debounce timer
let debounceTimer = null;

// Tiempo maximo que se espera una tabla antes de rendirse.
//
// Habia AbortController para cancelar la peticion ANTERIOR cuando llega una
// nueva, pero ningun temporizador para una colgada: con red degradada o un
// worker atascado el boton se quedaba en "loading" hasta que el navegador
// decidiera fallar la conexion, y el usuario no sabia si esperar o reintentar.
//
// 45s es holgado sobre el peor caso real medido (BREAKEVEN_POINTS, ~2.7 MB) y
// queda por debajo del WORKER_TIMEOUT de gunicorn, asi que el mensaje lo da la
// UI antes de que el servidor corte por su cuenta.
const REQUEST_TIMEOUT_MS = 45000;

// Distingue "lo canceló una peticion mas nueva" de "se agoto el tiempo": las dos
// llegan al catch como AbortError, y la primera debe seguir siendo silenciosa.
let abortadoPorTimeout = false;

// AbortController for canceling previous requests
let abortController = null;

// Render locks to prevent simultaneous rendering
let renderingRE24 = false;
let renderingRE24Probabilities = false;
let re288RenderInProgress = false; // RE288 uses async batching

// Base state labels for downloaded files
const FILES_BASE_STATE_LABELS = {
    "000": "-- -- --",
    "100": "1B -- --",
    "010": "-- 2B --",
    "110": "1B 2B --",
    "001": "-- -- 3B",
    "101": "1B -- 3B",
    "011": "-- 2B 3B",
    "111": "1B 2B 3B",
    "AVG": "Average"
};

// Initialize the application
// Check if server is ready (data loaded)
function checkServerReady() {
    return fetch('/healthz', { 
        method: 'GET',
        cache: 'no-cache'
    })
    .then(response => response.ok)
    .catch(() => false);
}

// Hide loading container and show parameters container once server is ready
async function waitForServerReady() {
    const loadingContainer = document.getElementById('loading-container');
    const parametersContainer = document.getElementById('parameters-container');
    generateBtn = document.querySelector('.btn-primary-custom');
    if (generateBtn) {
        generateBtn.disabled = false; // Always enabled
    }

    if (!loadingContainer || !parametersContainer) return;

    const maxAttempts = 60; // 60 seconds max wait
    let attempts = 0;

    while (attempts < maxAttempts) {
        const isReady = await checkServerReady();
        if (isReady) {
            // Server is ready, hide loading and show parameters
            loadingContainer.style.display = 'none';
            parametersContainer.style.display = 'block';
            return;
        }

        // Wait 1 second before next check
        await new Promise(resolve => setTimeout(resolve, 1000));
        attempts++;
    }

    // If we get here, server didn't respond in time
    // Show parameters anyway and let user see any errors
    loadingContainer.style.display = 'none';
    parametersContainer.style.display = 'block';
}

// Start checking server readiness immediately
waitForServerReady();

document.addEventListener('DOMContentLoaded', function() {
    // Initialize globalHeatmapEnabled based on the first toggle's state
    const firstToggle = document.getElementById('re24-heatmap-toggle');
    if (firstToggle) {
        globalHeatmapEnabled = firstToggle.checked;
    }
    
    // Prepare season option sets for dynamic switching
    initializeSeasonLists();

    // Setup event listeners
    setupEventListeners();

    // Populate RE24 event types dropdown from server data
    populateEventTypeOptions();
    // If still empty (server didn’t send data), leave existing options intact

    // Apply initial season list based on current matrix type
    const matrixTypeSelect = document.querySelector('select[name="matrix_type"]');
    if (matrixTypeSelect) {
        updateSeasonOptionsForMatrix(matrixTypeSelect.value);
        toggleEventTypeVisibility(matrixTypeSelect.value);
        toggleInningVisibility(matrixTypeSelect.value);
        toggleTransitionModeVisibility(matrixTypeSelect.value);
        toggleTransitionTypeVisibility(matrixTypeSelect.value);
        togglePythagTypeVisibility(matrixTypeSelect.value);
        toggleSeasonVisibility(matrixTypeSelect.value);
        toggleSeasonDropdownWidth(matrixTypeSelect.value);
        toggleAverageVisibility(matrixTypeSelect.value);
        toggleTeamVisibility(matrixTypeSelect.value);
        toggleBreakevenTypeVisibility(matrixTypeSelect.value);
        toggleReImpactTypeVisibility(matrixTypeSelect.value);
        updateParametersDescription(matrixTypeSelect.value);
        // Sync season dropdowns for the initial breakeven type if on BREAKEVEN_POINTS
        if (matrixTypeSelect.value === 'BREAKEVEN_POINTS') {
            const initBreakevenType = document.getElementById('breakeven_type')?.value;
            if (initBreakevenType) updateSeasonOptionsForBreakevenType(initBreakevenType);
        }
    }
    
    // Initially disable all download buttons
    disableAllDownloadButtons();

    // Render obfuscated email link in footer
    renderObfuscatedEmail();
    
    // Fix aria-hidden accessibility issue with Bootstrap modal
    const tipModal = document.getElementById('tipModal');
    if (tipModal) {
        // Remove focus from any element inside modal before hiding to prevent aria-hidden warning
        tipModal.addEventListener('hide.bs.modal', function(event) {
            // If focus is inside the modal, blur it before Bootstrap sets aria-hidden
            const activeElement = document.activeElement;
            if (tipModal.contains(activeElement)) {
                activeElement.blur();
            }
        });
        
        // Ensure aria-hidden is removed when modal is shown
        tipModal.addEventListener('show.bs.modal', function() {
            this.removeAttribute('aria-hidden');
        });
        tipModal.addEventListener('shown.bs.modal', function() {
            this.removeAttribute('aria-hidden');
        });
    }
});

// Constants and caches for dynamic season lists
// Los tres los inyecta la plantilla desde el servidor, que a su vez los deriva
// de los parquets. NO se escriben aqui: eran una tercera copia de numeros que
// decide el pipeline (utils/seasons.py), y una copia mas es una copia que puede
// discrepar. El respaldo solo cubre que la plantilla no los mande.
const RE288_MIN_SEASON = window.RE288_MIN_SEASON ?? 1989;
const MIN_SEASON = window.MIN_SEASON ?? 1910;
const MAX_SEASON = window.MAX_SEASON ?? new Date().getUTCFullYear() - 1;

// Ultimo anio en que los lanzadores batearon con regularidad, antes del DH
// universal de 2022. No es uno de los tres parametros de rango: es una frontera
// del juego, y la vista dbo.v_re_analysis_ph_pitchers la aplica igual del lado
// del servidor. Aqui esta para que el selector no ofrezca temporadas vacias.
const PITCHERS_BATTED_THROUGH = 2021;

// Cuantos caracteres de un error que NO es JSON se muestran. Suficiente para
// leer un "502 Bad Gateway", corto para no volcar una pagina de error entera.
const MAX_CARACTERES_ERROR = 200;

/**
 * Saca un mensaje legible de una respuesta con error.
 *
 * Antes se hacia `await response.json()` a secas. Cuando el error NO viene de
 * la aplicacion sino de una capa de por medio -- un 502 o un 503 de nginx, un
 * 413 -- el cuerpo es HTML, el parseo lanza, y todo acababa en el catch
 * generico: el usuario leia "Failed to generate table" mientras la respuesta
 * traia justo la informacion que hacia falta.
 */
async function mensajeDeError(response) {
    const tipo = response.headers.get('content-type') || '';
    try {
        if (tipo.includes('application/json')) {
            const datos = await response.json();
            if (datos && datos.detail) return datos.detail;
        } else {
            // Se limpian las etiquetas: el cuerpo puede ser una pagina entera, y
            // ademas asi no se inyecta HTML ajeno en el hueco del mensaje.
            const texto = (await response.text()).replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
            if (texto) return `${response.status} ${response.statusText}: ${texto.slice(0, MAX_CARACTERES_ERROR)}`;
        }
    } catch {
        // Cuerpo ilegible o conexion cortada a mitad: se cae al generico.
    }
    return `${response.status} ${response.statusText || 'Error generating table'}`;
}
let ORIGINAL_FROM_OPTIONS_HTML = '';
let ORIGINAL_TO_OPTIONS_HTML = '';

function initializeSeasonLists() {
    const fromSelect = document.querySelector('select[name="from_season"]');
    const toSelect = document.querySelector('select[name="to_season"]');
    if (!fromSelect || !toSelect) return;

    // Cache the original options markup so we can restore later
    ORIGINAL_FROM_OPTIONS_HTML = fromSelect.innerHTML;
    ORIGINAL_TO_OPTIONS_HTML = toSelect.innerHTML;
}

function buildFilteredOptionsHTML(originalOptionsHTML, minSeason) {
    const wrapper = document.createElement('div');
    wrapper.innerHTML = `<select>${originalOptionsHTML}</select>`;
    const sel = wrapper.firstElementChild;
    const options = Array.from(sel.querySelectorAll('option'));

    const filtered = options.filter(o => {
        const v = parseInt(o.value);
        return !Number.isNaN(v) && v >= minSeason;
    });

    // Remove existing selected attributes; we'll set selection by value later
    filtered.forEach(o => o.removeAttribute('selected'));

    sel.innerHTML = '';
    filtered.forEach(o => sel.appendChild(o));
    return sel.innerHTML;
}

function setSelectOptions(selectEl, optionsHTML, preferredValue) {
    const prev = preferredValue ?? selectEl.value;
    selectEl.innerHTML = optionsHTML;
    // Try to restore previous selection
    if (Array.from(selectEl.options).some(o => o.value === String(prev))) {
        selectEl.value = String(prev);
    } else if (selectEl.options.length) {
        // Fallback to first option (most recent season)
        selectEl.selectedIndex = 0;
    }
}

function updateSeasonOptionsForMatrix(matrixType) {
    const fromSelect = document.querySelector('select[name="from_season"]');
    const toSelect = document.querySelector('select[name="to_season"]');
    if (!fromSelect || !toSelect) return;

    const prevFrom = fromSelect.value;
    const prevTo = toSelect.value;

    if (matrixType === 'RE288' || matrixType === 'RE288_FREQ' || matrixType === 'RE288_TRANS') {
        const filteredFromHTML = buildFilteredOptionsHTML(ORIGINAL_FROM_OPTIONS_HTML, RE288_MIN_SEASON);
        const filteredToHTML = buildFilteredOptionsHTML(ORIGINAL_TO_OPTIONS_HTML, RE288_MIN_SEASON);
        setSelectOptions(fromSelect, filteredFromHTML, prevFrom);
        setSelectOptions(toSelect, filteredToHTML, prevTo);
    } else {
        setSelectOptions(fromSelect, ORIGINAL_FROM_OPTIONS_HTML, prevFrom);
        setSelectOptions(toSelect, ORIGINAL_TO_OPTIONS_HTML, prevTo);
    }

    // Enforce From <= To after list switches
    const fVal = parseInt(fromSelect.value);
    const tVal = parseInt(toSelect.value);
    if (!Number.isNaN(fVal) && !Number.isNaN(tVal) && fVal > tVal) {
        const latest = toSelect.options[0]?.value;
        if (latest) {
            fromSelect.value = latest;
            toSelect.value = latest;
        }
    }
}

function updateSeasonOptionsForReImpact(analysisType) {
    const fromSelect = document.querySelector('select[name="from_season"]');
    const toSelect = document.querySelector('select[name="to_season"]');
    if (!fromSelect || !toSelect || !ORIGINAL_FROM_OPTIONS_HTML || !ORIGINAL_TO_OPTIONS_HTML) return;

    const prevFrom = fromSelect.value;
    const prevTo = toSelect.value;

    // PH for pitchers: restrict to 2021 and earlier
    if (analysisType === 'ph_pitcher') {
        // El regex de antes listaba a mano 2022-2029, 2030-2099 y 2100+. Con
        // el corte como numero se lee lo que hace y no hay que reescribirlo
        // cuando el calendario avance.
        const quitarPosteriores = (html) => {
            const w = document.createElement('div');
            w.innerHTML = `<select>${html}</select>`;
            Array.from(w.querySelectorAll('option')).forEach(o => {
                if (parseInt(o.value, 10) > PITCHERS_BATTED_THROUGH) o.remove();
            });
            return w.firstElementChild.innerHTML;
        };
        const filteredFromHTML = quitarPosteriores(buildFilteredOptionsHTML(ORIGINAL_FROM_OPTIONS_HTML, MIN_SEASON));
        const filteredToHTML = quitarPosteriores(buildFilteredOptionsHTML(ORIGINAL_TO_OPTIONS_HTML, MIN_SEASON));
        
        // Parse and filter properly
        const wrapper = document.createElement('div');
        wrapper.innerHTML = `<select>${filteredFromHTML}</select>`;
        const tempSelect = wrapper.firstElementChild;
        const options = Array.from(tempSelect.querySelectorAll('option'));
        const validOptions = options.filter(o => {
            const v = parseInt(o.value);
            return !Number.isNaN(v) && v <= PITCHERS_BATTED_THROUGH;
        });
        
        tempSelect.innerHTML = '';
        validOptions.forEach(o => tempSelect.appendChild(o));
        const finalHTML = tempSelect.innerHTML;
        
        setSelectOptions(fromSelect, finalHTML, PITCHERS_BATTED_THROUGH);
        setSelectOptions(toSelect, finalHTML, PITCHERS_BATTED_THROUGH);
    } else {
        // All other analysis types: use full range
        setSelectOptions(fromSelect, ORIGINAL_FROM_OPTIONS_HTML, prevFrom);
        setSelectOptions(toSelect, ORIGINAL_TO_OPTIONS_HTML, prevTo);
    }

    // Enforce From <= To after list switches
    const fVal = parseInt(fromSelect.value);
    const tVal = parseInt(toSelect.value);
    if (!Number.isNaN(fVal) && !Number.isNaN(tVal) && fVal > tVal) {
        const latest = toSelect.options[0]?.value;
        if (latest) {
            fromSelect.value = latest;
            toSelect.value = latest;
        }
    }
}

function updateSeasonOptionsForBreakevenType(breakevenType) {
    const fromSelect = document.querySelector('select[name="from_season"]');
    const toSelect = document.querySelector('select[name="to_season"]');
    if (!fromSelect || !toSelect) return;

    const prevFrom = fromSelect.value;
    const prevTo = toSelect.value;

    if (breakevenType === 'swing-take' || breakevenType === 'swing-take-zone') {
        const seasonSource = breakevenType === 'swing-take-zone'
            ? window.SWING_TAKE_ZONE_SEASONS
            : window.SWING_TAKE_SEASONS;
        const seasons = (seasonSource || []).slice().sort((a, b) => b - a); // descending
        if (seasons.length === 0) return;

        const fromFrag = document.createDocumentFragment();
        const toFrag = document.createDocumentFragment();
        seasons.forEach(s => {
            const o1 = document.createElement('option');
            o1.value = s;
            o1.textContent = s;
            fromFrag.appendChild(o1);
            const o2 = document.createElement('option');
            o2.value = s;
            o2.textContent = s;
            toFrag.appendChild(o2);
        });
        fromSelect.innerHTML = '';
        fromSelect.appendChild(fromFrag);
        toSelect.innerHTML = '';
        toSelect.appendChild(toFrag);

        // Restore previous values where valid, otherwise default to most recent
        const seasonsSet = new Set(seasons.map(String));
        fromSelect.value = seasonsSet.has(String(prevFrom)) ? prevFrom : String(seasons[0]);
        toSelect.value = seasonsSet.has(String(prevTo)) ? prevTo : String(seasons[0]);
    } else {
        // Restore full original season list
        setSelectOptions(fromSelect, ORIGINAL_FROM_OPTIONS_HTML, prevFrom);
        setSelectOptions(toSelect, ORIGINAL_TO_OPTIONS_HTML, prevTo);
    }

    // Enforce From <= To
    const fVal = parseInt(fromSelect.value);
    const tVal = parseInt(toSelect.value);
    if (!Number.isNaN(fVal) && !Number.isNaN(tVal) && fVal > tVal) {
        fromSelect.value = toSelect.value;
    }
}

function toggleEventTypeVisibility(matrixType) {
    const container = document.getElementById('event-type-container');
    const eventSelect = document.getElementById('event_cd');
    if (!container) return;

    const shouldShow = matrixType === 'RE24' || matrixType === 'RE24_TRANSITIONS';
    container.classList.toggle('d-none', !shouldShow);

    if (!shouldShow && eventSelect && eventSelect.options.length) {
        eventSelect.selectedIndex = 0; // Reset to default when hidden
    }
}

function toggleInningVisibility(matrixType) {
    const container = document.getElementById('inning-container');
    const inningSelect = document.getElementById('inning');
    if (!container) return;

    const shouldShow = matrixType === 'RE24_PCT';
    container.classList.toggle('d-none', !shouldShow);

    if (!shouldShow && inningSelect && inningSelect.options.length) {
        inningSelect.selectedIndex = 0; // Reset to default when hidden
    }
}

function toggleBreakevenTypeVisibility(matrixType) {
    const container = document.getElementById('breakeven-type-container');
    const breakevenSelect = document.getElementById('breakeven_type');
    const fromSeasonContainer = document.getElementById('from-season-container');
    const toSeasonContainer = document.getElementById('to-season-container');
    if (!container) return;

    const shouldShow = matrixType === 'BREAKEVEN_POINTS';
    container.classList.toggle('d-none', !shouldShow);
    container.classList.toggle('analysis-wide', shouldShow);
    fromSeasonContainer?.classList.toggle('season-compact', shouldShow);
    toSeasonContainer?.classList.toggle('season-compact', shouldShow);

    if (!shouldShow && breakevenSelect && breakevenSelect.options.length) {
        breakevenSelect.selectedIndex = 0; // Reset to "stealing" when hidden
        updateSeasonOptionsForBreakevenType('stealing'); // Restore full season list
    } else if (shouldShow && breakevenSelect) {
        updateSeasonOptionsForBreakevenType(breakevenSelect.value);
    }

    // Show/hide scenario picker based on selected type
    toggleBreakevenScenarioVisibility();
}

function toggleBreakevenScenarioVisibility() {
    const scenarioContainer = document.getElementById('breakeven-scenario-container');
    if (!scenarioContainer) return;

    scenarioContainer.classList.add('d-none');
}

function toggleReImpactTypeVisibility(matrixType) {
    const container = document.getElementById('re-impact-type-container');
    const reImpactSelect = document.getElementById('re_impact_type');
    if (!container) return;

    const shouldShow = matrixType === 'RE_IMPACT';
    container.classList.toggle('d-none', !shouldShow);

    if (!shouldShow && reImpactSelect && reImpactSelect.options.length) {
        reImpactSelect.selectedIndex = 0; // Reset to "sac_bunt" when hidden
    }

    // Adjust season lists based on RE Impact type
    if (shouldShow && reImpactSelect) {
        updateSeasonOptionsForReImpact(reImpactSelect.value);
    }
}

function toggleTransitionTypeVisibility(matrixType) {
    const container = document.getElementById('transition-type-container');
    const transitionSelect = document.getElementById('transition_type');
    if (!container) return;

    const shouldShow = matrixType === 'RE288_TRANS';
    container.classList.toggle('d-none', !shouldShow);

    if (!shouldShow && transitionSelect && transitionSelect.options.length) {
        transitionSelect.selectedIndex = 0; // Reset to default when hidden
    }
}

function toggleTransitionModeVisibility(matrixType) {
    const container = document.getElementById('transition-mode-container');
    const modeSelect = document.getElementById('transition_mode');
    if (!container) return;

    const shouldShow = matrixType === 'RE288_TRANS';
    container.classList.toggle('d-none', !shouldShow);

    if (!shouldShow && modeSelect && modeSelect.options.length) {
        modeSelect.selectedIndex = 0; // Reset to default when hidden
    }
}

function toggleRE24TransitionsValueTypeVisibility(matrixType) {
    const container = document.getElementById('re24-transitions-value-type-container');
    const valueTypeSelect = document.getElementById('re24_transitions_value_type');
    if (!container) return;

    const shouldShow = matrixType === 'RE24_TRANSITIONS';
    container.classList.toggle('d-none', !shouldShow);

    if (!shouldShow && valueTypeSelect && valueTypeSelect.options.length) {
        valueTypeSelect.selectedIndex = 0; // Reset to default when hidden
    }
}

function togglePythagTypeVisibility(matrixType) {
    // Pythag type dropdown has been removed - this function is kept for compatibility
    // but doesn't do anything anymore
}

function toggleSeasonDropdownWidth(matrixType) {
    const fromSeasonContainer = document.getElementById('from-season-container');
    const toSeasonContainer = document.getElementById('to-season-container');
    
    if (!fromSeasonContainer || !toSeasonContainer) return;

    const isRE288Transitions = matrixType === 'RE288_TRANS';
    const isRE24Transitions = matrixType === 'RE24_TRANSITIONS';
    
    // For RE288 Transitions or RE24 Transitions: use col-lg-1, otherwise use col-lg-2
    [fromSeasonContainer, toSeasonContainer].forEach(container => {
        if (isRE288Transitions || isRE24Transitions) {
            container.classList.remove('col-lg-2');
            container.classList.add('col-lg-1');
        } else {
            container.classList.remove('col-lg-1');
            container.classList.add('col-lg-2');
        }
    });
}

function toggleAverageVisibility(matrixType) {
    const container = document.getElementById('average-container');
    const averageSelect = document.getElementById('average');
    if (!container) return;

    const shouldShow = matrixType === 'PARK_FACTORS';
    container.classList.toggle('d-none', !shouldShow);

    if (!shouldShow && averageSelect && averageSelect.options.length) {
        averageSelect.selectedIndex = 0; // Reset to 1 season (index 0) when hidden
    }
}

function toggleTeamVisibility(matrixType) {
    const container = document.getElementById('team-container');
    const teamSelect = document.getElementById('team');
    if (!container) return;

    const shouldShow = matrixType === 'WIN_EXPECTANCY' || matrixType === 'WIN_PROBABILITY_ADDED' || matrixType === 'LEVERAGE_INDEX';
    container.classList.toggle('d-none', !shouldShow);

    if (!shouldShow && teamSelect && teamSelect.options.length) {
        teamSelect.selectedIndex = 0; // Reset to Away (index 0) when hidden
    }
}

// Sync all heatmap toggles to the same state
function syncAllHeatmapToggles(isEnabled) {
    globalHeatmapEnabled = isEnabled;
    
    const toggleIds = [
        're24-heatmap-toggle',
        're24-probabilities-heatmap-toggle',
        're288-heatmap-toggle',
        'park-factors-heatmap-toggle',
        'woba-heatmap-toggle',
        'linear-weights-heatmap-toggle',
        'r24-frequencies-heatmap-toggle',
        'win-expectancy-heatmap-toggle',
        'win-probability-added-heatmap-toggle',
        'leverage-index-heatmap-toggle',
        'pythag-exponents-heatmap-toggle',
        'runs-per-win-heatmap-toggle',
        'sb-breakeven-heatmap-toggle',
        'sac-bunt-re-heatmap-toggle',
        're-impact-heatmap-toggle'
    ];
    
    toggleIds.forEach(id => {
        const toggle = document.getElementById(id);
        if (toggle) {
            toggle.checked = isEnabled;
        }
    });
}

function bindHeatmapToggle(toggleId, onToggle) {
    const toggle = document.getElementById(toggleId);
    if (!toggle) return;

    toggle.addEventListener('change', (e) => {
        syncAllHeatmapToggles(e.target.checked);
        if (typeof onToggle === 'function') {
            onToggle();
        }
    });
}

function toggleSeasonVisibility(matrixType) {
    const fromSeasonContainer = document.getElementById('from-season-container');
    const toSeasonContainer = document.getElementById('to-season-container');
    const outsContainer = document.getElementById('outs-container');
    const re24TransitionsOutsContainer = document.getElementById('re24-transitions-outs-container');
    const seasonRangeGuidance = document.getElementById('season-range-guidance');
    const fromSeasonLabel = fromSeasonContainer?.querySelector('label');
    if (!fromSeasonContainer || !toSeasonContainer) return;

    if (matrixType === 'PARK_FACTORS') {
        // For Park Factors: show only from season, label it as "Season"
        fromSeasonContainer.classList.remove('d-none');
        toSeasonContainer.classList.add('d-none');
        if (outsContainer) outsContainer.classList.add('d-none');
        if (re24TransitionsOutsContainer) re24TransitionsOutsContainer.classList.add('d-none');
        if (fromSeasonLabel) fromSeasonLabel.textContent = 'Season';
    } else if (matrixType === 'WOBA_COEFFS' || matrixType === 'FIP_CONST' || matrixType === 'PYTHAG_EXPONENTS' || matrixType === 'RUNS_PER_WIN' || matrixType === 'WRC_COMPONENTS') {
        // For wOBA, FIP, Pythagorean Exponents, and Runs Per Win: hide both seasons
        fromSeasonContainer.classList.add('d-none');
        toSeasonContainer.classList.add('d-none');
        if (outsContainer) outsContainer.classList.add('d-none');
        if (re24TransitionsOutsContainer) re24TransitionsOutsContainer.classList.add('d-none');
    } else if (matrixType === 'WIN_EXPECTANCY' || matrixType === 'WIN_PROBABILITY_ADDED' || matrixType === 'LEVERAGE_INDEX') {
        // For Win Expectancy, Win Probability Added, and Leverage Index: show both
        fromSeasonContainer.classList.remove('d-none');
        toSeasonContainer.classList.remove('d-none');
        if (outsContainer) outsContainer.classList.add('d-none');
        if (re24TransitionsOutsContainer) re24TransitionsOutsContainer.classList.add('d-none');
        if (fromSeasonLabel) fromSeasonLabel.textContent = 'From Season';
    } else if (matrixType === 'LINEAR_WEIGHTS') {
        // For Linear Weights: show both seasons and outs dropdown
        fromSeasonContainer.classList.remove('d-none');
        toSeasonContainer.classList.remove('d-none');
        if (outsContainer) outsContainer.classList.remove('d-none');
        if (re24TransitionsOutsContainer) re24TransitionsOutsContainer.classList.add('d-none');
        if (fromSeasonLabel) fromSeasonLabel.textContent = 'From Season';
    } else if (matrixType === 'RE24_TRANSITIONS') {
        // For RE24 Transitions: show both seasons and re24_transitions_outs dropdown
        fromSeasonContainer.classList.remove('d-none');
        toSeasonContainer.classList.remove('d-none');
        if (outsContainer) outsContainer.classList.add('d-none');
        if (re24TransitionsOutsContainer) re24TransitionsOutsContainer.classList.remove('d-none');
        if (fromSeasonLabel) fromSeasonLabel.textContent = 'From Season';
    } else {
        // For others: show both, label as "From Season"
        fromSeasonContainer.classList.remove('d-none');
        toSeasonContainer.classList.remove('d-none');
        if (outsContainer) outsContainer.classList.add('d-none');
        if (re24TransitionsOutsContainer) re24TransitionsOutsContainer.classList.add('d-none');
        if (fromSeasonLabel) fromSeasonLabel.textContent = 'From Season';
    }

    if (seasonRangeGuidance) {
        const showGuidance =
            !fromSeasonContainer.classList.contains('d-none') &&
            !toSeasonContainer.classList.contains('d-none');
        seasonRangeGuidance.classList.toggle('d-none', !showGuidance);
    }
}

function updateParametersDescription(matrixType) {
    const descriptionElement = document.getElementById('parameters-description');
    if (!descriptionElement) return;
    
    if (matrixType === 'RE24') {
        descriptionElement.textContent = 'Season range, and event type';
    } else if (matrixType === 'RE24_PCT') {
        descriptionElement.textContent = 'Season range, and inning';
    } else if (matrixType === 'RE24_TRANSITIONS') {
        descriptionElement.textContent = 'Season range, event type, type, and outs';
    } else if (matrixType === 'R24_FREQUENCIES') {
        descriptionElement.textContent = 'Season range';
    } else if (matrixType === 'RE288' || matrixType === 'RE288_FREQ') {
        descriptionElement.textContent = 'Season range';
    } else if (matrixType === 'RE288_TRANS') {
        descriptionElement.textContent = 'Season range, type, and transition type';
    } else if (matrixType === 'LINEAR_WEIGHTS') {
        descriptionElement.textContent = 'Season range';
    } else if (matrixType === 'WRC_COMPONENTS') {
        descriptionElement.textContent = '';
    } else if (matrixType === 'WOBA_COEFFS') {
        descriptionElement.textContent = '';
    } else if (matrixType === 'FIP_CONST') {
        descriptionElement.textContent = '';
    } else if (matrixType === 'PYTHAG_EXPONENTS') {
        descriptionElement.textContent = 'Select exponent type (fitted or Pythagenpat)';
    } else if (matrixType === 'RUNS_PER_WIN') {
        descriptionElement.textContent = '';
    } else if (matrixType === 'BREAKEVEN_POINTS') {
        descriptionElement.textContent = 'Season range and analysis type for weighted average';
    } else if (matrixType === 'RE_IMPACT') {
        descriptionElement.textContent = 'Season range and analysis type for weighted average RE impact';
    } else if (matrixType === 'PARK_FACTORS') {
        descriptionElement.textContent = 'Park factors by season with averaging period';
    } else if (matrixType === 'WIN_EXPECTANCY') {
        descriptionElement.textContent = 'Season range, and team (home/away)';
    } else if (matrixType === 'WIN_PROBABILITY_ADDED') {
        descriptionElement.textContent = 'Season range, and team (home/away)';
    } else if (matrixType === 'LEVERAGE_INDEX') {
        descriptionElement.textContent = 'Season range, and team (home/away)';
    }
}

function populateEventTypeOptions() {
    const eventSelect = document.getElementById('event_cd');
    const container = document.getElementById('event-type-container');
    if (!eventSelect) return;

    const items = Array.isArray(window.RE24_EVENT_TYPES) ? window.RE24_EVENT_TYPES : [];
    
    // If no data came through, keep any server-rendered options instead of overriding
    if (!items.length) {
        return;
    }

    const frag = document.createDocumentFragment();
    items.forEach((evt) => {
        const opt = document.createElement('option');
        opt.value = evt.event_cd;
        opt.textContent = evt.event;
        frag.appendChild(opt);
    });
    eventSelect.innerHTML = '';
    eventSelect.appendChild(frag);
}

function populateInningsOptions() {
    const inningSelect = document.getElementById('inning');
    if (!inningSelect) return;

    const items = Array.isArray(window.INNINGS) ? window.INNINGS : [];
    
    if (!items.length) {
        return;
    }

    const frag = document.createDocumentFragment();
    items.forEach((inn) => {
        const opt = document.createElement('option');
        opt.value = inn.inn_id;
        opt.textContent = inn.inn_name;
        frag.appendChild(opt);
    });
    inningSelect.innerHTML = '';
    inningSelect.appendChild(frag);
}

// Disable all download buttons
function disableAllDownloadButtons() {
    const downloadButtons = [
        'download-re24-btn',
        'download-probabilities-btn',
        'download-re288-btn',
        'download-woba-btn',
        'download-fip-btn',
        'download-pythag-exponents-btn',
        'download-runs-per-win-btn',
        'download-park-factors-btn',
        'linear-weights-download-btn',
        'r24-frequencies-download-btn',
        'win-expectancy-download-btn',
        'leverage-index-download-btn',
        'download-sb-breakeven-btn',
        'download-sac-bunt-re-btn',
        'download-re-impact-btn'
    ];
    
    downloadButtons.forEach(btnId => {
        const btn = document.getElementById(btnId);
        if (btn) {
            btn.disabled = true;
            btn.style.opacity = '0.5';
            btn.style.cursor = 'not-allowed';
        }
    });
}

// Enable specific download button for table type
function enableDownloadButton(tableType) {
    let buttonId;
    if (tableType === 're24') {
        buttonId = 'download-re24-btn';
    } else if (tableType === 're24_probabilities') {
        buttonId = 'download-probabilities-btn';
    } else if (tableType === 're288' || tableType === 're288_freq' || tableType === 're288_trans') {
        buttonId = 'download-re288-btn';
    } else if (tableType === 'woba') {
        buttonId = 'download-woba-btn';
    } else if (tableType === 'fip') {
        buttonId = 'download-fip-btn';
    } else if (tableType === 'pythag_exponents') {
        buttonId = 'download-pythag-exponents-btn';
    } else if (tableType === 'runs_per_win') {
        buttonId = 'download-runs-per-win-btn';
    } else if (tableType === 'park_factors') {
        buttonId = 'download-park-factors-btn';
    } else if (tableType === 'linear_weights') {
        buttonId = 'linear-weights-download-btn';
    } else if (tableType === 'r24_frequencies') {
        buttonId = 'r24-frequencies-download-btn';
    } else if (tableType === 'win_expectancy') {
        buttonId = 'win-expectancy-download-btn';
    } else if (tableType === 'win_probability_added') {
        buttonId = 'win-probability-added-download-btn';
    } else if (tableType === 'leverage_index') {
        buttonId = 'leverage-index-download-btn';
    } else if (tableType === 're24_transitions') {
        buttonId = 'download-re24-btn';
    } else if (tableType === 'breakeven_points') {
        buttonId = 'download-sb-breakeven-btn';
    } else if (tableType === 'sac_bunt_re') {
        buttonId = 'download-sac-bunt-re-btn';
    } else if (tableType === 're_impact') {
        buttonId = 'download-re-impact-btn';
    }
    
    const btn = document.getElementById(buttonId);
    if (btn) {
        btn.disabled = false;
        btn.style.opacity = '1';
        btn.style.cursor = 'pointer';
    }
}

// Fetch and display global download statistics
// Build footer email from obfuscated parts to deter simple scrapers
function renderObfuscatedEmail() {
    const el = document.getElementById('contact-email');
    if (!el) return;
    const u = el.getAttribute('data-u') || '';
    const d = el.getAttribute('data-d') || '';
    if (!u || !d) return;
    const addr = `${u}@${d}`;
    const a = document.createElement('a');
    a.href = `mailto:${addr}`;
    a.textContent = addr;
    a.className = 'small fw-medium text-secondary-custom text-decoration-none';
    el.replaceWith(a);
}

function setupEventListeners() {
    // Generate Table button click handler
    const generateBtn = document.querySelector('.btn-primary-custom');
    if (generateBtn) {
        generateBtn.addEventListener('click', handleGenerateTable);
    }
    
    // Download button click handlers for each table
    const downloadRE24Btn = document.getElementById('download-re24-btn');
    if (downloadRE24Btn) {
        downloadRE24Btn.addEventListener('click', () => {
            // Check if we have RE24 Transitions data, otherwise use regular RE24
            const tableType = currentTableData.re24_transitions ? 're24_transitions' : 're24';
            handleDownload(tableType);
        });
    }
    
    const downloadProbabilitiesBtn = document.getElementById('download-probabilities-btn');
    if (downloadProbabilitiesBtn) {
        downloadProbabilitiesBtn.addEventListener('click', () => handleDownload('re24_probabilities'));
    }
    
    const downloadRE288Btn = document.getElementById('download-re288-btn');
    if (downloadRE288Btn) {
        downloadRE288Btn.addEventListener('click', () => {
            // Check if we have RE288_FREQ data, otherwise use re288
            const tableType = currentTableData.re288_trans ? 're288_trans' : (currentTableData.re288_freq ? 're288_freq' : 're288');
            handleDownload(tableType);
        });
    }
    
    const downloadWOBABtn = document.getElementById('download-woba-btn');
    if (downloadWOBABtn) {
        downloadWOBABtn.addEventListener('click', () => handleDownload('woba'));
    }
    
    const downloadFIPBtn = document.getElementById('download-fip-btn');
    if (downloadFIPBtn) {
        downloadFIPBtn.addEventListener('click', () => handleDownload('fip'));
    }
    
    const downloadPythagExponentsBtn = document.getElementById('download-pythag-exponents-btn');
    if (downloadPythagExponentsBtn) {
        downloadPythagExponentsBtn.addEventListener('click', () => handleDownload('pythag_exponents'));
    }
    
    const downloadRunsPerWinBtn = document.getElementById('download-runs-per-win-btn');
    if (downloadRunsPerWinBtn) {
        downloadRunsPerWinBtn.addEventListener('click', () => handleDownload('runs_per_win'));
    }
    
    const downloadParkFactorsBtn = document.getElementById('download-park-factors-btn');
    if (downloadParkFactorsBtn) {
        downloadParkFactorsBtn.addEventListener('click', () => handleDownload('park_factors'));
    }
    
    const downloadLinearWeightsBtn = document.getElementById('linear-weights-download-btn');
    if (downloadLinearWeightsBtn) {
        downloadLinearWeightsBtn.addEventListener('click', () => handleDownload('linear_weights'));
    }
    
    const downloadR24FrequenciesBtn = document.getElementById('r24-frequencies-download-btn');
    if (downloadR24FrequenciesBtn) {
        downloadR24FrequenciesBtn.addEventListener('click', () => handleDownload('r24_frequencies'));
    }
    
    const downloadWinExpectancyBtn = document.getElementById('win-expectancy-download-btn');
    if (downloadWinExpectancyBtn) {
        downloadWinExpectancyBtn.addEventListener('click', () => handleDownload('win_expectancy'));
    }
    
    const downloadWinProbabilityAddedBtn = document.getElementById('win-probability-added-download-btn');
    if (downloadWinProbabilityAddedBtn) {
        downloadWinProbabilityAddedBtn.addEventListener('click', () => handleDownload('win_probability_added'));
    }
    
    const downloadLeverageIndexBtn = document.getElementById('leverage-index-download-btn');
    if (downloadLeverageIndexBtn) {
        downloadLeverageIndexBtn.addEventListener('click', () => handleDownload('leverage_index'));
    }
    
    const downloadSBBreakevenBtn = document.getElementById('download-sb-breakeven-btn');
    if (downloadSBBreakevenBtn) {
        downloadSBBreakevenBtn.addEventListener('click', () => handleDownload('breakeven_points'));
    }
    
    const downloadSacBuntReBtn = document.getElementById('download-sac-bunt-re-btn');
    if (downloadSacBuntReBtn) {
        downloadSacBuntReBtn.addEventListener('click', () => handleDownload('sac_bunt_re'));
    }
    
    const downloadReImpactBtn = document.getElementById('download-re-impact-btn');
    if (downloadReImpactBtn) {
        downloadReImpactBtn.addEventListener('click', () => handleDownload('re_impact'));
    }
    
    // Hide table containers when parameters change (debounced)
    const fromSeasonSelect = document.querySelector('select[name="from_season"]');
    const toSeasonSelect = document.querySelector('select[name="to_season"]');
    const matrixTypeSelect = document.querySelector('select[name="matrix_type"]');
    const eventCdSelect = document.querySelector('select[name="event_cd"]');
    
    const hideTableContainers = () => {
        document.getElementById('re24-table-container')?.classList.add('d-none');
        document.getElementById('re24-probabilities-table-container')?.classList.add('d-none');
        document.getElementById('re288-table-container')?.classList.add('d-none');
        document.getElementById('woba-table-container')?.classList.add('d-none');
        document.getElementById('fip-table-container')?.classList.add('d-none');
        document.getElementById('pythag-exponents-table-container')?.classList.add('d-none');
        document.getElementById('runs-per-win-table-container')?.classList.add('d-none');
        document.getElementById('park-factors-table-container')?.classList.add('d-none');
        document.getElementById('linear-weights-table-container')?.classList.add('d-none');
        document.getElementById('r24-frequencies-table-container')?.classList.add('d-none');
        document.getElementById('win-expectancy-container')?.classList.add('d-none');
        document.getElementById('win-probability-added-container')?.classList.add('d-none');
        document.getElementById('leverage-index-container')?.classList.add('d-none');
        document.getElementById('sb-breakeven-table-container')?.classList.add('d-none');
        document.getElementById('sac-bunt-re-table-container')?.classList.add('d-none');
        document.getElementById('re-impact-table-container')?.classList.add('d-none');
        
        // Disable download buttons when parameters change
        disableAllDownloadButtons();
    };
    
    // Debounced version with 100ms delay
    const debouncedHide = () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(hideTableContainers, 100);
    };

    const resetSeasonSelectorsToDefault = () => {
        if (fromSeasonSelect && fromSeasonSelect.options.length) {
            fromSeasonSelect.selectedIndex = 0;
        }
        if (toSeasonSelect && toSeasonSelect.options.length) {
            toSeasonSelect.selectedIndex = 0;
        }
    };
    
    const handleSeasonChange = (e) => {
        const activeMatrix = matrixTypeSelect?.value;
        if (activeMatrix === 'RE_IMPACT' && currentTableData.re_impact) {
            // Enforce from_season <= to_season (clamp like other views)
            const fVal = parseInt(fromSeasonSelect?.value);
            const tVal = parseInt(toSeasonSelect?.value);
            if (!Number.isNaN(fVal) && !Number.isNaN(tVal) && fVal > tVal) {
                if (e?.target === fromSeasonSelect) {
                    fromSeasonSelect.value = String(tVal);
                } else {
                    toSeasonSelect.value = String(fVal);
                }
            }
            // Re-render client-side with new season range — no need to re-fetch
            renderReImpactPage();
        } else {
            debouncedHide();
        }
    };
    if (fromSeasonSelect) {
        fromSeasonSelect.addEventListener('change', handleSeasonChange);
    }
    if (toSeasonSelect) {
        toSeasonSelect.addEventListener('change', handleSeasonChange);
    }
    if (eventCdSelect) {
        eventCdSelect.addEventListener('change', debouncedHide);
    }
    const inningSelect = document.querySelector('select[name="inning"]');
    if (inningSelect) {
        inningSelect.addEventListener('change', debouncedHide);
    }
    const transitionModeSelect = document.getElementById('transition_mode');
    if (transitionModeSelect) {
        transitionModeSelect.addEventListener('change', debouncedHide);
    }
    const transitionTypeSelect = document.getElementById('transition_type');
    if (transitionTypeSelect) {
        transitionTypeSelect.addEventListener('change', debouncedHide);
    }
    const re24TransitionsValueTypeSelect = document.getElementById('re24_transitions_value_type');
    if (re24TransitionsValueTypeSelect) {
        re24TransitionsValueTypeSelect.addEventListener('change', debouncedHide);
    }
    const pythagTypeSelect = document.getElementById('pythag_type');
    if (pythagTypeSelect) {
        pythagTypeSelect.addEventListener('change', debouncedHide);
    }
    if (matrixTypeSelect) {
        matrixTypeSelect.addEventListener('change', (e) => {
            // Update season lists based on selected matrix type
            updateSeasonOptionsForMatrix(e.target.value);
            toggleEventTypeVisibility(e.target.value);
            toggleInningVisibility(e.target.value);
            toggleTransitionModeVisibility(e.target.value);
            toggleTransitionTypeVisibility(e.target.value);
            toggleRE24TransitionsValueTypeVisibility(e.target.value);
            togglePythagTypeVisibility(e.target.value);
            toggleSeasonVisibility(e.target.value);
            toggleSeasonDropdownWidth(e.target.value);
            toggleAverageVisibility(e.target.value);
            toggleTeamVisibility(e.target.value);
            toggleBreakevenTypeVisibility(e.target.value);
            toggleReImpactTypeVisibility(e.target.value);
            updateParametersDescription(e.target.value);
            // Then hide tables (debounced) and disable downloads
            debouncedHide();
        });
    }
    
    bindHeatmapToggle('re24-heatmap-toggle', () => {
        if (currentTableData.re24) {
            renderRE24Table(currentTableData.re24);
        } else if (currentTableData.re24_transitions) {
            renderRE24TransitionsTable(currentTableData.re24_transitions);
        }
    });

    const heatmapBindings = [
        {
            id: 're24-probabilities-heatmap-toggle',
            rerender: () => {
                if (currentTableData.re24_probabilities) {
                    renderRE24ProbabilitiesTable(currentTableData.re24_probabilities);
                }
            }
        },
        {
            id: 're288-heatmap-toggle',
            rerender: () => {
                if (currentTableData.re288) {
                    renderRE288Table(currentTableData.re288);
                }
            }
        },
        {
            id: 'park-factors-heatmap-toggle',
            rerender: () => {
                if (currentTableData.park_factors) {
                    renderParkFactorsPage();
                }
            }
        },
        {
            id: 'woba-heatmap-toggle',
            rerender: () => {
                if (wobaPagination.data) {
                    renderWOBAPage();
                }
            }
        },
        {
            id: 'linear-weights-heatmap-toggle',
            rerender: () => {
                if (currentTableData.linear_weights) {
                    renderLinearWeightsTable(currentTableData.linear_weights);
                }
            }
        },
        {
            id: 'r24-frequencies-heatmap-toggle',
            rerender: () => {
                if (currentTableData.r24_frequencies) {
                    renderR24FrequenciesTable(currentTableData.r24_frequencies);
                }
            }
        },
        {
            id: 'win-expectancy-heatmap-toggle',
            rerender: () => {
                if (currentTableData.win_expectancy) {
                    renderWinExpectancyTable(currentTableData.win_expectancy);
                }
            }
        },
        {
            id: 'win-probability-added-heatmap-toggle',
            rerender: () => {
                if (currentTableData.win_probability_added) {
                    renderWinProbabilityAddedTable(currentTableData.win_probability_added);
                }
            }
        },
        {
            id: 'leverage-index-heatmap-toggle',
            rerender: () => {
                if (currentTableData.leverage_index) {
                    renderLeverageIndexTable(currentTableData.leverage_index);
                }
            }
        },
        {
            id: 'pythag-exponents-heatmap-toggle',
            rerender: () => {
                if (currentTableData.pythag_exponents) {
                    renderPythagExponentsPage();
                }
            }
        },
        {
            id: 'runs-per-win-heatmap-toggle',
            rerender: () => {
                if (currentTableData.runs_per_win) {
                    renderRunsPerWinPage();
                }
            }
        },
        {
            id: 'sb-breakeven-heatmap-toggle',
            rerender: () => {
                if (currentTableData.breakeven_points) {
                    renderSBBreakevenPage();
                }
            }
        },
        {
            id: 'sac-bunt-re-heatmap-toggle',
            rerender: () => {
                if (currentTableData.sac_bunt_re) {
                    renderSacBuntRePage();
                }
            }
        },
        {
            id: 're-impact-heatmap-toggle',
            rerender: () => {
                if (currentTableData.re_impact) {
                    renderReImpactPage();
                }
            }
        }
    ];

    heatmapBindings.forEach(({ id, rerender }) => bindHeatmapToggle(id, rerender));

    // Break-even type selector re-renders with different scenarios
    const breakevenTypeSelect = document.getElementById('breakeven_type');
    if (breakevenTypeSelect) {
        breakevenTypeSelect.addEventListener('change', (e) => {
            toggleBreakevenScenarioVisibility();
            updateSeasonOptionsForBreakevenType(e.target.value);
            hideTableContainers();
        });
    }

    // Break-even scenario selector (for SB by Count) re-renders with selected scenario
    const breakevenScenarioSelect = document.getElementById('breakeven_scenario');
    if (breakevenScenarioSelect) {
        breakevenScenarioSelect.addEventListener('change', () => {
            if (currentTableData.breakeven_points) {
                renderSBBreakevenPage();
            }
        });
    }

    // RE Impact type selector updates season ranges and re-renders
    const reImpactTypeSelect = document.getElementById('re_impact_type');
    if (reImpactTypeSelect) {
        reImpactTypeSelect.addEventListener('change', (e) => {
            updateSeasonOptionsForReImpact(e.target.value);
            // Reset season dropdowns to default (most recent) and clear any rendered table
            const fromSeasonEl = document.getElementById('from_season');
            const toSeasonEl = document.getElementById('to_season');
            if (fromSeasonEl && fromSeasonEl.options.length) fromSeasonEl.selectedIndex = 0;
            if (toSeasonEl && toSeasonEl.options.length) toSeasonEl.selectedIndex = 0;
            // Clear stored data and hide table
            currentTableData.re_impact = null;
            document.getElementById('re-impact-table-container')?.classList.add('d-none');
        });
    }

    // Percent/value toggle event listener for RE Impact
    const reImpactPercentToggle = document.getElementById('re-impact-percent-toggle');
    if (reImpactPercentToggle) {
        reImpactPercentToggle.addEventListener('change', () => {
            if (currentTableData.re_impact) {
                renderReImpactPage();
            }
        });
    }
}

function showError(message) {
    const errorToast = document.getElementById('errorToast');
    const errorMessage = document.getElementById('errorMessage');
    
    if (errorToast && errorMessage) {
        errorMessage.textContent = message;
        const toast = new bootstrap.Toast(errorToast);
        toast.show();
    }
}

function showSuccess(message) {
    const successToast = document.getElementById('successToast');
    const successMessage = document.getElementById('successMessage');
    
    if (successToast && successMessage) {
        successMessage.textContent = message;
        const toast = new bootstrap.Toast(successToast);
        toast.show();
    }
}

// Get heatmap color from blue (low) to red (high)
function getHeatmapColor(value, min, max, reverse = false) {
  const v = Number(value);
  const mn = Number(min);
  const mx = Number(max);

  if (!Number.isFinite(v) || !Number.isFinite(mn) || !Number.isFinite(mx) || mn === mx) {
    return { bgColor: '', textColor: '', isExtreme: false };
  }

  // Check if this value is the min or max
  const isExtreme = (v === mn || v === mx);

  let normalized = (v - mn) / (mx - mn);

  if (reverse) normalized = 1 - normalized;

  // clamp to [0, 1]
  normalized = Math.max(0, Math.min(1, normalized));

  let r, g, b;

  if (normalized < 0.25) {
    const t = normalized / 0.25;
    r = 0;
    g = Math.round(t * 255);
    b = 255;
  } else if (normalized < 0.5) {
    const t = (normalized - 0.25) / 0.25;
    r = Math.round(t * 255);
    g = 255;
    b = Math.round((1 - t) * 255);
  } else if (normalized < 0.75) {
    const t = (normalized - 0.5) / 0.25;
    r = 255;
    g = Math.round((1 - t * 0.5) * 255);
    b = 0;
  } else {
    const t = (normalized - 0.75) / 0.25;
    r = 255;
    g = Math.round((1 - t) * 128);
    b = 0;
  }

  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  const textColor = luminance > 0.5 ? '#000000' : '#ffffff';
  const bgColor = `rgba(${r}, ${g}, ${b}, 0.7)`;

  return { bgColor, textColor, isExtreme };
}

function getHeatmapRangeFromRows(rows, columnKeys, rawSuffix = '_raw') {
    let minValue = Infinity;
    let maxValue = -Infinity;

    rows.forEach(row => {
        columnKeys.forEach(key => {
            const rawValue = row[`${key}${rawSuffix}`];
            if (rawValue !== null && rawValue !== undefined) {
                minValue = Math.min(minValue, rawValue);
                maxValue = Math.max(maxValue, rawValue);
            }
        });
    });

    return { minValue, maxValue };
}

function renderHeatmapBadge(displayValue, rawValue, minValue, maxValue, options = {}) {
    if (rawValue === null || rawValue === undefined) {
        return null;
    }

    const {
        reverse = false,
        minWidth = '50px',
        padding = '2px 6px',
        borderRadius = '3px',
        fontSize = null
    } = options;

    const { bgColor, textColor, isExtreme } = getHeatmapColor(rawValue, minValue, maxValue, reverse);
    const extremeStyle = isExtreme ? 'box-shadow: 0 0 0 2px white;' : '';
    const fontSizeStyle = fontSize ? `font-size: ${fontSize};` : '';

    return `<span style="background-color: ${bgColor}; color: ${textColor}; padding: ${padding}; border-radius: ${borderRadius}; display: inline-block; min-width: ${minWidth}; ${fontSizeStyle}${extremeStyle}">${displayValue}</span>`;
}

function serializeSVGElement(svgElement) {
    return new XMLSerializer().serializeToString(svgElement);
}

function getBaseStateSVGMarkup(runners) {
    const svg = createBaseStateSVG(runners);
    return serializeSVGElement(svg);
}

function getHeatmapRangeFromHeaders(rows, headers, startIndex = 0) {
    const columnKeys = headers.slice(startIndex);
    let minValue = Infinity;
    let maxValue = -Infinity;

    rows.forEach(row => {
        columnKeys.forEach(columnKey => {
            const value = row[columnKey];
            if (value !== null && value !== undefined && !isNaN(value)) {
                minValue = Math.min(minValue, value);
                maxValue = Math.max(maxValue, value);
            }
        });
    });

    return { minValue, maxValue };
}


async function handleGenerateTable() {
    // Use the global generateBtn reference
    if (!generateBtn) {
        generateBtn = document.querySelector('.btn-primary-custom');
    }
    if (!generateBtn) return;
    if (isLoading || generateBtn.disabled) {
        return;
    }
    if (abortController) {
        abortController.abort();
    }
    abortController = new AbortController();
    abortadoPorTimeout = false;
    const miController = abortController;
    // El temporizador NO se arranca aqui: entre este punto y el try/finally hay
    // validaciones que hacen return (rango de temporadas invertido, RE288 antes
    // de 1989), y ese finally es quien lo limpia. Arrancandolo aqui, cada
    // validacion fallida dejaba un timer vivo 45s que al disparar ponia
    // abortadoPorTimeout = true -- una variable GLOBAL --, y la siguiente
    // peticion legitima mostraba "se agoto el tiempo" sin haberse agotado nada.
    // Se arranca justo antes del fetch, dentro del try.
    let temporizador = null;
    isLoading = true;
    generateBtn.disabled = true;
    
    // El respaldo era 2023, un anio suelto que ya se habia quedado atras. Si el
    // selector no tuviera valor, la peticion saldria con una temporada que no
    // es ni la ultima ni ninguna que el usuario haya elegido.
    const fromSeason = parseInt(document.querySelector('select[name="from_season"]')?.value) || MAX_SEASON;
    const toSeason = parseInt(document.querySelector('select[name="to_season"]')?.value) || MAX_SEASON;
    const matrixType = document.querySelector('select[name="matrix_type"]')?.value || 'RE24';
    
    // Get event_cd value
    let eventCd = null;
    if (matrixType === 'RE24') {
        const eventSelect = document.querySelector('select[name="event_cd"]');
        if (eventSelect && eventSelect.value) {
            eventCd = parseInt(eventSelect.value);
        }
    }
    
    // Skip season validation for WOBA_COEFFS, FIP_CONST, and PARK_FACTORS
    if (matrixType !== 'WOBA_COEFFS' && matrixType !== 'FIP_CONST' && matrixType !== 'PARK_FACTORS' && matrixType !== 'WRC_COMPONENTS') {
        // Validation: From Season must be <= To Season
        if (fromSeason > toSeason) {
            showError('From Season must be less than or equal to To Season');
            isLoading = false;
            generateBtn.disabled = false;
            return;
        }
        
        // El suelo se lee de la constante en vez de repetir el ano: estaba
        // escrito tres veces en estas tres lineas y una mas arriba.
        if ((matrixType === 'RE288' || matrixType === 'RE288_FREQ' || matrixType === 'RE288_TRANS') && fromSeason < RE288_MIN_SEASON) {
            showError(`RE288 Run Values requires seasons from ${RE288_MIN_SEASON} or later`);
            isLoading = false;
            generateBtn.disabled = false;
            return;
        }
    }
    
    // Store original button content
    const originalContent = generateBtn.innerHTML;
    
    // Update button to show loading spinner
    generateBtn.innerHTML = `
        <span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>
        <span>Loading...</span>
    `;
    
    // Call API to generate table
    try {
        const requestBody = {
            matrix_type: matrixType
        };
        
        // Only add seasons if not WOBA_COEFFS, FIP_CONST, BREAKEVEN_POINTS, or RE_IMPACT
        if (matrixType !== 'WOBA_COEFFS' && matrixType !== 'FIP_CONST' && matrixType !== 'BREAKEVEN_POINTS' && matrixType !== 'RE_IMPACT' && matrixType !== 'WRC_COMPONENTS') {
            requestBody.from_season = fromSeason;
            // For PARK_FACTORS, only from_season is used (shown as "Season")
            if (matrixType !== 'PARK_FACTORS') {
                requestBody.to_season = toSeason;
            }
        }
        
        // Add average only if it's PARK_FACTORS
        if (matrixType === 'PARK_FACTORS') {
            const averageSelect = document.getElementById('average');
            if (averageSelect && averageSelect.value !== '') {
                const averageValue = parseInt(averageSelect.value);
                if (!isNaN(averageValue)) {
                    requestBody.average = averageValue;
                }
            }
        }
        
        // Add event_cd only if it's RE24 and has a value
        if (matrixType === 'RE24' && eventCd !== null && !isNaN(eventCd)) {
            requestBody.event_cd = eventCd;
        }
        
        // Add inning only if it's RE24_PCT and has a value
        if (matrixType === 'RE24_PCT') {
            const inningSelect = document.getElementById('inning');
            if (inningSelect && inningSelect.value !== '') {
                const inningValue = parseInt(inningSelect.value);
                if (!isNaN(inningValue)) {
                    requestBody.inning = inningValue;
                }
            }
        }
        
        // Add transition_type only if it's RE288_TRANS and has a value
        if (matrixType === 'RE288_TRANS') {
            const transitionSelect = document.getElementById('transition_type');
            if (transitionSelect && transitionSelect.value !== '') {
                requestBody.transition_type = transitionSelect.value;
            } else {
                showError('Please select a transition type');
                isLoading = false;
                generateBtn.disabled = false;
                generateBtn.innerHTML = originalContent;
                return;
            }
            // Add transition_mode for RE288_TRANS
            const transitionModeSelect = document.getElementById('transition_mode');
            if (transitionModeSelect && transitionModeSelect.value !== '') {
                const modeValue = parseInt(transitionModeSelect.value);
                if (!isNaN(modeValue)) {
                    requestBody.transition_mode = modeValue;
                }
            }
        }
        
        // Add team only if it's WIN_EXPECTANCY, WIN_PROBABILITY_ADDED, or LEVERAGE_INDEX and has a value
        if (matrixType === 'WIN_EXPECTANCY' || matrixType === 'WIN_PROBABILITY_ADDED' || matrixType === 'LEVERAGE_INDEX') {
            const teamSelect = document.getElementById('team');
            if (teamSelect && teamSelect.value !== '') {
                const teamValue = parseInt(teamSelect.value);
                if (!isNaN(teamValue)) {
                    requestBody.team = teamValue;
                } else {
                    showError('Please select a team');
                    isLoading = false;
                    generateBtn.disabled = false;
                    generateBtn.innerHTML = originalContent;
                    return;
                }
            } else {
                showError('Please select a team (Home or Away)');
                isLoading = false;
                generateBtn.disabled = false;
                generateBtn.innerHTML = originalContent;
                return;
            }
        }
        
        // Add outs only if it's LINEAR_WEIGHTS and has a value
        if (matrixType === 'LINEAR_WEIGHTS') {
            const outsSelect = document.getElementById('outs');
            if (outsSelect && outsSelect.value !== '') {
                const outsValue = parseInt(outsSelect.value);
                if (!isNaN(outsValue)) {
                    requestBody.outs = outsValue;
                }
            }
        }
        
        // PYTHAG_EXPONENTS no longer uses pythag_type parameter
        
        // El tipo de break-even viaja al servidor para que mande solo ese.
        //
        // Antes llegaban los seis juntos -- 2,055 KB de tabla mas 878 KB de la
        // version por conteo -- y eligiendo "Stolen Bases" se descartaba el 90%.
        // Y no se ahorraba en cache: cambiar de tipo llama a hideTableContainers()
        // y obliga a pedir otra vez, asi que ese 90% se bajaba y se tiraba cada vez.
        //
        // Las TEMPORADAS siguen viniendo todas a proposito: la agregacion por
        // rango la hace este fichero, y es lo que hace instantaneo el selector.
        if (matrixType === 'BREAKEVEN_POINTS') {
            const breakevenTypeSelect = document.getElementById('breakeven_type');
            if (breakevenTypeSelect && breakevenTypeSelect.value) {
                requestBody.breakeven_type = breakevenTypeSelect.value;
            }
        }

        // Add analysis_type for RE_IMPACT
        if (matrixType === 'RE_IMPACT') {
            const analysisTypeSelect = document.getElementById('re_impact_type');
            if (analysisTypeSelect && analysisTypeSelect.value !== '') {
                requestBody.analysis_type = analysisTypeSelect.value;
            } else {
                showError('Please select an RE Impact analysis type');
                isLoading = false;
                generateBtn.disabled = false;
                generateBtn.innerHTML = originalContent;
                return;
            }
        }
        
        // Add re24_transitions parameters
        if (matrixType === 'RE24_TRANSITIONS') {
            // Get outs
            const outsSelect = document.getElementById('re24_transitions_outs');
            if (outsSelect && outsSelect.value !== '') {
                const outsValue = parseInt(outsSelect.value);
                if (!isNaN(outsValue)) {
                    requestBody.re24_transitions_outs = outsValue;
                } else {
                    showError('Please select outs');
                    isLoading = false;
                    generateBtn.disabled = false;
                    generateBtn.innerHTML = originalContent;
                    return;
                }
            }
            
            // Get event_cd
            const eventSelect = document.querySelector('select[name="event_cd"]');
            if (eventSelect && eventSelect.value !== '') {
                const eventValue = parseInt(eventSelect.value);
                if (!isNaN(eventValue)) {
                    requestBody.event_cd = eventValue;
                }
            }
            
            // Get value type
            const valueTypeSelect = document.getElementById('re24_transitions_value_type');
            if (valueTypeSelect && valueTypeSelect.value !== '') {
                const valueTypeValue = parseInt(valueTypeSelect.value);
                if (!isNaN(valueTypeValue)) {
                    requestBody.re24_transitions_value_type = valueTypeValue;

                    // Block probability requests for grouped event types (>=90)
                    if (
                        matrixType === 'RE24_TRANSITIONS' &&
                        typeof requestBody.event_cd === 'number' &&
                        requestBody.event_cd >= 90 &&
                        valueTypeValue === 2
                    ) {
                        showError('Unable to show probabilities for a grouped event type.');
                        isLoading = false;
                        generateBtn.disabled = false;
                        generateBtn.innerHTML = originalContent;
                        return;
                    }
                } else {
                    showError('Please select value type');
                    isLoading = false;
                    generateBtn.disabled = false;
                    generateBtn.innerHTML = originalContent;
                    return;
                }
            }
        }
        
        temporizador = setTimeout(() => {
            if (abortController === miController) {
                abortadoPorTimeout = true;
                miController.abort();
            }
        }, REQUEST_TIMEOUT_MS);

        const response = await fetch('/api/generate-table', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestBody),
            signal: abortController.signal
        });
        
        if (!response.ok) {
            showError(await mensajeDeError(response));
            // Reset loading state
            isLoading = false;
            generateBtn.disabled = false;
            generateBtn.innerHTML = originalContent;
            return;
        }
        
        const data = await response.json();

        // Clear all previous table data to avoid conflicts
        currentTableData = {};

        // Render the table based on matrix type
        if (matrixType === 'RE24') {
            renderRE24Table(data);
            enableDownloadButton('re24');
        } else if (matrixType === 'RE24_PCT') {
            renderRE24ProbabilitiesTable(data);
            enableDownloadButton('re24_probabilities');
        } else if (matrixType === 'RE288') {
            renderRE288Table(data);
            enableDownloadButton('re288');
        } else if (matrixType === 'RE288_FREQ') {
            renderRE288Table(data);
            enableDownloadButton('re288_freq');
        } else if (matrixType === 'RE288_TRANS') {
            renderRE288Table(data);
            enableDownloadButton('re288_trans');
        } else if (matrixType === 'WRC_COMPONENTS') {
            renderWRCComponentsTable(data);
        } else if (matrixType === 'WOBA_COEFFS') {
            renderWOBATable(data);
            enableDownloadButton('woba');
        } else if (matrixType === 'FIP_CONST') {
            renderFIPTable(data);
            enableDownloadButton('fip');
        } else if (matrixType === 'PARK_FACTORS') {
            renderParkFactorsTable(data);
            enableDownloadButton('park_factors');
        } else if (matrixType === 'LINEAR_WEIGHTS') {
            renderLinearWeightsTable(data);
            enableDownloadButton('linear_weights');
        } else if (matrixType === 'R24_FREQUENCIES') {
            renderR24FrequenciesTable(data);
            enableDownloadButton('r24_frequencies');
        } else if (matrixType === 'WIN_EXPECTANCY') {
            renderWinExpectancyTable(data);
            enableDownloadButton('win_expectancy');
        } else if (matrixType === 'WIN_PROBABILITY_ADDED') {
            renderWinProbabilityAddedTable(data);
            enableDownloadButton('win_probability_added');
        } else if (matrixType === 'LEVERAGE_INDEX') {
            renderLeverageIndexTable(data);
            enableDownloadButton('leverage_index');
        } else if (matrixType === 'PYTHAG_EXPONENTS') {
            renderPythagExponentsTable(data);
            enableDownloadButton('pythag_exponents');
        } else if (matrixType === 'RUNS_PER_WIN') {
            renderRunsPerWinTable(data);
            enableDownloadButton('runs_per_win');
        } else if (matrixType === 'RE24_TRANSITIONS') {
            renderRE24TransitionsTable(data);
            enableDownloadButton('re24_transitions');
        } else if (matrixType === 'BREAKEVEN_POINTS') {
            renderSBBreakevenTable(data);
            enableDownloadButton('breakeven_points');
        } else if (matrixType === 'RE_IMPACT') {
            renderReImpactTable(data);
            enableDownloadButton('re_impact');
        } else if (matrixType === 'SAC_BUNT_RE') {
            // Legacy support - redirect to RE_IMPACT with sac_bunt type
            renderSacBuntReTable(data);
            enableDownloadButton('sac_bunt_re');
        }
        
        // Show the appropriate table container
        if (matrixType === 'RE24') {
            document.getElementById('re24-table-container').classList.remove('d-none');
            document.getElementById('re24-probabilities-table-container')?.classList.add('d-none');
            document.getElementById('re288-table-container')?.classList.add('d-none');
            document.getElementById('woba-table-container')?.classList.add('d-none');
            document.getElementById('fip-table-container')?.classList.add('d-none');
            document.getElementById('park-factors-table-container')?.classList.add('d-none');
            document.getElementById('win-expectancy-container')?.classList.add('d-none');
            document.getElementById('pythag-exponents-table-container')?.classList.add('d-none');
            document.getElementById('runs-per-win-table-container')?.classList.add('d-none');
        } else if (matrixType === 'RE24_PCT') {
            document.getElementById('re24-probabilities-table-container').classList.remove('d-none');
            document.getElementById('re24-table-container')?.classList.add('d-none');
            document.getElementById('re288-table-container')?.classList.add('d-none');
            document.getElementById('woba-table-container')?.classList.add('d-none');
            document.getElementById('fip-table-container')?.classList.add('d-none');
            document.getElementById('park-factors-table-container')?.classList.add('d-none');
            document.getElementById('win-expectancy-container')?.classList.add('d-none');
            document.getElementById('pythag-exponents-table-container')?.classList.add('d-none');
            document.getElementById('runs-per-win-table-container')?.classList.add('d-none');
        } else if (matrixType === 'RE288' || matrixType === 'RE288_FREQ' || matrixType === 'RE288_TRANS') {
            document.getElementById('re288-table-container').classList.remove('d-none');
            document.getElementById('re24-table-container')?.classList.add('d-none');
            document.getElementById('re24-probabilities-table-container')?.classList.add('d-none');
            document.getElementById('woba-table-container')?.classList.add('d-none');
            document.getElementById('fip-table-container')?.classList.add('d-none');
            document.getElementById('park-factors-table-container')?.classList.add('d-none');
            document.getElementById('win-expectancy-container')?.classList.add('d-none');
            document.getElementById('pythag-exponents-table-container')?.classList.add('d-none');
            document.getElementById('runs-per-win-table-container')?.classList.add('d-none');
        } else if (matrixType === 'WRC_COMPONENTS') {
            document.getElementById('wrc-components-table-container').classList.remove('d-none');
        } else if (matrixType === 'WOBA_COEFFS') {
            document.getElementById('woba-table-container').classList.remove('d-none');
            document.getElementById('re24-table-container')?.classList.add('d-none');
            document.getElementById('re24-probabilities-table-container')?.classList.add('d-none');
            document.getElementById('re288-table-container')?.classList.add('d-none');
            document.getElementById('fip-table-container')?.classList.add('d-none');
            document.getElementById('park-factors-table-container')?.classList.add('d-none');
            document.getElementById('win-expectancy-container')?.classList.add('d-none');
            document.getElementById('pythag-exponents-table-container')?.classList.add('d-none');
            document.getElementById('runs-per-win-table-container')?.classList.add('d-none');
        } else if (matrixType === 'FIP_CONST') {
            document.getElementById('fip-table-container').classList.remove('d-none');
            document.getElementById('re24-table-container')?.classList.add('d-none');
            document.getElementById('re24-probabilities-table-container')?.classList.add('d-none');
            document.getElementById('re288-table-container')?.classList.add('d-none');
            document.getElementById('woba-table-container')?.classList.add('d-none');
            document.getElementById('win-expectancy-container')?.classList.add('d-none');
            document.getElementById('park-factors-table-container')?.classList.add('d-none');
            document.getElementById('pythag-exponents-table-container')?.classList.add('d-none');
            document.getElementById('runs-per-win-table-container')?.classList.add('d-none');
        } else if (matrixType === 'PARK_FACTORS') {
            document.getElementById('park-factors-table-container').classList.remove('d-none');
            document.getElementById('re24-table-container')?.classList.add('d-none');
            document.getElementById('re24-probabilities-table-container')?.classList.add('d-none');
            document.getElementById('re288-table-container')?.classList.add('d-none');
            document.getElementById('woba-table-container')?.classList.add('d-none');
            document.getElementById('fip-table-container')?.classList.add('d-none');
            document.getElementById('linear-weights-table-container')?.classList.add('d-none');
            document.getElementById('win-expectancy-container')?.classList.add('d-none');
            document.getElementById('pythag-exponents-table-container')?.classList.add('d-none');
            document.getElementById('runs-per-win-table-container')?.classList.add('d-none');
        } else if (matrixType === 'LINEAR_WEIGHTS') {
            document.getElementById('linear-weights-table-container').classList.remove('d-none');
            document.getElementById('re24-table-container')?.classList.add('d-none');
            document.getElementById('re24-probabilities-table-container')?.classList.add('d-none');
            document.getElementById('re288-table-container')?.classList.add('d-none');
            document.getElementById('woba-table-container')?.classList.add('d-none');
            document.getElementById('fip-table-container')?.classList.add('d-none');
            document.getElementById('park-factors-table-container')?.classList.add('d-none');
            document.getElementById('r24-frequencies-table-container')?.classList.add('d-none');
            document.getElementById('win-expectancy-container')?.classList.add('d-none');
            document.getElementById('leverage-index-container')?.classList.add('d-none');
            document.getElementById('pythag-exponents-table-container')?.classList.add('d-none');
            document.getElementById('runs-per-win-table-container')?.classList.add('d-none');
        } else if (matrixType === 'R24_FREQUENCIES') {
            document.getElementById('r24-frequencies-table-container').classList.remove('d-none');
            document.getElementById('re24-table-container')?.classList.add('d-none');
            document.getElementById('re24-probabilities-table-container')?.classList.add('d-none');
            document.getElementById('re288-table-container')?.classList.add('d-none');
            document.getElementById('woba-table-container')?.classList.add('d-none');
            document.getElementById('fip-table-container')?.classList.add('d-none');
            document.getElementById('park-factors-table-container')?.classList.add('d-none');
            document.getElementById('linear-weights-table-container')?.classList.add('d-none');
            document.getElementById('win-expectancy-container')?.classList.add('d-none');
            document.getElementById('leverage-index-container')?.classList.add('d-none');
            document.getElementById('pythag-exponents-table-container')?.classList.add('d-none');
            document.getElementById('runs-per-win-table-container')?.classList.add('d-none');
        } else if (matrixType === 'WIN_EXPECTANCY') {
            document.getElementById('win-expectancy-container').classList.remove('d-none');
            document.getElementById('re24-table-container')?.classList.add('d-none');
            document.getElementById('re24-probabilities-table-container')?.classList.add('d-none');
            document.getElementById('re288-table-container')?.classList.add('d-none');
            document.getElementById('woba-table-container')?.classList.add('d-none');
            document.getElementById('fip-table-container')?.classList.add('d-none');
            document.getElementById('park-factors-table-container')?.classList.add('d-none');
            document.getElementById('linear-weights-table-container')?.classList.add('d-none');
            document.getElementById('r24-frequencies-table-container')?.classList.add('d-none');
            document.getElementById('win-probability-added-container')?.classList.add('d-none');
            document.getElementById('leverage-index-container')?.classList.add('d-none');
            document.getElementById('pythag-exponents-table-container')?.classList.add('d-none');
            document.getElementById('runs-per-win-table-container')?.classList.add('d-none');
        } else if (matrixType === 'WIN_PROBABILITY_ADDED') {
            document.getElementById('win-probability-added-container').classList.remove('d-none');
            document.getElementById('re24-table-container')?.classList.add('d-none');
            document.getElementById('re24-probabilities-table-container')?.classList.add('d-none');
            document.getElementById('re288-table-container')?.classList.add('d-none');
            document.getElementById('woba-table-container')?.classList.add('d-none');
            document.getElementById('fip-table-container')?.classList.add('d-none');
            document.getElementById('park-factors-table-container')?.classList.add('d-none');
            document.getElementById('linear-weights-table-container')?.classList.add('d-none');
            document.getElementById('r24-frequencies-table-container')?.classList.add('d-none');
            document.getElementById('win-expectancy-container')?.classList.add('d-none');
            document.getElementById('leverage-index-container')?.classList.add('d-none');
            document.getElementById('pythag-exponents-table-container')?.classList.add('d-none');
            document.getElementById('runs-per-win-table-container')?.classList.add('d-none');
        } else if (matrixType === 'LEVERAGE_INDEX') {
            document.getElementById('leverage-index-container').classList.remove('d-none');
            document.getElementById('re24-table-container')?.classList.add('d-none');
            document.getElementById('re24-probabilities-table-container')?.classList.add('d-none');
            document.getElementById('re288-table-container')?.classList.add('d-none');
            document.getElementById('woba-table-container')?.classList.add('d-none');
            document.getElementById('fip-table-container')?.classList.add('d-none');
            document.getElementById('park-factors-table-container')?.classList.add('d-none');
            document.getElementById('linear-weights-table-container')?.classList.add('d-none');
            document.getElementById('r24-frequencies-table-container')?.classList.add('d-none');
            document.getElementById('win-expectancy-container')?.classList.add('d-none');
            document.getElementById('win-probability-added-container')?.classList.add('d-none');
            document.getElementById('pythag-exponents-table-container')?.classList.add('d-none');
            document.getElementById('runs-per-win-table-container')?.classList.add('d-none');
        } else if (matrixType === 'PYTHAG_EXPONENTS') {
            document.getElementById('pythag-exponents-table-container').classList.remove('d-none');
            document.getElementById('re24-table-container')?.classList.add('d-none');
            document.getElementById('re24-probabilities-table-container')?.classList.add('d-none');
            document.getElementById('re288-table-container')?.classList.add('d-none');
            document.getElementById('woba-table-container')?.classList.add('d-none');
            document.getElementById('fip-table-container')?.classList.add('d-none');
            document.getElementById('park-factors-table-container')?.classList.add('d-none');
            document.getElementById('linear-weights-table-container')?.classList.add('d-none');
            document.getElementById('r24-frequencies-table-container')?.classList.add('d-none');
            document.getElementById('win-expectancy-container')?.classList.add('d-none');
            document.getElementById('win-probability-added-container')?.classList.add('d-none');
            document.getElementById('leverage-index-container')?.classList.add('d-none');
            document.getElementById('runs-per-win-table-container')?.classList.add('d-none');
        } else if (matrixType === 'RUNS_PER_WIN') {
            document.getElementById('runs-per-win-table-container').classList.remove('d-none');
            document.getElementById('re24-table-container')?.classList.add('d-none');
            document.getElementById('re24-probabilities-table-container')?.classList.add('d-none');
            document.getElementById('re288-table-container')?.classList.add('d-none');
            document.getElementById('woba-table-container')?.classList.add('d-none');
            document.getElementById('fip-table-container')?.classList.add('d-none');
            document.getElementById('park-factors-table-container')?.classList.add('d-none');
            document.getElementById('linear-weights-table-container')?.classList.add('d-none');
            document.getElementById('r24-frequencies-table-container')?.classList.add('d-none');
            document.getElementById('win-expectancy-container')?.classList.add('d-none');
            document.getElementById('win-probability-added-container')?.classList.add('d-none');
            document.getElementById('leverage-index-container')?.classList.add('d-none');
            document.getElementById('pythag-exponents-table-container')?.classList.add('d-none');
        } else if (matrixType === 'RE24_TRANSITIONS') {
            document.getElementById('re24-table-container').classList.remove('d-none');
            document.getElementById('re24-probabilities-table-container')?.classList.add('d-none');
            document.getElementById('re288-table-container')?.classList.add('d-none');
            document.getElementById('woba-table-container')?.classList.add('d-none');
            document.getElementById('fip-table-container')?.classList.add('d-none');
            document.getElementById('park-factors-table-container')?.classList.add('d-none');
            document.getElementById('linear-weights-table-container')?.classList.add('d-none');
            document.getElementById('r24-frequencies-table-container')?.classList.add('d-none');
            document.getElementById('win-expectancy-container')?.classList.add('d-none');
            document.getElementById('leverage-index-container')?.classList.add('d-none');
            document.getElementById('pythag-exponents-table-container')?.classList.add('d-none');
            document.getElementById('runs-per-win-table-container')?.classList.add('d-none');
            document.getElementById('sb-breakeven-table-container')?.classList.add('d-none');
            
            // Show heatmap toggle for RE24 Transitions
            document.getElementById('re24-heatmap-toggle-container')?.classList.remove('d-none');
        } else if (matrixType === 'BREAKEVEN_POINTS') {
            document.getElementById('sb-breakeven-table-container').classList.remove('d-none');
            document.getElementById('re24-table-container')?.classList.add('d-none');
            document.getElementById('re24-probabilities-table-container')?.classList.add('d-none');
            document.getElementById('re288-table-container')?.classList.add('d-none');
            document.getElementById('woba-table-container')?.classList.add('d-none');
            document.getElementById('fip-table-container')?.classList.add('d-none');
            document.getElementById('park-factors-table-container')?.classList.add('d-none');
            document.getElementById('linear-weights-table-container')?.classList.add('d-none');
            document.getElementById('r24-frequencies-table-container')?.classList.add('d-none');
            document.getElementById('win-expectancy-container')?.classList.add('d-none');
            document.getElementById('win-probability-added-container')?.classList.add('d-none');
            document.getElementById('leverage-index-container')?.classList.add('d-none');
            document.getElementById('pythag-exponents-table-container')?.classList.add('d-none');
            document.getElementById('runs-per-win-table-container')?.classList.add('d-none');
            document.getElementById('sac-bunt-re-table-container')?.classList.add('d-none');
            document.getElementById('re-impact-table-container')?.classList.add('d-none');
        } else if (matrixType === 'RE_IMPACT') {
            document.getElementById('re-impact-table-container').classList.remove('d-none');
            document.getElementById('re24-table-container')?.classList.add('d-none');
            document.getElementById('re24-probabilities-table-container')?.classList.add('d-none');
            document.getElementById('re288-table-container')?.classList.add('d-none');
            document.getElementById('woba-table-container')?.classList.add('d-none');
            document.getElementById('fip-table-container')?.classList.add('d-none');
            document.getElementById('park-factors-table-container')?.classList.add('d-none');
            document.getElementById('linear-weights-table-container')?.classList.add('d-none');
            document.getElementById('r24-frequencies-table-container')?.classList.add('d-none');
            document.getElementById('win-expectancy-container')?.classList.add('d-none');
            document.getElementById('win-probability-added-container')?.classList.add('d-none');
            document.getElementById('leverage-index-container')?.classList.add('d-none');
            document.getElementById('pythag-exponents-table-container')?.classList.add('d-none');
            document.getElementById('runs-per-win-table-container')?.classList.add('d-none');
            document.getElementById('sb-breakeven-table-container')?.classList.add('d-none');
            document.getElementById('sac-bunt-re-table-container')?.classList.add('d-none');
        } else if (matrixType === 'SAC_BUNT_RE') {
            document.getElementById('sac-bunt-re-table-container').classList.remove('d-none');
            document.getElementById('re24-table-container')?.classList.add('d-none');
            document.getElementById('re24-probabilities-table-container')?.classList.add('d-none');
            document.getElementById('re288-table-container')?.classList.add('d-none');
            document.getElementById('woba-table-container')?.classList.add('d-none');
            document.getElementById('fip-table-container')?.classList.add('d-none');
            document.getElementById('park-factors-table-container')?.classList.add('d-none');
            document.getElementById('linear-weights-table-container')?.classList.add('d-none');
            document.getElementById('r24-frequencies-table-container')?.classList.add('d-none');
            document.getElementById('win-expectancy-container')?.classList.add('d-none');
            document.getElementById('win-probability-added-container')?.classList.add('d-none');
            document.getElementById('leverage-index-container')?.classList.add('d-none');
            document.getElementById('pythag-exponents-table-container')?.classList.add('d-none');
            document.getElementById('runs-per-win-table-container')?.classList.add('d-none');
            document.getElementById('sb-breakeven-table-container')?.classList.add('d-none');
        }
        
    } catch (error) {
        if (error.name === 'AbortError') {
            // Cancelada por una peticion mas nueva: silencio, es lo esperado.
            // Cancelada por el temporizador: hay que decirlo, o el usuario se
            // queda mirando un boton que ya no va a hacer nada.
            if (abortadoPorTimeout) {
                showError(
                    `The request took longer than ${Math.round(REQUEST_TIMEOUT_MS / 1000)} seconds and was stopped. ` +
                    `Try again, or narrow the season range.`
                );
            }
            return;
        }
        console.error('Error generating table:', error);
        showError('Failed to generate table. Please try again.');
    } finally {
        if (temporizador !== null) clearTimeout(temporizador);
        abortadoPorTimeout = false;
        isLoading = false;
        if (generateBtn) {
            generateBtn.disabled = false;
            generateBtn.innerHTML = originalContent;
        }
        abortController = null;
    }
}

function renderRE24Table(data) {
    // Prevent simultaneous rendering
    if (renderingRE24) {
        console.warn('RE24 render already in progress, skipping');
        return;
    }
    renderingRE24 = true;
    
    try {
        // Store data for download
        currentTableData.re24 = data;
        
        // Render RE24 table in the DOM
        const tableHead = document.getElementById('table-head');
        const tableBody = document.getElementById('table-body');
        
        if (!tableHead || !tableBody) {
            console.error('Table elements not found');
            return;
        }
        
        // Clear existing content first
        tableHead.innerHTML = '';
        tableBody.innerHTML = '';
        
        // Update table title
        const tableTitle = document.getElementById('table-title');
        if (tableTitle) {
            const fromSeason = data.from_season;
            const toSeason = data.to_season;
            const eventName = data.event_name;
            
            let titleHTML = 'RE24 Values - ';
            if (fromSeason === toSeason) {
                titleHTML += `<span class="primary-badge">${fromSeason} Season</span>`;
            } else {
                titleHTML += `<span class="primary-badge">${fromSeason}-${toSeason} Seasons</span>`;
            }
            if (eventName) {
                titleHTML += ` - <span class="primary-badge">${eventName}</span>`;
            }
            tableTitle.innerHTML = titleHTML;
        }
        const tableSubtitle = document.getElementById('table-title-subtitle');
        if (tableSubtitle) {
            if (data.event_name && data.event_name !== 'All Events') {
                tableSubtitle.textContent = 'If this play happens now, how does it change expected runs for the rest of the inning?';
            } else {
                tableSubtitle.textContent = 'Starting from this exact base–out situation, how many runs will an average team score before the inning ends?';
            }
        }
        
        // Update event count footer
        const eventCountDiv = document.getElementById('re24-event-count');
        if (eventCountDiv && data.total_events !== null && data.total_events !== undefined) {
            setBreakevenFooterText(eventCountDiv, `${data.total_events.toLocaleString()} events processed`);
        }
        
        // Check if heatmap is enabled
        const heatmapEnabled = globalHeatmapEnabled;
        
        // Calculate min/max for heatmap if enabled
        let minValue = Infinity;
        let maxValue = -Infinity;
        if (heatmapEnabled) {
            ({ minValue, maxValue } = getHeatmapRangeFromRows(data.rows, ['out_0', 'out_1', 'out_2']));
        }
        const reliabilityStats = buildReliabilityStats(
            data.rows,
            (row) => [row.out_0_n, row.out_1_n, row.out_2_n],
            RELIABILITY_DEFAULT_CONFIG
        );
        
        // Build header row HTML
        let headerHTML = '<tr>';
        headerHTML += '<th class="bg-primary text-center align-middle sticky-col fs-5" colspan="2">Runners</th>';
        data.headers.slice(1).forEach(header => {
            headerHTML += `<th class="bg-primary text-center align-middle fs-5">${header}</th>`;
        });
        headerHTML += '</tr>';
        tableHead.innerHTML = headerHTML;
        
        // Build body rows HTML
        let bodyHTML = '';
        data.rows.forEach(row => {
            const svgString = getBaseStateSVGMarkup(row.runners);
            
            bodyHTML += '<tr>';
            bodyHTML += `<td class="sticky-col text-end align-middle svg-cell">${svgString}</td>`;
            
            // Check if a specific event is selected (not "All Events")
            let labelContent = row.label;
            if (data.event_name && data.event_name !== 'All Events') {
                labelContent = `${row.label}<br><span style="color: #888; font-size: 0.85em;">${data.event_name}</span>`;
            }
            bodyHTML += `<td class="sticky-col text-start align-middle fs-6 label-cell label-cell-sticky">${labelContent}</td>`;
            
            ['out_0', 'out_1', 'out_2'].forEach((outKey, idx) => {
                const rawValue = row[`${outKey}_raw`];
                const nValue = row[`${outKey}_n`];
                if (rawValue === null || rawValue === undefined || isNaN(rawValue)) {
                    bodyHTML += '<td class="text-center align-middle table-cell-compact" style="font-size: 1rem;">-</td>';
                    return;
                }

                const cellHtml = renderMetricCellWithReliability(rawValue, nValue, {
                    formatValue: (v) => Number(v).toFixed(3).replace(',', '.'),
                    heatmapEnabled,
                    hMin: minValue,
                    hMax: maxValue,
                    reliabilityStats,
                    reliabilityConfig: RELIABILITY_DEFAULT_CONFIG
                });
                bodyHTML += `<td class="text-center align-middle table-cell-compact" style="font-size: 1rem;">${cellHtml}</td>`;
            });
            
            bodyHTML += '</tr>';
        });
        tableBody.innerHTML = bodyHTML;
    } finally {
        renderingRE24 = false;
    }
}

function createBaseStateSVG(runners) {
    // Create SVG for base state visualization
    // runners format: "000", "100", "010", etc.
    // Position 0 = first base, Position 1 = second base, Position 2 = third base
    
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('width', '40');
    svg.setAttribute('height', '40');
    svg.setAttribute('viewBox', '0 0 48 48');
    
    // Create a group with 90-degree clockwise rotation
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('transform', 'rotate(0 24 24)');
    
    // Diamond paths (correct baseball diamond orientation)
    const bases = [
        { x: 38, y: 24, base: 0 },  // First base (right)
        { x: 24, y: 10, base: 1 },  // Second base (top)
        { x: 10, y: 24, base: 2 }   // Third base (left)
    ];
    
    bases.forEach(({ x, y, base }) => {
        const diamond = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        diamond.setAttribute('x', x - 4);
        diamond.setAttribute('y', y - 4);
        diamond.setAttribute('width', '8');
        diamond.setAttribute('height', '8');
        diamond.setAttribute('transform', `rotate(45 ${x} ${y})`);
        
        // Check if runner is on this base
        if (runners[base] === '1') {
            diamond.setAttribute('fill', '#ec8013');
            diamond.setAttribute('stroke', '#ec8013');
            diamond.setAttribute('stroke-width', '1.5');
        } else {
            diamond.setAttribute('fill', '#ffffff');
            diamond.setAttribute('stroke', '#ffffff');
            diamond.setAttribute('stroke-width', '1.5');
        }
        
        g.appendChild(diamond);
    });
    
    svg.appendChild(g);
    return svg;
}

function renderRE24ProbabilitiesTable(data, resetPage = true) {
    // Prevent simultaneous rendering
    if (renderingRE24Probabilities) {
        console.warn('RE24 Probabilities render already in progress, skipping');
        return;
    }
    renderingRE24Probabilities = true;
    
    try {
        // Store data for download and pagination
        currentTableData.re24_probabilities = data;
        re24ProbabilitiesPagination.data = data;
        re24ProbabilitiesPagination.totalRows = data.rows.length;
        if (resetPage) {
            re24ProbabilitiesPagination.currentPage = 1;
        }
        
        // Render RE24 Probabilities table in the DOM
        const tableHead = document.getElementById('probabilities-table-head');
        const tableBody = document.getElementById('probabilities-table-body');
        
        if (!tableHead || !tableBody) {
            console.error('Probabilities table elements not found');
            return;
        }
        
        // Update table title
        const tableTitle = document.getElementById('probabilities-table-title');
        if (tableTitle) {
            const fromSeason = data.from_season;
            const toSeason = data.to_season;
            const innName = data.inn_name;
            
            let titleHTML = 'RE24 Scoring Distribution - ';
            if (fromSeason === toSeason) {
                titleHTML += `<span class="primary-badge">${fromSeason} Season</span>`;
            } else {
                titleHTML += `<span class="primary-badge">${fromSeason}-${toSeason} Seasons</span>`;
            }
            if (innName) {
                // Add "Inning" text unless it's "All Innings" or "Extra Innings"
                const inningText = (innName === 'All Innings' || innName === 'Extra Innings') ? innName : `${innName} Inning`;
                titleHTML += ` - <span class="primary-badge">${inningText}</span>`;
            }
            tableTitle.innerHTML = titleHTML;
        }
        const probSubtitle = document.getElementById('probabilities-table-title-subtitle');
        if (probSubtitle) {
            probSubtitle.textContent = 'Starting from this base–out state, how likely is it that the inning produces 0 runs? 1 run? 2 runs? etc';
        }
        
        // Update event count footer
        const eventCountDiv = document.getElementById('re24-probabilities-event-count');
        if (eventCountDiv && data.total_events !== null && data.total_events !== undefined) {
            setBreakevenFooterText(eventCountDiv, `${data.total_events.toLocaleString()} events processed`);
        }
        
        // Check if heatmap is enabled
        const heatmapEnabled = globalHeatmapEnabled;
        
        // Calculate min/max for heatmap if enabled
        let minValue = Infinity;
        let maxValue = -Infinity;
        if (heatmapEnabled) {
            ({ minValue, maxValue } = getHeatmapRangeFromRows(data.rows, data.bucket_columns));
        }
        const reliabilityStats = buildBreakevenReliabilityStats(
            data.rows,
            data.bucket_columns.map(bucket => `${bucket}_n`)
        );
        
        // Calculate pagination
        const totalPages = Math.ceil(re24ProbabilitiesPagination.totalRows / re24ProbabilitiesPagination.rowsPerPage);
        const startIdx = (re24ProbabilitiesPagination.currentPage - 1) * re24ProbabilitiesPagination.rowsPerPage;
        const endIdx = startIdx + re24ProbabilitiesPagination.rowsPerPage;
        const paginatedRows = data.rows.slice(startIdx, endIdx);
        
        // Clear existing content
        tableHead.innerHTML = '';
        tableBody.innerHTML = '';
        
        // Build header row HTML
        let headerHTML = '<tr>';
        headerHTML += '<th class="text-center fs-5 fw-bold" colspan="2" style="background-color: var(--primary)">Runners</th>';
        headerHTML += '<th class="text-center fs-5 fw-bold" style="background-color: var(--primary)">Outs</th>';
        
        const bucketHeaders = data.headers.slice(2);
        bucketHeaders.forEach(header => {
            headerHTML += `<th class="text-center fs-5 fw-bold" style="background-color: var(--primary)">${header}</th>`;
        });
        headerHTML += '</tr>';
        tableHead.innerHTML = headerHTML;
        
        // Build body rows HTML
        let bodyHTML = '';
        paginatedRows.forEach((row, index) => {
            // Add thicker border every 3rd row (visual separator for base state groups)
            const borderStyle = (index + 1) % 3 === 0 ? ' style="border-bottom: 2px solid #495057;"' : '';
            bodyHTML += `<tr${borderStyle}>`;
            
            // Only add SVG and label columns on first row of each base state group
            if (index % 3 === 0) {
                const svgString = getBaseStateSVGMarkup(row.runners);
                
                bodyHTML += `<td class="text-end sticky-col align-middle svg-cell" rowspan="3">${svgString}</td>`;
                
                // Check if a specific inning is selected (not "All Innings")
                let labelContent = row.label;
                if (data.inn_name && data.inn_name !== 'All Innings') {
                    // Add "Inning" text unless it's "Extra Innings"
                    const inningText = data.inn_name === 'Extra Innings' ? data.inn_name : `${data.inn_name} Inning`;
                    labelContent = `${row.label}<br><span style="color: #888; font-size: 0.85em;">${inningText}</span>`;
                }
                bodyHTML += `<td class="text-start fs-6 sticky-col align-middle label-cell label-cell-sticky" rowspan="3">${labelContent}</td>`;
            }
            
            bodyHTML += `<td class="text-center fs-5">${row.outs}</td>`;
            
            // Bucket value columns
            data.bucket_columns.forEach(bucket => {
                const value = row[bucket];
                const rawValue = row[`${bucket}_raw`];
                const nValue = row[`${bucket}_n`];

                if (rawValue === null || rawValue === undefined || isNaN(rawValue)) {
                    bodyHTML += '<td class="text-center fs-5">-</td>';
                    return;
                }

                const cellHtml = renderMetricCellWithReliability(rawValue, nValue, {
                    formatValue: (v) => `${(Number(v) * 100).toFixed(2)}%`,
                    heatmapEnabled,
                    hMin: minValue,
                    hMax: maxValue,
                    reliabilityStats,
                    reliabilityConfig: RELIABILITY_DEFAULT_CONFIG
                });
                bodyHTML += `<td class="text-center fs-5">${cellHtml}</td>`;
            });
            
            bodyHTML += '</tr>';
        });
        tableBody.innerHTML = bodyHTML;
        
        // Render pagination controls
        renderRE24ProbabilitiesPagination(totalPages);
    } finally {
        renderingRE24Probabilities = false;
    }
}

function renderRE24ProbabilitiesPagination(totalPages) {
    renderPaginationControls({
        containerId: 're24-probabilities-pagination',
        currentPage: re24ProbabilitiesPagination.currentPage,
        totalPages,
        changeHandlerName: 'changeRE24ProbabilitiesPage',
        labels: {
            first: '⏮',
            previous: '◀',
            next: '▶',
            last: '⏭'
        }
    });
}

function changeRE24ProbabilitiesPage(newPage) {
    const totalPages = Math.ceil(re24ProbabilitiesPagination.totalRows / re24ProbabilitiesPagination.rowsPerPage);
    setPaginationPage(re24ProbabilitiesPagination, newPage, totalPages, () => {
        renderRE24ProbabilitiesTable(re24ProbabilitiesPagination.data, false); // Don't reset page
    });
}

// Expose function to global scope for onclick handlers
window.changeRE24ProbabilitiesPage = changeRE24ProbabilitiesPage;

function renderRE288Table(data) {
    // Prevent multiple simultaneous renders
    if (re288RenderInProgress) {
        console.warn('RE288 render already in progress, skipping');
        return;
    }
    re288RenderInProgress = true;
    
    try {
        // Store data for download
        currentTableData.re288 = data;
        
        // Also store under re288_freq key if it's a frequency table
        if (data.matrix_type === 'RE288_FREQ') {
            currentTableData.re288_freq = data;
        }
        
        // Also store under re288_trans key if it's a transitions table
        if (data.matrix_type === 'RE288_TRANS') {
            currentTableData.re288_trans = data;
        }
        
        // Render RE288 table in the DOM
        const tableHead = document.getElementById('re288-table-head');
        const tableBody = document.getElementById('re288-table-body');
        
        if (!tableHead || !tableBody) {
            console.error('RE288 table elements not found');
            return;
        }
        
        // Determine table type flags early - used throughout function
        const isFrequency = data.matrix_type === 'RE288_FREQ';
        const isTransition = data.matrix_type === 'RE288_TRANS';
        const isProbability = isTransition && data.transition_mode === 2;
        
        // Update table title
        const tableTitle = document.getElementById('re288-table-title');
        if (tableTitle) {
            const fromSeason = data.from_season;
            const toSeason = data.to_season;
            
            let titleText = 'RE288 Values';
            if (isFrequency) {
                titleText = 'RE288 Frequencies';
            }
            
            if (isTransition) {
                // Antes era una cadena de ternarios que solo conocia B y S, y
                // mandaba TODO lo demas a 'To Ball In Play'. Con F y H ahora
                // disponibles, eso habria etiquetado mal dos categorias.
                const TRANSITION_LABELS = {
                    B: 'To Ball',
                    S: 'To Strike',
                    F: 'To Foul',
                    X: 'To Ball In Play',
                    H: 'To Hit By Pitch'
                };
                const transitionLabel = TRANSITION_LABELS[data.transition_type] || data.transition_type;
                const valueTypeLabel = data.transition_mode === 2 ? 'Probability' : (data.transition_mode === 3 ? 'Expected ΔRun Expectancy' : 'ΔRun Expectancy');
                // For transitions, construct title with seasons badge first, then value type, then transition type badge
                const seasonBadge = fromSeason === toSeason 
                    ? `<span class="primary-badge">${fromSeason} Season</span>`
                    : `<span class="primary-badge">${fromSeason}-${toSeason} Seasons</span>`;
                tableTitle.innerHTML = `RE288 Transitions - ${seasonBadge} <span class="primary-badge">${valueTypeLabel}</span> <span class="primary-badge">${transitionLabel}</span>`;
            } else if (fromSeason === toSeason) {
                tableTitle.innerHTML = `${titleText} - <span class="primary-badge">${fromSeason} Season</span>`;
            } else {
                tableTitle.innerHTML = `${titleText} - <span class="primary-badge">${fromSeason}-${toSeason} Seasons</span>`;
            }
        }
        
        const re288Subtitle = document.getElementById('re288-table-title-subtitle');
        if (re288Subtitle) {
            if (isTransition) {
                if (isProbability) {
                    re288Subtitle.textContent = 'The probability of the selected transition occurring in a given base-out-count situation.';
                } else if (data.transition_mode === 3) {
                    re288Subtitle.textContent = 'When weighted by frequency, how expected inning runs change with each ball, strike, or ball in play for a given situation.';
                } else {
                    re288Subtitle.textContent = 'How much the inning\'s expected runs increase or decrease when a ball or strike is added to the count, or when the ball is put in play, in a given situation.';
                }
            } else if (isFrequency) {
                re288Subtitle.textContent = 'From this base-out state, how often does a PA end at this count?';
            } else {
                re288Subtitle.textContent = 'Starting from this exact base–out–count situation, how many runs will an average team score before the inning ends?';
            }
        }
        
        // Update pitch count footer
        const pitchCountDiv = document.getElementById('re288-event-count');
        if (pitchCountDiv) {
            const countLabel = isTransition ? 'transitions' : 'pitches';
            const countText = (data.total_pitches !== null && data.total_pitches !== undefined)
                ? `${data.total_pitches.toLocaleString()} ${countLabel} processed`
                : '';
            setBreakevenFooterText(pitchCountDiv, countText);
        }
        
        // Check if heatmap is enabled
        const heatmapEnabled = globalHeatmapEnabled;
        
        // Calculate min/max for heatmap if enabled
        let minValue = Infinity;
        let maxValue = -Infinity;
        if (heatmapEnabled) {
            ({ minValue, maxValue } = getHeatmapRangeFromRows(data.rows, data.count_columns));
        }
        const reliabilityStats = buildBreakevenReliabilityStats(
            data.rows,
            data.count_columns.map(count => `${count}_n`)
        );
        
        // Clear existing content
        tableHead.innerHTML = '';
        tableBody.innerHTML = '';
        
        // Build header row HTML
        let headerHTML = '<tr>';
        headerHTML += '<th class="text-center fs-5 fw-bold" colspan="2" style="background-color: var(--primary)">Runners</th>';
        headerHTML += '<th class="text-center fs-5 fw-bold" style="background-color: var(--primary)">Outs</th>';
        
        const countHeaders = data.headers.slice(2);
        countHeaders.forEach((header, index) => {
            // Replace "0-0" header with "First<br>Pitch" for UI display only
            const displayHeader = (header === '0-0') ? 'First<br>Pitch' : header;
            headerHTML += `<th class="text-center fs-5 fw-bold" style="background-color: var(--primary)">${displayHeader}</th>`;
        });
        headerHTML += '</tr>';
        tableHead.innerHTML = headerHTML;
        
        // Use pagination for all RE288 table types
        if (isTransition) {
            // Store data for pagination
            re288TransitionsPagination.data = data;
            re288TransitionsPagination.totalRows = data.rows.length;
            re288TransitionsPagination.currentPage = 1;
            
            // Render first page
            renderRE288TransitionsPage(heatmapEnabled, minValue, maxValue, reliabilityStats);
            renderRE288TransitionsPagination();
            
            re288RenderInProgress = false;
        } else {
            // Use pagination for RE288 Values and Frequencies
            re288Pagination.data = data;
            re288Pagination.totalRows = data.rows.length;
            re288Pagination.currentPage = 1;
            
            // Render first page
            renderRE288Page(heatmapEnabled, minValue, maxValue, reliabilityStats);
            renderRE288Pagination();
            
            re288RenderInProgress = false;
        }
    } catch (error) {
        console.error('Error rendering RE288 table:', error);
        re288RenderInProgress = false;
    }
}

function renderRE288TransitionsPage(heatmapEnabled, minValue, maxValue, reliabilityStats = null) {
    const tableBody = document.getElementById('re288-table-body');
    if (!tableBody || !re288TransitionsPagination.data) return;
    
    const data = re288TransitionsPagination.data;
    const startIdx = (re288TransitionsPagination.currentPage - 1) * re288TransitionsPagination.rowsPerPage;
    const endIdx = Math.min(startIdx + re288TransitionsPagination.rowsPerPage, data.rows.length);
    const pageData = data.rows.slice(startIdx, endIdx);
    
    let bodyHTML = '';
    const isProbability = data.matrix_type === 'RE288_TRANS' && data.transition_mode === 2;
    
    pageData.forEach((row, pageIndex) => {
        const actualIndex = startIdx + pageIndex;
        // Add thicker border every 3rd row (visual separator for base state groups)
        const borderStyle = (actualIndex + 1) % 3 === 0 ? ' style="border-bottom: 2px solid #495057;"' : '';
        bodyHTML += `<tr${borderStyle}>`;
        
        // Only add SVG and label columns on first row of each base state group
        if (actualIndex % 3 === 0) {
            const svgString = getBaseStateSVGMarkup(row.runners);
            
            bodyHTML += `<td class="text-end sticky-col align-middle svg-cell" rowspan="3">${svgString}</td>`;
            
            // Build transition label based on transition_type
            const transitionLabel = data.transition_type === 'B' ? 'To Ball' : 
                                   data.transition_type === 'S' ? 'To Strike' : 
                                   'To Ball In Play';
            const labelWithTransition = `${row.label}<br><span style="color: #888; font-size: 0.85em;">${transitionLabel}</span>`;
            bodyHTML += `<td class="text-start fs-6 sticky-col align-middle label-cell label-cell-sticky" rowspan="3">${labelWithTransition}</td>`;
        }
        
        bodyHTML += `<td class="text-center fs-5">${row.outs}</td>`;
        
        // Count value columns
        data.count_columns.forEach(count => {
            const rawValue = row[`${count}_raw`];
            const nValue = row[`${count}_n`];

            if (rawValue !== null && rawValue !== undefined) {
                const cellHtml = renderMetricCellWithReliability(rawValue, nValue, {
                    formatValue: (v) => isProbability
                        ? `${(Number(v) * 100).toFixed(2)}%`
                        : Number(v).toFixed(3).replace(',', '.'),
                    heatmapEnabled,
                    hMin: minValue,
                    hMax: maxValue,
                    reliabilityStats,
                    reliabilityConfig: RELIABILITY_DEFAULT_CONFIG
                });
                bodyHTML += `<td class="text-center fs-5">${cellHtml}</td>`;
            } else {
                bodyHTML += `<td class="text-center fs-5">-</td>`;
            }
        });
        
        bodyHTML += '</tr>';
    });
    
    tableBody.innerHTML = bodyHTML;
}

function renderRE288TransitionsPagination() {
    const totalPages = Math.ceil(re288TransitionsPagination.totalRows / re288TransitionsPagination.rowsPerPage);
    renderPaginationControls({
        containerId: 're288-pagination',
        currentPage: re288TransitionsPagination.currentPage,
        totalPages,
        changeHandlerName: 'changeRE288TransitionsPage',
        labels: {
            first: '⏮',
            previous: '◀',
            next: '▶',
            last: '⏭'
        },
        useDisabledVariant: false
    });
}

function changeRE288TransitionsPage(newPage) {
    const totalPages = Math.ceil(re288TransitionsPagination.totalRows / re288TransitionsPagination.rowsPerPage);
    setPaginationPage(re288TransitionsPagination, newPage, totalPages, () => {
        // Recalculate heatmap min/max for current page if heatmap is enabled
        const heatmapEnabled = globalHeatmapEnabled;

        let minValue = Infinity;
        let maxValue = -Infinity;
        if (heatmapEnabled && re288TransitionsPagination.data) {
            ({ minValue, maxValue } = getHeatmapRangeFromRows(
                re288TransitionsPagination.data.rows,
                re288TransitionsPagination.data.count_columns
            ));
        }

        const reliabilityStats = buildBreakevenReliabilityStats(
            re288TransitionsPagination.data.rows,
            re288TransitionsPagination.data.count_columns.map(count => `${count}_n`)
        );

        renderRE288TransitionsPage(heatmapEnabled, minValue, maxValue, reliabilityStats);
        renderRE288TransitionsPagination();
    });
}

function renderRE288Page(heatmapEnabled, minValue, maxValue, reliabilityStats = null) {
    const tableBody = document.getElementById('re288-table-body');
    if (!tableBody || !re288Pagination.data) return;
    
    const data = re288Pagination.data;
    const startIdx = (re288Pagination.currentPage - 1) * re288Pagination.rowsPerPage;
    const endIdx = Math.min(startIdx + re288Pagination.rowsPerPage, data.rows.length);
    const pageData = data.rows.slice(startIdx, endIdx);
    
    let bodyHTML = '';
    const isFrequency = data.matrix_type === 'RE288_FREQ';
    
    pageData.forEach((row, pageIndex) => {
        const actualIndex = startIdx + pageIndex;
        // Add thicker border every 3rd row (visual separator for base state groups)
        const borderStyle = (actualIndex + 1) % 3 === 0 ? ' style="border-bottom: 2px solid #495057;"' : '';
        bodyHTML += `<tr${borderStyle}>`;
        
        // Only add SVG and label columns on first row of each base state group
        if (actualIndex % 3 === 0) {
            const svgString = getBaseStateSVGMarkup(row.runners);
            
            bodyHTML += `<td class="text-end sticky-col align-middle svg-cell" rowspan="3">${svgString}</td>`;
            bodyHTML += `<td class="text-start fs-6 sticky-col align-middle label-cell label-cell-sticky" rowspan="3">${row.label}</td>`;
        }
        
        bodyHTML += `<td class="text-center fs-5">${row.outs}</td>`;
        
        // Count value columns - format based on table type
        data.count_columns.forEach(count => {
            const rawValue = row[`${count}_raw`];
            const nValue = row[`${count}_n`];

            if (rawValue !== null && rawValue !== undefined) {
                const cellHtml = renderMetricCellWithReliability(rawValue, nValue, {
                    formatValue: (v) => isFrequency
                        ? `${(Number(v) * 100).toFixed(2)}%`
                        : Number(v).toFixed(3).replace(',', '.'),
                    heatmapEnabled,
                    hMin: minValue,
                    hMax: maxValue,
                    reliabilityStats,
                    reliabilityConfig: RELIABILITY_DEFAULT_CONFIG
                });
                bodyHTML += `<td class="text-center fs-5">${cellHtml}</td>`;
            } else {
                bodyHTML += `<td class="text-center fs-5">-</td>`;
            }
        });
        
        bodyHTML += '</tr>';
    });
    
    tableBody.innerHTML = bodyHTML;
}

function renderRE288Pagination() {
    if (!re288Pagination.data) return;

    const totalPages = Math.ceil(re288Pagination.totalRows / re288Pagination.rowsPerPage);
    renderPaginationControls({
        containerId: 're288-pagination',
        currentPage: re288Pagination.currentPage,
        totalPages,
        changeHandlerName: 'changeRE288Page',
        labels: {
            first: '⏮',
            previous: '◀',
            next: '▶',
            last: '⏭'
        }
    });
}

function changeRE288Page(newPage) {
    const totalPages = Math.ceil(re288Pagination.totalRows / re288Pagination.rowsPerPage);
    setPaginationPage(re288Pagination, newPage, totalPages, () => {
        // Recalculate heatmap min/max if heatmap is enabled
        const heatmapEnabled = globalHeatmapEnabled;

        let minValue = Infinity;
        let maxValue = -Infinity;
        if (heatmapEnabled && re288Pagination.data) {
            ({ minValue, maxValue } = getHeatmapRangeFromRows(
                re288Pagination.data.rows,
                re288Pagination.data.count_columns
            ));
        }

        const reliabilityStats = buildBreakevenReliabilityStats(
            re288Pagination.data.rows,
            re288Pagination.data.count_columns.map(count => `${count}_n`)
        );

        renderRE288Page(heatmapEnabled, minValue, maxValue, reliabilityStats);
        renderRE288Pagination();
    });
}

// Expose functions to global scope for onclick handlers
window.changeRE288Page = changeRE288Page;
window.changeRE288TransitionsPage = changeRE288TransitionsPage;

function getDownloadConfig(tableType, data) {
    const breakEvenTypeLabels = {
        stealing: 'Stolen_Bases',
        running: 'Base_Running',
        tagging: 'Tag_Up',
        'bunt-for-hit': 'Bunt_For_Hit',
        sb_by_count: 'SB_By_Count',
        'swing-take': 'Swing_Take',
        'swing-take-zone': 'Swing_Take_Zone'
    };

    const reImpactTypeLabels = {
        'sac_bunt': 'Sac_Bunt',
        'gidp': 'GIDP',
        'ibb': 'IBB',
        'ph_position': 'PH_Position',
        'ph_pitcher': 'PH_Pitcher',
        'platoon_baseline': 'Pitcher_Batter_Matchup',
        'tto': 'Pitcher_Batter_Times_Faced',
        'steal_runs': 'Stolen_Bases_Runs',
        'running_runs': 'Base_Running_Runs',
        'tagging_runs': 'Tag_Up_Runs'
    };

    const teamLabel = data.team_name || (data.team === 0 ? 'Away' : 'Home');

    const configMap = {
        re24: {
            formatSelectorId: 'format-select',
            tableNamePart: 'RE24_Values'
        },
        re24_probabilities: {
            formatSelectorId: 'probabilities-format-select',
            tableNamePart: 'RE24_Scoring_Distribution'
        },
        re288: {
            formatSelectorId: 're288-format-select',
            tableNamePart: 'RE288_Values'
        },
        re288_freq: {
            formatSelectorId: 're288-format-select',
            tableNamePart: 'RE288_Frequencies'
        },
        re288_trans: {
            formatSelectorId: 're288-format-select',
            tableNamePart: `RE288_Transitions_${data.transition_mode === 2 ? 'Probability' : (data.transition_mode === 3 ? 'ExpectedRunExpectancy' : 'RunExpectancy')}_${data.transition_type === 'B' ? 'ToBall' : data.transition_type === 'S' ? 'ToStrike' : 'ToBallInPlay'}`
        },
        woba: {
            formatSelectorId: 'woba-format-select',
            tableNamePart: 'wOBA_Coefficients'
        },
        fip: {
            formatSelectorId: 'fip-format-select',
            tableNamePart: 'FIP_Constants'
        },
        pythag_exponents: {
            formatSelectorId: 'pythag-exponents-format-select',
            tableNamePart: 'Pythagorean_Exponents'
        },
        runs_per_win: {
            formatSelectorId: 'runs-per-win-format-select',
            tableNamePart: 'Runs_Per_Win'
        },
        park_factors: {
            formatSelectorId: 'park-factors-format-select',
            tableNamePart: 'Park_Factors'
        },
        linear_weights: {
            formatSelectorId: 'linear-weights-format-select',
            tableNamePart: `Linear_Weights${data.outs !== undefined && data.outs !== null && data.outs !== -1 ? `_${data.outs}Outs` : ''}`
        },
        r24_frequencies: {
            formatSelectorId: 'r24-frequencies-format-select',
            tableNamePart: 'R24_Frequencies'
        },
        win_expectancy: {
            formatSelectorId: 'win-expectancy-format-select',
            tableNamePart: `Win_Expectancy_${teamLabel}`
        },
        win_probability_added: {
            formatSelectorId: 'win-probability-added-format-select',
            tableNamePart: `Win_Probability_Added_${teamLabel}`
        },
        leverage_index: {
            formatSelectorId: 'leverage-index-format-select',
            tableNamePart: `Leverage_Index_${teamLabel}`
        },
        re24_transitions: {
            formatSelectorId: 'format-select',
            tableNamePart: `RE24_Transitions_${data.value_type === 2 ? 'Probability' : (data.value_type === 3 ? 'ExpectedRunValue' : 'RunValue')}_${data.from_outs !== undefined ? `${data.from_outs}Outs` : ''}_${data.event_name ? data.event_name.replace(/\s+/g, '_') : 'AllEvents'}`
        },
        breakeven_points: {
            formatSelectorId: 'sb-breakeven-format-select',
            tableNamePart: `BreakEven_${breakEvenTypeLabels[data.selected_type] || 'Points'}`
        },
        sac_bunt_re: {
            formatSelectorId: 'sac-bunt-re-format-select',
            tableNamePart: 'Sac_Bunt_RE'
        },
        re_impact: {
            formatSelectorId: 're-impact-format-select',
            tableNamePart: `RE_Impact_${reImpactTypeLabels[data.analysis_type] || 'Analysis'}`
        }
    };

    return configMap[tableType] || null;
}

function buildDownloadFilename(tableType, data, tableNamePart, format) {
    if (tableType === 'woba' || tableType === 'fip' || tableType === 'pythag_exponents' || tableType === 'runs_per_win') {
        return `${tableNamePart}.${format}`;
    }

    if (tableType === 'park_factors') {
        return `${data.season}_${tableNamePart}_${data.average}Yr.${format}`;
    }

    const fromSeason = data.from_season;
    const toSeason = data.to_season;

    if (fromSeason === toSeason) {
        return `${fromSeason}_${tableNamePart}.${format}`;
    }
    return `${fromSeason}_${toSeason}_${tableNamePart}.${format}`;
}

function getReImpactDisplayMode(data) {
    return data.display_mode === 'percentage' ? 'percentage' : 'value';
}

function formatReImpactCsvValue(value, displayMode) {
    if (value === null || value === undefined) return '';
    return value;
}

function formatReImpactJsonValue(value, displayMode) {
    if (value === null || value === undefined) return null;
    return value;
}

function quoteCsvString(value) {
    return `"${String(value ?? '').replace(/"/g, '""')}"`;
}

function handleDownload(tableType) {
    // Ensure break-even exports always use the latest rendered/pivoted dataset.
    if (tableType === 'breakeven_points') {
        const selectedType = document.getElementById('breakeven_type')?.value || 'stealing';
        if (selectedType === 'sb_by_count') {
            renderSBBreakevenByCountPage();
        } else if (selectedType === 'swing-take') {
            renderSwingTakeByCountPage();
        } else if (selectedType === 'swing-take-zone') {
            renderSwingTakeZonePage();
        } else {
            renderSBBreakevenPage();
        }
    }

    // Get the data for the specific table
    const data = currentTableData[tableType];

    if (!data) {
        showError('No table data available. Please generate a table first.');
        return;
    }

    const downloadConfig = getDownloadConfig(tableType, data);
    if (!downloadConfig) {
        showError(`Unsupported table type for download: ${tableType}`);
        return;
    }

    const { formatSelectorId, tableNamePart } = downloadConfig;
    const format = document.getElementById(formatSelectorId)?.value || 'csv';
    const filename = buildDownloadFilename(tableType, data, tableNamePart, format);
    
    try {
        // Generate file content based on format
        let content, mimeType;
        
        if (format === 'csv') {
            content = convertToCSV(data);
            mimeType = 'text/csv';
        } else if (format === 'json') {
            content = convertToJSON(data);
            mimeType = 'application/json';
        } else {
            throw new Error(`Unsupported format: ${format}`);
        }
        
        // Validate content was generated
        if (!content || content.length === 0) {
            throw new Error('Failed to generate file content');
        }
        
        // Create blob and trigger download
        const blob = new Blob([content], { type: mimeType });
        
        // Verify blob was created successfully
        if (!blob || blob.size === 0) {
            throw new Error('Failed to create download file');
        }
        
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        
        // Clean up blob URL after a short delay to ensure download started
        setTimeout(() => {
            URL.revokeObjectURL(url);
        }, 100);
        
        // Show success message
        showSuccess(`Successfully downloaded ${filename}`);
        
    } catch (error) {
        console.error('Download error:', error);
        showError(`Failed to download file: ${error.message}`);
    }
}

function convertToCSV(data) {
    // Handle wOBA Coefficients separately
    if (data.matrix_type === 'WOBA_COEFFS') {
        const rows = [];
        const headers = ['season', 'league_avg_obp', 'woba_scale', 'wuBB', 'wHBP', 'w1B', 'w2B', 'w3B', 'wHR'];
        rows.push(headers.map(h => `"${h}"`).join(','));
        
        // Add all data rows with unrounded values
        data.table_data.forEach(row => {
            const values = [
                row.season,
                row.League_Avg_OBP,
                row.WOBA_Scale,
                row.wuBB,
                row.wHBP,
                row.w1B,
                row.w2B,
                row.w3B,
                row.wHR
            ];
            rows.push(values.join(','));
        });
        
        return rows.join('\n');
    }
    
    // Handle FIP Constants separately
    if (data.matrix_type === 'FIP_CONST') {
        const rows = [];
        const headers = ['season', 'lgERA', 'FIP_Component', 'FIP_Constant', 'lgHR', 'lgBB', 'lgHBP', 'lgK', 'lgER', 'lgIP'];
        rows.push(headers.map(h => `"${h}"`).join(','));
        
        // Add all data rows with unrounded values
        data.table_data.forEach(row => {
            const values = [
                row.season,
                row.lgERA,
                row.FIP_Component,
                row.FIP_Constant,
                row.lgHR,
                row.lgBB,
                row.lgHBP,
                row.lgK,
                row.lgER,
                row.lgIP
            ];
            rows.push(values.join(','));
        });
        
        return rows.join('\n');
    }
    
    // Handle Pythagorean Exponents separately
    if (data.matrix_type === 'PYTHAG_EXPONENTS') {
        const rows = [];
        const headers = ['season', '1_year', '2_years', '3_years', '5_years', '8_years', '10_years', '12_years', 'pythagenpat'];
        rows.push(headers.map(h => `"${h}"`).join(','));
        
        data.table_data.forEach(row => {
            const val = (v) => v !== null && v !== undefined ? v : '';
            const values = [
                row.season,
                val(row['1 year']),
                val(row['2 years']),
                val(row['3 years']),
                val(row['5 years']),
                val(row['8 years']),
                val(row['10 years']),
                val(row['12 years']),
                val(row['Pythagenpat'])
            ];
            rows.push(values.join(','));
        });
        
        return rows.join('\n');
    }
    
    // Handle Runs Per Win separately
    if (data.matrix_type === 'RUNS_PER_WIN') {
        const rows = [];
        const headers = ['season', '1_year', '2_years', '3_years', '5_years', '8_years', '10_years', '12_years', 'pythagenpat', 'fangraphs'];
        rows.push(headers.map(h => `"${h}"`).join(','));
        
        data.table_data.forEach(row => {
            const val = (v) => v !== null && v !== undefined ? v : '';
            const values = [
                row.season,
                val(row['1 year']),
                val(row['2 years']),
                val(row['3 years']),
                val(row['5 years']),
                val(row['8 years']),
                val(row['10 years']),
                val(row['12 years']),
                val(row['Pythagenpat']),
                val(row['FanGraphs'])
            ];
            rows.push(values.join(','));
        });
        
        return rows.join('\n');
    }
    
    // Handle Break-Even Points separately
    if (data.matrix_type === 'BREAKEVEN_POINTS') {
        const rows = [];
        const isByCount = data.selected_type === 'sb_by_count';
        const isSwingTake = data.selected_type === 'swing-take' || data.selected_type === 'swing_take';
        const isSwingTakeZone = data.selected_type === 'swing-take-zone' || data.selected_type === 'swing_take_zone';
        const isTagging = data.selected_type === 'tagging';
        const displayRows = Array.isArray(data.display_data) ? data.display_data : [];
        const metaHeaders = ['matrix_type', 'from_season', 'to_season'];
        const metaValues = [`"${data.matrix_type}"`, data.from_season ?? '', data.to_season ?? ''];
        let headers;
        if (isByCount || isSwingTake) {
            headers = metaHeaders.concat(['scenario', 'outs'], COUNT_ORDER);
        } else if (isSwingTakeZone) {
            headers = metaHeaders.concat(['zone', 'handedness'], COUNT_ORDER);
        } else if (isTagging) {
            headers = metaHeaders.concat(['scenario', '0_outs', '1_out']);
        } else {
            headers = metaHeaders.concat(['scenario', '0_outs', '1_out', '2_outs']);
        }
        rows.push(headers.map(h => `"${h}"`).join(','));
        
        displayRows.forEach(row => {
            if (isByCount || isSwingTake) {
                const scenario = row.scenario.includes(',') ? `"${row.scenario.replace(/"/g, '""')}"` : `"${row.scenario}"`;
                const val = (v) => v !== null && v !== undefined ? v : '';
                const values = [...metaValues, scenario, row.outs];
                COUNT_ORDER.forEach(count => values.push(val(row[count])));
                rows.push(values.join(','));
            } else if (isSwingTakeZone) {
                const zone = row.zone ?? '';
                const handedness = row.handedness || '';
                const val = (v) => v !== null && v !== undefined ? v : '';
                const values = [...metaValues, zone, `"${String(handedness).replace(/"/g, '""')}"`];
                COUNT_ORDER.forEach(count => values.push(val(row[count])));
                rows.push(values.join(','));
            } else {
                const label = row.scenario.includes(',') ? `"${row.scenario.replace(/"/g, '""')}"` : `"${row.scenario}"`;
                const val = (v) => v !== null && v !== undefined ? v : '';
                const values = [...metaValues, label, val(row.outs_0), val(row.outs_1)];
                if (!isTagging) values.push(val(row.outs_2));
                rows.push(values.join(','));
            }
        });
        
        return rows.join('\n');
    }
    
    // Handle Sac Bunt RE separately
    if (data.matrix_type === 'SAC_BUNT_RE') {
        const rows = [];
        const headers = ['scenario', '0_outs', '1_out'];
        rows.push(headers.map(h => `"${h}"`).join(','));
        
        data.display_data.forEach(row => {
            const scenario = row.scenario.includes(',') ? `"${row.scenario.replace(/"/g, '""')}"` : `"${row.scenario}"`;
            const val = (v) => v !== null && v !== undefined ? v : '';
            const values = [
                scenario,
                val(row.outs_0),
                val(row.outs_1)
            ];
            rows.push(values.join(','));
        });
        
        return rows.join('\n');
    }
    
    // Handle RE Impact Analysis separately (4 analysis types)
    if (data.matrix_type === 'RE_IMPACT') {
        const rows = [];
        const analysisType = data.analysis_type;
        const displayMode = getReImpactDisplayMode(data);
        const metaHeaders = ['matrix_type', 'from_season', 'to_season'];
        const metaValues = [`"${data.matrix_type}"`, data.from_season ?? '', data.to_season ?? ''];
        
        if (escenariosPorEstadoDeBases(analysisType)) {
            // Escenario por estado de bases x 2 outs
            const headers = metaHeaders.concat(['scenario', 'display_mode', '0_outs', '1_out']);
            rows.push(headers.map(h => `"${h}"`).join(','));
            
            data.display_data.forEach(row => {
                const scenario = `"${row.scenario}"`;
                const values = [
                    ...metaValues,
                    quoteCsvString(row.scenario),
                    `"${displayMode}"`,
                    formatReImpactCsvValue(row.outs_0, displayMode),
                    formatReImpactCsvValue(row.outs_1, displayMode)
                ];
                rows.push(values.join(','));
            });
        } else if (analysisType === 'ibb') {
            // 7 scenarios x 3 outs
            const headers = metaHeaders.concat(['scenario', 'display_mode', '0_outs', '1_out', '2_outs']);
            rows.push(headers.map(h => `"${h}"`).join(','));
            
            data.display_data.forEach(row => {
                const scenario = `"${row.scenario}"`;
                const values = [
                    ...metaValues,
                    quoteCsvString(row.scenario),
                    `"${displayMode}"`,
                    formatReImpactCsvValue(row.outs_0, displayMode),
                    formatReImpactCsvValue(row.outs_1, displayMode),
                    formatReImpactCsvValue(row.outs_2, displayMode)
                ];
                rows.push(values.join(','));
            });
        } else if (escenariosDeImpactoEnCarreras(analysisType)) {
            // Misma forma que ibb: escenario por filas, tres estados de outs.
            //
            // ESTA ES LA SEXTA CADENA por tipo. Sin esta rama el generador no
            // anade NINGUNA fila, el fichero sale vacio y el navegador no llega
            // a disparar la descarga: el boton no hace nada, sin error en
            // consola y sin excepcion. Por eso hay un test que PULSA el boton y
            // espera el evento de descarga -- comprobar que el tipo existe en
            // los mapas no prueba que baje un fichero.
            const headers = metaHeaders.concat(['scenario', 'display_mode', '0_outs', '1_out', '2_outs']);
            rows.push(headers.map(h => `"${h}"`).join(','));

            filasDeImpactoEnCarreras(data).forEach(row => {
                const values = [
                    ...metaValues,
                    quoteCsvString(row.scenario),
                    `"${displayMode}"`,
                    formatReImpactCsvValue(row.outs_0, displayMode),
                    formatReImpactCsvValue(row.outs_1, displayMode),
                    formatReImpactCsvValue(row.outs_2, displayMode)
                ];
                rows.push(values.join(','));
            });
        } else if (analysisType === 'ph_position' || analysisType === 'ph_pitcher') {
            const headers = metaHeaders.concat(['pitcher_batter_matchup', 'display_mode', '0_outs', '1_out', '2_outs']);
            rows.push(headers.map(h => `"${h}"`).join(','));

            data.display_data.forEach(row => {
                const matchup = row.matchup ?? row.label ?? row.scenario ?? '';
                const values = [
                    ...metaValues,
                    quoteCsvString(matchup),
                    `"${displayMode}"`,
                    formatReImpactCsvValue(row.outs_0, displayMode),
                    formatReImpactCsvValue(row.outs_1, displayMode),
                    formatReImpactCsvValue(row.outs_2, displayMode)
                ];
                rows.push(values.join(','));
            });
        } else if (analysisType === 'platoon_baseline') {
            const headers = metaHeaders.concat(['pitcher_batter_matchup', 'display_mode', '0_outs', '1_out', '2_outs', 'overall_value']);
            rows.push(headers.map(h => `"${h}"`).join(','));

            data.display_data.forEach(row => {
                const platoonMatch = row.platoon_match ?? row.label ?? row.scenario ?? '';
                const values = [
                    ...metaValues,
                    quoteCsvString(platoonMatch),
                    `"${displayMode}"`,
                    formatReImpactCsvValue(row.outs_0, displayMode),
                    formatReImpactCsvValue(row.outs_1, displayMode),
                    formatReImpactCsvValue(row.outs_2, displayMode),
                    formatReImpactCsvValue(row.weighted_mean_outs, displayMode)
                ];
                rows.push(values.join(','));
            });
        } else if (analysisType === 'tto') {
            const headers = metaHeaders.concat(['runners', 'handedness', 'display_mode', 'first_time_faced', 'second_time_faced', 'third_plus_times_faced']);
            rows.push(headers.map(h => `"${h}"`).join(','));

            data.display_data.forEach(row => {
                const runners = row.runners ?? row.scenario ?? '';
                const handedness = row.handedness ?? row.handedness_split ?? '';
                const values = [
                    ...metaValues,
                    quoteCsvString(runners),
                    quoteCsvString(handedness),
                    `"${displayMode}"`,
                    formatReImpactCsvValue(row.tto_1, displayMode),
                    formatReImpactCsvValue(row.tto_2, displayMode),
                    formatReImpactCsvValue(row.tto_3, displayMode)
                ];
                rows.push(values.join(','));
            });
        }
        
        return rows.join('\n');
    }
    
    // Handle Park Factors separately
    if (data.matrix_type === 'PARK_FACTORS') {
        const rows = [];
        const headers = ['season', 'park_id', 'park_name', 'team_name', 'seasons', 'pf_r', 'pf_1b', 'pf_2b', 'pf_3b', 'pf_hr', 'pf_bb', 'pf_so'];
        rows.push(headers.map(h => `"${h}"`).join(','));
        
        // Add all data rows with values
        data.rows.forEach(row => {
            const parkName = row.park_name.includes(',') ? `"${row.park_name.replace(/"/g, '""')}"` : row.park_name;
            const teamName = row.team_name.includes(',') ? `"${row.team_name.replace(/"/g, '""')}"` : row.team_name;
            const values = [
                row.season,
                row.park_id,
                parkName,
                teamName,
                row.seasons,
                row.pf_r !== null ? row.pf_r : '',
                row.pf_1b !== null ? row.pf_1b : '',
                row.pf_2b !== null ? row.pf_2b : '',
                row.pf_3b !== null ? row.pf_3b : '',
                row.pf_hr !== null ? row.pf_hr : '',
                row.pf_bb !== null ? row.pf_bb : '',
                row.pf_so !== null ? row.pf_so : ''
            ];
            rows.push(values.join(','));
        });
        
        return rows.join('\n');
    }
    
    // Handle Linear Weights separately
    if (data.matrix_type === 'LINEAR_WEIGHTS') {
        const rows = [];
        const headers = ['from_season', 'to_season', 'outs', 'event', 'average', FILES_BASE_STATE_LABELS['000'], FILES_BASE_STATE_LABELS['100'], FILES_BASE_STATE_LABELS['010'], FILES_BASE_STATE_LABELS['001'], FILES_BASE_STATE_LABELS['110'], FILES_BASE_STATE_LABELS['101'], FILES_BASE_STATE_LABELS['011'], FILES_BASE_STATE_LABELS['111']];
        rows.push(headers.map(h => `"${h}"`).join(','));
        
        // Get outs value from dropdown (not from individual rows)
        const outsValue = data.outs !== undefined && data.outs !== null ? data.outs : -1;
        
        // Add all data rows
        data.rows.forEach(row => {
            const eventName = row.event.includes(',') ? `"${row.event.replace(/"/g, '""')}"` : row.event;
            const values = [
                data.from_season,
                data.to_season,
                outsValue,
                eventName,
                row['AVG'] !== null ? row['AVG'] : '',
                row['000'] !== null ? row['000'] : '',
                row['100'] !== null ? row['100'] : '',
                row['010'] !== null ? row['010'] : '',
                row['001'] !== null ? row['001'] : '',
                row['110'] !== null ? row['110'] : '',
                row['101'] !== null ? row['101'] : '',
                row['011'] !== null ? row['011'] : '',
                row['111'] !== null ? row['111'] : ''
            ];
            rows.push(values.join(','));
        });
        
        return rows.join('\n');
    }
    
    // Handle R24 Frequencies separately
    if (data.matrix_type === 'R24_FREQUENCIES') {
        const rows = [];
        const headers = ['from_season', 'to_season', 'runners', '0_outs', '1_out', '2_outs'];
        rows.push(headers.map(h => `"${h}"`).join(','));
        
        // Add all data rows with frequency as percentage
        data.rows.forEach(row => {
            // Map runner codes to readable labels
            const runnersLabel = FILES_BASE_STATE_LABELS[row.runners] || row.runners;
            const values = [
                data.from_season,
                data.to_season,
                runnersLabel,
                row['0'] !== null ? row['0'] : '',
                row['1'] !== null ? row['1'] : '',
                row['2'] !== null ? row['2'] : ''
            ];
            rows.push(values.join(','));
        });
        
        return rows.join('\n');
    }
    
    // Create CSV content from table data
    const rows = [];
    
    // For RE24 Probabilities, use bucket_columns instead of headers for bucket names
    let transformedHeaders;
    if (data.bucket_columns) {
        // RE24 Probabilities: use raw bucket names (no_runs, at_least_one_run, etc.)
        const baseHeaders = data.headers.slice(0, 2).map(h => h.toLowerCase().replace(/\s+/g, '_')); // "Runners", "Outs"
        transformedHeaders = [...baseHeaders, ...data.bucket_columns];
    } else if (data.matrix_type === 'WIN_EXPECTANCY' || data.matrix_type === 'LEVERAGE_INDEX') {
        // Win Expectancy and Leverage Index: transform run differential columns
        transformedHeaders = data.headers.map(h => {
            // Check if header is a run differential column
            if (h.match(/^\-\d+\+?$/)) {
                // Negative: "-4" or "-4+" -> behind_by_4_runs or behind_by_4_plus_runs
                const num = h.substring(1).replace('+', '');
                const suffix = h.endsWith('+') ? '_plus_runs' : '_runs';
                return `behind_by_${num}${suffix}`;
            } else if (h.toLowerCase() === 'tie') {
                return 'tie';
            } else if (h.match(/^\+\d+\+?$/)) {
                // Positive: "+1" or "+4+" -> ahead_by_1_runs or ahead_by_4_plus_runs
                const num = h.substring(1).replace('+', '');
                const suffix = h.endsWith('+') ? '_plus_runs' : '_runs';
                return `ahead_by_${num}${suffix}`;
            } else {
                // Regular headers like "Inning", "Outs"
                return h.toLowerCase().replace(/\s+/g, '_');
            }
        });
    } else {
        // Other tables: transform headers to lowercase with underscores
        transformedHeaders = data.headers.map(h => h.toLowerCase().replace(/\s+/g, '_'));
    }
    
    // Build header row
    let headerRow = ['from_season', 'to_season'];
    
    // For RE24 Values, add event_name as third column
    if (data.matrix_type === 'RE24') {
        headerRow.push('event_name');
    }
    
    // For RE24 Probabilities, add inn_name as third column
    if (data.matrix_type === 'RE24_PCT') {
        headerRow.push('inn_name');
    }
    
    // For RE288 Transitions, add value_type and transition_type columns
    if (data.matrix_type === 'RE288_TRANS') {
        headerRow.push('value_type');
        headerRow.push('transition_type');
    }
    
    // For RE24 Transitions, add event_type, type, and outs columns
    if (data.matrix_type === 'RE24_TRANSITIONS') {
        headerRow.push('event_type');
        headerRow.push('type');
        headerRow.push('outs');
    }
    
    // For Win Expectancy, Win Probability Added, and Leverage Index, add team as third column
    if (data.matrix_type === 'WIN_EXPECTANCY' || data.matrix_type === 'WIN_PROBABILITY_ADDED' || data.matrix_type === 'LEVERAGE_INDEX') {
        headerRow.push('team');
    }
    
    headerRow.push(...transformedHeaders);
    const quotedHeaders = headerRow.map(h => `"${h}"`);
    rows.push(quotedHeaders.join(','));
    
    // Add data rows
    data.rows.forEach(row => {
        const values = [];
        
        // Add from_season and to_season as first values
        values.push(data.from_season.toString());
        values.push(data.to_season.toString());
        
        // For RE24 Values, add event_name as third column
        if (data.matrix_type === 'RE24') {
            const eventName = data.event_name || 'All Events';
            // Escape commas and quotes
            const escapedEventName = eventName.includes(',') || eventName.includes('"') 
                ? `"${eventName.replace(/"/g, '""')}"` 
                : eventName;
            values.push(escapedEventName);
        }
        
        // For RE24 Probabilities, add inn_name as third column
        if (data.matrix_type === 'RE24_PCT') {
            const innName = data.inn_name || 'All Innings';
            // Escape commas and quotes
            const escapedInnName = innName.includes(',') || innName.includes('"') 
                ? `"${innName.replace(/"/g, '""')}"` 
                : innName;
            values.push(escapedInnName);
        }
        
        // For RE288 Transitions, add transition_type as third column
        if (data.matrix_type === 'RE288_TRANS') {
            const transitionType = data.transition_type === 'B' ? 'To Ball' : data.transition_type === 'S' ? 'To Strike' : 'To Ball In Play';
            const valueTypeLabel = data.transition_mode === 2 ? 'Probability' : (data.transition_mode === 3 ? 'Expected Run Expectancy' : 'Delta Run Expectancy');
            values.push(valueTypeLabel);
            values.push(transitionType);
            // Note: For transition_mode=2 (Probability), values are exported as decimals (0-1 range), not percentages
        }
        
        // For RE24 Transitions, add event_type, type, and outs
        if (data.matrix_type === 'RE24_TRANSITIONS') {
            const eventType = data.event_type || 'All Events';
            const escapedEventType = eventType.includes(',') || eventType.includes('"') 
                ? `"${eventType.replace(/"/g, '""')}"` 
                : eventType;
            values.push(escapedEventType);
            const typeLabel = data.type || 'Run Value';
            values.push(typeLabel);
            values.push(data.outs.toString());
        }
        
        // For Win Expectancy, Win Probability Added, and Leverage Index, add team
        if (data.matrix_type === 'WIN_EXPECTANCY' || data.matrix_type === 'WIN_PROBABILITY_ADDED' || data.matrix_type === 'LEVERAGE_INDEX') {
            const teamName = data.team_name || (data.team === 0 ? 'Away' : 'Home');
            values.push(teamName);
        }
        
        // For RE24 Probabilities with bucket_columns, iterate over bucket_columns for data values
        // For RE24 Transitions with to_state_columns, iterate over to_state_columns for data values
        let headersToIterate = data.headers;
        if (data.bucket_columns) {
            headersToIterate = [...data.headers.slice(0, 2), ...data.bucket_columns];
        } else if (data.to_state_columns) {
            // For RE24 Transitions: First column is "From State" (from_runners), rest are to_state columns
            headersToIterate = [...data.headers.slice(0, 1), ...data.to_state_columns];
        }
        
        headersToIterate.forEach((header, index) => {
            let value;
            
            // Handle special cases based on header pattern
            if (header === 'Runners') {
                // Use FILES_BASE_STATE_LABELS for downloaded files
                value = FILES_BASE_STATE_LABELS[row.runners] || row.label || row.base_state;
            } else if (header === 'From State') {
                // For RE24 Transitions: use from_runners
                value = row.from_runners || row.label;
            } else if (header === 'Outs') {
                value = row.outs !== undefined ? row.outs : row.outs_ct;
            } else if (header === 'Inning' || header === 'inning') {
                value = row.inn_ct >= 11 ? 'ExInn' : row.inn_ct;
            } else if (header.match(/^[0-2]\s+(Out|Outs)$/)) {
                // RE24 table: "0 Outs", "1 Out", "2 Outs" - use raw values
                const outNum = header.charAt(0);
                value = row[`out_${outNum}_raw`] !== undefined ? row[`out_${outNum}_raw`] : row[`out_${outNum}`];
            } else if (data.bucket_columns && index >= 2) {
                // RE24 Probabilities with bucket_columns: use raw values (already 0-1 range)
                value = row[`${header}_raw`] !== undefined ? row[`${header}_raw`] : row[header];
            } else if (data.to_state_columns && index >= 1) {
                // RE24 Transitions with to_state_columns: use raw values (not _raw suffix anymore)
                value = row[header];
            } else if (header.match(/^\d+-\d+$/)) {
                // RE288: count columns like "0-0", "0-1", etc. - use raw values
                value = row[`${header}_raw`] !== undefined ? row[`${header}_raw`] : row[header];
                // For RE288_TRANS with probability mode, values are already in 0-1 range (not percentages)
                // No conversion needed - they export as decimals
            } else if ((data.matrix_type === 'WIN_EXPECTANCY' || data.matrix_type === 'WIN_PROBABILITY_ADDED' || data.matrix_type === 'LEVERAGE_INDEX') && (header.match(/^\-\d+\+?$/) || header.toLowerCase() === 'tie' || header.match(/^\+\d+\+?$/))) {
                // Win Expectancy, Win Probability Added, and Leverage Index run differential columns: convert from percentage to raw decimal (only WE and WPA are percentages)
                value = row[header];
                if (value !== null && value !== undefined && typeof value === 'number') {
                    // For WIN_EXPECTANCY and WIN_PROBABILITY_ADDED, convert from percentage; for LEVERAGE_INDEX, keep as-is
                    if (data.matrix_type === 'WIN_EXPECTANCY' || data.matrix_type === 'WIN_PROBABILITY_ADDED') {
                        value = value / 100; // Convert 49.4 to 0.494
                    }
                    // LEVERAGE_INDEX values are already raw decimals, no conversion needed
                }
            } else {
                // Try direct access
                value = row[header];
            }
            
            // Format value
            if (value === null || value === undefined) {
                value = '';
            } else if (typeof value === 'number') {
                value = value.toString();
            } else {
                value = value.toString();
            }
            
            // Escape commas and quotes in CSV
            if (value.includes(',') || value.includes('"')) {
                value = `"${value.replace(/"/g, '""')}"`;
            }
            
            values.push(value);
        });
        rows.push(values.join(','));
    });
    
    return rows.join('\n');
}

function convertToJSON(data) {
    // Handle wOBA Coefficients separately
    if (data.matrix_type === 'WOBA_COEFFS') {
        const jsonData = {
            matrix_type: 'WOBA_COEFFS',
            data: data.table_data.map(row => ({
                season: row.season,
                league_avg_obp: row.League_Avg_OBP,
                woba_scale: row.WOBA_Scale,
                wuBB: row.wuBB,
                wHBP: row.wHBP,
                w1B: row.w1B,
                w2B: row.w2B,
                w3B: row.w3B,
                wHR: row.wHR
            }))
        };
        return JSON.stringify(jsonData, null, 2);
    }
    
    // Handle FIP Constants separately
    if (data.matrix_type === 'FIP_CONST') {
        const jsonData = {
            matrix_type: 'FIP_CONST',
            data: data.table_data.map(row => ({
                season: row.season,
                lgERA: row.lgERA,
                FIP_Component: row.FIP_Component,
                FIP_Constant: row.FIP_Constant,
                lgHR: row.lgHR,
                lgBB: row.lgBB,
                lgHBP: row.lgHBP,
                lgK: row.lgK,
                lgER: row.lgER,
                lgIP: row.lgIP
            }))
        };
        return JSON.stringify(jsonData, null, 2);
    }
    
    // Handle Pythagorean Exponents separately
    if (data.matrix_type === 'PYTHAG_EXPONENTS') {
        const jsonData = {
            matrix_type: 'PYTHAG_EXPONENTS',
            data: data.table_data.map(row => ({
                season: row.season,
                '1_year': row['1 year'],
                '2_years': row['2 years'],
                '3_years': row['3 years'],
                '5_years': row['5 years'],
                '8_years': row['8 years'],
                '10_years': row['10 years'],
                '12_years': row['12 years'],
                'pythagenpat': row['Pythagenpat']
            }))
        };
        return JSON.stringify(jsonData, null, 2);
    }
    
    // Handle Runs Per Win separately
    if (data.matrix_type === 'RUNS_PER_WIN') {
        const jsonData = {
            matrix_type: 'RUNS_PER_WIN',
            data: data.table_data.map(row => ({
                season: row.season,
                '1_year': row['1 year'],
                '2_years': row['2 years'],
                '3_years': row['3 years'],
                '5_years': row['5 years'],
                '8_years': row['8 years'],
                '10_years': row['10 years'],
                '12_years': row['12 years'],
                'pythagenpat': row['Pythagenpat'],
                'fangraphs': row['FanGraphs']
            }))
        };
        return JSON.stringify(jsonData, null, 2);
    }
    
    // Handle Break-Even Points separately
    if (data.matrix_type === 'BREAKEVEN_POINTS') {
        const isByCount = data.selected_type === 'sb_by_count';
        const isSwingTake = data.selected_type === 'swing-take' || data.selected_type === 'swing_take';
        const isSwingTakeZone = data.selected_type === 'swing-take-zone' || data.selected_type === 'swing_take_zone';
        const isTagging = data.selected_type === 'tagging';
        const displayRows = Array.isArray(data.display_data) ? data.display_data : [];
        const jsonData = {
            matrix_type: 'BREAKEVEN_POINTS',
            type: data.selected_type,
            from_season: data.from_season,
            to_season: data.to_season,
            data: displayRows.map(row => {
                const toFrac = (v) => v !== null && v !== undefined ? v : null;
                const obj = {};
                if (isByCount || isSwingTake) {
                    obj.scenario = row.scenario;
                    obj.outs = row.outs;
                    COUNT_ORDER.forEach(count => {
                        obj[count] = toFrac(row[count]);
                    });
                } else if (isSwingTakeZone) {
                    obj.zone = row.zone;
                    obj.handedness = row.handedness;
                    COUNT_ORDER.forEach(count => {
                        obj[count] = toFrac(row[count]);
                    });
                } else {
                    obj.scenario = row.scenario;
                    obj['0_outs'] = toFrac(row.outs_0);
                    obj['1_out'] = toFrac(row.outs_1);
                    if (!isTagging) obj['2_outs'] = toFrac(row.outs_2);
                }
                return obj;
            })
        };
        return JSON.stringify(jsonData, null, 2);
    }
    
    // Handle Sac Bunt RE separately
    if (data.matrix_type === 'SAC_BUNT_RE') {
        const jsonData = {
            matrix_type: 'SAC_BUNT_RE',
            from_season: data.from_season,
            to_season: data.to_season,
            data: data.display_data.map(row => ({
                scenario: row.scenario,
                '0_outs': row.outs_0,
                '1_out': row.outs_1
            }))
        };
        return JSON.stringify(jsonData, null, 2);
    }
    
    // Handle RE Impact Analysis separately
    if (data.matrix_type === 'RE_IMPACT') {
        const analysisType = data.analysis_type;
        const displayMode = getReImpactDisplayMode(data);
        const jsonData = {
            matrix_type: 'RE_IMPACT',
            analysis_type: analysisType,
            from_season: data.from_season,
            to_season: data.to_season,
            display_mode: displayMode,
            data: null
        };
        
        if (escenariosPorEstadoDeBases(analysisType)) {
            jsonData.data = data.display_data.map(row => ({
                scenario: row.scenario,
                '0_outs': formatReImpactJsonValue(row.outs_0, displayMode),
                '1_out': formatReImpactJsonValue(row.outs_1, displayMode)
            }));
        } else if (analysisType === 'ibb') {
            jsonData.data = data.display_data.map(row => ({
                scenario: row.scenario,
                '0_outs': formatReImpactJsonValue(row.outs_0, displayMode),
                '1_out': formatReImpactJsonValue(row.outs_1, displayMode),
                '2_outs': formatReImpactJsonValue(row.outs_2, displayMode)
            }));
        } else if (escenariosDeImpactoEnCarreras(analysisType)) {
            jsonData.data = filasDeImpactoEnCarreras(data).map(row => ({
                scenario: row.scenario,
                '0_outs': formatReImpactJsonValue(row.outs_0, displayMode),
                '1_out': formatReImpactJsonValue(row.outs_1, displayMode),
                '2_outs': formatReImpactJsonValue(row.outs_2, displayMode)
            }));
        } else if (analysisType === 'ph_position' || analysisType === 'ph_pitcher') {
            jsonData.data = data.display_data.map(row => ({
                pitcher_batter_matchup: row.matchup ?? row.label ?? row.scenario,
                '0_outs': formatReImpactJsonValue(row.outs_0, displayMode),
                '1_out': formatReImpactJsonValue(row.outs_1, displayMode),
                '2_outs': formatReImpactJsonValue(row.outs_2, displayMode)
            }));
        } else if (analysisType === 'platoon_baseline') {
            jsonData.data = data.display_data.map(row => ({
                pitcher_batter_matchup: row.platoon_match ?? row.label ?? row.scenario,
                '0_outs': formatReImpactJsonValue(row.outs_0, displayMode),
                '1_out': formatReImpactJsonValue(row.outs_1, displayMode),
                '2_outs': formatReImpactJsonValue(row.outs_2, displayMode),
                'overall_value': formatReImpactJsonValue(row.weighted_mean_outs, displayMode)
            }));
        } else if (analysisType === 'tto') {
            jsonData.data = data.display_data.map(row => ({
                runners: row.runners ?? row.scenario,
                handedness: row.handedness ?? row.handedness_split,
                'first_time_faced': formatReImpactJsonValue(row.tto_1, displayMode),
                'second_time_faced': formatReImpactJsonValue(row.tto_2, displayMode),
                'third_plus_times_faced': formatReImpactJsonValue(row.tto_3, displayMode)
            }));
        }
        
        return JSON.stringify(jsonData, null, 2);
    }
    
    // Handle Park Factors separately
    if (data.matrix_type === 'PARK_FACTORS') {
        const jsonData = {
            data: data.rows.map(row => ({
                season: row.season,
                park_id: row.park_id,
                park_name: row.park_name,
                team_name: row.team_name,
                seasons: row.seasons,
                pf_r: row.pf_r,
                pf_1b: row.pf_1b,
                pf_2b: row.pf_2b,
                pf_3b: row.pf_3b,
                pf_hr: row.pf_hr,
                pf_bb: row.pf_bb,
                pf_so: row.pf_so
            }))
        };
        return JSON.stringify(jsonData, null, 2);
    }
    
    // Handle Linear Weights separately
    if (data.matrix_type === 'LINEAR_WEIGHTS') {
        const jsonData = {
            matrix_type: 'LINEAR_WEIGHTS',
            from_season: data.from_season,
            to_season: data.to_season,
            outs: data.outs !== undefined && data.outs !== null ? data.outs : -1,
            data: data.rows.map(row => ({
                event: row.event,
                average: row['AVG'],
                [FILES_BASE_STATE_LABELS['000']]: row['000'],
                [FILES_BASE_STATE_LABELS['100']]: row['100'],
                [FILES_BASE_STATE_LABELS['010']]: row['010'],
                [FILES_BASE_STATE_LABELS['001']]: row['001'],
                [FILES_BASE_STATE_LABELS['110']]: row['110'],
                [FILES_BASE_STATE_LABELS['101']]: row['101'],
                [FILES_BASE_STATE_LABELS['011']]: row['011'],
                [FILES_BASE_STATE_LABELS['111']]: row['111']
            }))
        };
        return JSON.stringify(jsonData, null, 2);
    }
    
    // Handle R24 Frequencies separately
    if (data.matrix_type === 'R24_FREQUENCIES') {
        const jsonData = {
            matrix_type: 'R24_FREQUENCIES',
            from_season: data.from_season,
            to_season: data.to_season,
            data: data.rows.map(row => ({
                runners: FILES_BASE_STATE_LABELS[row.runners] || row.runners,
                '0_outs': row['0'],
                '1_out': row['1'],
                '2_outs': row['2']
            }))
        };
        return JSON.stringify(jsonData, null, 2);
    }
    
    // Create JSON content from table data
    
    // For RE24 Probabilities, use bucket_columns instead of headers for bucket names
    let transformedHeaders;
    if (data.bucket_columns) {
        // RE24 Probabilities: use raw bucket names (no_runs, at_least_one_run, etc.)
        const baseHeaders = data.headers.slice(0, 2).map(h => h.toLowerCase().replace(/\s+/g, '_')); // "Runners", "Outs"
        transformedHeaders = [...baseHeaders, ...data.bucket_columns];
    } else if (data.matrix_type === 'WIN_EXPECTANCY' || data.matrix_type === 'LEVERAGE_INDEX') {
        // Win Expectancy and Leverage Index: transform run differential columns
        transformedHeaders = data.headers.map(h => {
            // Check if header is a run differential column
            if (h.match(/^\-\d+\+?$/)) {
                // Negative: "-4" or "-4+" -> behind_by_4_runs or behind_by_4_plus_runs
                const num = h.substring(1).replace('+', '');
                const suffix = h.endsWith('+') ? '_plus_runs' : '_runs';
                return `behind_by_${num}${suffix}`;
            } else if (h.toLowerCase() === 'tie') {
                return 'tie';
            } else if (h.match(/^\+\d+\+?$/)) {
                // Positive: "+1" or "+4+" -> ahead_by_1_runs or ahead_by_4_plus_runs
                const num = h.substring(1).replace('+', '');
                const suffix = h.endsWith('+') ? '_plus_runs' : '_runs';
                return `ahead_by_${num}${suffix}`;
            } else {
                // Regular headers like "Inning", "Outs"
                return h.toLowerCase().replace(/\s+/g, '_');
            }
        });
    } else {
        // Other tables: transform headers to lowercase with underscores
        transformedHeaders = data.headers.map(h => h.toLowerCase().replace(/\s+/g, '_'));
    }
    
    const jsonData = {
        from_season: data.from_season,
        to_season: data.to_season
    };
    
    // For RE24 Values, add event_name
    if (data.matrix_type === 'RE24') {
        jsonData.event_name = data.event_name || 'All Events';
    }
    
    // For RE24 Probabilities, add inn_name
    if (data.matrix_type === 'RE24_PCT') {
        jsonData.inn_name = data.inn_name || 'All Innings';
    }
    
    // For RE288 Transitions, add value_type and transition_type
    if (data.matrix_type === 'RE288_TRANS') {
        jsonData.value_type = data.transition_mode === 2 ? 'Probability' : (data.transition_mode === 3 ? 'Expected Run Expectancy' : 'Delta Run Expectancy');
        jsonData.transition_type = data.transition_type === 'B' ? 'To Ball' : data.transition_type === 'S' ? 'To Strike' : 'To Ball In Play';
        // Note: For transition_mode=2 (Probability), values are exported as decimals (0-1 range), not percentages
    }

    // For RE24 Transitions, add type and outs
    if (data.matrix_type === 'RE24_TRANSITIONS') {
        jsonData.type = data.type || 'Run Value';
        jsonData.outs = data.outs;
    }
    
    // For Win Expectancy, Win Probability Added, and Leverage Index, add team
    if (data.matrix_type === 'WIN_EXPECTANCY' || data.matrix_type === 'WIN_PROBABILITY_ADDED' || data.matrix_type === 'LEVERAGE_INDEX') {
        jsonData.team = data.team_name || (data.team === 0 ? 'Away' : 'Home');
    }
    
    jsonData.headers = transformedHeaders;
    jsonData.rows = data.rows.map(row => {
            const cleanRow = {};
            
            // For RE24 Probabilities with bucket_columns, iterate over bucket_columns for data values
            const headersToIterate = data.bucket_columns ? [...data.headers.slice(0, 2), ...data.bucket_columns] : data.headers;
            
            headersToIterate.forEach((header, index) => {
                let value;
                
                // Handle special cases based on header pattern
                if (header === 'Runners') {
                    // Use FILES_BASE_STATE_LABELS for downloaded files
                    value = FILES_BASE_STATE_LABELS[row.runners] || row.label || row.base_state;
                } else if (header === 'Outs') {
                    value = row.outs !== undefined ? row.outs : row.outs_ct;
                } else if (header === 'Inning' || header === 'inning') {
                    value = row.inn_ct >= 11 ? 'ExInn' : row.inn_ct;
                } else if (header.match(/^[0-2]\s+(Out|Outs)$/)) {
                    // RE24 table: "0 Outs", "1 Out", "2 Outs" - use raw values
                    const outNum = header.charAt(0);
                    value = row[`out_${outNum}_raw`] !== undefined ? row[`out_${outNum}_raw`] : row[`out_${outNum}`];
                } else if (data.bucket_columns && index >= 2) {
                    // RE24 Probabilities with bucket_columns: use raw values (already 0-1 range)
                    value = row[`${header}_raw`] !== undefined ? row[`${header}_raw`] : row[header];
                } else if (header.match(/^\d+-\d+$/)) {
                    // RE288: count columns like "0-0", "0-1", etc. - use raw values
                    value = row[`${header}_raw`] !== undefined ? row[`${header}_raw`] : row[header];
                } else if ((data.matrix_type === 'WIN_EXPECTANCY' || data.matrix_type === 'WIN_PROBABILITY_ADDED' || data.matrix_type === 'LEVERAGE_INDEX') && (header.match(/^\-\d+\+?$/) || header.toLowerCase() === 'tie' || header.match(/^\+\d+\+?$/))) {
                    // Win Expectancy, Win Probability Added, and Leverage Index run differential columns
                    value = row[header];
                } else {
                    // Try direct access
                    value = row[header];
                }
                
                // Use transformed header as key
                cleanRow[transformedHeaders[index]] = value;
            });
            return cleanRow;
        });
    
    return JSON.stringify(jsonData, null, 2);
}

// Global pagination state for Win Expectancy table
let re24ProbabilitiesPagination = {
    currentPage: 1,
    rowsPerPage: 12,
    totalRows: 0,
    data: null
};

let winExpectancyPagination = {
    currentPage: 1,
    rowsPerPage: 24,
    totalRows: 0,
    data: null
};

let winProbabilityAddedPagination = {
    currentPage: 1,
    rowsPerPage: 24,
    totalRows: 0,
    data: null
};

let leverageIndexPagination = {
    currentPage: 1,
    rowsPerPage: 24,
    totalRows: 0,
    data: null
};

let linearWeightsPagination = {
    currentPage: 1,
    rowsPerPage: 12,
    totalRows: 0,
    data: null
};

// Global pagination state for RE288 Values and Frequencies table
let re288Pagination = {
    currentPage: 1,
    rowsPerPage: 12,
    totalRows: 0,
    data: null
};

// Global pagination state for RE288 Transitions table
let re288TransitionsPagination = {
    data: null,
    currentPage: 1,
    rowsPerPage: 12,
    totalRows: 0
};

// Global pagination state for wOBA table
let wobaPagination = {
    currentPage: 1,
    rowsPerPage: 12,
    totalRows: 0,
    data: null,
    sortColumn: 'season',
    sortDirection: 'desc'
};

let reImpactTtoPagination = {
    currentPage: 1,
    rowsPerPage: 8,
    totalRows: 0
};

// wRC Components: las constantes con las que un investigador calcula wRC, wRC+ y
// wRAA. Sabertables no publica jugadores; publica con que calcularlos.
//
// Autocontenido a proposito, sin clonar renderWOBATable: son 116 filas fijas, una
// por temporada, y no necesitan paginacion ni ordenacion. Menos codigo compartido
// es menos sitios donde el proximo tipo se olvide.
const WRC_COMPONENTS_COLUMNAS = [
    ['season',              'Season',            0],
    ['League_Avg_OBP',      'lgwOBA',            5],
    ['WOBA_Scale',          'wOBA Scale',        5],
    ['lg_r_per_pa',         'lgR/PA',            5],
    ['lg_wrc_per_pa_sin_p', 'lgwRC/PA (non-P)',  5],
    ['lg_woba_sin_p',       'lgwOBA (non-P)',    5],
    ['lg_pa',               'League PA',         0],
    ['lg_runs',             'League Runs',       0],
    ['lg_share_pa_pitchers','Pitcher PA %',      3]
];

function renderWRCComponentsTable(data) {
    currentTableData.wrc_components = data;
    const titulo = document.getElementById('wrc-components-table-title');
    const sub = document.getElementById('wrc-components-table-subtitle');
    const head = document.getElementById('wrc-components-table-head');
    const body = document.getElementById('wrc-components-table-body');
    if (!head || !body) { console.error('wRC Components table elements not found'); return; }

    if (titulo) titulo.textContent = 'wRC Components by Season';
    if (sub) sub.textContent = 'Everything needed to compute wRAA, wRC and wRC+ for any batter. '
        + 'wRAA = ((wOBA − lgwOBA) / wOBA Scale) × PA. '
        + 'wRC = (((wOBA − lgwOBA) / wOBA Scale) + lgR/PA) × PA. '
        + 'wRC+ = ((wRAA/PA + lgR/PA) + (lgR/PA − PF × lgR/PA)) / lgwRC/PA (non-P) × 100. '
        + 'Park factors (PF) are published in the Park Factors table. Regular season only.';

    head.innerHTML = '<tr>' + WRC_COMPONENTS_COLUMNAS
        .map(([, etiqueta]) => `<th scope="col">${etiqueta}</th>`).join('') + '</tr>';

    body.innerHTML = (data.table_data || []).map(fila => '<tr>' + WRC_COMPONENTS_COLUMNAS
        .map(([clave, , dec]) => {
            const v = fila[clave];
            if (v === null || v === undefined) return '<td>&mdash;</td>';
            return `<td>${dec ? Number(v).toFixed(dec) : Number(v).toLocaleString()}</td>`;
        }).join('') + '</tr>').join('');
}

function renderWOBATable(data) {
    // Store data for download and pagination
    currentTableData.woba = data;
    wobaPagination.data = data.table_data;
    wobaPagination.totalRows = data.table_data.length;
    wobaPagination.currentPage = 1;
    
    const titleEl = document.getElementById('woba-table-title');
    const subtitleEl = document.getElementById('woba-table-title-subtitle');
    const bodyEl = document.getElementById('woba-table-body');
    const eventCountEl = document.getElementById('woba-event-count');
    
    if (!titleEl || !bodyEl) {
        console.error('wOBA table elements not found');
        return;
    }
    
    // Update title
    titleEl.textContent = 'wOBA Coefficients by Season';
    if (subtitleEl) {
        subtitleEl.textContent = 'wOBA coefficients measure the OBP-scaled offensive value of each batting event, derived from its average run value';
    }
    
    // Update event count with total seasons
    if (eventCountEl) {
        eventCountEl.textContent = `${wobaPagination.totalRows} seasons`;
    }
    
    // Render headers with sorting
    renderWOBAHeaders();
    
    // Render current page
    renderWOBAPage();
    
    // Render pagination controls
    renderWOBAPagination();
}

function renderWOBAHeaders() {
    const getSortIcon = (column) => {
        if (wobaPagination.sortColumn !== column) {
            return '<span style="opacity: 0.3;">⇅</span>';
        }
        return wobaPagination.sortDirection === 'asc' ? '▲' : '▼';
    };
    
    const headEl = document.getElementById('woba-table-head');
    if (!headEl) return;
    
    headEl.innerHTML = `
        <tr>
            <th class="bg-primary text-center" style="cursor: pointer; user-select: none;" data-accion="sortWOBA" data-clave="season">
                Season ${getSortIcon('season')}
            </th>
            <th class="bg-primary text-center" style="cursor: pointer; user-select: none;" data-accion="sortWOBA" data-clave="League_Avg_OBP">
                League Avg OBP ${getSortIcon('League_Avg_OBP')}
            </th>
            <th class="bg-primary text-center" style="cursor: pointer; user-select: none;" data-accion="sortWOBA" data-clave="WOBA_Scale">
                wOBA Scale ${getSortIcon('WOBA_Scale')}
            </th>
            <th class="bg-primary text-center" style="cursor: pointer; user-select: none;" data-accion="sortWOBA" data-clave="wuBB">
                wBB ${getSortIcon('wuBB')}
            </th>
            <th class="bg-primary text-center" style="cursor: pointer; user-select: none;" data-accion="sortWOBA" data-clave="wHBP">
                wHBP ${getSortIcon('wHBP')}
            </th>
            <th class="bg-primary text-center" style="cursor: pointer; user-select: none;" data-accion="sortWOBA" data-clave="w1B">
                w1B ${getSortIcon('w1B')}
            </th>
            <th class="bg-primary text-center" style="cursor: pointer; user-select: none;" data-accion="sortWOBA" data-clave="w2B">
                w2B ${getSortIcon('w2B')}
            </th>
            <th class="bg-primary text-center" style="cursor: pointer; user-select: none;" data-accion="sortWOBA" data-clave="w3B">
                w3B ${getSortIcon('w3B')}
            </th>
            <th class="bg-primary text-center" style="cursor: pointer; user-select: none;" data-accion="sortWOBA" data-clave="wHR">
                wHR ${getSortIcon('wHR')}
            </th>
        </tr>
    `;
}

function renderWOBAPage() {
    const bodyEl = document.getElementById('woba-table-body');
    if (!bodyEl || !wobaPagination.data) return;
    
    // Sort the data
    const sortedData = [...wobaPagination.data].sort((a, b) => {
        const column = wobaPagination.sortColumn;
        let aVal = a[column];
        let bVal = b[column];
        
        // Handle null values
        if (aVal === null && bVal === null) return 0;
        if (aVal === null) return 1;
        if (bVal === null) return -1;
        
        const result = aVal - bVal;
        return wobaPagination.sortDirection === 'asc' ? result : -result;
    });
    
    const startIdx = (wobaPagination.currentPage - 1) * wobaPagination.rowsPerPage;
    const endIdx = Math.min(startIdx + wobaPagination.rowsPerPage, wobaPagination.totalRows);
    const pageData = sortedData.slice(startIdx, endIdx);
    
    // Check if heatmap is enabled
    const heatmapEnabled = globalHeatmapEnabled;
    
    // Calculate min/max for w* columns if heatmap is enabled
    let minMax = {};
    if (heatmapEnabled) {
        const wColumns = ['wuBB', 'wHBP', 'w1B', 'w2B', 'w3B', 'wHR'];
        wColumns.forEach(col => {
            const values = wobaPagination.data.map(r => r[col]);
            minMax[col] = {
                min: Math.min(...values),
                max: Math.max(...values)
            };
        });
    }
    
    // Helper function to render cell with optional heatmap
    const renderCell = (value, columnKey) => {
        const displayValue = value.toFixed(3);
        if (heatmapEnabled && minMax[columnKey]) {
            const { bgColor, textColor } = getHeatmapColor(value, minMax[columnKey].min, minMax[columnKey].max);
            return `<span style="background-color: ${bgColor}; color: ${textColor}; padding: 2px 6px; border-radius: 3px; display: inline-block; min-width: 50px;">${displayValue}</span>`;
        }
        return displayValue;
    };
    
    const rows = pageData.map(row => {
        return `
            <tr>
                <td class="text-center fw-medium">${row.season}</td>
                <td class="text-center">${row.League_Avg_OBP.toFixed(3)}</td>
                <td class="text-center">${row.WOBA_Scale.toFixed(3)}</td>
                <td class="text-center">${renderCell(row.wuBB, 'wuBB')}</td>
                <td class="text-center">${renderCell(row.wHBP, 'wHBP')}</td>
                <td class="text-center">${renderCell(row.w1B, 'w1B')}</td>
                <td class="text-center">${renderCell(row.w2B, 'w2B')}</td>
                <td class="text-center">${renderCell(row.w3B, 'w3B')}</td>
                <td class="text-center">${renderCell(row.wHR, 'wHR')}</td>
            </tr>
        `;
    }).join('');
    
    bodyEl.innerHTML = rows;
}

function renderWOBAPagination() {
    const totalPages = Math.ceil(wobaPagination.totalRows / wobaPagination.rowsPerPage);
    renderPaginationControls({
        containerId: 'woba-pagination',
        currentPage: wobaPagination.currentPage,
        totalPages,
        changeHandlerName: 'changeWOBAPage',
        labels: {
            first: '⏮',
            previous: '◀',
            next: '▶',
            last: '⏭'
        },
        useDisabledVariant: false
    });
}

function changeWOBAPage(newPage) {
    const totalPages = Math.ceil(wobaPagination.totalRows / wobaPagination.rowsPerPage);
    setPaginationPage(wobaPagination, newPage, totalPages, () => {
        renderWOBAPage();
        renderWOBAPagination();
    });
}

function sortWOBA(column) {
    // Toggle sort direction if clicking the same column
    if (wobaPagination.sortColumn === column) {
        wobaPagination.sortDirection = wobaPagination.sortDirection === 'asc' ? 'desc' : 'asc';
    } else {
        // New column, default to descending
        wobaPagination.sortColumn = column;
        wobaPagination.sortDirection = column === 'season' ? 'desc' : 'desc';
    }
    
    // Reset to first page when sorting
    wobaPagination.currentPage = 1;
    
    // Re-render headers and table
    renderWOBAHeaders();
    renderWOBAPage();
    renderWOBAPagination();
}

// Global pagination state for FIP table
let fipPagination = {
    currentPage: 1,
    rowsPerPage: 12,
    totalRows: 0,
    data: null
};

// Global pagination state for Pythagorean Exponents table
let pythagExponentsPagination = {
    currentPage: 1,
    rowsPerPage: 12,
    totalRows: 0,
    data: null
};

function renderFIPTable(data) {
    // Store data for download and pagination
    currentTableData.fip = data;
    fipPagination.data = data.table_data;
    fipPagination.totalRows = data.table_data.length;
    fipPagination.currentPage = 1;
    
    const titleEl = document.getElementById('fip-table-title');
    const subtitleEl = document.getElementById('fip-table-title-subtitle');
    const headEl = document.getElementById('fip-table-head');
    const bodyEl = document.getElementById('fip-table-body');
    const eventCountEl = document.getElementById('fip-event-count');
    
    if (!titleEl || !headEl || !bodyEl) {
        console.error('FIP table elements not found');
        return;
    }
    
    // Update title
    titleEl.textContent = 'FIP Constants by Season';
    if (subtitleEl) {
        subtitleEl.innerHTML = 'FIP components measure the run contribution of pitcher-controlled events using fixed linear weights.<br>The FIP Constant is a calibration term that aligns league-average FIP with league-average ERA for a given season.';
    }
    
    // Update event count with total seasons
    if (eventCountEl) {
        eventCountEl.textContent = `${fipPagination.totalRows} seasons`;
    }
    
    // Generate table headers
    const headers = ['Season', 'lgERA', 'FIP Component', 'FIP Constant', 'lgHR', 'lgBB', 'lgHBP', 'lgK', 'lgER', 'lgIP'];
    headEl.innerHTML = `
        <tr>
            ${headers.map((h, i) => `<th class="bg-primary ${i === 9 ? 'text-end' : 'text-center'}">${h}</th>`).join('')}
        </tr>
    `;
    
    // Render first page
    renderFIPPage();
    renderFIPPagination();
}

function renderFIPPage() {
    const bodyEl = document.getElementById('fip-table-body');
    if (!bodyEl || !fipPagination.data) return;
    
    const startIdx = (fipPagination.currentPage - 1) * fipPagination.rowsPerPage;
    const endIdx = Math.min(startIdx + fipPagination.rowsPerPage, fipPagination.totalRows);
    const pageData = fipPagination.data.slice(startIdx, endIdx);
    
    bodyEl.innerHTML = pageData.map(row => {
        // Format lgIP with fractional outs
        let lgIPFormatted;
        const ipWhole = Math.floor(row.lgIP);
        const ipFraction = row.lgIP - ipWhole;
        
        if (ipFraction > 0.9) {
            // .999... rounds to next whole number
            lgIPFormatted = (ipWhole + 1).toLocaleString();
        } else if (ipFraction >= 0.6 && ipFraction < 0.7) {
            // .666... is 2/3
            lgIPFormatted = `${ipWhole.toLocaleString()}⅔`;
        } else if (ipFraction >= 0.3 && ipFraction < 0.4) {
            // .333... is 1/3
            lgIPFormatted = `${ipWhole.toLocaleString()}⅓`;
        } else {
            // Whole number (0 outs)
            lgIPFormatted = ipWhole.toLocaleString();
        }
        
        return `
        <tr>
            <td class="text-center">${row.season}</td>
            <td class="text-center">${Number(row.lgERA).toFixed(2)}</td>
            <td class="text-center">${Number(row.FIP_Component).toFixed(3)}</td>
            <td class="text-center">${Number(row.FIP_Constant).toFixed(3)}</td>
            <td class="text-center">${row.lgHR.toLocaleString()}</td>
            <td class="text-center">${row.lgBB.toLocaleString()}</td>
            <td class="text-center">${row.lgHBP.toLocaleString()}</td>
            <td class="text-center">${row.lgK.toLocaleString()}</td>
            <td class="text-center">${row.lgER.toLocaleString()}</td>
            <td class="text-end">${lgIPFormatted}</td>
        </tr>
        `;
    }).join('');
}

function renderFIPPagination() {
    const totalPages = Math.ceil(fipPagination.totalRows / fipPagination.rowsPerPage);
    renderPaginationControls({
        containerId: 'fip-pagination',
        currentPage: fipPagination.currentPage,
        totalPages,
        changeHandlerName: 'changeFIPPage',
        labels: {
            first: '⏮',
            previous: '◀',
            next: '▶',
            last: '⏭'
        },
        useDisabledVariant: false
    });
}

function changeFIPPage(newPage) {
    const totalPages = Math.ceil(fipPagination.totalRows / fipPagination.rowsPerPage);
    setPaginationPage(fipPagination, newPage, totalPages, () => {
        renderFIPPage();
        renderFIPPagination();
    });
}

// Columnas de valor de las tablas de exponente pitagorico y carreras por victoria.
// Cada una tiene su gemela '<col>_n' con las equipo-temporadas que alimentaron el
// ajuste, que es lo que alimenta la senal de confianza.
const PYTHAG_EXPONENT_COLUMNS = ['1 year', '2 years', '3 years', '5 years', '8 years', '10 years', '12 years', 'Pythagenpat'];

// Pythagenpat no lleva marcador de fiabilidad, y no es una excepcion decorativa.
//
// El marcador mide el tamano de muestra de un AJUSTE: las columnas de 1, 2, 3...
// anos salen de minimos cuadrados sobre equipos-temporada, y con mas puntos el
// exponente se estima mejor. Pythagenpat no ajusta nada -- es la formula cerrada
// ((RS+RA)/G)^0.287 promediada por equipos --, asi que su n (los ~30 equipos de
// la temporada) no significa lo mismo y la caia en el cuantil bajo.
//
// Medido con bootstrap remuestreando equipos, B=400 sobre las 125 temporadas:
//
//     Pythagenpat             SE mediana 0.0080
//     exponente de 1 ano      SE mediana 0.1264   (16x mas ruidoso)
//     exponente de 12 anos    SE         0.040
//
// O sea que la unica columna marcada como poco fiable era, con diferencia, la
// mas precisa de la tabla: 5x mejor que la de 12 anos. Contrastado ademas con el
// SE analitico de la media ponderada, que correlaciona 0.9933 con el bootstrap.
//
// Ademas de no marcarla, se la saca del calculo de los umbrales: su n = 30
// arrastraba el cuantil hacia abajo y con ello la escala de las demas columnas.
// FanGraphs esta por lo mismo: su RPW es 9*(Total_RS/Total_IP)*1.5+3, una razon
// de TOTALES DE LIGA sobre ~2,430 juegos, y no usa el exponente ajustado. Hereda
// el n de la columna de 1 ano (FanGraphs_n = 1 year_n) solo porque habia que
// ponerle alguno, asi que salia marcada como poco fiable siendo una cantidad sin
// muestreo por equipos.
const RELIABILITY_EXEMPT_COLUMNS = new Set(['Pythagenpat', 'FanGraphs']);
const PYTHAG_RELIABILITY_COLUMNS = PYTHAG_EXPONENT_COLUMNS.filter(c => !RELIABILITY_EXEMPT_COLUMNS.has(c));
const RUNS_PER_WIN_COLUMNS = [...PYTHAG_EXPONENT_COLUMNS, 'FanGraphs'];
const RUNS_PER_WIN_RELIABILITY_COLUMNS = RUNS_PER_WIN_COLUMNS.filter(c => !RELIABILITY_EXEMPT_COLUMNS.has(c));

function renderPythagExponentsTable(data) {
    currentTableData.pythag_exponents = data;
    pythagExponentsPagination.data = data.table_data;
    pythagExponentsPagination.totalRows = data.table_data.length;
    pythagExponentsPagination.currentPage = 1;
    
    const titleEl = document.getElementById('pythag-exponents-table-title');
    const subtitleEl = document.getElementById('pythag-exponents-table-title-subtitle');
    const headEl = document.getElementById('pythag-exponents-table-head');
    const bodyEl = document.getElementById('pythag-exponents-table-body');
    const eventCountEl = document.getElementById('pythag-exponents-event-count');
    
    if (!titleEl || !headEl || !bodyEl) {
        console.error('Pythagorean Exponents table elements not found');
        return;
    }
    
    // Update title and subtitle
    titleEl.textContent = 'Pythagorean Exponents by Season';
    if (subtitleEl) {
        subtitleEl.innerHTML = 'Each fitted column answers: what exponent would have best predicted team win totals over the last 1, 2, 3... 12 seasons?<br>Pythagenpat skips the guessing and computes the exponent directly from how many runs were scored per game.';
    }
    
    if (eventCountEl) {
        eventCountEl.innerHTML = `${pythagExponentsPagination.totalRows} seasons &nbsp; ${getReliabilityLegendSpan()}`;
    }

    // El exponente se ajusta con un punto por equipo, asi que su muestra real es
    // el numero de equipo-temporadas de la ventana: 30 para 1 ano, 361 para 12.
    // Eso reproduce el error estandar medido por bootstrap (0.126 a 0.040) y deja
    // que la misma maquinaria de confianza del resto de tablas marque la columna
    // de 1 ano como estimacion de baja confianza, en vez de presentarla con el
    // mismo peso visual que la de 12.
    pythagExponentsPagination.reliabilityStats = buildReliabilityStats(
        data.table_data,
        (row) => PYTHAG_RELIABILITY_COLUMNS.map((col) => row[`${col}_n`]),
        RELIABILITY_DEFAULT_CONFIG
    );

    const headers = ['Season', '1 Year', '2 Years', '3 Years', '5 Years', '8 Years', '10 Years', '12 Years', 'Pythagenpat'];
    headEl.innerHTML = `
        <tr>
            ${headers.map(h => `<th class="bg-primary text-center">${h}</th>`).join('')}
        </tr>
    `;
    
    renderPythagExponentsPage();
    renderPythagExponentsPagination();
}

function renderPythagExponentsPage() {
    const bodyEl = document.getElementById('pythag-exponents-table-body');
    if (!bodyEl || !pythagExponentsPagination.data) return;
    
    const startIdx = (pythagExponentsPagination.currentPage - 1) * pythagExponentsPagination.rowsPerPage;
    const endIdx = Math.min(startIdx + pythagExponentsPagination.rowsPerPage, pythagExponentsPagination.totalRows);
    const pageData = pythagExponentsPagination.data.slice(startIdx, endIdx);
    
    // Check if heatmap is enabled
    const heatmapEnabled = globalHeatmapEnabled;
    
    // Calculate min/max for each column for heatmap
    let minMax = {};
    if (heatmapEnabled) {
        const columns = ['1 year', '2 years', '3 years', '5 years', '8 years', '10 years', '12 years', 'Pythagenpat'];
        columns.forEach(col => {
            const values = pythagExponentsPagination.data.map(r => r[col]).filter(v => v !== null && v !== undefined && !isNaN(v));
            if (values.length > 0) {
                minMax[col] = {
                    min: Math.min(...values),
                    max: Math.max(...values)
                };
            }
        });
    }
    
    const reliabilityStats = pythagExponentsPagination.reliabilityStats;

    // Helper function to render cell with optional heatmap
    const renderCell = (row, columnKey) => {
        const value = row[columnKey];
        if (value === null || value === undefined || isNaN(value)) return '—';
        const displayValue = Number(value).toFixed(4);
        const nValue = Number(row[`${columnKey}_n`]);
        // Ver RELIABILITY_EXEMPT_COLUMNS: Pythagenpat no es una estimacion
        // ajustada, asi que el marcador no le aplica.
        const marcable = !RELIABILITY_EXEMPT_COLUMNS.has(columnKey);
        if (heatmapEnabled && minMax[columnKey]) {
            const badge = renderHeatmapBadge(displayValue, value, minMax[columnKey].min, minMax[columnKey].max, { minWidth: '60px' });
            return marcable ? appendReliabilityMarkerInsideBadge(badge, nValue, reliabilityStats) : badge;
        }
        return marcable ? appendReliabilityMarker(displayValue, nValue, reliabilityStats) : displayValue;
    };

    bodyEl.innerHTML = pageData.map(row => {
        return `
        <tr>
            <td class="text-center">${row.season}</td>
            ${PYTHAG_EXPONENT_COLUMNS.map(col => `<td class="text-center">${renderCell(row, col)}</td>`).join('')}
        </tr>
        `;
    }).join('');
}

function renderPythagExponentsPagination() {
    const totalPages = Math.ceil(pythagExponentsPagination.totalRows / pythagExponentsPagination.rowsPerPage);
    renderPaginationControls({
        containerId: 'pythag-exponents-pagination',
        currentPage: pythagExponentsPagination.currentPage,
        totalPages,
        changeHandlerName: 'changePythagExponentsPage',
        labels: {
            first: '⏮',
            previous: '◀',
            next: '▶',
            last: '⏭'
        },
        useDisabledVariant: false
    });
}

function changePythagExponentsPage(newPage) {
    const totalPages = Math.ceil(pythagExponentsPagination.totalRows / pythagExponentsPagination.rowsPerPage);
    setPaginationPage(pythagExponentsPagination, newPage, totalPages, () => {
        renderPythagExponentsPage();
        renderPythagExponentsPagination();
    });
}

// Runs Per Win table pagination
let runsPerWinPagination = {
    currentPage: 1,
    rowsPerPage: 12,
    totalRows: 0,
    data: null
};

function renderRunsPerWinTable(data) {
    currentTableData.runs_per_win = data;
    runsPerWinPagination.data = data.table_data;
    runsPerWinPagination.totalRows = data.table_data.length;
    runsPerWinPagination.currentPage = 1;
    
    const titleEl = document.getElementById('runs-per-win-table-title');
    const subtitleEl = document.getElementById('runs-per-win-table-title-subtitle');
    const headEl = document.getElementById('runs-per-win-table-head');
    const bodyEl = document.getElementById('runs-per-win-table-body');
    const eventCountEl = document.getElementById('runs-per-win-event-count');
    
    if (!titleEl || !headEl || !bodyEl) {
        console.error('Runs Per Win table elements not found');
        return;
    }
    
    // Update title and subtitle
    titleEl.textContent = 'Runs Per Win by Season';
    if (subtitleEl) {
        subtitleEl.innerHTML = 'How many more runs does a team need to score (or prevent) to win one additional game? That\u2019s what Runs Per Win measures.<br>The Pythagorean columns calculate it using the Pythagorean exponent and league scoring. The FanGraphs column uses an alternative formula developed by Tom Tango.';
    }
    
    if (eventCountEl) {
        eventCountEl.innerHTML = `${runsPerWinPagination.totalRows} seasons &nbsp; ${getReliabilityLegendSpan()}`;
    }

    // Cada columna de RPW se deriva del exponente de su misma ventana, asi que
    // hereda su tamano de muestra. FanGraphs no usa el exponente pero sale de los
    // totales de una sola temporada, con lo que su muestra es la de 1 ano.
    runsPerWinPagination.reliabilityStats = buildReliabilityStats(
        data.table_data,
        (row) => RUNS_PER_WIN_RELIABILITY_COLUMNS.map((col) => row[`${col}_n`]),
        RELIABILITY_DEFAULT_CONFIG
    );

    const headers = ['Season', '1 Year', '2 Years', '3 Years', '5 Years', '8 Years', '10 Years', '12 Years', 'Pythagenpat', 'FanGraphs'];
    headEl.innerHTML = `
        <tr>
            ${headers.map(h => `<th class="bg-primary text-center">${h}</th>`).join('')}
        </tr>
    `;
    
    renderRunsPerWinPage();
    renderRunsPerWinPagination();
}

function renderRunsPerWinPage() {
    const bodyEl = document.getElementById('runs-per-win-table-body');
    if (!bodyEl || !runsPerWinPagination.data) return;

    const startIdx = (runsPerWinPagination.currentPage - 1) * runsPerWinPagination.rowsPerPage;
    const endIdx = Math.min(startIdx + runsPerWinPagination.rowsPerPage, runsPerWinPagination.totalRows);
    const pageData = runsPerWinPagination.data.slice(startIdx, endIdx);

    const heatmapEnabled = globalHeatmapEnabled;
    let minMax = {};

    if (heatmapEnabled) {
        const columns = ['1 year', '2 years', '3 years', '5 years', '8 years', '10 years', '12 years', 'Pythagenpat', 'FanGraphs'];
        columns.forEach(col => {
            const values = runsPerWinPagination.data
                .map(r => r[col])
                .filter(v => v !== null && v !== undefined && !isNaN(v));
            if (values.length > 0) {
                minMax[col] = {
                    min: Math.min(...values),
                    max: Math.max(...values)
                };
            }
        });
    }

    const reliabilityStats = runsPerWinPagination.reliabilityStats;

    const renderCell = (row, columnKey) => {
        const value = row[columnKey];
        if (value === null || value === undefined || isNaN(value)) return '—';
        const displayValue = Number(value).toFixed(2);
        const nValue = Number(row[`${columnKey}_n`]);
        // Ver RELIABILITY_EXEMPT_COLUMNS: Pythagenpat no es una estimacion
        // ajustada, asi que el marcador no le aplica.
        const marcable = !RELIABILITY_EXEMPT_COLUMNS.has(columnKey);
        if (heatmapEnabled && minMax[columnKey]) {
            const badge = renderHeatmapBadge(displayValue, value, minMax[columnKey].min, minMax[columnKey].max, { minWidth: '60px' });
            return marcable ? appendReliabilityMarkerInsideBadge(badge, nValue, reliabilityStats) : badge;
        }
        return marcable ? appendReliabilityMarker(displayValue, nValue, reliabilityStats) : displayValue;
    };

    bodyEl.innerHTML = pageData.map(row => {
        return `
        <tr>
            <td class="text-center">${row.season}</td>
            ${RUNS_PER_WIN_COLUMNS.map(col => `<td class="text-center">${renderCell(row, col)}</td>`).join('')}
        </tr>
        `;
    }).join('');
}

function renderRunsPerWinPagination() {
    const totalPages = Math.ceil(runsPerWinPagination.totalRows / runsPerWinPagination.rowsPerPage);
    renderPaginationControls({
        containerId: 'runs-per-win-pagination',
        currentPage: runsPerWinPagination.currentPage,
        totalPages,
        changeHandlerName: 'changeRunsPerWinPage',
        labels: {
            first: '⏮',
            previous: '◀',
            next: '▶',
            last: '⏭'
        },
        useDisabledVariant: false
    });
}

function changeRunsPerWinPage(newPage) {
    const totalPages = Math.ceil(runsPerWinPagination.totalRows / runsPerWinPagination.rowsPerPage);
    setPaginationPage(runsPerWinPagination, newPage, totalPages, () => {
        renderRunsPerWinPage();
        renderRunsPerWinPagination();
    });
}

// Park Factors table pagination
const parkFactorsPagination = {
    currentPage: 1,
    rowsPerPage: 12,
    totalRows: 0,
    sortColumn: 'pf_r',
    sortDirection: 'desc'
};

function renderParkFactorsTable(data) {
    // Store current data globally for download
    currentTableData.park_factors = data;
    
    // Update pagination info
    parkFactorsPagination.totalRows = data.rows.length;
    parkFactorsPagination.currentPage = 1;
    
    // Update title with season and average info
    const titleEl = document.getElementById('park-factors-table-title');
    const subtitleEl = document.getElementById('park-factors-table-title-subtitle');
    
    const averageLabel = data.average === 1 ? '1 Season' : `${data.average} Seasons`;
    titleEl.innerHTML = `Park Factors - <span class="primary-badge">${data.season} Season</span> <span class="primary-badge">${averageLabel}</span>`;
    subtitleEl.innerHTML = 'Park factor values measure the relative effect of a ballpark on run scoring or event rates compared to a neutral league environment.<br>A value of 1.00 represents a neutral park, values above 1.00 indicate a hitter-friendly park, and values below 1.00 indicate a pitcher-friendly park.';
    
    // Generate table headers with sorting
    renderParkFactorsHeaders();
    
    // Render first page
    renderParkFactorsPage();
    renderParkFactorsPagination();
}

function renderParkFactorsHeaders() {
    const getSortIcon = (column) => {
        if (parkFactorsPagination.sortColumn !== column) {
            return '<span style="opacity: 0.3;">⇅</span>';
        }
        return parkFactorsPagination.sortDirection === 'asc' ? '▲' : '▼';
    };
    
    const thead = document.getElementById('park-factors-table-head');
    thead.innerHTML = `
        <tr>
            <th class="bg-primary text-center" style="width: 50px;">#</th>
            <th class="bg-primary text-start" style="cursor: pointer; user-select: none;" data-accion="sortParkFactors" data-clave="park_name">
                Park ${getSortIcon('park_name')}
            </th>
            <th class="bg-primary text-start" style="cursor: pointer; user-select: none;" data-accion="sortParkFactors" data-clave="team_name">
                Team ${getSortIcon('team_name')}
            </th>
            <th class="bg-primary text-center" style="cursor: pointer; user-select: none;" data-accion="sortParkFactors" data-clave="pf_r">
                Runs ${getSortIcon('pf_r')}
            </th>
            <th class="bg-primary text-center" style="cursor: pointer; user-select: none;" data-accion="sortParkFactors" data-clave="pf_1b">
                1B ${getSortIcon('pf_1b')}
            </th>
            <th class="bg-primary text-center" style="cursor: pointer; user-select: none;" data-accion="sortParkFactors" data-clave="pf_2b">
                2B ${getSortIcon('pf_2b')}
            </th>
            <th class="bg-primary text-center" style="cursor: pointer; user-select: none;" data-accion="sortParkFactors" data-clave="pf_3b">
                3B ${getSortIcon('pf_3b')}
            </th>
            <th class="bg-primary text-center" style="cursor: pointer; user-select: none;" data-accion="sortParkFactors" data-clave="pf_hr">
                HR ${getSortIcon('pf_hr')}
            </th>
            <th class="bg-primary text-center" style="cursor: pointer; user-select: none;" data-accion="sortParkFactors" data-clave="pf_bb">
                BB ${getSortIcon('pf_bb')}
            </th>
            <th class="bg-primary text-center" style="cursor: pointer; user-select: none;" data-accion="sortParkFactors" data-clave="pf_so">
                SO ${getSortIcon('pf_so')}
            </th>
        </tr>
    `;
}

function renderParkFactorsPage() {
    const tbody = document.getElementById('park-factors-table-body');
    const data = currentTableData.park_factors;
    
    // Sort the data
    const sortedRows = [...data.rows].sort((a, b) => {
        const column = parkFactorsPagination.sortColumn;
        let aVal = a[column];
        let bVal = b[column];
        
        // Handle null values
        if (aVal === null && bVal === null) return 0;
        if (aVal === null) return 1;
        if (bVal === null) return -1;
        
        // String comparison for text columns
        if (column === 'park_name' || column === 'team_name') {
            aVal = aVal.toString().toLowerCase();
            bVal = bVal.toString().toLowerCase();
        }
        
        let result;
        if (typeof aVal === 'string') {
            result = aVal.localeCompare(bVal);
        } else {
            result = aVal - bVal;
        }
        
        return parkFactorsPagination.sortDirection === 'asc' ? result : -result;
    });
    
    const startIdx = (parkFactorsPagination.currentPage - 1) * parkFactorsPagination.rowsPerPage;
    const endIdx = Math.min(startIdx + parkFactorsPagination.rowsPerPage, sortedRows.length);
    const pageRows = sortedRows.slice(startIdx, endIdx);
    
    // Check if heatmap is enabled
    const heatmapEnabled = globalHeatmapEnabled;
    
    // Calculate min/max for each column for heatmap
    let minMax = {};
    if (heatmapEnabled) {
        const columns = ['pf_r', 'pf_1b', 'pf_2b', 'pf_3b', 'pf_hr', 'pf_bb', 'pf_so'];
        columns.forEach(col => {
            const values = data.rows.map(r => r[col]).filter(v => v !== null);
            if (values.length > 0) {
                minMax[col] = {
                    min: Math.min(...values),
                    max: Math.max(...values)
                };
            }
        });
    }
    
    tbody.innerHTML = pageRows.map((row, idx) => {
        // Calculate the index based on current page and position
        const rowIndex = startIdx + idx + 1;
        
        // Helper function to render cell with optional heatmap
        const renderCell = (value, columnKey) => {
            if (value === null) return '-';
            const displayValue = value.toFixed(3);
            if (heatmapEnabled && minMax[columnKey]) {
                const { bgColor, textColor } = getHeatmapColor(value, minMax[columnKey].min, minMax[columnKey].max);
                return `<span style="background-color: ${bgColor}; color: ${textColor}; padding: 2px 6px; border-radius: 3px; display: inline-block; min-width: 50px;">${displayValue}</span>`;
            }
            return displayValue;
        };
        
        return `
            <tr>
                <td class="text-center text-muted">${rowIndex}</td>
                <td class="text-start">${row.park_name}</td>
                <td class="text-start">${row.team_name}</td>
                <td class="text-center">${renderCell(row.pf_r, 'pf_r')}</td>
                <td class="text-center">${renderCell(row.pf_1b, 'pf_1b')}</td>
                <td class="text-center">${renderCell(row.pf_2b, 'pf_2b')}</td>
                <td class="text-center">${renderCell(row.pf_3b, 'pf_3b')}</td>
                <td class="text-center">${renderCell(row.pf_hr, 'pf_hr')}</td>
                <td class="text-center">${renderCell(row.pf_bb, 'pf_bb')}</td>
                <td class="text-center">${renderCell(row.pf_so, 'pf_so')}</td>
            </tr>
        `;
    }).join('');
}

function renderParkFactorsPagination() {
    const totalPages = Math.ceil(parkFactorsPagination.totalRows / parkFactorsPagination.rowsPerPage);
    renderPaginationControls({
        containerId: 'park-factors-pagination',
        currentPage: parkFactorsPagination.currentPage,
        totalPages,
        changeHandlerName: 'changeParkFactorsPage',
        labels: {
            first: '⏮',
            previous: '◀',
            next: '▶',
            last: '⏭'
        },
        useDisabledVariant: false
    });
}

function changeParkFactorsPage(newPage) {
    const totalPages = Math.ceil(parkFactorsPagination.totalRows / parkFactorsPagination.rowsPerPage);
    setPaginationPage(parkFactorsPagination, newPage, totalPages, () => {
        renderParkFactorsPage();
        renderParkFactorsPagination();
    });
}

function sortParkFactors(column) {
    // Toggle sort direction if clicking the same column
    if (parkFactorsPagination.sortColumn === column) {
        parkFactorsPagination.sortDirection = parkFactorsPagination.sortDirection === 'asc' ? 'desc' : 'asc';
    } else {
        // New column, default to descending for numeric columns, ascending for text
        parkFactorsPagination.sortColumn = column;
        parkFactorsPagination.sortDirection = (column === 'park_name' || column === 'team_name') ? 'asc' : 'desc';
    }
    
    // Reset to first page when sorting
    parkFactorsPagination.currentPage = 1;
    
    // Re-render headers and table
    renderParkFactorsHeaders();
    renderParkFactorsPage();
    renderParkFactorsPagination();
}

// Linear Weights table rendering
function renderLinearWeightsTable(data, resetPage = true) {
    // Store data for download
    currentTableData.linear_weights = data;
    
    const titleEl = document.getElementById('linear-weights-table-title');
    const subtitleEl = document.getElementById('linear-weights-table-title-subtitle');
    const eventCountEl = document.getElementById('linear-weights-event-count');
    const bodyEl = document.getElementById('linear-weights-table-body');
    const headEl = document.getElementById('linear-weights-table-head');
    
    if (!titleEl || !bodyEl || !headEl) {
        console.error('Linear Weights table elements not found');
        return;
    }
    
    // Store pagination data
    linearWeightsPagination.data = data;
    linearWeightsPagination.totalRows = data.rows.length;
    if (resetPage) {
        linearWeightsPagination.currentPage = 1;
    }
    
    // Update title with outs badge
    const seasonLabel = data.from_season === data.to_season 
        ? `${data.from_season} Season` 
        : `${data.from_season}-${data.to_season} Seasons`;
    
    // Add outs badge to title - default to 'All Outs' if not specified
    let outsBadge = '';
    const outsValue = data.outs !== undefined && data.outs !== null ? data.outs : -1;
    if (outsValue === -1) {
        outsBadge = '<span class="primary-badge">All Outs</span>';
    } else if (outsValue === 0) {
        outsBadge = '<span class="primary-badge">0 Outs</span>';
    } else if (outsValue === 1) {
        outsBadge = '<span class="primary-badge">1 Out</span>';
    } else if (outsValue === 2) {
        outsBadge = '<span class="primary-badge">2 Outs</span>';
    }
    
    titleEl.innerHTML = `Linear Weights - <span class="primary-badge">${seasonLabel}</span> ${outsBadge}`;
    if (subtitleEl) {
        subtitleEl.textContent = 'The average change in run expectancy caused by an event, measured relative to the game state before the play.';
    }
    if (eventCountEl && data.total_events !== null && data.total_events !== undefined) {
        setBreakevenFooterText(eventCountEl, `${data.total_events.toLocaleString()} events processed`);
    } else {
        setReliabilityLegendOnly(eventCountEl);
    }
    
    // Calculate pagination
    const totalPages = Math.ceil(linearWeightsPagination.totalRows / linearWeightsPagination.rowsPerPage);
    const startIdx = (linearWeightsPagination.currentPage - 1) * linearWeightsPagination.rowsPerPage;
    const endIdx = startIdx + linearWeightsPagination.rowsPerPage;
    const paginatedRows = data.rows.slice(startIdx, endIdx);
    
    // Check if heatmap is enabled
    const heatmapEnabled = globalHeatmapEnabled;
    
    // Calculate min/max for heatmap if enabled (use all rows, not just paginated)
    let minValue = Infinity;
    let maxValue = -Infinity;
    if (heatmapEnabled) {
        data.rows.forEach(row => {
            ['AVG', '000', '100', '010', '001', '110', '101', '011', '111'].forEach(runner => {
                const value = row[runner];
                if (value !== null && value !== undefined) {
                    const numValue = Number(value);
                    if (Number.isFinite(numValue)) {
                        minValue = Math.min(minValue, numValue);
                        maxValue = Math.max(maxValue, numValue);
                    }
                }
            });
        });
    }

    const linearWeightNKeys = ['AVG_n', '000_n', '100_n', '010_n', '001_n', '110_n', '101_n', '011_n', '111_n'];
    const reliabilityStats = buildReliabilityStats(data.rows, (row) => linearWeightNKeys.map((key) => row[key]), RELIABILITY_DEFAULT_CONFIG);
    
    // Build table headers - remove Outs column
    headEl.innerHTML = `
        <tr>
            <th class="bg-primary text-start sticky-header">Event</th>
            ${data.headers.slice(2).map(header => `<th class="bg-primary text-center">${header}</th>`).join('')}
        </tr>
    `;
    
    // Group rows by event for rowspan (use paginated rows)
    const eventGroups = [];
    let currentEvent = null;
    let currentGroup = [];
    
    paginatedRows.forEach((row, index) => {
        if (row.event !== currentEvent) {
            if (currentGroup.length > 0) {
                eventGroups.push({ event: currentEvent, rows: currentGroup });
            }
            currentEvent = row.event;
            currentGroup = [row];
        } else {
            currentGroup.push(row);
        }
    });
    
    // Push last group
    if (currentGroup.length > 0) {
        eventGroups.push({ event: currentEvent, rows: currentGroup });
    }
    
    // Build table body with rowspan - remove Outs column
    let bodyHTML = '';
    eventGroups.forEach(group => {
        group.rows.forEach((row, rowIndex) => {
            const cells = [];
            
            // Add event cell only on first row of group with rowspan
            if (rowIndex === 0) {
                cells.push(`<td class="text-start fw-medium sticky-column" rowspan="${group.rows.length}">${row.event || ''}</td>`);
            }
            
            // Remove the outs cell - it's no longer displayed in the table
            
            // Add AVG column first if it exists in headers
            if (data.headers.includes('Average')) {
                const avgValue = row['AVG'];
                const avgN = row['AVG_n'];
                const formatted = avgValue !== null && avgValue !== undefined ? Number(avgValue).toFixed(3) : '—';
                
                if (heatmapEnabled && avgValue !== null && avgValue !== undefined && !isNaN(avgValue)) {
                    const badgeHTML = renderHeatmapBadge(formatted, avgValue, minValue, maxValue, { minWidth: '50px' });
                    cells.push(`<td class="text-center">${appendReliabilityMarkerInsideBadge(badgeHTML, avgN, reliabilityStats)}</td>`);
                } else {
                    const cellContent = (avgValue !== null && avgValue !== undefined && !isNaN(avgValue))
                        ? appendReliabilityMarker(formatted, avgN, reliabilityStats)
                        : formatted;
                    cells.push(`<td class="text-center">${cellContent}</td>`);
                }
            }
            
            // Add runner state columns
            ['000', '100', '010', '001', '110', '101', '011', '111'].forEach(runner => {
                const value = row[runner];
                const nValue = row[`${runner}_n`];
                const formatted = value !== null && value !== undefined ? Number(value).toFixed(3) : '—';
                
                if (heatmapEnabled && value !== null && value !== undefined && !isNaN(value)) {
                    const badgeHTML = renderHeatmapBadge(formatted, value, minValue, maxValue, { minWidth: '50px' });
                    cells.push(`<td class="text-center">${appendReliabilityMarkerInsideBadge(badgeHTML, nValue, reliabilityStats)}</td>`);
                } else {
                    const cellContent = (value !== null && value !== undefined && !isNaN(value))
                        ? appendReliabilityMarker(formatted, nValue, reliabilityStats)
                        : formatted;
                    cells.push(`<td class="text-center">${cellContent}</td>`);
                }
            });
            
            bodyHTML += `<tr>${cells.join('')}</tr>`;
        });
    });
    
    bodyEl.innerHTML = bodyHTML;
    
    // Render pagination controls
    renderLinearWeightsPagination(totalPages);
}

function renderLinearWeightsPagination(totalPages) {
    renderPaginationControls({
        containerId: 'linear-weights-pagination',
        currentPage: linearWeightsPagination.currentPage,
        totalPages,
        changeHandlerName: 'changeLinearWeightsPage',
        labels: {
            first: '⏮',
            previous: '◀',
            next: '▶',
            last: '⏭'
        },
        hideWhenSinglePage: false
    });
}

function changeLinearWeightsPage(page) {
    const totalPages = Math.max(1, Math.ceil(linearWeightsPagination.totalRows / linearWeightsPagination.rowsPerPage));
    setPaginationPage(linearWeightsPagination, page, totalPages, () => {
        renderLinearWeightsTable(linearWeightsPagination.data, false);
    });
}

// Expose to window for onclick handlers
window.changeLinearWeightsPage = changeLinearWeightsPage;

// R24 Frequencies table rendering
function renderR24FrequenciesTable(data) {
    // Store data for download
    currentTableData.r24_frequencies = data;
    
    const titleEl = document.getElementById('r24-frequencies-table-title');
    const subtitleEl = document.getElementById('r24-frequencies-table-title-subtitle');
    const bodyEl = document.getElementById('r24-frequencies-table-body');
    const headEl = document.getElementById('r24-frequencies-table-head');
    
    if (!titleEl || !bodyEl || !headEl) {
        console.error('R24 Frequencies table elements not found');
        return;
    }
    
    // Update title
    const seasonLabel = data.from_season === data.to_season 
        ? `${data.from_season} Season` 
        : `${data.from_season}-${data.to_season} Seasons`;
    titleEl.innerHTML = `R24 Frequencies - <span class="primary-badge">${seasonLabel}</span>`;
    if (subtitleEl) {
        subtitleEl.textContent = 'How frequently does each base–out state occur on average across all games?';
    }
    
    // Update event count footer
    const eventCountDiv = document.getElementById('r24-frequencies-event-count');
    if (eventCountDiv && data.total_events !== null && data.total_events !== undefined) {
        eventCountDiv.textContent = `${data.total_events.toLocaleString()} events processed`;
    }
    
    // Check if heatmap is enabled
    const heatmapEnabled = globalHeatmapEnabled;
    
    // Calculate min/max for heatmap if enabled (convert to percentage)
    let minValue = Infinity;
    let maxValue = -Infinity;
    if (heatmapEnabled) {
        data.rows.forEach(row => {
            ['0', '1', '2'].forEach(outKey => {
                const value = row[outKey];
                if (value !== null && value !== undefined && !isNaN(value)) {
                    // Convert to percentage for heatmap
                    const percentValue = value * 100;
                    minValue = Math.min(minValue, percentValue);
                    maxValue = Math.max(maxValue, percentValue);
                }
            });
        });
    }
    
    // Build table headers
    headEl.innerHTML = `
        <tr>
            <th class="bg-primary text-center align-middle sticky-col fs-5" colspan="2">Runners</th>
            <th class="bg-primary text-center fs-5">0 Outs</th>
            <th class="bg-primary text-center fs-5">1 Out</th>
            <th class="bg-primary text-center fs-5">2 Outs</th>
        </tr>
    `;
    
    // Build table body
    const rows = data.rows.map(row => {
        const svgString = getBaseStateSVGMarkup(row.runners);
        
        const cells = [];
        cells.push(`<td class="sticky-col text-end align-middle svg-cell">${svgString}</td>`);
        cells.push(`<td class="sticky-col text-start align-middle fs-6 label-cell label-cell-sticky">${row.label}</td>`);
        
        // Add out state columns (0, 1, 2)
        ['0', '1', '2'].forEach(outKey => {
            const value = row[outKey];
            // Convert from decimal (0.05) to percentage (5.00%)
            const formatted = value !== null && value !== undefined ? (Number(value) * 100).toFixed(2) + '%' : '—';
            
            if (heatmapEnabled && value !== null && value !== undefined && !isNaN(value)) {
                // Multiply by 100 for heatmap calculation too
                const percentValue = Number(value) * 100;
                const badgeHTML = renderHeatmapBadge(formatted, percentValue, minValue, maxValue, {
                    minWidth: '60px',
                    fontSize: '1rem'
                });
                cells.push(`<td class="text-center align-middle table-cell-compact" style="font-size: 1rem;">${badgeHTML}</td>`);
            } else {
                cells.push(`<td class="text-center align-middle table-cell-compact" style="font-size: 1rem;">${formatted}</td>`);
            }
        });
        
        return `<tr>${cells.join('')}</tr>`;
    }).join('');
    
    bodyEl.innerHTML = rows;
}

/**
 * Format inning number with ordinal suffix or "Extra Innings"
 * @param {number} inning - The inning number
 * @returns {string} Formatted inning string
 */
function formatInning(inning, team = null) {
    if (inning === null || inning === undefined) return '—';
    if (inning >= 11) {
        const inningText = 'Extra Innings';
        if (team !== null && team !== undefined) {
            const half = team === 0 ? 'Top' : 'Bottom';
            return `${half}<br>${inningText}`;
        }
        return inningText;
    }
    
    const ordinals = ['', '1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th', '9th', '10th'];
    const inningText = ordinals[inning] || `${inning}th`;
    
    if (team !== null && team !== undefined) {
        const half = team === 0 ? 'Top' : 'Bottom';
        return `${half}<br>${inningText}`;
    }
    
    return inningText;
}

/**
 * Render Win Expectancy table
 * @param {Object} data - The response data from API containing win expectancy data
 * @param {boolean} resetPage - Whether to reset to page 1 (default true)
 */
function renderWinExpectancyTable(data, resetPage = true) {
    // Store the data for export and pagination
    currentTableData.win_expectancy = data;
    winExpectancyPagination.data = data;
    winExpectancyPagination.totalRows = data.rows.length;
    if (resetPage) {
        winExpectancyPagination.currentPage = 1;
    }
    
    const titleEl = document.getElementById('win-expectancy-table-title');
    const subtitleEl = document.getElementById('win-expectancy-table-title-subtitle');
    const eventCountEl = document.getElementById('win-expectancy-event-count');
    const bodyEl = document.getElementById('win-expectancy-table-body');
    const headEl = document.getElementById('win-expectancy-table-head');
    
    if (!titleEl || !bodyEl || !headEl) {
        console.error('Win Expectancy table elements not found');
        return;
    }
    
    // Update title
    const seasonLabel = data.from_season === data.to_season 
        ? `${data.from_season} Season` 
        : `${data.from_season}-${data.to_season} Seasons`;
    const teamName = data.team_name || (data.team === 0 ? 'Away' : 'Home');
    titleEl.innerHTML = `Win Expectancy - <span class="primary-badge">${seasonLabel}</span> - <span class="primary-badge">${teamName}</span>`;
    if (subtitleEl) {
        // El signo de las columnas se explica en la propia cabecera
        // ("<equipo> team's lead"), no aqui. Ver buildRunDiffHeaderHTML.
        subtitleEl.innerHTML = `Every cell is the <strong style="text-decoration: underline;">HOME team's</strong> chance of winning the game from this state.`;
    }
    if (eventCountEl && data.total_events !== null && data.total_events !== undefined) {
        setBreakevenFooterText(eventCountEl, `${data.total_events.toLocaleString()} events processed`);
    } else {
        setReliabilityLegendOnly(eventCountEl);
    }
    
    // Check if heatmap is enabled
    const heatmapEnabled = globalHeatmapEnabled;
    
    // Calculate pagination
    const totalPages = Math.ceil(winExpectancyPagination.totalRows / winExpectancyPagination.rowsPerPage);
    const startIdx = (winExpectancyPagination.currentPage - 1) * winExpectancyPagination.rowsPerPage;
    const endIdx = startIdx + winExpectancyPagination.rowsPerPage;
    const paginatedRows = data.rows.slice(startIdx, endIdx);
    
    // Calculate min/max for heatmap if enabled
    let minValue = Infinity;
    let maxValue = -Infinity;
    if (heatmapEnabled) {
        ({ minValue, maxValue } = getHeatmapRangeFromHeaders(data.rows, data.headers, 3));
    }
    const winExpectancyNHeaders = data.headers.slice(3);
    const reliabilityStats = buildReliabilityStats(data.rows, (row) => winExpectancyNHeaders.map((header) => row[`${header}_n`]), RELIABILITY_DEFAULT_CONFIG);

    // Cabecera en dos filas: el grupo nombra de quien es la ventaja que miden las
    // columnas de diferencial. Ver buildRunDiffHeaderHTML.
    headEl.innerHTML = buildRunDiffHeaderHTML(data.headers, teamName);
    
    // Calculate rowspan for inning column (group consecutive rows with same inn_ct)
    const inningGroups = [];
    let currentInning = null;
    let currentGroup = null;
    
    paginatedRows.forEach((row, index) => {
        if (row.inn_ct !== currentInning) {
            // New inning group
            currentInning = row.inn_ct;
            currentGroup = { startIndex: index, count: 1 };
            inningGroups.push(currentGroup);
        } else {
            // Same inning, increment count
            currentGroup.count++;
        }
    });
    
    // Calculate rowspan for outs column (group consecutive rows with same outs_ct)
    const outsGroups = [];
    let currentOuts = null;
    let currentOutsGroup = null;
    
    paginatedRows.forEach((row, index) => {
        if (row.outs_ct !== currentOuts) {
            // New outs group
            currentOuts = row.outs_ct;
            currentOutsGroup = { startIndex: index, count: 1 };
            outsGroups.push(currentOutsGroup);
        } else {
            // Same outs, increment count
            currentOutsGroup.count++;
        }
    });
    
    // Build table body
    const rows = paginatedRows.map((row, rowIndex) => {
        const cells = [];
        
        // Check if this row should render the inning cell
        const inningGroup = inningGroups.find(g => g.startIndex === rowIndex);
        if (inningGroup) {
            // First row of this inning group - render with rowspan
            const inningText = formatInning(row.inn_ct, data.team);
            const rowspanAttr = inningGroup.count > 1 ? ` rowspan="${inningGroup.count}"` : '';
            cells.push(`<td class="text-center align-middle fs-5"${rowspanAttr}>${inningText}</td>`);
        }
        // Otherwise skip the inning cell (it's merged with previous row)
        
        // Check if this row should render the outs cell
        const outsGroup = outsGroups.find(g => g.startIndex === rowIndex);
        if (outsGroup) {
            // First row of this outs group - render with rowspan
            const rowspanAttr = outsGroup.count > 1 ? ` rowspan="${outsGroup.count}"` : '';
            const outsBgColor = row.outs_ct === 1 ? '#f9d8b8' : '#b8d8f9';
            cells.push(`<td class="text-center align-middle fs-5 fw-semibold" style="background-color: ${outsBgColor}; color: black;"${rowspanAttr}>${row.outs_ct !== null && row.outs_ct !== undefined ? row.outs_ct : '—'}</td>`);
        }
        // Otherwise skip the outs cell (it's merged with previous row)
        
        // Create SVG baseball diamond for runners
        // Convert formatted runners text back to raw format for SVG
        const runnersMap = {
            '---': '000',
            '1--': '100',
            '-2-': '010',
            '--3': '001',
            '12-': '110',
            '1-3': '101',
            '-23': '011',
            '123': '111'
        };
        const rawRunners = runnersMap[row.runners] || '000';
        const svgString = getBaseStateSVGMarkup(rawRunners);
        cells.push(`<td class="text-center align-middle svg-cell">${svgString}</td>`);
        
        // Add dynamic run differential columns
        // Skip the first 3 headers (Inning, Outs, Runners) and process the rest
        for (let i = 3; i < data.headers.length; i++) {
            const header = data.headers[i];
            const value = row[header];
            const nValue = row[`${header}_n`];

            if (value !== null && value !== undefined && !isNaN(value)) {
                const cellHtml = renderMetricCellWithReliability(value, nValue, {
                    formatValue: (v) => `${Number(v).toFixed(2)}%`,
                    heatmapEnabled,
                    hMin: minValue,
                    hMax: maxValue,
                    reliabilityStats,
                    reliabilityConfig: RELIABILITY_DEFAULT_CONFIG
                });
                cells.push(`<td class="text-center align-middle">${cellHtml}</td>`);
            } else {
                cells.push('<td class="text-center align-middle">—</td>');
            }
        }
        
        // Add thicker border every 8th row (visual separator for inning changes)
        const borderClass = (rowIndex + 1) % 8 === 0 ? ' style="border-bottom: 2px solid #495057;"' : '';
        
        return `<tr${borderClass}>${cells.join('')}</tr>`;
    }).join('');
    
    bodyEl.innerHTML = rows;
    
    // Render pagination controls
    renderWinExpectancyPagination(totalPages);
}

function renderWinExpectancyPagination(totalPages) {
    renderPaginationControls({
        containerId: 'win-expectancy-pagination',
        currentPage: winExpectancyPagination.currentPage,
        totalPages,
        changeHandlerName: 'changeWinExpectancyPage',
        labels: {
            first: '⏮',
            previous: '◀',
            next: '▶',
            last: '⏭'
        }
    });
}

function changeWinExpectancyPage(newPage) {
    const totalPages = Math.ceil(winExpectancyPagination.totalRows / winExpectancyPagination.rowsPerPage);
    setPaginationPage(winExpectancyPagination, newPage, totalPages, () => {
        renderWinExpectancyTable(winExpectancyPagination.data, false); // Don't reset page
    });
}

// Expose function to global scope for onclick handlers
window.changeWinExpectancyPage = changeWinExpectancyPage;

// ==================== WIN PROBABILITY ADDED RENDERING ====================

function renderWinProbabilityAddedTable(data, resetPage = true) {
    // Store the data for export and pagination
    currentTableData.win_probability_added = data;
    winProbabilityAddedPagination.data = data;
    winProbabilityAddedPagination.totalRows = data.rows.length;
    if (resetPage) {
        winProbabilityAddedPagination.currentPage = 1;
    }
    
    const titleEl = document.getElementById('win-probability-added-table-title');
    const subtitleEl = document.getElementById('win-probability-added-table-title-subtitle');
    const eventCountEl = document.getElementById('win-probability-added-event-count');
    const bodyEl = document.getElementById('win-probability-added-table-body');
    const headEl = document.getElementById('win-probability-added-table-head');
    
    if (!titleEl || !bodyEl || !headEl) {
        console.error('Win Probability Added table elements not found');
        return;
    }
    
    // Update title
    const seasonLabel = data.from_season === data.to_season 
        ? `${data.from_season} Season` 
        : `${data.from_season}-${data.to_season} Seasons`;
    const teamName = data.team_name || (data.team === 0 ? 'Away' : 'Home');
    titleEl.innerHTML = `Win Probability Added - <span class="primary-badge">${seasonLabel}</span> - <span class="primary-badge">${teamName}</span>`;
    if (subtitleEl) {
        subtitleEl.innerHTML = `How much does each event change the <strong style="text-decoration: underline;">HOME team's</strong> win probability on average from this game state?`;
    }
    if (eventCountEl && data.total_events !== null && data.total_events !== undefined) {
        setBreakevenFooterText(eventCountEl, `${data.total_events.toLocaleString()} events processed`);
    } else {
        setReliabilityLegendOnly(eventCountEl);
    }
    
    // Check if heatmap is enabled
    const heatmapEnabled = globalHeatmapEnabled;
    
    // Calculate pagination
    const totalPages = Math.ceil(winProbabilityAddedPagination.totalRows / winProbabilityAddedPagination.rowsPerPage);
    const startIdx = (winProbabilityAddedPagination.currentPage - 1) * winProbabilityAddedPagination.rowsPerPage;
    const endIdx = startIdx + winProbabilityAddedPagination.rowsPerPage;
    const paginatedRows = data.rows.slice(startIdx, endIdx);
    
    // Calculate min/max for heatmap if enabled
    let minValue = Infinity;
    let maxValue = -Infinity;
    if (heatmapEnabled) {
        ({ minValue, maxValue } = getHeatmapRangeFromHeaders(data.rows, data.headers, 3));
    }
    const winProbabilityAddedNHeaders = data.headers.slice(3);
    const reliabilityStats = buildReliabilityStats(data.rows, (row) => winProbabilityAddedNHeaders.map((header) => row[`${header}_n`]), RELIABILITY_DEFAULT_CONFIG);

    headEl.innerHTML = buildRunDiffHeaderHTML(data.headers, teamName);
    
    // Calculate rowspan for inning column (group consecutive rows with same inn_ct)
    const inningGroups = [];
    let currentInning = null;
    let currentGroup = null;
    
    paginatedRows.forEach((row, index) => {
        if (row.inn_ct !== currentInning) {
            // New inning group
            currentInning = row.inn_ct;
            currentGroup = { startIndex: index, count: 1 };
            inningGroups.push(currentGroup);
        } else {
            // Same inning, increment count
            currentGroup.count++;
        }
    });
    
    // Calculate rowspan for outs column (group consecutive rows with same outs_ct)
    const outsGroups = [];
    let currentOuts = null;
    let currentOutsGroup = null;
    
    paginatedRows.forEach((row, index) => {
        if (row.outs_ct !== currentOuts) {
            // New outs group
            currentOuts = row.outs_ct;
            currentOutsGroup = { startIndex: index, count: 1 };
            outsGroups.push(currentOutsGroup);
        } else {
            // Same outs, increment count
            currentOutsGroup.count++;
        }
    });
    
    // Reverse mapping from formatted runners text to raw format for SVG
    const runnersMap = {
        '---': '000',
        '1--': '100',
        '-2-': '010',
        '12-': '110',
        '--3': '001',
        '1-3': '101',
        '-23': '011',
        '123': '111'
    };
    
    // Build table body
    const rows = paginatedRows.map((row, rowIndex) => {
        const cells = [];
        
        // Check if this row should render the inning cell
        const inningGroup = inningGroups.find(g => g.startIndex === rowIndex);
        if (inningGroup) {
            // First row of this inning group - render with rowspan
            const inningText = formatInning(row.inn_ct, data.team);
            const rowspanAttr = inningGroup.count > 1 ? ` rowspan="${inningGroup.count}"` : '';
            cells.push(`<td class="text-center align-middle fs-5"${rowspanAttr}>${inningText}</td>`);
        }
        // Otherwise skip the inning cell (it's merged with previous row)
        
        // Check if this row should render the outs cell
        const outsGroup = outsGroups.find(g => g.startIndex === rowIndex);
        if (outsGroup) {
            // First row of this outs group - render with rowspan
            const rowspanAttr = outsGroup.count > 1 ? ` rowspan="${outsGroup.count}"` : '';
            const outsBgColor = row.outs_ct === 1 ? '#f9d8b8' : '#b8d8f9';
            cells.push(`<td class="text-center align-middle fs-5 fw-semibold" style="background-color: ${outsBgColor}; color: black;"${rowspanAttr}>${row.outs_ct !== null && row.outs_ct !== undefined ? row.outs_ct : '—'}</td>`);
        }
        // Otherwise skip the outs cell (it's merged with previous row)
        
        // Convert runners from formatted text back to raw format for SVG
        const rawRunners = runnersMap[row.runners] || '000';
        const svgString = getBaseStateSVGMarkup(rawRunners);
        cells.push(`<td class="text-center align-middle svg-cell">${svgString}</td>`);
        
        // Add dynamic run differential columns
        // Skip the first 3 headers (Inning, Outs, Runners) and process the rest
        for (let i = 3; i < data.headers.length; i++) {
            const header = data.headers[i];
            const value = row[header];
            const nValue = row[`${header}_n`];

            if (value !== null && value !== undefined && !isNaN(value)) {
                const cellHtml = renderMetricCellWithReliability(value, nValue, {
                    formatValue: (v) => `${Number(v).toFixed(2)}%`,
                    heatmapEnabled,
                    hMin: minValue,
                    hMax: maxValue,
                    reliabilityStats,
                    reliabilityConfig: RELIABILITY_DEFAULT_CONFIG
                });
                cells.push(`<td class="text-center align-middle">${cellHtml}</td>`);
            } else {
                cells.push('<td class="text-center align-middle">—</td>');
            }
        }
        
        // Add thicker border every 8th row (visual separator for inning changes)
        const borderClass = (rowIndex + 1) % 8 === 0 ? ' style="border-bottom: 2px solid #495057;"' : '';
        
        return `<tr${borderClass}>${cells.join('')}</tr>`;
    }).join('');
    
    bodyEl.innerHTML = rows;
    
    // Render pagination controls
    renderWinProbabilityAddedPagination(totalPages);
}

function renderWinProbabilityAddedPagination(totalPages) {
    renderPaginationControls({
        containerId: 'win-probability-added-pagination',
        currentPage: winProbabilityAddedPagination.currentPage,
        totalPages,
        changeHandlerName: 'changeWinProbabilityAddedPage',
        labels: {
            first: '⏮',
            previous: '◀',
            next: '▶',
            last: '⏭'
        }
    });
}

function changeWinProbabilityAddedPage(newPage) {
    const totalPages = Math.ceil(winProbabilityAddedPagination.totalRows / winProbabilityAddedPagination.rowsPerPage);
    setPaginationPage(winProbabilityAddedPagination, newPage, totalPages, () => {
        renderWinProbabilityAddedTable(winProbabilityAddedPagination.data, false); // Don't reset page
    });
}

// Expose function to global scope for onclick handlers
window.changeWinProbabilityAddedPage = changeWinProbabilityAddedPage;

// ==================== LEVERAGE INDEX RENDERING ====================

function renderLeverageIndexTable(data, resetPage = true) {
    // Store the data for export and pagination
    currentTableData.leverage_index = data;
    leverageIndexPagination.data = data;
    leverageIndexPagination.totalRows = data.rows.length;
    if (resetPage) {
        leverageIndexPagination.currentPage = 1;
    }
    
    const titleEl = document.getElementById('leverage-index-table-title');
    const subtitleEl = document.getElementById('leverage-index-table-title-subtitle');
    const eventCountEl = document.getElementById('leverage-index-event-count');
    const bodyEl = document.getElementById('leverage-index-table-body');
    const headEl = document.getElementById('leverage-index-table-head');
    
    if (!titleEl || !bodyEl || !headEl) {
        console.error('Leverage Index table elements not found');
        return;
    }
    
    // Update title
    const seasonLabel = data.from_season === data.to_season 
        ? `${data.from_season} Season` 
        : `${data.from_season}-${data.to_season} Seasons`;
    const teamName = data.team_name || (data.team === 0 ? 'Away' : 'Home');
    titleEl.innerHTML = `Leverage Index - <span class="primary-badge">${seasonLabel}</span> - <span class="primary-badge">${teamName}</span>`;
    if (subtitleEl) {
        // "Average leverage is 1.0" era cierto del JUEGO y falso de la TABLA. La
        // normalizacion se hace sobre el turno promedio, ponderando por cuantas
        // veces ocurre cada estado: asi sale 1.0164 en 2025. Pero la media de las
        // celdas que se ven en pantalla es 1.5664, la mediana 1.2489 y el 58.9%
        // estan por encima de 1.0 — porque los estados frecuentes son los
        // aburridos. Quien miraba la tabla veia mayoria de valores >1 y la frase
        // le parecia falsa.
        //
        // Ahora se calcula sobre la seleccion real. El corte por equipo no es
        // neutro y el numero lo enseña: el local batea ultimo, asi que sus turnos
        // deciden mas (2025: away 0.9953, home 1.0384; el home sale mas alto
        // todas las temporadas).
        const avg = computeWeightedLeverage(data);
        const avgText = avg === null ? '' :
            ` — weighted by how often each state actually happens, <strong>${teamName}</strong> `
            + `batters average <strong>${avg.toFixed(3)}</strong> across these seasons`;
        subtitleEl.innerHTML = `How much can win expectancy change based on the next play's outcome? `
            + `Higher values indicate more critical situations.`
            + `<br>An average plate appearance rates 1.0${avgText}.`;
    }
    if (eventCountEl && data.total_events !== null && data.total_events !== undefined) {
        setBreakevenFooterText(eventCountEl, `${data.total_events.toLocaleString()} events processed`);
    } else {
        setReliabilityLegendOnly(eventCountEl);
    }
    
    // Check if heatmap is enabled
    const heatmapEnabled = globalHeatmapEnabled;
    
    // Calculate pagination
    const totalPages = Math.ceil(leverageIndexPagination.totalRows / leverageIndexPagination.rowsPerPage);
    const startIdx = (leverageIndexPagination.currentPage - 1) * leverageIndexPagination.rowsPerPage;
    const endIdx = startIdx + leverageIndexPagination.rowsPerPage;
    const paginatedRows = data.rows.slice(startIdx, endIdx);
    
    // Calculate min/max for heatmap if enabled
    let minValue = Infinity;
    let maxValue = -Infinity;
    if (heatmapEnabled) {
        ({ minValue, maxValue } = getHeatmapRangeFromHeaders(data.rows, data.headers, 3));
    }

    const leverageNHeaders = data.headers.slice(3);
    const reliabilityStats = buildReliabilityStats(data.rows, (row) => leverageNHeaders.map((header) => row[`${header}_n`]), RELIABILITY_DEFAULT_CONFIG);

    headEl.innerHTML = buildRunDiffHeaderHTML(data.headers, teamName);
    
    // Calculate rowspan for inning column (group consecutive rows with same inn_ct)
    const inningGroups = [];
    let currentInning = null;
    let currentGroup = null;
    
    paginatedRows.forEach((row, index) => {
        if (row.inn_ct !== currentInning) {
            // New inning group
            currentInning = row.inn_ct;
            currentGroup = { startIndex: index, count: 1 };
            inningGroups.push(currentGroup);
        } else {
            // Same inning, increment count
            currentGroup.count++;
        }
    });
    
    // Calculate rowspan for outs column (group consecutive rows with same outs_ct)
    const outsGroups = [];
    let currentOuts = null;
    let currentOutsGroup = null;
    
    paginatedRows.forEach((row, index) => {
        if (row.outs_ct !== currentOuts) {
            // New outs group
            currentOuts = row.outs_ct;
            currentOutsGroup = { startIndex: index, count: 1 };
            outsGroups.push(currentOutsGroup);
        } else {
            // Same outs, increment count
            currentOutsGroup.count++;
        }
    });
    
    // Reverse mapping from formatted runners text to raw format for SVG
    const runnersMap = {
        '---': '000',
        '1--': '100',
        '-2-': '010',
        '12-': '110',
        '--3': '001',
        '1-3': '101',
        '-23': '011',
        '123': '111'
    };
    
    // Build table body
    const rows = paginatedRows.map((row, rowIndex) => {
        const cells = [];
        
        // Check if this row should render the inning cell
        const inningGroup = inningGroups.find(g => g.startIndex === rowIndex);
        if (inningGroup) {
            // First row of this inning group - render with rowspan
            const inningText = formatInning(row.inn_ct, data.team);
            const rowspanAttr = inningGroup.count > 1 ? ` rowspan="${inningGroup.count}"` : '';
            cells.push(`<td class="text-center align-middle fs-5"${rowspanAttr}>${inningText}</td>`);
        }
        // Otherwise skip the inning cell (it's merged with previous row)
        
        // Check if this row should render the outs cell
        const outsGroup = outsGroups.find(g => g.startIndex === rowIndex);
        if (outsGroup) {
            // First row of this outs group - render with rowspan
            const rowspanAttr = outsGroup.count > 1 ? ` rowspan="${outsGroup.count}"` : '';
            const outsBgColor = row.outs_ct === 1 ? '#f9d8b8' : '#b8d8f9';
            cells.push(`<td class="text-center align-middle fs-5 fw-semibold" style="background-color: ${outsBgColor}; color: black;"${rowspanAttr}>${row.outs_ct !== null && row.outs_ct !== undefined ? row.outs_ct : '—'}</td>`);
        }
        // Otherwise skip the outs cell (it's merged with previous row)
        
        // Convert runners from formatted text back to raw format for SVG
        const rawRunners = runnersMap[row.runners] || '000';
        const svgString = getBaseStateSVGMarkup(rawRunners);
        cells.push(`<td class="text-center align-middle svg-cell">${svgString}</td>`);
        
        // Add dynamic run differential columns
        // Skip the first 3 headers (Inning, Outs, Runners) and process the rest
        for (let i = 3; i < data.headers.length; i++) {
            const header = data.headers[i];
            const value = row[header];
            const formatted = value !== null && value !== undefined && !isNaN(value) 
                ? Number(value).toFixed(3)
                : '—';
            
            if (heatmapEnabled && value !== null && value !== undefined && !isNaN(value)) {
                const badgeHTML = renderHeatmapBadge(formatted, value, minValue, maxValue, { minWidth: '60px' });
                const nValue = row[`${header}_n`];
                cells.push(`<td class="text-center align-middle">${appendReliabilityMarkerInsideBadge(badgeHTML, nValue, reliabilityStats)}</td>`);
            } else {
                const nValue = row[`${header}_n`];
                const cellContent = (value !== null && value !== undefined && !isNaN(value))
                    ? appendReliabilityMarker(formatted, nValue, reliabilityStats)
                    : formatted;
                cells.push(`<td class="text-center align-middle">${cellContent}</td>`);
            }
        }
        
        // Add thicker border every 8th row (visual separator for inning changes)
        const borderClass = (rowIndex + 1) % 8 === 0 ? ' style="border-bottom: 2px solid #495057;"' : '';
        
        return `<tr${borderClass}>${cells.join('')}</tr>`;
    }).join('');
    
    bodyEl.innerHTML = rows;
    
    // Render pagination controls
    renderLeverageIndexPagination(totalPages);
}

function renderLeverageIndexPagination(totalPages) {
    renderPaginationControls({
        containerId: 'leverage-index-pagination',
        currentPage: leverageIndexPagination.currentPage,
        totalPages,
        changeHandlerName: 'changeLeverageIndexPage',
        labels: {
            first: '⏮',
            previous: '◀',
            next: '▶',
            last: '⏭'
        }
    });
}

function changeLeverageIndexPage(newPage) {
    const totalPages = Math.ceil(leverageIndexPagination.totalRows / leverageIndexPagination.rowsPerPage);
    setPaginationPage(leverageIndexPagination, newPage, totalPages, () => {
        renderLeverageIndexTable(leverageIndexPagination.data, false); // Don't reset page
    });
}

// Expose function to global scope for onclick handlers
window.changeLeverageIndexPage = changeLeverageIndexPage;

// Helper function to convert compact runner notation to descriptive labels
function formatRunnerState(state) {
    const mapping = {
        '---': 'Empty',
        '1--': '1st',
        '-2-': '2nd',
        '--3': '3rd',
        '12-': '1st & 2nd',
        '1-3': '1st & 3rd',
        '-23': '2nd & 3rd',
        '123': 'Loaded',
        'END': 'End'
    };
    return mapping[state] || state;
}

function convertRunnersToBinary(state) {
    // Convert from display format (e.g., "12-", "-2-", "---") to binary format (e.g., "110", "010", "000")
    // Binary format: position 0 = 1st base, position 1 = 2nd base, position 2 = 3rd base
    const mapping = {
        '---': '000',
        '1--': '100',
        '-2-': '010',
        '--3': '001',
        '12-': '110',
        '1-3': '101',
        '-23': '011',
        '123': '111',
        'END': '000'
    };
    return mapping[state] || '000';
}

// Break-Even Points rendering
// Scenario display order and metadata per analysis type
// Los tres analisis *_runs de RE Impact son los MISMOS escenarios que
// Break-Even, contados en carreras en vez de como umbral. Reusan su tabla:
// de ahi sale el estado de bases para el rombo del campo y el conjunto de filas.
//
// No se duplica el mapeo. Llego a duplicarse en el pipeline --publicando el
// estado de bases como `scenario`-- y ademas de sobrar rompio el cruce entre las
// dos tablas, que es lo que vigila BE = coste / (ganancia + coste).
const RE_IMPACT_RUNS_A_BREAKEVEN = {
    steal_runs: 'stealing',
    running_runs: 'running',
    tagging_runs: 'tagging'
};

// Tipos cuyo `scenario` YA ES el estado de bases, con columnas 0 y 1 out. El
// rombo sale solo porque getBaseStateSVGMarkup recibe el escenario tal cual.
//
// gidp son los cuatro estados CON FORZADO EN PRIMERA: sin corredor ahi la tasa
// de doble matanza va del 0% al 2% y no hay jugada que medir.
const ESCENARIOS_POR_ESTADO_DE_BASES = {
    sac_bunt: ['100', '010', '110'],
    gidp:     ['100', '101', '110', '111']
};
function escenariosPorEstadoDeBases(analysisType) {
    return ESCENARIOS_POR_ESTADO_DE_BASES[analysisType] || null;
}

function escenariosDeImpactoEnCarreras(analysisType) {
    const familia = RE_IMPACT_RUNS_A_BREAKEVEN[analysisType];
    return familia ? BREAKEVEN_SCENARIOS[familia] : null;
}

// Las filas que se DESCARGAN son las que se VEN: mismo conjunto y mismo orden
// que la tabla, que los toma de BREAKEVEN_SCENARIOS. Recorrer display_data a
// pelo daria un CSV que no cuadra con la pantalla.
function filasDeImpactoEnCarreras(data) {
    const escenarios = escenariosDeImpactoEnCarreras(data.analysis_type);
    if (!escenarios) return [];
    const porEscenario = new Map((data.display_data || []).map(r => [r.scenario, r]));
    return escenarios.map(e => porEscenario.get(e.key)).filter(Boolean);
}

const BREAKEVEN_SCENARIOS = {
    stealing: [
        { key: 'Runner on 1B steals 2B',                             runners: '100', label: 'Runner on 1B steals 2B' },
        { key: 'Runner on 2B steals 3B',                             runners: '010', label: 'Runner on 2B steals 3B' },
        { key: 'Runners on 1B & 3B, runner on 1B steals 2B',        runners: '101', label: 'Runners on 1B & 3B, runner on 1B steals 2B' },
        { key: 'Double steal',                                       runners: '110', label: 'Double steal' }
    ],
    // Estirar un batazo pasa a medirse con eventos reales (ver
    // v_re_analysis_stretch): solo con las bases limpias, porque con corredores
    // los cubos dejan de ser comparables. Por eso quedan dos escenarios y no
    // seis; los otros cuatro eran transiciones teoricas que coincidian con
    // robos.
    // Todo 'running' pasa a medirse con eventos reales. Los tres primeros son
    // el bateador estirando su propio batazo (bases limpias); los tres ultimos,
    // un corredor ya en base tomando una base de mas. Ver v_re_analysis_stretch
    // y v_re_analysis_advance.
    // Todo 'running' se mide con eventos reales (v_re_analysis_stretch y
    // v_re_analysis_advance).
    //
    // El campo runners alimenta el diagrama de bases y muestra el PUNTO DE
    // DECISION -- donde esta el corredor cuando decide si sigue -- no el estado
    // previo al lanzamiento. Al estirar un sencillo el bateador ya esta en
    // primera; al estirar un doble, en segunda. En los tres avances el corredor
    // decide desde la base a la que llego, con el bateador ya en la suya.
    running: [
        { key: 'Single stretched to double',      runners: '100', label: 'Single stretched to double' },
        { key: 'Double stretched to triple',      runners: '010', label: 'Double stretched to triple' },
        { key: 'Runner on 1B to 3B on a single',  runners: '110', label: 'Runner on 1B to 3B on a single' },
        { key: 'Runner on 2B scores on a single', runners: '101', label: 'Runner on 2B scores on a single' },
        { key: 'Runner on 1B scores on a double', runners: '011', label: 'Runner on 1B scores on a double' }
    ],
    // Tag-up pasa a medirse con elevados atrapados reales, no con transiciones
    // teoricas, asi que cubre los cuatro estados con corredor en 3B y los dos
    // de 2B con tercera vacia. El label describe la decision y el diagrama de
    // bases muestra el estado, por eso no se repite en el texto.
    tagging: [
        { key: 'Runner on 3B tags up to score (3rd)',        runners: '001', label: 'Runner on 3B tags up to score' },
        { key: 'Runner on 3B tags up to score (1st & 3rd)',  runners: '101', label: 'Runner on 3B tags up to score' },
        { key: 'Runner on 3B tags up to score (2nd & 3rd)',  runners: '011', label: 'Runner on 3B tags up to score' },
        { key: 'Runner on 3B tags up to score (Loaded)',     runners: '111', label: 'Runner on 3B tags up to score' },
        { key: 'Runner on 2B tags up to 3B (2nd)',           runners: '010', label: 'Runner on 2B tags up to 3B' },
        { key: 'Runner on 2B tags up to 3B (1st & 2nd)',     runners: '110', label: 'Runner on 2B tags up to 3B' }
    ],
    // Dos jugadas, no ocho estados de corredores. El calculo anterior recorria
    // todos los estados apoyandose en SH_FL para separar el toque sorpresa del
    // sacrificio, y eso no funciona: un sacrificio FALLIDO no se anota como SH,
    // asi que caia en el cubo del toque para hit. Ahora la intencion se aisla
    // con el estado de corredores -- bases limpias no admite sacrificio, y
    // corredor solo en 3B no admite duda sobre a quien se adelanta.
    'bunt-for-hit': [
        { key: 'Bunt for a hit', runners: '000', label: 'Bunt for a hit' },
        // El punto naranja va en tercera: es el corredor cuya carrera se decide.
        { key: 'Squeeze',        runners: '001', label: 'Squeeze' }
    ]
};

const RUNNERS_LABEL_TO_CODE = {
    'Bases empty': '000',
    'Runner on 1B': '100',
    'Runner on 2B': '010',
    'Runner on 3B': '001',
    'Runners on 1B & 2B': '110',
    'Runners on 1B & 3B': '101',
    'Runners on 2B & 3B': '011',
    'Bases loaded': '111',
    'Empty': '000',
    '1st': '100',
    '2nd': '010',
    '3rd': '001',
    '1st & 2nd': '110',
    '1st & 3rd': '101',
    '2nd & 3rd': '011',
    'Loaded': '111'
};

const RUNNERS_LABEL_ALIASES = {
    'Empty': 'Bases empty',
    '1st': 'Runner on 1B',
    '2nd': 'Runner on 2B',
    '3rd': 'Runner on 3B',
    '1st & 2nd': 'Runners on 1B & 2B',
    '1st & 3rd': 'Runners on 1B & 3B',
    '2nd & 3rd': 'Runners on 2B & 3B',
    'Loaded': 'Bases loaded'
};

// Break-even pagination for large Swing/Take tables
const sbBreakevenPagination = {
    currentPage: 1,
    rowsPerPage: 16,
    totalRows: 0,
    viewKey: null,
    filterKey: null
};

function renderSwingTakeFavoredBadge(value, takeBadgeStyle, swingBadgeStyle, heatmapEnabled = true) {
    const TAKE_FAVORED_BADGE_BG = '#E81700';
    const SWING_FAVORED_BADGE_BG = '#08F600';
    const baseStyle = 'padding:2px 6px;border-radius:4px;font-weight:600;font-size:0.66rem;line-height:1.05;display:inline-block;min-width:78px;white-space:normal;text-align:center;';
    const takeBg = takeBadgeStyle?.bgColor || TAKE_FAVORED_BADGE_BG;
    const takeText = takeBadgeStyle?.textColor || '#fff';
    const swingBg = swingBadgeStyle?.bgColor || SWING_FAVORED_BADGE_BG;
    const swingText = swingBadgeStyle?.textColor || '#111';

    if (!heatmapEnabled) {
        if (value >= 100) {
            return `<span style="color:inherit;${baseStyle}">Take<br>favored</span>`;
        }
        if (value <= 0) {
            return `<span style="color:inherit;${baseStyle}">Swing<br>favored</span>`;
        }
        return null;
    }

    if (value >= 100) {
        return `<span style="background-color:${takeBg};color:${takeText};${baseStyle}">Take<br>favored</span>`;
    }
    if (value <= 0) {
        return `<span style="background-color:${swingBg};color:${swingText};${baseStyle}">Swing<br>favored</span>`;
    }
    return null;
}

function updateSBBreakevenPagination(viewKey, fromSeason, toSeason, totalRows) {
    const filterKey = `${viewKey}|${fromSeason}|${toSeason}`;
    if (sbBreakevenPagination.filterKey !== filterKey) {
        sbBreakevenPagination.currentPage = 1;
        sbBreakevenPagination.filterKey = filterKey;
        sbBreakevenPagination.viewKey = viewKey;
    }

    sbBreakevenPagination.totalRows = totalRows;
    const totalPages = Math.max(1, Math.ceil(totalRows / sbBreakevenPagination.rowsPerPage));
    if (sbBreakevenPagination.currentPage > totalPages) {
        sbBreakevenPagination.currentPage = totalPages;
    }

    const startIdx = (sbBreakevenPagination.currentPage - 1) * sbBreakevenPagination.rowsPerPage;
    const endIdx = Math.min(startIdx + sbBreakevenPagination.rowsPerPage, totalRows);
    return { startIdx, endIdx, totalPages };
}

function renderSBBreakevenPagination() {
    const totalPages = Math.ceil(sbBreakevenPagination.totalRows / sbBreakevenPagination.rowsPerPage);
    renderPaginationControls({
        containerId: 'sb-breakeven-pagination',
        currentPage: sbBreakevenPagination.currentPage,
        totalPages,
        changeHandlerName: 'changeSBBreakevenPage',
        labels: {
            first: '⏮',
            previous: '◀',
            next: '▶',
            last: '⏭'
        },
        useDisabledVariant: false
    });
}

function changeSBBreakevenPage(newPage) {
    const totalPages = Math.ceil(sbBreakevenPagination.totalRows / sbBreakevenPagination.rowsPerPage);
    setPaginationPage(sbBreakevenPagination, newPage, totalPages, () => {
        if (sbBreakevenPagination.viewKey === 'swing-take') {
            renderSwingTakeByCountPage();
        } else if (sbBreakevenPagination.viewKey === 'swing-take-zone') {
            renderSwingTakeZonePage();
        }
    });
}

window.changeSBBreakevenPage = changeSBBreakevenPage;

function getBreakevenScenarios(selectedType, allData) {
    if (selectedType !== 'swing-take') {
        return BREAKEVEN_SCENARIOS[selectedType] || BREAKEVEN_SCENARIOS.stealing;
    }

    const countOrder = ['0-0', '0-1', '0-2', '1-0', '1-1', '1-2', '2-0', '2-1', '2-2', '3-0', '3-1', '3-2'];
    const runnersOrder = ['000', '100', '010', '001', '110', '101', '011', '111'];
    const seen = new Set();
    const scenarios = [];

    (allData || []).forEach(row => {
        if (row.type !== 'swing-take' || typeof row.scenario !== 'string') return;
        if (seen.has(row.scenario)) return;

        const match = row.scenario.match(/^Count\s+([0-3]-[0-2])\s+\|\s+(.+)$/);
        if (!match) return;

        const count = match[1];
        const runnersLabel = match[2].trim();
        const runners = RUNNERS_LABEL_TO_CODE[runnersLabel] || '000';

        scenarios.push({
            key: row.scenario,
            runners,
            label: `Count ${count} | ${runnersLabel}`,
            count,
        });
        seen.add(row.scenario);
    });

    scenarios.sort((a, b) => {
        const c = countOrder.indexOf(a.count) - countOrder.indexOf(b.count);
        if (c !== 0) return c;
        return runnersOrder.indexOf(a.runners) - runnersOrder.indexOf(b.runners);
    });

    return scenarios;
}

function renderSBBreakevenTable(data) {
    // Store raw data for re-rendering with different season ranges
    currentTableData.breakeven_points = {
        matrix_type: 'BREAKEVEN_POINTS',
        all_data: data.table_data,
        by_count_data: data.by_count_data || [],
        seasons: data.seasons,
        seasons_by_count: data.seasons_by_count || [],
        from_season: null,
        to_season: null,
        display_data: null
    };

    const allBreakevenRows = Array.isArray(data.table_data) ? data.table_data : [];
    window.SWING_TAKE_SEASONS = [...new Set(allBreakevenRows
        .filter(r => r.type === 'swing-take' && Number.isFinite(Number(r.season)))
        .map(r => Number(r.season)))].sort((a, b) => a - b);
    window.SWING_TAKE_ZONE_SEASONS = [...new Set(allBreakevenRows
        .filter(r => r.type === 'swing_take_zone' && Number.isFinite(Number(r.season)))
        .map(r => Number(r.season)))].sort((a, b) => a - b);

    // Populate season selectors from the API-provided seasons
    const fromSelect = document.querySelector('select[name="from_season"]');
    const toSelect = document.querySelector('select[name="to_season"]');
    if (fromSelect && toSelect && data.seasons && data.seasons.length > 0) {
        const seasons = data.seasons;
        const seasonsDesc = [...seasons].reverse();
        // Read current selection before repopulating
        const currentFrom = parseInt(fromSelect.value);
        const currentTo = parseInt(toSelect.value);

        // Only repopulate if the first/last option doesn't match
        const needsRepopulate = (
            parseInt(fromSelect.options[0]?.value) !== seasonsDesc[0] ||
            parseInt(fromSelect.options[fromSelect.options.length - 1]?.value) !== seasonsDesc[seasonsDesc.length - 1]
        );

        if (needsRepopulate) {
            const fromFrag = document.createDocumentFragment();
            const toFrag = document.createDocumentFragment();
            seasonsDesc.forEach(s => {
                const opt1 = document.createElement('option');
                opt1.value = s;
                opt1.textContent = s;
                fromFrag.appendChild(opt1);
                const opt2 = document.createElement('option');
                opt2.value = s;
                opt2.textContent = s;
                toFrag.appendChild(opt2);
            });
            fromSelect.innerHTML = '';
            fromSelect.appendChild(fromFrag);
            toSelect.innerHTML = '';
            toSelect.appendChild(toFrag);
        }

        // Restore selection or default to latest season
        const fromVal = seasons.includes(currentFrom) ? currentFrom : seasons[seasons.length - 1];
        const toVal = seasons.includes(currentTo) ? currentTo : seasons[seasons.length - 1];
        fromSelect.value = fromVal;
        toSelect.value = toVal;
    }

    renderSBBreakevenPage();
}

function renderSBBreakevenPage() {
    const stored = currentTableData.breakeven_points;
    if (!stored || !stored.all_data) return;

    const selectedType = document.getElementById('breakeven_type')?.value || 'stealing';
    const sbPaginationEl = document.getElementById('sb-breakeven-pagination');

    if (selectedType === 'sb_by_count') {
        if (sbPaginationEl) sbPaginationEl.innerHTML = '';
        renderSBBreakevenByCountPage();
        return;
    }

    if (selectedType === 'swing-take') {
        renderSwingTakeByCountPage();
        return;
    }

    if (selectedType === 'swing-take-zone') {
        renderSwingTakeZonePage();
        return;
    }

    if (sbPaginationEl) sbPaginationEl.innerHTML = '';

    const scenarios = getBreakevenScenarios(selectedType, stored.all_data);

    // Repopulate season selectors with seasons available for the selected type.
    const fromSelect = document.querySelector('select[name="from_season"]');
    const toSelect = document.querySelector('select[name="to_season"]');
    const typeSeasons = [...new Set((stored.all_data || [])
        .filter(r => r.type === selectedType && Number.isFinite(r.season))
        .map(r => Number(r.season)))].sort((a, b) => a - b);

    if (fromSelect && toSelect && typeSeasons.length > 0) {
        const seasons = typeSeasons;
        const seasonsDesc = [...seasons].reverse();
        const currentFrom = parseInt(fromSelect.value);
        const currentTo = parseInt(toSelect.value);

        const needsRepopulate = (
            parseInt(fromSelect.options[0]?.value) !== seasonsDesc[0] ||
            parseInt(fromSelect.options[fromSelect.options.length - 1]?.value) !== seasonsDesc[seasonsDesc.length - 1]
        );

        if (needsRepopulate) {
            const fromFrag = document.createDocumentFragment();
            const toFrag = document.createDocumentFragment();
            seasonsDesc.forEach(s => {
                const opt1 = document.createElement('option');
                opt1.value = s;
                opt1.textContent = s;
                fromFrag.appendChild(opt1);
                const opt2 = document.createElement('option');
                opt2.value = s;
                opt2.textContent = s;
                toFrag.appendChild(opt2);
            });
            fromSelect.innerHTML = '';
            fromSelect.appendChild(fromFrag);
            toSelect.innerHTML = '';
            toSelect.appendChild(toFrag);
        }

        const fromVal = seasons.includes(currentFrom) ? currentFrom : seasons[seasons.length - 1];
        const toVal = seasons.includes(currentTo) ? currentTo : seasons[seasons.length - 1];
        fromSelect.value = fromVal;
        toSelect.value = toVal;
    }

    const fallbackSeasons = typeSeasons.length > 0 ? typeSeasons : stored.seasons;
    const fromSeason = parseInt(fromSelect?.value) || fallbackSeasons[fallbackSeasons.length - 1];
    const toSeason = parseInt(toSelect?.value) || fromSeason;

    // Filter to selected season range AND selected type
    const filtered = stored.all_data.filter(r => r.season >= fromSeason && r.season <= toSeason && r.type === selectedType);

    // Compute weighted averages: for each (scenario, outs), sum(breakeven_pct * n) / sum(n)
    const agg = {};
    const labelByScenario = {};
    let totalN = 0;
    filtered.forEach(r => {
        const scenarioKey = selectedType === 'bunt-for-hit'
            ? (RUNNERS_LABEL_ALIASES[r.scenario] || r.scenario)
            : r.scenario;
        const key = `${scenarioKey}|${r.outs}`;
        if (!agg[key]) {
            agg[key] = { scenario: scenarioKey, outs: r.outs, sumPctN: 0, sumN: 0 };
        }
        if (typeof r.label === 'string' && r.label.trim()) {
            labelByScenario[scenarioKey] = RUNNERS_LABEL_ALIASES[r.label.trim()] || r.label.trim();
        }
        agg[key].sumPctN += r.breakeven_pct * r.n;
        agg[key].sumN += r.n;
        totalN += r.n;
    });

    // Tagging scenarios only have 0 and 1 outs (fly ball adds an out)
    const isTagging = selectedType === 'tagging';
    const outCols = isTagging ? [0, 1] : [0, 1, 2];

    // Build display_data: one row per scenario with columns for each out count + overall
    const displayData = scenarios.map(s => {
        const row = { scenario: s.key, label: labelByScenario[s.key] || s.label, runners: s.runners, note: s.note || '' };
        outCols.forEach(outs => {
            const a = agg[`${s.key}|${outs}`];
            row[`outs_${outs}`] = a && a.sumN > 0 ? Math.max(0, Math.min((a.sumPctN / a.sumN) * 100, 100)) : null;
            row[`outs_${outs}_n`] = a && a.sumN > 0 ? a.sumN : null;
        });
        return row;
    }).filter(row => outCols.some(outs => row[`outs_${outs}`] !== null && row[`outs_${outs}`] !== undefined && !isNaN(row[`outs_${outs}`])));

    // Update stored data for download
    stored.from_season = fromSeason;
    stored.to_season = toSeason;
    stored.display_data = displayData;
    stored.selected_type = selectedType;

    // Type-specific titles and subtitles
    const typeTitles = {
        stealing: 'Stolen Bases',
        running: 'Base Running',
        tagging: 'Tag-Up',
        'bunt-for-hit': 'Bunt',
        'swing-take': 'Swing / Take - w/Runners'
    };
    const typeSubtitles = {
        stealing: 'How often a stolen base attempt must succeed to be worth it.',
        running: 'How often an extra base attempt must succeed to be worth it.',
        tagging: 'How often a tag-up attempt must succeed to be worth it.',
        'bunt-for-hit': 'How often a bunt has to work to be worth it — reaching base on the surprise bunt, scoring the runner on the squeeze.',
        'swing-take': 'Required contact rate on swings for swinging to break even with taking the pitch.'
    };

    // Update title
    const titleEl = document.getElementById('sb-breakeven-table-title');
    if (titleEl) {
        const seasonLabel = fromSeason === toSeason ? `${fromSeason} Season` : `${fromSeason}-${toSeason} Seasons`;
        titleEl.innerHTML = `Break-Even Points - <span class="primary-badge">${typeTitles[selectedType]} - ${seasonLabel}</span>`;
    }

    const subtitleEl = document.getElementById('sb-breakeven-table-title-subtitle');
    if (subtitleEl) {
        subtitleEl.innerHTML = `${typeSubtitles[selectedType]}<br>If a player can't beat these percentages, the attempt costs more runs than it gains.`;
    }

    // Update event count
    const eventCountEl = document.getElementById('sb-breakeven-event-count');
    setBreakevenFooterText(eventCountEl, getBreakevenFooterText(selectedType, totalN), selectedType);

    // Render table headers
    const headEl = document.getElementById('sb-breakeven-table-head');
    if (headEl) {
        let headerHTML = '<tr><th class="bg-primary text-start">Scenario</th>';
        outCols.forEach(o => {
            headerHTML += `<th class="bg-primary text-center">${o === 1 ? '1 Out' : o + ' Outs'}</th>`;
        });
        headerHTML += '</tr>';
        headEl.innerHTML = headerHTML;
    }

    // Compute min/max across all displayed values (for highlighting and heatmap)
    const heatmapEnabled = globalHeatmapEnabled;
    let allValues = [];
    displayData.forEach(row => {
        outCols.forEach(o => {
            const v = row[`outs_${o}`];
            if (v !== null && v !== undefined && !isNaN(v)) allValues.push(v);
        });
    });
    const hMin = allValues.length > 0 ? Math.min(...allValues) : 0;
    const hMax = allValues.length > 0 ? Math.max(...allValues) : 1;
    const reliabilityStats = buildBreakevenReliabilityStats(displayData, outCols.map(o => `outs_${o}_n`));
    const takeBadgeColor = '#E81700';
    const swingBadgeColor = '#08F600';

    const renderCell = (value, nValue) => {
        if (value === null || value === undefined || isNaN(value)) return '\u2014';
        if (value >= 100) {
            return renderBreakevenReliabilityBadge(
                'Take<br>favored',
                `background-color:${takeBadgeColor};color:#fff;padding:2px 6px;border-radius:4px;font-weight:600;font-size:0.66rem;line-height:1.05;display:inline-block;min-width:78px;white-space:normal;text-align:center;`,
                nValue,
                reliabilityStats
            );
        }
        if (value <= 0) {
            return renderBreakevenReliabilityBadge(
                'Swing<br>favored',
                `background-color:${swingBadgeColor};color:#111;padding:2px 6px;border-radius:4px;font-weight:600;font-size:0.66rem;line-height:1.05;display:inline-block;min-width:78px;white-space:normal;text-align:center;`,
                nValue,
                reliabilityStats
            );
        }
        return renderBreakevenPercentCell(value, nValue, {
            heatmapEnabled: heatmapEnabled && allValues.length > 1,
            hMin,
            hMax,
            reliabilityStats,
            minWidth: '60px'
        });
    };

    // Render table body
    const bodyEl = document.getElementById('sb-breakeven-table-body');
    if (bodyEl) {
        bodyEl.innerHTML = displayData.map(row => {
            const svgString = getBaseStateSVGMarkup(row.runners);
            const noteHtml = row.note ? ` <small class="text-muted">(${row.note})</small>` : '';
            let cells = `<td class="text-start align-middle">${svgString} ${row.label}${noteHtml}</td>`;
            outCols.forEach(o => {
                cells += `<td class="text-center">${renderCell(row[`outs_${o}`], row[`outs_${o}_n`])}</td>`;
            });
            return `<tr>${cells}</tr>`;
        }).join('');
    }
}

// Count display order for SB by Count table (matches RE288 sort_order_count — frequency-based)
const COUNT_ORDER = ['0-2', '1-2', '0-1', '2-2', '1-1', '0-0', '1-0', '2-1', '3-2', '2-0', '3-1', '3-0'];

const SWING_TAKE_SCENARIOS = [
    { key: 'Bases empty', runners: '000', label: 'Bases empty' },
    { key: 'Runner on 1B', runners: '100', label: 'Runner on 1B' },
    { key: 'Runner on 2B', runners: '010', label: 'Runner on 2B' },
    { key: 'Runner on 3B', runners: '001', label: 'Runner on 3B' },
    { key: 'Runners on 1B & 2B', runners: '110', label: 'Runners on 1B & 2B' },
    { key: 'Runners on 1B & 3B', runners: '101', label: 'Runners on 1B & 3B' },
    { key: 'Runners on 2B & 3B', runners: '011', label: 'Runners on 2B & 3B' },
    { key: 'Bases loaded', runners: '111', label: 'Bases loaded' }
];

function parseSwingTakeScenario(scenarioText) {
    if (typeof scenarioText !== 'string') return null;
    const match = scenarioText.match(/^Count\s+([0-3]-[0-2])\s+\|\s+(.+)$/);
    if (!match) return null;
    const rawLabel = match[2].trim();
    const normalizedLabel = RUNNERS_LABEL_ALIASES[rawLabel] || rawLabel;
    return { count: match[1], runnersLabel: normalizedLabel };
}

function renderSwingTakeByCountPage() {
    const stored = currentTableData.breakeven_points;
    if (!stored || !stored.all_data) return;

    const swingData = stored.all_data.filter(r => r.type === 'swing-take');
    if (swingData.length === 0) return;

    const seasons = [...new Set(swingData.map(r => Number(r.season)).filter(Number.isFinite))].sort((a, b) => a - b);
    if (seasons.length === 0) return;

    // Repopulate season selectors with swing-take range only.
    const fromSelect = document.querySelector('select[name="from_season"]');
    const toSelect = document.querySelector('select[name="to_season"]');
    if (fromSelect && toSelect) {
        const seasonsDesc = [...seasons].reverse();
        const currentFrom = parseInt(fromSelect.value);
        const currentTo = parseInt(toSelect.value);

        const needsRepopulate = (
            parseInt(fromSelect.options[0]?.value) !== seasonsDesc[0] ||
            parseInt(fromSelect.options[fromSelect.options.length - 1]?.value) !== seasonsDesc[seasonsDesc.length - 1]
        );

        if (needsRepopulate) {
            const fromFrag = document.createDocumentFragment();
            const toFrag = document.createDocumentFragment();
            seasonsDesc.forEach(s => {
                const opt1 = document.createElement('option');
                opt1.value = s;
                opt1.textContent = s;
                fromFrag.appendChild(opt1);
                const opt2 = document.createElement('option');
                opt2.value = s;
                opt2.textContent = s;
                toFrag.appendChild(opt2);
            });
            fromSelect.innerHTML = '';
            fromSelect.appendChild(fromFrag);
            toSelect.innerHTML = '';
            toSelect.appendChild(toFrag);
        }

        const fromVal = seasons.includes(currentFrom) ? currentFrom : seasons[seasons.length - 1];
        const toVal = seasons.includes(currentTo) ? currentTo : seasons[seasons.length - 1];
        fromSelect.value = fromVal;
        toSelect.value = toVal;
    }

    const fromSeason = parseInt(fromSelect?.value) || seasons[seasons.length - 1];
    const toSeason = parseInt(toSelect?.value) || fromSeason;

    const filtered = swingData.filter(r => r.season >= fromSeason && r.season <= toSeason);

    // Aggregate weighted averages keyed by (runnersLabel, count, outs)
    const agg = {};
    const labelByRunners = {};
    let totalN = 0;
    filtered.forEach(r => {
        const parsed = parseSwingTakeScenario(r.scenario);
        if (!parsed) return;
        const explicitLabel = (typeof r.label === 'string' && r.label.trim()) ? r.label.trim() : null;
        const normalizedRunnersLabel = RUNNERS_LABEL_ALIASES[explicitLabel || parsed.runnersLabel] || (explicitLabel || parsed.runnersLabel);
        const key = `${normalizedRunnersLabel}|${parsed.count}|${r.outs}`;
        if (!agg[key]) {
            agg[key] = { sumPctN: 0, sumN: 0, hasNaN: false };
        }
        if (explicitLabel) {
            labelByRunners[normalizedRunnersLabel] = normalizedRunnersLabel;
        }
        if (r.breakeven_pct === null || (typeof r.breakeven_pct === 'number' && isNaN(r.breakeven_pct))) {
            agg[key].hasNaN = true;
        } else if (typeof r.breakeven_pct === 'number') {
            agg[key].sumPctN += r.breakeven_pct * r.n;
            agg[key].sumN += r.n;
        }
        totalN += r.n;
    });

    // Build display_data: one row per (runners scenario, outs), with count columns
    const displayData = [];
    SWING_TAKE_SCENARIOS.forEach(s => {
        [0, 1, 2].forEach(outs => {
            const row = { scenario: s.key, label: labelByRunners[s.key] || s.label, runners: s.runners, outs };
            COUNT_ORDER.forEach(count => {
                const a = agg[`${s.key}|${count}|${outs}`];
                if (a && a.hasNaN && a.sumN === 0) {
                    row[count] = NaN;
                    row[`${count}_n`] = null;
                } else if (a && a.sumN > 0) {
                    row[count] = Math.max(0, Math.min((a.sumPctN / a.sumN) * 100, 100));
                    row[`${count}_n`] = a.sumN;
                } else {
                    row[count] = null;
                    row[`${count}_n`] = null;
                }
            });
            displayData.push(row);
        });
    });

    stored.from_season = fromSeason;
    stored.to_season = toSeason;
    stored.display_data = displayData;
    stored.selected_type = 'swing-take';

    // Update title/subtitle
    const titleEl = document.getElementById('sb-breakeven-table-title');
    if (titleEl) {
        let titleHTML = 'Break-Even Points - ';
        if (fromSeason === toSeason) {
            titleHTML += `<span class="primary-badge">Swing / Take - w/Runners - ${fromSeason} Season</span>`;
        } else {
            titleHTML += `<span class="primary-badge">Swing / Take - w/Runners - ${fromSeason}-${toSeason} Seasons</span>`;
        }
        titleEl.innerHTML = titleHTML;
    }

    const subtitleEl = document.getElementById('sb-breakeven-table-title-subtitle');
    if (subtitleEl) {
        subtitleEl.innerHTML = 'How often you need to make contact when you swing for swinging to be as good as taking, for each count.<br>Empty cells (—) indicate counts where there isn\'t enough data to produce a reliable estimate.';
    }

    const eventCountEl = document.getElementById('sb-breakeven-event-count');
    setBreakevenFooterText(eventCountEl, getBreakevenFooterText('swing-take', totalN), 'swing-take');

    // Render headers in SB-by-count layout
    const headEl = document.getElementById('sb-breakeven-table-head');
    if (headEl) {
        let headerHTML = '<tr>';
        headerHTML += '<th class="text-center fw-bold" colspan="2" style="background-color: var(--primary)">Scenario</th>';
        headerHTML += '<th class="text-center fw-bold" style="background-color: var(--primary)">Outs</th>';
        COUNT_ORDER.forEach(count => {
            const displayHeader = count === '0-0' ? 'First<br>Pitch' : count;
            headerHTML += `<th class="text-center fw-bold" style="background-color: var(--primary)">${displayHeader}</th>`;
        });
        headerHTML += '</tr>';
        headEl.innerHTML = headerHTML;
    }

    const heatmapEnabled = globalHeatmapEnabled;
    let allValues = [];
    displayData.forEach(row => {
        COUNT_ORDER.forEach(count => {
            const v = row[count];
            if (v !== null && v !== undefined && !isNaN(v)) allValues.push(v);
        });
    });
    const hMin = allValues.length > 0 ? Math.min(...allValues) : 0;
    const hMax = allValues.length > 0 ? Math.max(...allValues) : 1;
    const getSwingTakeHeatmapColor = (valuePct) => {
        const v = Math.max(0, Math.min(100, Number(valuePct)));
        const t = v / 100;
        const start = { r: 8, g: 246, b: 0 };   // #08F600
        const end = { r: 232, g: 23, b: 0 };    // #E81700
        const r = Math.round(start.r + (end.r - start.r) * t);
        const g = Math.round(start.g + (end.g - start.g) * t);
        const b = Math.round(start.b + (end.b - start.b) * t);
        const bgColor = `rgb(${r}, ${g}, ${b})`;

        const toLinear = (c) => {
            const s = c / 255;
            return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
        };
        const luminance = 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b);
        const contrastWhite = (1.0 + 0.05) / (luminance + 0.05);
        const contrastBlack = (luminance + 0.05) / 0.05;
        const textColor = contrastBlack >= contrastWhite ? '#111' : '#fff';
        return { bgColor, textColor };
    };
    const takeBadgeStyle = getSwingTakeHeatmapColor(100);
    const swingBadgeStyle = getSwingTakeHeatmapColor(0);
    const reliabilityStats = buildBreakevenReliabilityStats(displayData, COUNT_ORDER.map(count => `${count}_n`));
    const renderCell = (value, nValue) => {
        if (value === null || value === undefined) return '\u2014';
        if (isNaN(value)) return 'N/A';
        const badge = renderSwingTakeFavoredBadge(value, takeBadgeStyle, swingBadgeStyle, heatmapEnabled);
        if (badge) {
            return appendReliabilityMarkerInsideBadge(badge, nValue, reliabilityStats);
        }
        return renderBreakevenPercentCell(value, nValue, {
            heatmapEnabled,
            hMin,
            hMax,
            reliabilityStats,
            getColorFn: (v, _min, _max) => getSwingTakeHeatmapColor(v),
            minWidth: '50px',
            decimals: 1
        });
    };

    sbBreakevenPagination.rowsPerPage = 12;
    const { startIdx, endIdx } = updateSBBreakevenPagination('swing-take', fromSeason, toSeason, displayData.length);
    const pageData = displayData.slice(startIdx, endIdx);

    // Render body in SB-by-count layout (rowspan=3)
    const bodyEl = document.getElementById('sb-breakeven-table-body');
    if (bodyEl) {
        let bodyHTML = '';
        pageData.forEach((row, idx) => {
            const globalIdx = startIdx + idx;
            const borderStyle = (globalIdx + 1) % 3 === 0 ? ' style="border-bottom: 2px solid #495057;"' : '';
            bodyHTML += `<tr${borderStyle}>`;

            const groupIndex = Math.floor(globalIdx / 3);
            const prevGroupIndex = idx > 0 ? Math.floor((globalIdx - 1) / 3) : -1;
            const startsNewVisibleGroup = idx === 0 || prevGroupIndex !== groupIndex;
            if (startsNewVisibleGroup) {
                const remainingInGroup = 3 - (globalIdx % 3);
                const rowSpan = Math.min(remainingInGroup, endIdx - globalIdx);
                const svgString = getBaseStateSVGMarkup(row.runners);
                bodyHTML += `<td class="text-end sticky-col align-middle svg-cell" rowspan="${rowSpan}">${svgString}</td>`;
                bodyHTML += `<td class="text-start sticky-col align-middle label-cell label-cell-sticky" rowspan="${rowSpan}">${row.label}</td>`;
            }

            bodyHTML += `<td class="text-center">${row.outs}</td>`;
            COUNT_ORDER.forEach(count => {
                bodyHTML += `<td class="text-center" style="font-size:0.84rem;">${renderCell(row[count], row[`${count}_n`])}</td>`;
            });

            bodyHTML += '</tr>';
        });
        bodyEl.innerHTML = bodyHTML;
    }

    renderSBBreakevenPagination();
}

function renderSBBreakevenByCountPage() {
    const stored = currentTableData.breakeven_points;
    if (!stored || !stored.by_count_data || stored.by_count_data.length === 0) return;

    const scenarios = BREAKEVEN_SCENARIOS.stealing;

    // Use seasons_by_count for season range (1989-2025)
    const seasons = stored.seasons_by_count;

    // Repopulate season selectors with by-count seasons (1989+)
    const fromSelect = document.querySelector('select[name="from_season"]');
    const toSelect = document.querySelector('select[name="to_season"]');
    if (fromSelect && toSelect && seasons.length > 0) {
        const seasonsDesc = [...seasons].reverse();
        const currentFrom = parseInt(fromSelect.value);
        const currentTo = parseInt(toSelect.value);

        const needsRepopulate = (
            parseInt(fromSelect.options[0]?.value) !== seasonsDesc[0] ||
            parseInt(fromSelect.options[fromSelect.options.length - 1]?.value) !== seasonsDesc[seasonsDesc.length - 1]
        );

        if (needsRepopulate) {
            const fromFrag = document.createDocumentFragment();
            const toFrag = document.createDocumentFragment();
            seasonsDesc.forEach(s => {
                const opt1 = document.createElement('option');
                opt1.value = s;
                opt1.textContent = s;
                fromFrag.appendChild(opt1);
                const opt2 = document.createElement('option');
                opt2.value = s;
                opt2.textContent = s;
                toFrag.appendChild(opt2);
            });
            fromSelect.innerHTML = '';
            fromSelect.appendChild(fromFrag);
            toSelect.innerHTML = '';
            toSelect.appendChild(toFrag);
        }

        const fromVal = seasons.includes(currentFrom) ? currentFrom : seasons[seasons.length - 1];
        const toVal = seasons.includes(currentTo) ? currentTo : seasons[seasons.length - 1];
        fromSelect.value = fromVal;
        toSelect.value = toVal;
    }

    const fromSeason = parseInt(fromSelect?.value) || seasons[seasons.length - 1];
    const toSeason = parseInt(toSelect?.value) || fromSeason;

    // Filter by season range (all scenarios)
    const filtered = stored.by_count_data.filter(r =>
        r.season >= fromSeason && r.season <= toSeason
    );

    // Aggregate weighted averages keyed by (scenario, count, outs)
    const agg = {};
    const labelByScenario = {};
    let totalN = 0;
    filtered.forEach(r => {
        const key = `${r.scenario}|${r.count}|${r.outs}`;
        if (!agg[key]) {
            agg[key] = { sumPctN: 0, sumN: 0, hasNaN: false };
        }
        if (typeof r.label === 'string' && r.label.trim()) {
            labelByScenario[r.scenario] = r.label.trim();
        }
        // Skip NaN/null values in aggregation, but mark if we encountered any
        // NaN serializes to null in JSON, so check both isNaN and null
        if (r.breakeven_pct === null || (typeof r.breakeven_pct === 'number' && isNaN(r.breakeven_pct))) {
            agg[key].hasNaN = true;
        } else if (typeof r.breakeven_pct === 'number') {
            agg[key].sumPctN += r.breakeven_pct * r.n;
            agg[key].sumN += r.n;
        }
        totalN += r.n;
    });

    // Build display_data: one row per (scenario, outs) with count values as columns
    // 4 scenarios × 3 outs = 12 rows, matching RE288 layout
    const displayData = [];
    scenarios.forEach(s => {
        [0, 1, 2].forEach(outs => {
            const row = { scenario: s.key, label: labelByScenario[s.key] || s.label, runners: s.runners, outs };
            COUNT_ORDER.forEach(count => {
                const a = agg[`${s.key}|${count}|${outs}`];
                // If hasNaN flag is set, mark as NaN; otherwise calculate the percentage
                if (a && a.hasNaN && a.sumN === 0) {
                    row[count] = NaN;  // Mark as NaN for display as "N/A"
                    row[`${count}_n`] = null;
                } else if (a && a.sumN > 0) {
                    row[count] = Math.max(0, Math.min((a.sumPctN / a.sumN) * 100, 100));
                    row[`${count}_n`] = a.sumN;
                } else {
                    row[count] = null;  // No data
                    row[`${count}_n`] = null;
                }
            });
            displayData.push(row);
        });
    });

    // Update stored data for download
    stored.from_season = fromSeason;
    stored.to_season = toSeason;
    stored.display_data = displayData;
    stored.selected_type = 'sb_by_count';

    // Update title
    const titleEl = document.getElementById('sb-breakeven-table-title');
    if (titleEl) {
        let titleHTML = 'Break-Even Points - ';
        if (fromSeason === toSeason) {
            titleHTML += `<span class="primary-badge">SB by Count - ${fromSeason} Season</span>`;
        } else {
            titleHTML += `<span class="primary-badge">SB by Count - ${fromSeason}-${toSeason} Seasons</span>`;
        }
        titleEl.innerHTML = titleHTML;
    }

    const subtitleEl = document.getElementById('sb-breakeven-table-title-subtitle');
    if (subtitleEl) {
        subtitleEl.innerHTML = 'Minimum success rate needed to make a steal attempt worth it, based on the pitch count.<br>If a player can\'t beat these percentages, the attempt costs more runs than it gains.';
    }

    // Update event count
    const eventCountEl = document.getElementById('sb-breakeven-event-count');
    setBreakevenFooterText(eventCountEl, getBreakevenFooterText('sb_by_count', totalN), 'sb_by_count');

    // Render table headers — counts as columns, matching RE288 layout
    const headEl = document.getElementById('sb-breakeven-table-head');
    if (headEl) {
        let headerHTML = '<tr>';
        headerHTML += '<th class="text-center fw-bold" colspan="2" style="background-color: var(--primary)">Scenario</th>';
        headerHTML += '<th class="text-center fw-bold" style="background-color: var(--primary)">Outs</th>';
        COUNT_ORDER.forEach(count => {
            const displayHeader = count === '0-0' ? 'First<br>Pitch' : count;
            headerHTML += `<th class="text-center fw-bold" style="background-color: var(--primary)">${displayHeader}</th>`;
        });
        headerHTML += '</tr>';
        headEl.innerHTML = headerHTML;
    }

    // Heatmap across all count values
    const heatmapEnabled = globalHeatmapEnabled;
    let allValues = [];
    displayData.forEach(row => {
        COUNT_ORDER.forEach(count => {
            const v = row[count];
            if (v !== null && v !== undefined && !isNaN(v)) allValues.push(v);
        });
    });
    const hMin = allValues.length > 0 ? Math.min(...allValues) : 0;
    const hMax = allValues.length > 0 ? Math.max(...allValues) : 1;
    const getSwingTakeHeatmapColor = (valuePct) => {
        const v = Math.max(0, Math.min(100, Number(valuePct)));
        const t = v / 100;
        const start = { r: 8, g: 246, b: 0 };   // #08F600
        const end = { r: 232, g: 23, b: 0 };    // #E81700
        const r = Math.round(start.r + (end.r - start.r) * t);
        const g = Math.round(start.g + (end.g - start.g) * t);
        const b = Math.round(start.b + (end.b - start.b) * t);
        const bgColor = `rgb(${r}, ${g}, ${b})`;
        const textColor = t >= 0.55 ? '#fff' : '#111';
        return { bgColor, textColor };
    };
    const takeBadgeColor = getSwingTakeHeatmapColor(100).bgColor;
    const swingBadgeColor = getSwingTakeHeatmapColor(0).bgColor;
    const reliabilityStats = buildBreakevenReliabilityStats(displayData, COUNT_ORDER.map(count => `${count}_n`));

    const renderCell = (value, nValue) => {
        if (value === null || value === undefined) return '\u2014';
        if (isNaN(value)) return 'N/A';
        if (value >= 100) {
            return renderBreakevenReliabilityBadge(
                'Do not<br>Attempt',
                `background-color:${takeBadgeColor};color:#fff;padding:2px 6px;border-radius:4px;font-weight:600;font-size:0.66rem;line-height:1.05;display:inline-block;min-width:78px;white-space:normal;text-align:center;`,
                nValue,
                reliabilityStats
            );
        }
        const clampedValue = Math.max(0, value);
        return renderBreakevenPercentCell(clampedValue, nValue, {
            heatmapEnabled: heatmapEnabled && allValues.length > 1,
            hMin,
            hMax,
            reliabilityStats,
            minWidth: '50px',
            decimals: 1
        });
    };

    // Render table body — grouped by scenario (rowspan=3), matching RE288 layout
    const bodyEl = document.getElementById('sb-breakeven-table-body');
    if (bodyEl) {
        let bodyHTML = '';
        displayData.forEach((row, idx) => {
            // Thick border every 3rd row (scenario group separator)
            const borderStyle = (idx + 1) % 3 === 0 ? ' style="border-bottom: 2px solid #495057;"' : '';
            bodyHTML += `<tr${borderStyle}>`;

            // SVG + label on first row of each scenario group
            if (idx % 3 === 0) {
                const svgString = getBaseStateSVGMarkup(row.runners);
                bodyHTML += `<td class="text-end sticky-col align-middle svg-cell" rowspan="3">${svgString}</td>`;
                bodyHTML += `<td class="text-start sticky-col align-middle label-cell label-cell-sticky" rowspan="3">${row.label}</td>`;
            }

            bodyHTML += `<td class="text-center">${row.outs}</td>`;

            COUNT_ORDER.forEach(count => {
                bodyHTML += `<td class="text-center">${renderCell(row[count], row[`${count}_n`])}</td>`;
            });

            bodyHTML += '</tr>';
        });
        bodyEl.innerHTML = bodyHTML;
    }
}

// Sacrifice Bunt RE rendering
// Scenario display order and metadata
const SAC_BUNT_RE_SCENARIOS = [
    { key: 'Move runner to 2B',            runners: '100', label: 'Move runner to 2B' },
    { key: 'Move runner to 3B',            runners: '010', label: 'Move runner to 3B' },
    { key: 'Move runners to 2B and 3B',    runners: '110', label: 'Move runners to 2B and 3B' }
];

function renderSacBuntReTable(data) {
    // Store raw data for re-rendering with different season ranges
    currentTableData.sac_bunt_re = {
        matrix_type: 'SAC_BUNT_RE',
        all_data: data.table_data,
        seasons: data.seasons,
        from_season: null,
        to_season: null,
        display_data: null
    };

    // Populate season selectors from the API-provided seasons
    const fromSelect = document.querySelector('select[name="from_season"]');
    const toSelect = document.querySelector('select[name="to_season"]');
    if (fromSelect && toSelect && data.seasons && data.seasons.length > 0) {
        const seasons = data.seasons;
        const seasonsDesc = [...seasons].reverse();
        const currentFrom = parseInt(fromSelect.value);
        const currentTo = parseInt(toSelect.value);

        const needsRepopulate = (
            parseInt(fromSelect.options[0]?.value) !== seasonsDesc[0] ||
            parseInt(fromSelect.options[fromSelect.options.length - 1]?.value) !== seasonsDesc[seasonsDesc.length - 1]
        );

        if (needsRepopulate) {
            const fromFrag = document.createDocumentFragment();
            const toFrag = document.createDocumentFragment();
            seasonsDesc.forEach(s => {
                const opt1 = document.createElement('option');
                opt1.value = s;
                opt1.textContent = s;
                fromFrag.appendChild(opt1);
                const opt2 = document.createElement('option');
                opt2.value = s;
                opt2.textContent = s;
                toFrag.appendChild(opt2);
            });
            fromSelect.innerHTML = '';
            fromSelect.appendChild(fromFrag);
            toSelect.innerHTML = '';
            toSelect.appendChild(toFrag);
        }

        const fromVal = seasons.includes(currentFrom) ? currentFrom : seasons[seasons.length - 1];
        const toVal = seasons.includes(currentTo) ? currentTo : seasons[seasons.length - 1];
        fromSelect.value = fromVal;
        toSelect.value = toVal;
    }

    renderSacBuntRePage();
}

// Despachador de clics delegado.
//
// Antes estos botones y cabeceras llevaban onclick="..." en linea. Al pasar la CSP
// a nonce (5.4), el navegador dejo de ejecutarlos: los nonces cubren <script>, NO
// los manejadores en atributo -- el mismo caso que los style=, que si se detecto y
// este no. Efecto: la paginacion dejo de funcionar en TODAS las tablas, porque
// las cuatro flechas salen de un renderizador compartido, y con ella los 18
// encabezados ordenables de wOBA y Park Factors.
//
// Se resuelve con delegacion: un solo listener en document que lee data-accion.
// El mapa es explicito y no window[nombre]: asi un data-accion inventado en el
// DOM no puede invocar cualquier funcion global.
const ACCIONES_DELEGADAS = {};

function registrarAccion(nombre, fn) {
    ACCIONES_DELEGADAS[nombre] = fn;
}

document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-accion]');
    if (!el || el.disabled) return;
    const fn = ACCIONES_DELEGADAS[el.dataset.accion];
    if (!fn) {
        console.warn('Accion delegada desconocida:', el.dataset.accion);
        return;
    }
    if (el.dataset.pagina !== undefined) {
        fn(Number(el.dataset.pagina));
    } else if (el.dataset.clave !== undefined) {
        fn(el.dataset.clave);
    } else {
        fn();
    }
});

// Sentido del mapa de calor. Las dos familias van al reves la una de la otra, y
// no por capricho: miden cosas distintas.
//
//   RE Impact  el valor es un cambio de esperanza de carreras. Donde MAS se
//              pierde va rojo y donde MAS se gana va azul, asi que hay que
//              invertir la escala por defecto -- que pinta el maximo de rojo, y
//              aqui el maximo puede ser la MENOR perdida.
//
//   Break-even el valor es el porcentaje de exito que hace falta. Cuanto MAS
//              alto, mas dificil de justificar la jugada, asi que el maximo va
//              rojo: la escala por defecto ya vale.
//
// Estaban puestos a mano tabla por tabla y cuatro de las seis de RE Impact los
// tenian al reves -- sac bunt, IBB, bateador emergente y veces enfrentado --, de
// modo que el mismo dato se leia de dos maneras opuestas segun donde miraras.
const RE_IMPACT_HEATMAP_REVERSE = true;
const BREAKEVEN_HEATMAP_REVERSE = false;

function renderSacBuntRePage() {
    const stored = currentTableData.sac_bunt_re;
    if (!stored || !stored.all_data) return;

    const fromSeason = parseInt(document.querySelector('select[name="from_season"]')?.value) || stored.seasons[stored.seasons.length - 1];
    const toSeason = parseInt(document.querySelector('select[name="to_season"]')?.value) || fromSeason;

    // Filter to selected season range
    const filtered = stored.all_data.filter(r => r.season >= fromSeason && r.season <= toSeason);

    // Compute weighted averages: for each (scenario, outs), sum(delta_re * n) / sum(n)
    const agg = {};
    let totalN = 0;
    filtered.forEach(r => {
        const key = `${r.scenario}|${r.outs}`;
        if (!agg[key]) {
            agg[key] = { scenario: r.scenario, outs: r.outs, sumDeltaN: 0, sumN: 0 };
        }
        agg[key].sumDeltaN += r.delta_re * r.n;
        agg[key].sumN += r.n;
        totalN += r.n;
    });

    // Build display_data: one row per scenario with columns for 0 and 1 outs + overall
    const displayData = SAC_BUNT_RE_SCENARIOS.map(s => {
        const row = { scenario: s.key, label: s.label, runners: s.runners };
        [0, 1].forEach(outs => {
            const a = agg[`${s.key}|${outs}`];
            row[`outs_${outs}`] = a && a.sumN > 0 ? (a.sumDeltaN / a.sumN) : null;
        });
        return row;
    });

    // Update stored data for download
    stored.from_season = fromSeason;
    stored.to_season = toSeason;
    stored.display_data = displayData;

    // Update title
    const titleEl = document.getElementById('sac-bunt-re-table-title');
    if (titleEl) {
        let titleHTML = 'Sacrifice Bunt RE Impact - ';
        if (fromSeason === toSeason) {
            titleHTML += `<span class="primary-badge">${fromSeason} Season</span>`;
        } else {
            titleHTML += `<span class="primary-badge">${fromSeason}-${toSeason} Seasons</span>`;
        }
        titleEl.innerHTML = titleHTML;
    }

    const subtitleEl = document.getElementById('sac-bunt-re-table-title-subtitle');
    if (subtitleEl) {
        // "on average" sobraba: el valor es exacto, no promediado.
        subtitleEl.innerHTML = 'How many runs your team loses when executing a successful sacrifice bunt.';
    }

    // Update event count
    const eventCountEl = document.getElementById('sac-bunt-re-event-count');
    if (eventCountEl) {
        eventCountEl.textContent = `${totalN.toLocaleString()} base-out state occurrences`;
    }

    // Render table headers
    const headEl = document.getElementById('sac-bunt-re-table-head');
    if (headEl) {
        headEl.innerHTML = `
            <tr>
                <th class="bg-primary text-start">Scenario</th>
                <th class="bg-primary text-center">0 Outs</th>
                <th class="bg-primary text-center">1 Out</th>
            </tr>
        `;
    }

    // Compute min/max across all displayed values (for highlighting and heatmap)
    const heatmapEnabled = globalHeatmapEnabled;
    let allValues = [];
    displayData.forEach(row => {
        [0, 1].forEach(o => {
            const v = row[`outs_${o}`];
            if (v !== null && v !== undefined && !isNaN(v)) allValues.push(v);
        });
    });
    const hMin = allValues.length > 0 ? Math.min(...allValues) : 0;
    const hMax = allValues.length > 0 ? Math.max(...allValues) : 1;

    const renderCell = (value) => {
        if (value === null || value === undefined || isNaN(value)) return '\u2014';
        const displayValue = value.toFixed(3);
        if (heatmapEnabled && allValues.length > 1) {
            // reverse=true: most negative (worst) = red
            return renderHeatmapBadge(displayValue, value, hMin, hMax, {
                reverse: RE_IMPACT_HEATMAP_REVERSE,
                minWidth: '60px'
            });
        }
        return displayValue;
    };

    // Render table body
    const bodyEl = document.getElementById('sac-bunt-re-table-body');
    if (bodyEl) {
        bodyEl.innerHTML = displayData.map(row => {
            const svgString = getBaseStateSVGMarkup(row.runners);
            return `
            <tr>
                <td class="text-start align-middle">${svgString} ${row.label}</td>
                <td class="text-center">${renderCell(row.outs_0)}</td>
                <td class="text-center">${renderCell(row.outs_1)}</td>
            </tr>
            `;
        }).join('');
    }
}

function renderReImpactTable(data) {
    // Store data in current table data for downloads and season changes
    const reImpactPercentToggle = document.getElementById('re-impact-percent-toggle');
    if (reImpactPercentToggle) {
        reImpactPercentToggle.checked = false;
    }
    reImpactTtoPagination.currentPage = 1;
    reImpactTtoPagination.totalRows = 0;

    currentTableData.re_impact = {
        matrix_type: data.matrix_type,
        analysis_type: data.analysis_type,
        all_data: data.table_data,
        seasons: data.seasons,
        from_season: data.from_season || (data.seasons && data.seasons.length > 0 ? data.seasons[0] : null),
        to_season: data.to_season || (data.seasons && data.seasons.length > 0 ? data.seasons[0] : null),
        display_mode: 'value',
        display_data: []
    };
    // Populate season dropdowns
    const fromSeasonEl = document.getElementById('from_season');
    const toSeasonEl = document.getElementById('to_season');
    
    if (fromSeasonEl && toSeasonEl && data.seasons) {
        // Capture user's current selections before clearing
        const prevFrom = parseInt(fromSeasonEl.value);
        const prevTo = parseInt(toSeasonEl.value);
        const seasonSet = new Set(data.seasons.map(String));

        // Clear existing options
        fromSeasonEl.innerHTML = '';
        toSeasonEl.innerHTML = '';
        
        // Add season options (already in descending order from backend)
        data.seasons.forEach(season => {
            const fromOption = document.createElement('option');
            fromOption.value = season;
            fromOption.textContent = season;
            fromSeasonEl.appendChild(fromOption);
            
            const toOption = document.createElement('option');
            toOption.value = season;
            toOption.textContent = season;
            toSeasonEl.appendChild(toOption);
        });
        
        // Restore user's selections if valid, otherwise default to most recent
        const defaultSeason = data.seasons[0];
        fromSeasonEl.value = seasonSet.has(String(prevFrom)) ? String(prevFrom) : defaultSeason;
        toSeasonEl.value   = seasonSet.has(String(prevTo))   ? String(prevTo)   : defaultSeason;
        currentTableData.re_impact.from_season = parseInt(fromSeasonEl.value);
        currentTableData.re_impact.to_season   = parseInt(toSeasonEl.value);
    }
    
    // Render the table content
    renderReImpactPage();
}

function renderReImpactPage() {
    const stored = currentTableData.re_impact;
    if (!stored || !stored.all_data) {
        console.error('No RE Impact data stored', stored);
        return;
    }
    const analysisType = stored.analysis_type;
    if (analysisType !== 'tto') {
        const paginationEl = document.getElementById('re-impact-pagination');
        if (paginationEl) paginationEl.innerHTML = '';
        reImpactTtoPagination.currentPage = 1;
        reImpactTtoPagination.totalRows = 0;
    }
    const fromSeasonEl = document.getElementById('from_season');
    const toSeasonEl = document.getElementById('to_season');
    
    const fromSeason = parseInt(fromSeasonEl?.value) || stored.from_season;
    const toSeason = parseInt(toSeasonEl?.value) || stored.to_season;
    const reImpactPercentToggle = document.getElementById('re-impact-percent-toggle');
    const showPercentages = reImpactPercentToggle?.checked === true;
    const valueField = showPercentages ? 'delta_re_pct' : 'delta_re';
    
    // Filter data to selected season range and expected analysis type rows.
    // This keeps the UI resilient if backend payloads include mixed RE-impact types.
    const matchesAnalysisType = (row) => {
        // Backend already filters by analysis_type and may omit the type column.
        if (row.type === undefined || row.type === null) return true;
        if (escenariosPorEstadoDeBases(analysisType)) return row.type === analysisType;
        if (analysisType === 'ibb') return row.type === 'ibb';
        if (analysisType === 'ph_position') return row.type === 'ph_position' || row.type === 'ph';
        if (analysisType === 'ph_pitcher') return row.type === 'ph_pitcher' || row.type === 'ph';
        if (analysisType === 'platoon_baseline') return row.type === 'platoon_baseline';
        if (analysisType === 'tto') return row.type === 'tto';
        // Los tres en carreras. Son los mismos escenarios que en Break-Even
        // Points; lo que cambia es la unidad, no el analisis.
        if (analysisType === 'steal_runs') return row.type === 'steal_runs';
        if (analysisType === 'running_runs') return row.type === 'running_runs';
        if (analysisType === 'tagging_runs') return row.type === 'tagging_runs';
        return true;
    };

    const filteredData = stored.all_data.filter(row =>
        row.season >= fromSeason && row.season <= toSeason && matchesAnalysisType(row)
    );
    
    // Compute weighted averages
    const aggregated = {};
    
    filteredData.forEach(row => {
        // Build key based on analysis type (some have platoon, some don't)
        let key;
        if (escenariosPorEstadoDeBases(analysisType)) {
            key = `${row.scenario}|${row.outs}`;
        } else if (analysisType === 'ibb') {
            key = `${row.scenario}|${row.outs}`;
        } else if (analysisType === 'ph_position' || analysisType === 'ph_pitcher') {
            key = `${row.scenario}|${row.outs}`;
        } else if (analysisType === 'platoon_baseline') {
            key = `${row.handedness_split}|${row.outs}`;
        } else if (analysisType === 'tto') {
            key = `${row.scenario}|${row.handedness_split}|${row.tto_pass}`;
        } else if (analysisType === 'steal_runs' || analysisType === 'running_runs'
                   || analysisType === 'tagging_runs') {
            // Misma forma que sac_bunt: un escenario por estado de outs.
            //
            // SIN ESTA RAMA la clave queda undefined, el pivote no agrega nada
            // y la tabla sale EN BLANCO -- sin ningun error, y con la API
            // devolviendo 200. Anadir el tipo al modelo, al filtro de filas y al
            // selector no basta: hay que darle su clave de pivote.
            key = `${row.scenario}|${row.outs}`;
        }
        
        if (!aggregated[key]) {
            aggregated[key] = { sum_weighted: 0, sum_n: 0, scenario: row.scenario, outs: row.outs, label: row.label };
            if (analysisType === 'platoon_baseline') {
                aggregated[key].handedness_split = row.handedness_split;
                aggregated[key].label = row.handedness_split;
            } else if (analysisType === 'tto') {
                aggregated[key].scenario = row.scenario;
                aggregated[key].label = row.label;
                aggregated[key].handedness_split = row.handedness_split;
                aggregated[key].tto_pass = row.tto_pass;
            }
        }
        
        let metricValue = row[valueField];
        if (
            (analysisType === 'ph_position' || analysisType === 'ph_pitcher') &&
            (metricValue === null || metricValue === undefined || isNaN(metricValue))
        ) {
            metricValue = row.delta_re;
        }
        if (metricValue !== null && metricValue !== undefined && !isNaN(metricValue)) {
            aggregated[key].sum_weighted += metricValue * row.n;
            aggregated[key].sum_n += row.n;
        }
    });
    
    // Compute final weighted averages
    const displayData = [];
    Object.values(aggregated).forEach(agg => {
        const row = {
            scenario: agg.scenario,
            label: agg.label,
            outs: agg.outs,
            delta_re: agg.sum_n > 0 ? agg.sum_weighted / agg.sum_n : null,
            n: agg.sum_n
        };
        if (analysisType === 'platoon_baseline') {
            row.handedness_split = agg.handedness_split;
            row.scenario = agg.handedness_split;
            row.label = agg.handedness_split;
        } else if (analysisType === 'tto') {
            row.scenario = agg.scenario;
            row.label = agg.label;
            row.handedness_split = agg.handedness_split;
            row.tto_pass = agg.tto_pass;
        }
        displayData.push(row);
    });
    
    // Update stored data
    stored.from_season = fromSeason;
    stored.to_season = toSeason;
    stored.display_mode = showPercentages ? 'percentage' : 'value';
    
    // Pivot display_data for CSV download (scenario-based rows with outs as columns)
    const pivotedData = [];
    if (analysisType === 'steal_runs' || analysisType === 'running_runs'
        || analysisType === 'tagging_runs') {
        // Los escenarios se DERIVAN de los datos, no van escritos aqui.
        //
        // Las otras ramas llevan su lista a mano ('100','010','110'...), que
        // funciona porque son conjuntos cerrados. Estos tres no lo son: los
        // escenarios de tag-up dependen del estado de corredores y los de
        // estirar el batazo de la direccion, asi que una lista fija se quedaria
        // corta en cuanto la vista devolviera uno mas.
        //
        // SIN ESTA RAMA pivotedData queda VACIA y la tabla se pinta sin filas:
        // sin error, con la API en 200 y el titulo correcto. Fue exactamente lo
        // que paso.
        // Los escenarios y su orden salen de BREAKEVEN_SCENARIOS, igual que en
        // la tabla de umbrales: asi las dos ensenan LAS MISMAS filas y en el
        // mismo orden. Derivarlos de los datos dejaba fuera los que aun no
        // tuvieran fila y el orden dependia de como llegara el parquet.
        const definidos = escenariosDeImpactoEnCarreras(analysisType);
        const vistos = definidos ? definidos.map(e => e.key) : [];
        if (!definidos) {
            displayData.forEach(d => { if (!vistos.includes(d.scenario)) vistos.push(d.scenario); });
        }
        vistos.forEach(scenario => {
            const outs0 = displayData.find(d => d.scenario === scenario && d.outs === 0);
            const outs1 = displayData.find(d => d.scenario === scenario && d.outs === 1);
            const outs2 = displayData.find(d => d.scenario === scenario && d.outs === 2);
            const definido = definidos ? definidos.find(e => e.key === scenario) : null;
            pivotedData.push({
                scenario: scenario,
                // El rombo del campo se dibuja con esto. Sin `runners` sale
                // apagado, con la tabla llena de numeros correctos.
                runners: definido ? definido.runners : undefined,
                // La CLAVE, no `label`: en tag-up el label es corto a proposito
                // y cuatro escenarios distintos se llaman igual --"Runner on 3B
                // tags up to score"-- porque el rombo los separa. Break-Even
                // ensena la clave con su sufijo ((3rd), (Loaded)...), y es la
                // referencia. Con el label suelto, la tabla de RE Impact salia
                // con cuatro filas de texto identico.
                label: definido ? definido.key
                       : (outs0?.label || outs1?.label || outs2?.label || scenario),
                outs_0: outs0 ? outs0.delta_re : null,
                outs_1: outs1 ? outs1.delta_re : null,
                outs_2: outs2 ? outs2.delta_re : null,
                outs_0_n: outs0 ? outs0.n : 0,
                outs_1_n: outs1 ? outs1.n : 0,
                outs_2_n: outs2 ? outs2.n : 0
            });
        });
    } else if (escenariosPorEstadoDeBases(analysisType)) {
        const scenarios = escenariosPorEstadoDeBases(analysisType);
        scenarios.forEach(scenario => {
            const outs0 = displayData.find(d => d.scenario === scenario && d.outs === 0);
            const outs1 = displayData.find(d => d.scenario === scenario && d.outs === 1);
            const label = outs0?.label || outs1?.label || '';  // Get label from data
            const row = { 
                scenario: scenario,
                label: label,
                outs_0: outs0 ? outs0.delta_re : null,
                outs_1: outs1 ? outs1.delta_re : null,
                outs_0_n: outs0 ? outs0.n : 0,
                outs_1_n: outs1 ? outs1.n : 0
            };
            pivotedData.push(row);
        });
    } else if (analysisType === 'ibb') {
        const scenarios = ['001', '010', '011'];
        scenarios.forEach(scenario => {
            const outs0 = displayData.find(d => d.scenario === scenario && d.outs === 0);
            const outs1 = displayData.find(d => d.scenario === scenario && d.outs === 1);
            const outs2 = displayData.find(d => d.scenario === scenario && d.outs === 2);
            const label = outs0?.label || outs1?.label || outs2?.label || '';  // Get label from data
            const row = { 
                scenario: scenario,
                label: label,
                outs_0: outs0 ? outs0.delta_re : null,
                outs_1: outs1 ? outs1.delta_re : null,
                outs_2: outs2 ? outs2.delta_re : null,
                outs_0_n: outs0 ? outs0.n : 0,
                outs_1_n: outs1 ? outs1.n : 0,
                outs_2_n: outs2 ? outs2.n : 0
            };
            pivotedData.push(row);
        });
    } else if (analysisType === 'ph_position') {
        const handednessOrder = ['RHP vs RHB', 'LHP vs LHB', 'RHP vs LHB', 'LHP vs RHB', 'Same', 'Platoon'];
        handednessOrder.forEach(matchup => {
            const row = {
                matchup,
                scenario: matchup,
                label: matchup,
                outs_0: null,
                outs_1: null,
                outs_2: null,
                outs_0_n: 0,
                outs_1_n: 0,
                outs_2_n: 0
            };

            for (let outs = 0; outs <= 2; outs++) {
                const match = displayData.find(d => d.scenario === matchup && d.outs === outs);
                row[`outs_${outs}`] = match ? match.delta_re : null;
                row[`outs_${outs}_n`] = match ? match.n : 0;
            }
            pivotedData.push(row);
        });
    } else if (analysisType === 'ph_pitcher') {
        const handednessOrder = ['RHP vs RHB', 'LHP vs LHB', 'RHP vs LHB', 'LHP vs RHB', 'Same', 'Platoon'];
        handednessOrder.forEach(matchup => {
            const row = {
                matchup,
                scenario: matchup,
                label: matchup,
                outs_0: null,
                outs_1: null,
                outs_2: null,
                outs_0_n: 0,
                outs_1_n: 0,
                outs_2_n: 0
            };

            for (let outs = 0; outs <= 2; outs++) {
                const match = displayData.find(d => d.scenario === matchup && d.outs === outs);
                row[`outs_${outs}`] = match ? match.delta_re : null;
                row[`outs_${outs}_n`] = match ? match.n : 0;
            }
            pivotedData.push(row);
        });
    } else if (analysisType === 'platoon_baseline') {
        const platoonRowOrder = ['RHP vs RHB', 'LHP vs LHB', 'RHP vs LHB', 'LHP vs RHB'];
        platoonRowOrder.forEach(split => {
            const row = {
                platoon_match: split,
                label: split,
                outs_0: null,
                outs_1: null,
                outs_2: null,
                weighted_mean_outs: null
            };

            let weightedSum = 0;
            let weightedN = 0;

            for (let outs = 0; outs <= 2; outs++) {
                const match = displayData.find(d => d.handedness_split === split && d.outs === outs);
                row[`outs_${outs}`] = match ? match.delta_re : null;
                row[`outs_${outs}_n`] = match ? match.n : 0;
                if (match && match.delta_re !== null && match.delta_re !== undefined && !isNaN(match.delta_re) && match.n > 0) {
                    weightedSum += match.delta_re * match.n;
                    weightedN += match.n;
                }
            }

            row.weighted_mean_outs = weightedN > 0 ? (weightedSum / weightedN) : null;
            row.weighted_mean_outs_n = weightedN;

            pivotedData.push(row);
        });

        const createSyntheticRow = (label, sourceSplits) => {
            const row = {
                platoon_match: label,
                label: label,
                outs_0: null,
                outs_1: null,
                outs_2: null,
                weighted_mean_outs: null
            };

            let weightedSumAllOuts = 0;
            let weightedNAllOuts = 0;

            for (let outs = 0; outs <= 2; outs++) {
                let weightedSum = 0;
                let weightedN = 0;

                sourceSplits.forEach(split => {
                    const match = displayData.find(d => d.handedness_split === split && d.outs === outs);
                    if (match && match.delta_re !== null && match.delta_re !== undefined && !isNaN(match.delta_re) && match.n > 0) {
                        weightedSum += match.delta_re * match.n;
                        weightedN += match.n;
                    }
                });

                row[`outs_${outs}`] = weightedN > 0 ? (weightedSum / weightedN) : null;
                row[`outs_${outs}_n`] = weightedN;

                if (weightedN > 0) {
                    weightedSumAllOuts += weightedSum;
                    weightedNAllOuts += weightedN;
                }
            }

            row.weighted_mean_outs = weightedNAllOuts > 0 ? (weightedSumAllOuts / weightedNAllOuts) : null;
            row.weighted_mean_outs_n = weightedNAllOuts;
            return row;
        };

        pivotedData.push(createSyntheticRow('Same', ['RHP vs RHB', 'LHP vs LHB']));
        pivotedData.push(createSyntheticRow('Platoon', ['RHP vs LHB', 'LHP vs RHB']));
    } else if (analysisType === 'tto') {
        const runnerOrder = ['000', '100', '010', '001', '110', '101', '011', '111'];
        const handednessOrder = ['Same', 'Platoon'];
        const ttoOrder = ['TTO-1', 'TTO-2', 'TTO-3'];
        const runnersLabels = {
            '000': 'Empty',
            '100': '1st',
            '010': '2nd',
            '001': '3rd',
            '110': '1st & 2nd',
            '101': '1st & 3rd',
            '011': '2nd & 3rd',
            '111': 'Loaded'
        };

        const normalizeHandedness = (value) => {
            const v = String(value || '').trim().toLowerCase();
            if (v === 'same' || v === 'same-handed' || v === 'same handed') return 'Same';
            if (v === 'platoon') return 'Platoon';
            return value;
        };
        const normalizeTtoPass = (value) => {
            const v = String(value || '').trim().toUpperCase();
            if (v === '1' || v === 'TTO1' || v === 'TTO-1') return 'TTO-1';
            if (v === '2' || v === 'TTO2' || v === 'TTO-2') return 'TTO-2';
            if (v === '3' || v === 'TTO3' || v === 'TTO-3') return 'TTO-3';
            if (v === '4+' || v === '4PLUS' || v === 'TTO4+' || v === 'TTO-4+' || v === 'TTO-4PLUS') return 'TTO-3';
            return value;
        };

        runnerOrder.forEach(runners => {
            handednessOrder.forEach(handedness => {
                const row = {
                    runners: runners,
                    scenario: runners,
                    label: runnersLabels[runners] || runners,
                    handedness: handedness,
                    handedness_split: handedness,
                    tto_1: null,
                    tto_2: null,
                    tto_3: null,
                    tto_2_n: 0,
                    tto_3_n: 0
                };

                ttoOrder.forEach(pass => {
                    const match = displayData.find(d =>
                        d.scenario === runners &&
                        normalizeHandedness(d.handedness_split) === handedness &&
                        normalizeTtoPass(d.tto_pass) === pass
                    );
                    if (pass === 'TTO-1') row.tto_1 = match ? match.delta_re : null;
                    if (pass === 'TTO-2') {
                        row.tto_2 = match ? match.delta_re : null;
                        row.tto_2_n = match ? match.n : 0;
                    }
                    if (pass === 'TTO-3') {
                        row.tto_3 = match ? match.delta_re : null;
                        row.tto_3_n = match ? match.n : 0;
                    }
                });

                pivotedData.push(row);
            });
        });
    }
    stored.display_data = pivotedData;
    
    // Get table elements
    const containerEl = document.getElementById('re-impact-table-container');
    const titleEl = document.getElementById('re-impact-table-title');
    const subtitleEl = document.getElementById('re-impact-table-subtitle');
    const headEl = document.getElementById('re-impact-table-head');
    const bodyEl = document.getElementById('re-impact-table-body');
    const eventCountEl = document.getElementById('re-impact-event-count');
    
    if (!containerEl || !titleEl || !headEl || !bodyEl) {
        console.error('Table elements not found', {containerEl, titleEl, headEl, bodyEl});
        return;
    }

    // Show standard table container
    containerEl.classList.remove('d-none');

    // Build title
    const analysisTypeLabel = {
        'sac_bunt': 'Sacrifice Bunt',
        'gidp': 'Double Play',
        'ibb': 'Intentional Walk',
        'ph_position': 'PH for Position Player',
        'ph_pitcher': 'PH for Pitchers',
        'platoon_baseline': 'Pitcher-Batter Matchup',
        'tto': 'Pitcher-Batter Times Faced',
        'steal_runs': 'Stolen Bases',
        'running_runs': 'Base Running',
        'tagging_runs': 'Tag-up'
    }[analysisType] || analysisType;
    const analysisTypeFooterLabel = {
        'sac_bunt': 'sacrifice bunt',
        'gidp': 'double play',
        'ibb': 'intentional walk',
        'ph_position': 'PH for position player',
        'ph_pitcher': 'PH for pitchers',
        'platoon_baseline': 'pitcher-batter matchup',
        'tto': 'pitcher-batter times faced',
        'steal_runs': 'stolen bases',
        'running_runs': 'base running',
        'tagging_runs': 'tag-up'
    }[analysisType] || analysisTypeLabel;
    
    let titleHTML = `RE Impact Analysis - <span class="primary-badge">${analysisTypeLabel}</span> - `;
    if (fromSeason === toSeason) {
        titleHTML += `<span class="primary-badge">${fromSeason} Season</span>`;
    } else {
        titleHTML += `<span class="primary-badge">${fromSeason}-${toSeason} Seasons</span>`;
    }
    titleEl.innerHTML = titleHTML;
    
    // Update subtitle
    if (subtitleEl) {
        const subtitles = {
            // A diferencia del resto de analisis de esta tabla, este valor NO es un
            // promedio observado: es la diferencia entre dos casillas de la matriz
            // RE24, condicionada a que el toque salga. De ahi "successful" y la
            // ausencia de "usually".
            'sac_bunt': 'How many runs your team loses when executing a successful sacrifice bunt.',
            'gidp': 'How many runs a double play costs compared with a comparable ground out that retires only one runner.',
            'ibb': 'How many runs your team usually gains or loses when choosing to intentionally walk a batter.',
            'ph_position': 'What pinch-hitting for a position player is usually worth, by handedness matchup and outs.',
            'ph_pitcher': 'What pinch-hitting for the pitcher is usually worth, by handedness matchup and outs.',
            'platoon_baseline': 'How many runs are typically gained or lost by pitcher-batter handedness matchup state.',
            'tto': 'How run scoring tends to change each time a specific batter faces the same pitcher again (1st, 2nd, and 3rd+ times faced).'
        };
        subtitleEl.textContent = subtitles[analysisType] || '';
    }
    
    // Update event count
    const totalEvents = displayData.reduce((sum, row) => sum + row.n, 0);
    const reliabilityStats = buildReImpactReliabilityStats(pivotedData, analysisType);
    setBreakevenFooterText(eventCountEl, `${totalEvents.toLocaleString()} ${analysisTypeFooterLabel} events processed`);
    
    // Build table based on analysis type
    if (escenariosPorEstadoDeBases(analysisType)) {
        renderReImpactSacBuntTable(headEl, bodyEl, pivotedData, stored.display_mode, reliabilityStats);
    } else if (analysisType === 'ibb') {
        renderReImpactIbbTable(headEl, bodyEl, pivotedData, stored.display_mode, reliabilityStats);
    } else if (analysisType === 'ph_position') {
        renderReImpactPhPositionTable(headEl, bodyEl, pivotedData, stored.display_mode, reliabilityStats);
    } else if (analysisType === 'ph_pitcher') {
        renderReImpactPhPositionTable(headEl, bodyEl, pivotedData, stored.display_mode, reliabilityStats);
    } else if (analysisType === 'platoon_baseline') {
        renderReImpactPlatoonMatchupTable(headEl, bodyEl, pivotedData, stored.display_mode, reliabilityStats);
    } else if (analysisType === 'tto') {
        renderReImpactTtoTable(headEl, bodyEl, pivotedData, stored.display_mode, reliabilityStats);
    } else if (analysisType === 'steal_runs' || analysisType === 'running_runs'
               || analysisType === 'tagging_runs') {
        // Misma forma que sac_bunt: escenario por filas, outs por columnas.
        //
        // ESTA ES LA CADENA QUE DE VERDAD PINTA. Sin rama aqui no se llama a
        // ninguna funcion de dibujado y el cuerpo de la tabla se queda vacio:
        // sin error, con la API en 200, el titulo correcto y las cabeceras
        // puestas. Fue el ultimo de los sitios que habia que tocar.
        renderReImpactSacBuntTable(headEl, bodyEl, pivotedData, stored.display_mode, reliabilityStats);
    }
    
    // Scroll to table
    setTimeout(() => {
        containerEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 100);
}

function computeQuantileFromSorted(sortedValues, q) {
    if (!Array.isArray(sortedValues) || sortedValues.length === 0) return 0;
    if (sortedValues.length === 1) return sortedValues[0];
    const pos = (sortedValues.length - 1) * Math.max(0, Math.min(1, q));
    const base = Math.floor(pos);
    const rest = pos - base;
    const next = sortedValues[Math.min(base + 1, sortedValues.length - 1)];
    return sortedValues[base] + rest * (next - sortedValues[base]);
}

const RELIABILITY_DEFAULT_CONFIG = {
    minPoints: 4,
    lowQuantile: 0.2,
    mediumQuantile: 0.4,
    useStrictEffectiveN: true,
    strictMinGoodN: 30,
    strictMinMediumN: 10,
    strictLambda: 0.2,
    strictAlpha: 2,
    strictGoodEffectiveN: 30,
    strictMediumEffectiveN: 10,
    mediumOpacity: '0.80',
    lowOpacity: '0.68',
    mediumFilter: 'saturate(82%) brightness(90%)',
    lowFilter: 'saturate(70%) brightness(82%)',
    markerMedium: '●',
    markerLow: '••'
};

const RELIABILITY_LEGEND_STYLE = 'font-size:0.78rem; color:#b0b0b0; white-space:nowrap; display:inline-block; line-height:1.2;';
const RELIABILITY_LEGEND_TEXT = 'Reliability notes: ● medium confidence estimate, •• low confidence estimate';

function getReliabilityLegendSpan(classNames = '') {
    const classAttr = classNames ? ` class="${classNames}"` : '';
    return `<span${classAttr} style="${RELIABILITY_LEGEND_STYLE}">${RELIABILITY_LEGEND_TEXT}</span>`;
}

function buildReliabilityStats(displayData, nExtractor, config = RELIABILITY_DEFAULT_CONFIG) {
    const nValues = [];
    (displayData || []).forEach(row => {
        const extracted = nExtractor ? nExtractor(row) : [];
        if (Array.isArray(extracted)) {
            nValues.push(...extracted);
        }
    });

    const valid = nValues.filter(v => Number.isFinite(v) && v > 0);
    if (valid.length < config.minPoints) {
        return { enabled: false, sorted: [], mediumThreshold: 0, lowThreshold: 0 };
    }

    const sorted = valid.slice().sort((a, b) => a - b);
    const lowThreshold = computeQuantileFromSorted(sorted, config.lowQuantile);
    const mediumThreshold = computeQuantileFromSorted(sorted, config.mediumQuantile);

    return {
        enabled: true,
        sorted,
        mediumThreshold,
        lowThreshold
    };
}

function getOneSidedWeight(percentile, config = RELIABILITY_DEFAULT_CONFIG) {
    const p = Math.max(0, Math.min(1, Number(percentile) || 0));
    const lambda = Math.max(0, Math.min(1, Number(config.strictLambda) || 0));
    const alpha = Math.max(0.25, Number(config.strictAlpha) || 1);
    return lambda + (1 - lambda) * Math.pow(p, alpha);
}

function marcadorFiabilidadHtml(markerText) {
    // Un punto por nivel: uno = confianza media, dos = baja. Se dibujan con CSS
    // (.reliability-marker) y NO con los caracteres U+25CF / U+2022, que
    // dependen de que la tipografia tenga el glifo -- cuando no lo tiene salen
    // rombos blancos, que es lo que se veia.
    if (!markerText) return '';
    const puntos = markerText.length >= 2 ? 2 : 1;
    return `<span class="reliability-marker">${'<span></span>'.repeat(puntos)}</span>`;
}

function getReliabilityMeta(nValue, stats, config = RELIABILITY_DEFAULT_CONFIG) {
    if (!stats || !stats.enabled || !Number.isFinite(nValue) || nValue <= 0) {
        return { level: 'none', markerText: '', style: '', title: '' };
    }

    let level = 'none';
    const countLess = stats.sorted.filter(v => v < nValue).length;
    const countEqual = stats.sorted.filter(v => v === nValue).length;
    // Mid-rank percentile avoids hard 0/1 endpoints when values tie heavily.
    const percentile = (countLess + 0.5 * countEqual) / stats.sorted.length;
    const rank = Math.round(percentile * 100);

    if (config.useStrictEffectiveN) {
        const effectiveN = nValue * getOneSidedWeight(percentile, config);

        if (nValue < config.strictMinMediumN || effectiveN < config.strictMediumEffectiveN) {
            level = 'low';
        } else if (nValue < config.strictMinGoodN || effectiveN < config.strictGoodEffectiveN) {
            level = 'medium';
        } else {
            level = 'none';
        }

        if (level === 'none') {
            return {
                level,
                markerText: '',
                style: '',
                title: `n = ${Math.round(nValue).toLocaleString()} | ${rank}th percentile`
            };
        }

        const isLow = level === 'low';
        const markerText = isLow ? config.markerLow : config.markerMedium;
        const opacity = isLow ? config.lowOpacity : config.mediumOpacity;
        const filter = isLow ? config.lowFilter : config.mediumFilter;

        return {
            level,
            markerText,
            style: `box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.28); opacity:${opacity}; filter:${filter};`,
            title: `n = ${Math.round(nValue).toLocaleString()} | ${rank}th percentile`
        };
    }

    if (level === 'none') {
        return {
            level,
            markerText: '',
            style: '',
            title: `n = ${Math.round(nValue).toLocaleString()} | ${rank}th percentile in this table`
        };
    }

    const isLow = level === 'low';
    const markerText = isLow ? config.markerLow : config.markerMedium;
    const opacity = isLow ? config.lowOpacity : config.mediumOpacity;
    const filter = isLow ? config.lowFilter : config.mediumFilter;

    return {
        level,
        markerText,
        style: `box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.28); opacity:${opacity}; filter:${filter};`,
        title: `n = ${Math.round(nValue).toLocaleString()} | ${rank}th percentile in this table`
    };
}

function renderMetricCellWithReliability(value, nValue, {
    formatValue,
    heatmapEnabled,
    hMin,
    hMax,
    reverse = false,
    getColorFn = getHeatmapColor,
    reliabilityStats,
    reliabilityConfig = RELIABILITY_DEFAULT_CONFIG
}) {
    if (value === null || value === undefined || isNaN(value)) return 'N/A';

    const displayValue = formatValue ? formatValue(value) : String(value);
    const reliability = getReliabilityMeta(nValue, reliabilityStats, reliabilityConfig);

    let badgeStyle = 'padding: 2px 6px; border-radius: 3px; display: inline-block; min-width: 60px;';
    let textColor = '';
    let bgColor = '';
    let extreme = false;

    if (heatmapEnabled && Number.isFinite(hMin) && Number.isFinite(hMax) && hMin !== hMax) {
        const heat = getColorFn(value, hMin, hMax, reverse);
        bgColor = heat.bgColor;
        textColor = heat.textColor;
        extreme = heat.isExtreme;
    }

    if (!bgColor && reliability.level === 'none') {
        return displayValue;
    }

    if (bgColor) badgeStyle += ` background-color: ${bgColor}; color: ${textColor};`;
    if (reliability.style) badgeStyle += ` ${reliability.style}`;
    if (extreme) {
        badgeStyle += ' box-shadow: 0 0 0 2px white, inset 0 0 0 1px rgba(0, 0, 0, 0.28);';
    }

    const titleAttr = reliability.title ? ` title="${reliability.title}"` : '';
    const markerColor = bgColor ? textColor : 'currentColor';
    const markerHtml = reliability.markerText
        ? marcadorFiabilidadHtml(reliability.markerText)
        : '';
    return `<span${titleAttr} style="${badgeStyle}">${displayValue}${markerHtml}</span>`;
}

function buildReImpactReliabilityStats(displayData, analysisType) {
    return buildReliabilityStats(displayData, (row) => {
        if (escenariosPorEstadoDeBases(analysisType)) {
            return [row.outs_0_n, row.outs_1_n];
        }
        if (analysisType === 'ibb') {
            return [row.outs_0_n, row.outs_1_n, row.outs_2_n];
        }
        if (analysisType === 'ph_position' || analysisType === 'ph_pitcher') {
            return [row.outs_0_n, row.outs_1_n, row.outs_2_n];
        }
        if (analysisType === 'platoon_baseline') {
            return [row.outs_0_n, row.outs_1_n, row.outs_2_n, row.weighted_mean_outs_n];
        }
        if (analysisType === 'tto') {
            return [row.tto_2_n, row.tto_3_n];
        }
        return [];
    }, RELIABILITY_DEFAULT_CONFIG);
}

function getReImpactReliabilityMeta(nValue, stats) {
    return getReliabilityMeta(nValue, stats, RELIABILITY_DEFAULT_CONFIG);
}

function buildBreakevenReliabilityStats(displayData, nFieldKeys) {
    return buildReliabilityStats(displayData, (row) => {
        return (nFieldKeys || []).map(key => row[key]);
    }, RELIABILITY_DEFAULT_CONFIG);
}

// Confundidores conocidos que el numero publicado no refleja. Se muestran junto
// a la tabla porque son propiedades del diseño de la medida, no ruido muestral:
// mas datos no los hace desaparecer, asi que el marcador de fiabilidad no basta.
const METHOD_NOTES = {
    'swing-take': 'Take value is measured only on pitches the batter chose not to swing at, '
        + 'which skew toward balls. That raises the take side and with it the break-even.',
    'swing-take-zone': 'Take value is measured only on pitches the batter chose not to swing at. '
        + 'Splitting by zone reduces that skew but does not remove it.',
    sb_by_count: 'The count is not independent of the matchup — it already carries information '
        + 'about the batter and the pitcher. Part of the gap between counts is that, not the count itself.',
    'bunt-for-hit': 'The alternative is measured from these same batters’ own non-bunt plate '
        + 'appearances, not the league average. What it cannot capture is why they bunted on that '
        + 'pitch — a drawn-back third baseman does not show up in the record.',
    steal_runs: 'Same three run-expectancy states as the break-even table, expressed in runs '
        + 'instead of as a threshold. The net uses the observed success rate for that base-out '
        + 'state, so it answers what attempting was actually worth — not what rate it would take.',
    running_runs: 'Success is not recorded as such: a single stretched into a double is scored '
        + 'as a double. The three outcomes are reconstructed from the runner destination, so the '
        + 'same caveats as the break-even table apply here.',
    tagging_runs: 'Runners tag up when they are fairly sure, so failures are rare and the cost '
        + 'side rests on a smaller sample than the gain side.'
};

const METHOD_NOTE_STYLE = 'font-size:0.78rem; color:#b0b0b0; display:block; line-height:1.25; '
    + 'max-width:70ch; margin:0 auto 2px;';

function getMethodNoteSpan(noteKey) {
    const note = METHOD_NOTES[noteKey];
    return note ? `<span style="${METHOD_NOTE_STYLE}">Note: ${note}</span>` : '';
}

// Cabecera de las tablas de Win Expectancy, WPA y Leverage Index.
//
// Estas tres tablas mezclan dos equipos: el +/- de las columnas es la ventaja
// del equipo que BATEA (el del selector Away/Home, por eso las filas dicen "Top"
// o "Bottom"), mientras que el valor de la celda es siempre del equipo de casa.
// Con Away seleccionado los dos apuntan en direcciones opuestas: mas ventaja del
// away significa menos probabilidad de que gane el home.
//
// Antes eso se explicaba con una segunda linea de subtitulo que repetia que "+"
// es ir ganando -- lo unico obvio de todo el asunto -- y nunca decia de quien
// era ese "+". Ahora el equipo se nombra encima de las propias columnas.
//
// Las tres primeras columnas (Inning, Outs, Runners) describen el estado de
// juego y no cuelgan del equipo, asi que van con rowspan.
const RUN_DIFF_FIXED_COLUMNS = 3;

// Leverage medio de la seleccion, ponderado por ocurrencia.
//
// Es la misma ponderacion que ya usa la tabla de break-even al agregar
// temporadas: sum(valor * n) / sum(n). Cada fila trae tanto el LI como su `n`
// (los `<header>_n` que alimentan los marcadores de fiabilidad), asi que no hace
// falta pedir nada al servidor.
//
// Se calcula sobre data.rows, que son TODAS las filas de la seleccion: la
// paginacion solo recorta al pintar, y el promedio de una pagina suelta no
// significaria nada.
function computeWeightedLeverage(data) {
    const valueHeaders = data.headers.slice(RUN_DIFF_FIXED_COLUMNS);
    let weighted = 0;
    let total = 0;

    data.rows.forEach(row => {
        valueHeaders.forEach(header => {
            const value = row[header];
            const n = row[`${header}_n`];
            if (Number.isFinite(value) && Number.isFinite(n) && n > 0) {
                weighted += value * n;
                total += n;
            }
        });
    });

    return total > 0 ? weighted / total : null;
}

function buildRunDiffHeaderHTML(headers, teamName) {
    const fixed = headers.slice(0, RUN_DIFF_FIXED_COLUMNS);
    const runDiff = headers.slice(RUN_DIFF_FIXED_COLUMNS);

    if (runDiff.length === 0) {
        return `<tr>${headers.map(h => `<th class="bg-primary text-center align-middle">${h}</th>`).join('')}</tr>`;
    }

    const fixedCells = fixed
        .map(h => `<th class="bg-primary text-center align-middle" rowspan="2">${h}</th>`)
        .join('');
    const groupCell = `<th class="bg-primary text-center align-middle" colspan="${runDiff.length}">`
        + `${teamName} team's lead</th>`;
    const runDiffCells = runDiff
        .map(h => `<th class="bg-primary text-center align-middle">${h}</th>`)
        .join('');

    return `<tr>${fixedCells}${groupCell}</tr><tr>${runDiffCells}</tr>`;
}

function setBreakevenFooterText(eventCountEl, countText, noteKey) {
    if (!eventCountEl) return;
    const prefix = countText ? `${countText}<br>` : '';
    eventCountEl.innerHTML = `${prefix}${getMethodNoteSpan(noteKey)}${getReliabilityLegendSpan()}`;
}

function getBreakevenFooterText(selectedType, totalN) {
    const formattedTotal = Number(totalN || 0).toLocaleString();
    const labelByType = {
        stealing: 'stolen base opportunities',
        running: 'extra-base attempt opportunities',
        tagging: 'tag-up opportunities',
        'bunt-for-hit': 'bunt attempts',
        'swing-take': 'swing/take base-out-count situations',
        sb_by_count: 'stolen base count-state opportunities',
        'swing-take-zone': 'zone/count pitch observations'
    };
    return `${formattedTotal} ${labelByType[selectedType] || 'events processed'}`;
}

function setReliabilityLegendOnly(eventCountEl) {
    if (!eventCountEl) return;
    eventCountEl.innerHTML = getReliabilityLegendSpan();
}

function appendReliabilityMarker(cellHtml, nValue, reliabilityStats) {
    const reliability = getReliabilityMeta(nValue, reliabilityStats, RELIABILITY_DEFAULT_CONFIG);
    if (!reliability.markerText && !reliability.style && !reliability.title) {
        return cellHtml;
    }

    const titleAttr = reliability.title ? ` title="${reliability.title}"` : '';
    const markerHtml = reliability.markerText
        ? marcadorFiabilidadHtml(reliability.markerText)
        : '';
    const wrapperStyle = `display:inline-flex; align-items:center;${reliability.style ? ` ${reliability.style}` : ''}`;
    return `<span${titleAttr} style="${wrapperStyle}">${cellHtml}${markerHtml}</span>`;
}

function appendReliabilityMarkerInsideBadge(badgeHtml, nValue, reliabilityStats) {
    const reliability = getReliabilityMeta(nValue, reliabilityStats, RELIABILITY_DEFAULT_CONFIG);
    if (!reliability.markerText && !reliability.style && !reliability.title) {
        return badgeHtml;
    }

    const markerHtml = reliability.markerText
        ? marcadorFiabilidadHtml(reliability.markerText)
        : '';

    let updated = badgeHtml;

    if (reliability.title) {
        updated = updated.replace('<span ', `<span title="${reliability.title}" `);
    }

    if (reliability.style) {
        updated = updated.replace(/style="([^"]*)"/, (_m, styleValue) => `style="${styleValue} ${reliability.style}"`);
    }

    if (markerHtml) {
        updated = updated.replace('</span>', `${markerHtml}</span>`);
    }

    updated = updated.replace(/font-size:\s*0\.66rem;/g, 'font-size:0.60rem;');

    return updated;
}

function renderBreakevenReliabilityBadge(labelHtml, baseStyle, nValue, reliabilityStats) {
    const reliability = getReliabilityMeta(nValue, reliabilityStats, RELIABILITY_DEFAULT_CONFIG);
    const titleAttr = reliability.title ? ` title="${reliability.title}"` : '';
    const markerHtml = reliability.markerText
        ? marcadorFiabilidadHtml(reliability.markerText)
        : '';
    const style = `${baseStyle}${reliability.style ? ` ${reliability.style}` : ''}`;
    return `<span${titleAttr} style="${style}">${labelHtml}${markerHtml}</span>`;
}

function renderBreakevenPercentCell(value, nValue, {
    heatmapEnabled,
    hMin,
    hMax,
    reverse = BREAKEVEN_HEATMAP_REVERSE,
    getColorFn = getHeatmapColor,
    reliabilityStats,
    minWidth = '50px',
    decimals = 2
}) {
    return renderMetricCellWithReliability(value, nValue, {
        formatValue: (v) => `${Number(v).toFixed(decimals)}%`,
        heatmapEnabled,
        hMin,
        hMax,
        reverse,
        getColorFn,
        reliabilityStats,
        reliabilityConfig: RELIABILITY_DEFAULT_CONFIG
    }).replace('min-width: 60px;', `min-width: ${minWidth};`);
}

function renderReImpactMetricCell(value, nValue, {
    isPercentageMode,
    heatmapEnabled,
    hMin,
    hMax,
    reverse = false,
    reliabilityStats
}) {
    return renderMetricCellWithReliability(value, nValue, {
        formatValue: (v) => isPercentageMode ? `${(v * 100).toFixed(2)}%` : v.toFixed(3),
        heatmapEnabled,
        hMin,
        hMax,
        reverse,
        getColorFn: getHeatmapColor,
        reliabilityStats,
        reliabilityConfig: RELIABILITY_DEFAULT_CONFIG
    });
}

function renderReImpactSacBuntTable(headEl, bodyEl, displayData, displayMode = 'value', reliabilityStats = null) {
    // Simple table: 3 scenarios × 2 outs (0 and 1)
    // displayData is already pivoted with structure: {scenario, outs_0, outs_1}
    
    // Check if heatmap is enabled
    const heatmapEnabled = globalHeatmapEnabled;
    
    // Calculate min/max for heatmap
    let allValues = [];
    displayData.forEach(row => {
        [row.outs_0, row.outs_1].forEach(v => {
            if (v !== null && v !== undefined && !isNaN(v)) allValues.push(v);
        });
    });
    const hMin = allValues.length > 0 ? Math.min(...allValues) : 0;
    const hMax = allValues.length > 0 ? Math.max(...allValues) : 1;
    
    const isPercentageMode = displayMode === 'percentage';

    const renderCell = (value, nValue) => renderReImpactMetricCell(value, nValue, {
        isPercentageMode,
        heatmapEnabled,
        hMin,
        hMax,
        // reverse=true: aqui el valor es lo que el toque de sacrificio CUESTA,
        // asi que todos son negativos y el peor es el mas negativo. Sin reverse
        // la escala pintaba de rojo el menor coste, justo al reves de como se lee
        // el resto de tablas. El renderizador antiguo de esta misma tabla ya lo
        // tenia bien; al rehacerlo dentro de RE Impact no se traslado.
        reverse: RE_IMPACT_HEATMAP_REVERSE,
        reliabilityStats
    });
    
    // Build header
    headEl.innerHTML = `
        <tr>
            <th class="bg-primary text-start align-middle fs-5">Situation</th>
            <th class="bg-primary text-center align-middle fs-5">0 Outs</th>
            <th class="bg-primary text-center align-middle fs-5">1 Out</th>
        </tr>
    `;
    
    // Build body - use string serialization like RE24 tables
    const rows = displayData.map(row => {
        // `runners` si viene, y si no el escenario.
        //
        // En sac_bunt e IBB el escenario YA es el estado de bases, asi que
        // servia. Los tres analisis en carreras llevan el texto --para poder
        // cruzarlos con la tabla de break-even-- y su estado llega en `runners`,
        // puesto desde BREAKEVEN_SCENARIOS. Sin esto el rombo sale apagado con
        // la tabla llena de numeros correctos.
        const svgString = getBaseStateSVGMarkup(row.runners || row.scenario);
        
        return `
            <tr>
                <td class="text-start align-middle">${svgString} ${row.label || ''}</td>
                <td class="text-center">${renderCell(row.outs_0, row.outs_0_n)}</td>
                <td class="text-center">${renderCell(row.outs_1, row.outs_1_n)}</td>
            </tr>
        `;
    });
    
    bodyEl.innerHTML = rows.join('');
}

function renderReImpactIbbTable(headEl, bodyEl, displayData, displayMode = 'value', reliabilityStats = null) {
    // 3 scenarios × 3 outs table
    // displayData is already pivoted with structure: {scenario, outs_0, outs_1, outs_2}
    
    // Check if heatmap is enabled
    const heatmapEnabled = globalHeatmapEnabled;
    
    // Calculate min/max for heatmap
    let allValues = [];
    displayData.forEach(row => {
        [row.outs_0, row.outs_1, row.outs_2].forEach(v => {
            if (v !== null && v !== undefined && !isNaN(v)) allValues.push(v);
        });
    });
    const hMin = allValues.length > 0 ? Math.min(...allValues) : 0;
    const hMax = allValues.length > 0 ? Math.max(...allValues) : 1;
    
    const isPercentageMode = displayMode === 'percentage';

    const renderCell = (value, nValue) => renderReImpactMetricCell(value, nValue, {
        isPercentageMode,
        heatmapEnabled,
        hMin,
        hMax,
        reverse: RE_IMPACT_HEATMAP_REVERSE,
        reliabilityStats
    });
    
    // Build header
    headEl.innerHTML = `
        <tr>
            <th class="bg-primary text-start align-middle fs-5">Situation</th>
            <th class="bg-primary text-center align-middle fs-5">0 Outs</th>
            <th class="bg-primary text-center align-middle fs-5">1 Out</th>
            <th class="bg-primary text-center align-middle fs-5">2 Outs</th>
        </tr>
    `;
    
    // Build body - use string serialization like RE24 tables
    bodyEl.innerHTML = displayData.map(row => {
        const svgString = getBaseStateSVGMarkup(row.runners || row.scenario);
        
        return `
            <tr>
                <td class="text-start align-middle">${svgString} ${row.label || ''}</td>
                <td class="text-center">${renderCell(row.outs_0, row.outs_0_n)}</td>
                <td class="text-center">${renderCell(row.outs_1, row.outs_1_n)}</td>
                <td class="text-center">${renderCell(row.outs_2, row.outs_2_n)}</td>
            </tr>
        `;
    }).join('');
}

function renderReImpactPhPositionTable(headEl, bodyEl, displayData, displayMode = 'value', reliabilityStats = null) {
    // Handedness matchup rows x 3 outs columns
    const heatmapEnabled = globalHeatmapEnabled;

    let allValues = [];
    displayData.forEach(row => {
        [row.outs_0, row.outs_1, row.outs_2].forEach(v => {
            if (v !== null && v !== undefined && !isNaN(v)) allValues.push(v);
        });
    });
    const hMin = allValues.length > 0 ? Math.min(...allValues) : 0;
    const hMax = allValues.length > 0 ? Math.max(...allValues) : 1;

    const isPercentageMode = displayMode === 'percentage';
    const renderCell = (value, nValue) => renderReImpactMetricCell(value, nValue, {
        isPercentageMode,
        heatmapEnabled,
        hMin,
        hMax,
        reverse: RE_IMPACT_HEATMAP_REVERSE,
        reliabilityStats
    });

    headEl.innerHTML = `
        <tr>
            <th class="bg-primary text-start align-middle fs-5">Pitcher-Batter Matchup</th>
            <th class="bg-primary text-center align-middle fs-5">0 Outs</th>
            <th class="bg-primary text-center align-middle fs-5">1 Out</th>
            <th class="bg-primary text-center align-middle fs-5">2 Outs</th>
        </tr>
    `;

    bodyEl.innerHTML = displayData.map(row => `
        <tr>
            <td class="text-start align-middle">${row.matchup || row.label || row.scenario || ''}</td>
            <td class="text-center">${renderCell(row.outs_0, row.outs_0_n)}</td>
            <td class="text-center">${renderCell(row.outs_1, row.outs_1_n)}</td>
            <td class="text-center">${renderCell(row.outs_2, row.outs_2_n)}</td>
        </tr>
    `).join('');
}

function renderReImpactPhTable(headEl, bodyEl, displayData, displayMode = 'value', reliabilityStats = null) {
    // 8 scenarios × 3 outs × 2 platoon columns
    // displayData is already pivoted with structure: {scenario, outs_0_platoon, outs_0_same, outs_1_platoon, outs_1_same, outs_2_platoon, outs_2_same}
    
    // Check if heatmap is enabled
    const heatmapEnabled = globalHeatmapEnabled;
    
    // Calculate min/max for heatmap
    let allValues = [];
    displayData.forEach(row => {
        [row.outs_0_platoon, row.outs_0_same, row.outs_1_platoon, row.outs_1_same, row.outs_2_platoon, row.outs_2_same].forEach(v => {
            if (v !== null && v !== undefined && !isNaN(v)) allValues.push(v);
        });
    });
    const hMin = allValues.length > 0 ? Math.min(...allValues) : 0;
    const hMax = allValues.length > 0 ? Math.max(...allValues) : 1;
    
    const isPercentageMode = displayMode === 'percentage';

    const renderCell = (value, nValue) => renderReImpactMetricCell(value, nValue, {
        isPercentageMode,
        heatmapEnabled,
        hMin,
        hMax,
        reverse: RE_IMPACT_HEATMAP_REVERSE,
        reliabilityStats
    });
    
    // Build header with platoon advantage colspan
    headEl.innerHTML = `
        <tr>
            <th class="bg-primary text-start align-middle fs-5" rowspan="2">Situation</th>
            <th class="bg-primary text-center align-middle fs-5" colspan="2">0 Outs</th>
            <th class="bg-primary text-center align-middle fs-5" colspan="2">1 Out</th>
            <th class="bg-primary text-center align-middle fs-5" colspan="2">2 Outs</th>
        </tr>
        <tr>
            <th class="bg-primary text-center align-middle platoon-header">Platoon</th>
            <th class="bg-primary text-center align-middle same-handed-header">Same</th>
            <th class="bg-primary text-center align-middle platoon-header">Platoon</th>
            <th class="bg-primary text-center align-middle same-handed-header">Same</th>
            <th class="bg-primary text-center align-middle platoon-header">Platoon</th>
            <th class="bg-primary text-center align-middle same-handed-header">Same</th>
        </tr>
    `;
    
    // Build body - use string serialization like RE24 tables
    bodyEl.innerHTML = displayData.map(row => {
        const svgString = getBaseStateSVGMarkup(row.runners || row.scenario);
        
        return `
            <tr>
                <td class="text-start align-middle">${svgString} ${row.label || ''}</td>
                <td class="text-center">${renderCell(row.outs_0_platoon, row.outs_0_platoon_n)}</td>
                <td class="text-center">${renderCell(row.outs_0_same, row.outs_0_same_n)}</td>
                <td class="text-center">${renderCell(row.outs_1_platoon, row.outs_1_platoon_n)}</td>
                <td class="text-center">${renderCell(row.outs_1_same, row.outs_1_same_n)}</td>
                <td class="text-center">${renderCell(row.outs_2_platoon, row.outs_2_platoon_n)}</td>
                <td class="text-center">${renderCell(row.outs_2_same, row.outs_2_same_n)}</td>
            </tr>
        `;
    }).join('');
}

function renderRE24TransitionsTable(data) {
    // Store data in current table data for downloads
    currentTableData.re24_transitions = data;
    
    // Get elements (RE24 uses different IDs than other tables)
    const containerEl = document.getElementById('re24-table-container');
    const titleEl = document.getElementById('table-title');
    const headEl = document.getElementById('table-head');
    const bodyEl = document.getElementById('table-body');
    
    if (!containerEl || !titleEl || !headEl || !bodyEl) {
        console.error('RE24 Transitions table elements not found');
        return;
    }
    
    // Update title with badges
    let titleHTML = `RE24 Transitions - `;
    
    // Season badge
    if (data.from_season === data.to_season) {
        titleHTML += `<span class="primary-badge">${data.from_season} Season</span> `;
    } else {
        titleHTML += `<span class="primary-badge">${data.from_season}-${data.to_season} Seasons</span> `;
    }
    
    // Event badge
    if (data.event_name) {
        titleHTML += `<span class="primary-badge">${data.event_name}</span> `;
    }
    
    // Value type badge
    titleHTML += `<span class="primary-badge">${data.value_type_name}</span> `;
    
    // Outs badge
    titleHTML += `<span class="primary-badge">${data.from_outs} Out${data.from_outs === 1 ? '' : 's'}</span>`;
    
    titleEl.innerHTML = titleHTML;
    
    // Update subtitle based on value type
    const subtitleEl = document.getElementById('table-title-subtitle');
    if (subtitleEl) {
        if (data.value_type === 2) {
            subtitleEl.textContent = 'What are the chances of ending up in each base-out situation after this event?';
        } else if (data.value_type === 3) {
            subtitleEl.textContent = 'When you weight by how often it happens, how does run value change by transition?';
        } else {
            // For Run Expectancy mode: check if All Events or specific event
            if (data.event_cd === -1) {
                subtitleEl.textContent = 'How many runs does each base-out transition add or cost?';
            } else {
                subtitleEl.textContent = 'If this play happens now, how many runs does it add or take away for the rest of the inning?';
            }
        }
    }
    
    // Check if heatmap is enabled
    const heatmapToggle = document.getElementById('re24-heatmap-toggle');
    const heatmapEnabled = heatmapToggle?.checked || false;
    
    // Get all numeric values for heatmap min/max
    let allValues = [];
    if (heatmapEnabled) {
        data.rows.forEach(row => {
            data.to_state_columns.forEach(col => {
                const value = row[col];
                if (value !== null && value !== undefined && !isNaN(value)) {
                    allValues.push(parseFloat(value));
                }
            });
        });
    }
    const minValue = allValues.length > 0 ? Math.min(...allValues) : 0;
    const maxValue = allValues.length > 0 ? Math.max(...allValues) : 1;
    const reliabilityStats = buildBreakevenReliabilityStats(
        data.rows,
        data.to_state_columns.map(col => `${col}_n`)
    );
    
    // Choose display headers for UI (keep raw headers intact for downloads)
    const headerLabels = Array.isArray(data.headers_display) ? data.headers_display : data.headers;

    // Build table headers - replace only first space with line break and add min-width
    // Make first two columns sticky for horizontal scroll (SVG + From State)
    const headerRow = ['<th class="bg-primary text-center align-middle small" style="position: sticky; left: 0; z-index: 10; background-color: #0d6efd;"></th>'].concat(
        headerLabels.map((h, idx) => {
            // For non-first columns, parse the header to extract runner state and outs
            let displayText = h;
            if (idx > 0) {
                // Header format is like "--- 0" or "12- 1" (no "outs" text anymore)
                const parts = h.split(' ');
                if (parts.length >= 2) {
                    const runnerState = parts[0];
                    const outsNum = parts[1];
                    const outsLabel = outsNum === '1' ? 'out' : 'outs';
                    displayText = `${formatRunnerState(runnerState)}<br>${outsNum} ${outsLabel}`;
                } else {
                    displayText = h.replace(' ', '<br>');
                }
            } else {
                displayText = h.replace(' ', '<br>');
            }
            
            if (idx === 0) {
                return `<th class="bg-primary text-center align-middle small" style="min-width: 110px; position: sticky; left: 50px; z-index: 10; background-color: #0d6efd;">${displayText}</th>`;
            }
            return `<th class="bg-primary text-center align-middle small" style="min-width: 110px;">${displayText}</th>`;
        })
    ).join('');
    headEl.innerHTML = `<tr>${headerRow}</tr>`;
    
    // Build table body
    const rows = data.rows.map(row => {
        const cells = [];
        
        // Convert from_runners format (e.g., "12-", "-2-", "---") to binary format (e.g., "110", "010", "000")
        const runnersBinary = convertRunnersToBinary(row.from_runners);
        
        // SVG column - sticky for horizontal scroll
        const svgString = getBaseStateSVGMarkup(runnersBinary);
        cells.push(`<td class="sticky-col text-end align-middle svg-cell" style="position: sticky; left: 0; z-index: 5; background-color: #212529; box-shadow: 2px 0 5px rgba(0,0,0,0.1);">${svgString}</td>`);
        
        // From State column - sticky for horizontal scroll
        const formattedFromState = formatRunnerState(row.from_runners);
        const outsLabel = data.from_outs === 1 ? 'out' : 'outs';
        const fromStateWithOuts = `${formattedFromState}<br><span style="color: #888; font-size: 0.85em;">${data.from_outs} ${outsLabel}</span>`;
        cells.push(`<td class="text-center align-middle" style="min-width: 110px; position: sticky; left: 50px; z-index: 5; background-color: #212529; box-shadow: 2px 0 5px rgba(0,0,0,0.1);">${fromStateWithOuts}</td>`);
        
        // To State columns
        data.to_state_columns.forEach(col => {
            const value = row[col];
            const nValue = row[`${col}_n`];
            
            if (value !== null && value !== undefined && !isNaN(value)) {
                const cellHtml = renderMetricCellWithReliability(value, nValue, {
                    formatValue: (v) => data.value_type === 2
                        ? `${(Number(v) * 100).toFixed(2)}%`
                        : Number(v).toFixed(3),
                    heatmapEnabled,
                    hMin: minValue,
                    hMax: maxValue,
                    reliabilityStats,
                    reliabilityConfig: RELIABILITY_DEFAULT_CONFIG
                });
                cells.push(`<td class="text-center align-middle" style="min-width: 110px;">${cellHtml}</td>`);
            } else {
                cells.push(`<td class="text-center align-middle" style="min-width: 110px;">—</td>`);
            }
        });
        
        return `<tr>${cells.join('')}</tr>`;
    }).join('');
    
    bodyEl.innerHTML = rows;
    
    // Hide event count footer for RE24 Transitions
    const eventCountDiv = document.getElementById('re24-event-count');
    if (eventCountDiv) {
        setReliabilityLegendOnly(eventCountDiv);
    }
    
    // Show container
    containerEl.classList.remove('d-none');
}

const HANDEDNESS_ORDER = ['RHP vs RHB', 'LHP vs RHB', 'RHP vs LHB', 'LHP vs LHB'];

function parseSwingTakeZoneScenario(scenarioText) {
    if (typeof scenarioText !== 'string') return null;
    const match = scenarioText.match(/^Count\s+([0-3]-[0-2])\s+\|\s+Zone\s+(\d+)\s+\|\s+(.+)$/);
    if (!match) return null;
    return {
        count: match[1],
        zone: parseInt(match[2], 10),
        handedness: match[3].trim()
    };
}

function createStrikeZoneMapSVG(activeZone) {
    const active = Number(activeZone);
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('width', '112');
    svg.setAttribute('height', '112');
    svg.setAttribute('viewBox', '0 0 120 120');
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    svg.style.display = 'block';

    const background = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    background.setAttribute('x', '0');
    background.setAttribute('y', '0');
    background.setAttribute('width', '120');
    background.setAttribute('height', '120');
    background.setAttribute('fill', '#ffffff');
    svg.appendChild(background);

    // MLB layout: 1-9 in center grid, and 11/12/13/14 as corner L-shaped regions.
    const outerX = 12;
    const outerY = 8;
    const outerW = 96;
    const outerH = 104;
    const innerX = 30;
    const innerY = 28;
    const cell = 20;
    const innerW = cell * 3;
    const innerH = cell * 3;
    const xMid = outerX + outerW / 2;
    const yMid = outerY + outerH / 2;
    const stroke = '#30343a';

    const addPolygon = (points, fill) => {
        const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
        poly.setAttribute('points', points.map(p => `${p.x},${p.y}`).join(' '));
        poly.setAttribute('fill', fill);
        svg.appendChild(poly);
    };

    // Active highlight fill by true zone shape.
    if ([11, 12, 13, 14].includes(active)) {
        const activeFill = '#ec8013';
        const zoneShapes = {
            11: [
                { x: outerX, y: outerY },
                { x: xMid, y: outerY },
                { x: xMid, y: innerY },
                { x: innerX, y: innerY },
                { x: innerX, y: yMid },
                { x: outerX, y: yMid }
            ],
            12: [
                { x: xMid, y: outerY },
                { x: outerX + outerW, y: outerY },
                { x: outerX + outerW, y: yMid },
                { x: innerX + innerW, y: yMid },
                { x: innerX + innerW, y: innerY },
                { x: xMid, y: innerY }
            ],
            13: [
                { x: outerX, y: yMid },
                { x: innerX, y: yMid },
                { x: innerX, y: innerY + innerH },
                { x: xMid, y: innerY + innerH },
                { x: xMid, y: outerY + outerH },
                { x: outerX, y: outerY + outerH }
            ],
            14: [
                { x: innerX + innerW, y: yMid },
                { x: outerX + outerW, y: yMid },
                { x: outerX + outerW, y: outerY + outerH },
                { x: xMid, y: outerY + outerH },
                { x: xMid, y: innerY + innerH },
                { x: innerX + innerW, y: innerY + innerH }
            ]
        };
        addPolygon(zoneShapes[active], activeFill);
    }

    // Active highlight for center grid zones.
    if (active >= 1 && active <= 9) {
        const idx = active - 1;
        const col = idx % 3;
        const row = Math.floor(idx / 3);
        const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        rect.setAttribute('x', String(innerX + col * cell));
        rect.setAttribute('y', String(innerY + row * cell));
        rect.setAttribute('width', String(cell));
        rect.setAttribute('height', String(cell));
        rect.setAttribute('fill', '#ec8013');
        svg.appendChild(rect);
    }

    // Outer border.
    const outerBorder = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    outerBorder.setAttribute('x', String(outerX));
    outerBorder.setAttribute('y', String(outerY));
    outerBorder.setAttribute('width', String(outerW));
    outerBorder.setAttribute('height', String(outerH));
    outerBorder.setAttribute('fill', 'none');
    outerBorder.setAttribute('stroke', stroke);
    outerBorder.setAttribute('stroke-width', '1.2');
    svg.appendChild(outerBorder);

    // Inner 3x3 strike zone border and lines.
    const innerBorder = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    innerBorder.setAttribute('x', String(innerX));
    innerBorder.setAttribute('y', String(innerY));
    innerBorder.setAttribute('width', String(innerW));
    innerBorder.setAttribute('height', String(innerH));
    innerBorder.setAttribute('fill', 'none');
    innerBorder.setAttribute('stroke', stroke);
    innerBorder.setAttribute('stroke-width', '1.2');
    svg.appendChild(innerBorder);

    const v1 = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    v1.setAttribute('x1', String(innerX + cell));
    v1.setAttribute('y1', String(innerY));
    v1.setAttribute('x2', String(innerX + cell));
    v1.setAttribute('y2', String(innerY + innerH));
    v1.setAttribute('stroke', stroke);
    v1.setAttribute('stroke-width', '1.2');
    svg.appendChild(v1);

    const v2 = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    v2.setAttribute('x1', String(innerX + cell * 2));
    v2.setAttribute('y1', String(innerY));
    v2.setAttribute('x2', String(innerX + cell * 2));
    v2.setAttribute('y2', String(innerY + innerH));
    v2.setAttribute('stroke', stroke);
    v2.setAttribute('stroke-width', '1.2');
    svg.appendChild(v2);

    const h1 = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    h1.setAttribute('x1', String(innerX));
    h1.setAttribute('y1', String(innerY + cell));
    h1.setAttribute('x2', String(innerX + innerW));
    h1.setAttribute('y2', String(innerY + cell));
    h1.setAttribute('stroke', stroke);
    h1.setAttribute('stroke-width', '1.2');
    svg.appendChild(h1);

    const h2 = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    h2.setAttribute('x1', String(innerX));
    h2.setAttribute('y1', String(innerY + cell * 2));
    h2.setAttribute('x2', String(innerX + innerW));
    h2.setAttribute('y2', String(innerY + cell * 2));
    h2.setAttribute('stroke', stroke);
    h2.setAttribute('stroke-width', '1.2');
    svg.appendChild(h2);

    // Connector lines that define corner-zone boundaries.
    const topConnector = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    topConnector.setAttribute('x1', String(xMid));
    topConnector.setAttribute('y1', String(outerY));
    topConnector.setAttribute('x2', String(xMid));
    topConnector.setAttribute('y2', String(innerY));
    topConnector.setAttribute('stroke', stroke);
    topConnector.setAttribute('stroke-width', '1.2');
    svg.appendChild(topConnector);

    const bottomConnector = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    bottomConnector.setAttribute('x1', String(xMid));
    bottomConnector.setAttribute('y1', String(innerY + innerH));
    bottomConnector.setAttribute('x2', String(xMid));
    bottomConnector.setAttribute('y2', String(outerY + outerH));
    bottomConnector.setAttribute('stroke', stroke);
    bottomConnector.setAttribute('stroke-width', '1.2');
    svg.appendChild(bottomConnector);

    const leftConnector = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    leftConnector.setAttribute('x1', String(outerX));
    leftConnector.setAttribute('y1', String(yMid));
    leftConnector.setAttribute('x2', String(innerX));
    leftConnector.setAttribute('y2', String(yMid));
    leftConnector.setAttribute('stroke', stroke);
    leftConnector.setAttribute('stroke-width', '1.2');
    svg.appendChild(leftConnector);

    const rightConnector = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    rightConnector.setAttribute('x1', String(innerX + innerW));
    rightConnector.setAttribute('y1', String(yMid));
    rightConnector.setAttribute('x2', String(outerX + outerW));
    rightConnector.setAttribute('y2', String(yMid));
    rightConnector.setAttribute('stroke', stroke);
    rightConnector.setAttribute('stroke-width', '1.2');
    svg.appendChild(rightConnector);

    // Labels.
    const drawLabel = (textValue, x, y, isActive) => {
        const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        text.setAttribute('x', String(x));
        text.setAttribute('y', String(y));
        text.setAttribute('text-anchor', 'middle');
        text.setAttribute('font-size', '8.2');
        text.setAttribute('font-weight', '700');
        text.setAttribute('fill', isActive ? '#111111' : '#3b3f45');
        text.textContent = String(textValue);
        svg.appendChild(text);
    };

    // Corner labels.
    drawLabel(11, outerX + 14, outerY + 18, active === 11);
    drawLabel(12, outerX + outerW - 14, outerY + 18, active === 12);
    drawLabel(13, outerX + 14, outerY + outerH - 8, active === 13);
    drawLabel(14, outerX + outerW - 14, outerY + outerH - 8, active === 14);

    // Center grid labels.
    for (let zone = 1; zone <= 9; zone += 1) {
        const idx = zone - 1;
        const col = idx % 3;
        const row = Math.floor(idx / 3);
        drawLabel(
            zone,
            innerX + col * cell + cell / 2,
            innerY + row * cell + cell / 2 + 3,
            active === zone
        );
    }

    return svg;
}

function renderSwingTakeZonePage() {
    const stored = currentTableData.breakeven_points;
    if (!stored || !stored.all_data) return;

    const zoneData = stored.all_data.filter(r => r.type === 'swing_take_zone');
    if (zoneData.length === 0) return;

    const seasons = [...new Set(zoneData.map(r => Number(r.season)).filter(Number.isFinite))].sort((a, b) => a - b);
    if (seasons.length === 0) return;

    const fromSelect = document.querySelector('select[name="from_season"]');
    const toSelect = document.querySelector('select[name="to_season"]');
    if (fromSelect && toSelect) {
        const seasonsDesc = [...seasons].reverse();
        const currentFrom = parseInt(fromSelect.value);
        const currentTo = parseInt(toSelect.value);

        const needsRepopulate = (
            parseInt(fromSelect.options[0]?.value) !== seasonsDesc[0] ||
            parseInt(fromSelect.options[fromSelect.options.length - 1]?.value) !== seasonsDesc[seasonsDesc.length - 1]
        );

        if (needsRepopulate) {
            const fromFrag = document.createDocumentFragment();
            const toFrag = document.createDocumentFragment();
            seasonsDesc.forEach(s => {
                const opt1 = document.createElement('option');
                opt1.value = s;
                opt1.textContent = s;
                fromFrag.appendChild(opt1);
                const opt2 = document.createElement('option');
                opt2.value = s;
                opt2.textContent = s;
                toFrag.appendChild(opt2);
            });
            fromSelect.innerHTML = '';
            fromSelect.appendChild(fromFrag);
            toSelect.innerHTML = '';
            toSelect.appendChild(toFrag);
        }

        const fromVal = seasons.includes(currentFrom) ? currentFrom : seasons[seasons.length - 1];
        const toVal = seasons.includes(currentTo) ? currentTo : seasons[seasons.length - 1];
        fromSelect.value = fromVal;
        toSelect.value = toVal;
    }

    const fromSeason = parseInt(fromSelect?.value) || seasons[seasons.length - 1];
    const toSeason = parseInt(toSelect?.value) || fromSeason;
    const filtered = zoneData.filter(r => r.season >= fromSeason && r.season <= toSeason);

    const agg = {};
    let totalN = 0;
    filtered.forEach(r => {
        const parsed = parseSwingTakeZoneScenario(r.scenario);
        if (!parsed || !Number.isFinite(parsed.zone)) return;

        const key = `${parsed.zone}|${parsed.handedness}|${parsed.count}`;
        if (!agg[key]) {
            agg[key] = { sumPctN: 0, sumN: 0, hasNaN: false };
        }

        if (r.breakeven_pct === null || (typeof r.breakeven_pct === 'number' && isNaN(r.breakeven_pct))) {
            agg[key].hasNaN = true;
        } else if (typeof r.breakeven_pct === 'number') {
            agg[key].sumPctN += r.breakeven_pct * r.n;
            agg[key].sumN += r.n;
        }
        totalN += r.n;
    });

    const zones = [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14];
    const displayData = [];
    zones.forEach(zone => {
        HANDEDNESS_ORDER.forEach(handedness => {
            const row = {
                scenario: 'Strike Zone Map',
                zone,
                handedness
            };
            COUNT_ORDER.forEach(count => {
                const a = agg[`${zone}|${handedness}|${count}`];
                if (a && a.hasNaN && a.sumN === 0) {
                    row[count] = NaN;
                    row[`${count}_n`] = null;
                } else if (a && a.sumN > 0) {
                    row[count] = Math.max(0, Math.min((a.sumPctN / a.sumN) * 100, 100));
                    row[`${count}_n`] = a.sumN;
                } else {
                    row[count] = null;
                    row[`${count}_n`] = null;
                }
            });
            displayData.push(row);
        });
    });

    stored.from_season = fromSeason;
    stored.to_season = toSeason;
    stored.display_data = displayData;
    stored.selected_type = 'swing-take-zone';

    const titleEl = document.getElementById('sb-breakeven-table-title');
    if (titleEl) {
        let titleHTML = 'Break-Even Points - ';
        if (fromSeason === toSeason) {
            titleHTML += `<span class="primary-badge">Swing / Take - Zone Map - ${fromSeason} Season</span>`;
        } else {
            titleHTML += `<span class="primary-badge">Swing / Take - Zone Map - ${fromSeason}-${toSeason} Seasons</span>`;
        }
        titleEl.innerHTML = titleHTML;
    }

    const subtitleEl = document.getElementById('sb-breakeven-table-title-subtitle');
    if (subtitleEl) {
        subtitleEl.innerHTML = 'How often you need to make contact when you swing for swinging to be as good as taking, for each count.<br>Empty cells (—) indicate counts where there isn\'t enough data to produce a reliable estimate.';
    }

    const eventCountEl = document.getElementById('sb-breakeven-event-count');
    setBreakevenFooterText(eventCountEl, `${totalN.toLocaleString()} zone/count pitch observations`, 'swing-take-zone');

    const headEl = document.getElementById('sb-breakeven-table-head');
    if (headEl) {
        let headerHTML = '<tr>';
        headerHTML += '<th class="text-center fw-bold" style="background-color: var(--primary)">Zone Map</th>';
        headerHTML += '<th class="text-center fw-bold" style="background-color: var(--primary)">Handedness</th>';
        COUNT_ORDER.forEach(count => {
            const displayHeader = count === '0-0' ? 'First<br>Pitch' : count;
            headerHTML += `<th class="text-center fw-bold" style="background-color: var(--primary)">${displayHeader}</th>`;
        });
        headerHTML += '</tr>';
        headEl.innerHTML = headerHTML;
    }

    const heatmapEnabled = globalHeatmapEnabled;
    let allValues = [];
    displayData.forEach(row => {
        COUNT_ORDER.forEach(count => {
            const v = row[count];
            if (v !== null && v !== undefined && !isNaN(v)) allValues.push(v);
        });
    });
    const getSwingTakeHeatmapColor = (valuePct) => {
        const v = Math.max(0, Math.min(100, Number(valuePct)));
        const t = v / 100;
        const start = { r: 8, g: 246, b: 0 };   // #08F600
        const end = { r: 255, g: 0, b: 0 };     // #FF0000
        const r = Math.round(start.r + (end.r - start.r) * t);
        const g = Math.round(start.g + (end.g - start.g) * t);
        const b = Math.round(start.b + (end.b - start.b) * t);
        const bgColor = `rgb(${r}, ${g}, ${b})`;

        const toLinear = (c) => {
            const s = c / 255;
            return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
        };
        const luminance = 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b);
        const contrastWhite = (1.0 + 0.05) / (luminance + 0.05);
        const contrastBlack = (luminance + 0.05) / 0.05;
        const textColor = contrastBlack >= contrastWhite ? '#111' : '#fff';
        return { bgColor, textColor };
    };
    const takeBadgeStyle = getSwingTakeHeatmapColor(100);
    const swingBadgeStyle = getSwingTakeHeatmapColor(0);
    const reliabilityStats = buildBreakevenReliabilityStats(displayData, COUNT_ORDER.map(count => `${count}_n`));

    const renderCell = (value, nValue) => {
        if (value === null || value === undefined) return '\u2014';
        if (isNaN(value)) return 'N/A';
        const badge = renderSwingTakeFavoredBadge(value, takeBadgeStyle, swingBadgeStyle, heatmapEnabled);
        if (badge) {
            return appendReliabilityMarkerInsideBadge(badge, nValue, reliabilityStats);
        }
        return renderBreakevenPercentCell(value, nValue, {
            heatmapEnabled,
            hMin: 0,
            hMax: 100,
            reliabilityStats,
            getColorFn: (v, _min, _max) => getSwingTakeHeatmapColor(v),
            minWidth: '50px',
            decimals: 1
        });
    };

    sbBreakevenPagination.rowsPerPage = 16;
    const { startIdx, endIdx } = updateSBBreakevenPagination('swing-take-zone', fromSeason, toSeason, displayData.length);
    const pageData = displayData.slice(startIdx, endIdx);

    const bodyEl = document.getElementById('sb-breakeven-table-body');
    if (bodyEl) {
        let bodyHTML = '';
        pageData.forEach((row, idx) => {
            const globalIdx = startIdx + idx;
            const borderStyle = (globalIdx + 1) % 4 === 0 ? ' style="border-bottom: 2px solid #495057;"' : '';
            bodyHTML += `<tr${borderStyle}>`;

            const groupIndex = Math.floor(globalIdx / 4);
            const prevGroupIndex = idx > 0 ? Math.floor((globalIdx - 1) / 4) : -1;
            const startsNewVisibleGroup = idx === 0 || prevGroupIndex !== groupIndex;
            if (startsNewVisibleGroup) {
                const remainingInGroup = 4 - (globalIdx % 4);
                const rowSpan = Math.min(remainingInGroup, endIdx - globalIdx);
                const svg = createStrikeZoneMapSVG(row.zone);
                const svgString = serializeSVGElement(svg);
                bodyHTML += `<td class="text-center sticky-col align-middle svg-cell" rowspan="${rowSpan}" style="width:128px;min-width:128px;padding:6px;">${svgString}</td>`;
            }

            bodyHTML += `<td class="text-center">${row.handedness}</td>`;
            COUNT_ORDER.forEach(count => {
                bodyHTML += `<td class="text-center" style="font-size:0.88rem;">${renderCell(row[count], row[`${count}_n`])}</td>`;
            });

            bodyHTML += '</tr>';
        });
        bodyEl.innerHTML = bodyHTML;
    }

    renderSBBreakevenPagination();
}

function renderReImpactPlatoonMatchupTable(headEl, bodyEl, displayData, displayMode = 'value', reliabilityStats = null) {
    // 4 handedness rows x 3 outs columns
    const heatmapEnabled = globalHeatmapEnabled;

    let allValues = [];
    displayData.forEach(row => {
        [row.outs_0, row.outs_1, row.outs_2, row.weighted_mean_outs].forEach(v => {
            if (v !== null && v !== undefined && !isNaN(v)) allValues.push(v);
        });
    });
    const hMin = allValues.length > 0 ? Math.min(...allValues) : 0;
    const hMax = allValues.length > 0 ? Math.max(...allValues) : 1;

    const isPercentageMode = displayMode === 'percentage';

    const renderCell = (value, nValue) => renderReImpactMetricCell(value, nValue, {
        isPercentageMode,
        heatmapEnabled,
        hMin,
        hMax,
        reverse: RE_IMPACT_HEATMAP_REVERSE,
        reliabilityStats
    });

    headEl.innerHTML = `
        <tr>
            <th class="bg-primary text-start align-middle fs-5">Pitcher-Batter Matchup</th>
            <th class="bg-primary text-center align-middle fs-5">0 Outs</th>
            <th class="bg-primary text-center align-middle fs-5">1 Out</th>
            <th class="bg-primary text-center align-middle fs-5">2 Outs</th>
            <th class="bg-primary text-center align-middle fs-5">Overall Value</th>
        </tr>
    `;

    bodyEl.innerHTML = displayData.map(row => {
        return `
            <tr>
                <td class="text-start align-middle">${row.platoon_match || row.label || ''}</td>
                <td class="text-center">${renderCell(row.outs_0, row.outs_0_n)}</td>
                <td class="text-center">${renderCell(row.outs_1, row.outs_1_n)}</td>
                <td class="text-center">${renderCell(row.outs_2, row.outs_2_n)}</td>
                <td class="text-center">${renderCell(row.weighted_mean_outs, row.weighted_mean_outs_n)}</td>
            </tr>
        `;
    }).join('');
}

function renderReImpactTtoTable(headEl, bodyEl, displayData, displayMode = 'value', reliabilityStats = null) {
    const heatmapEnabled = globalHeatmapEnabled;

    let allValues = [];
    displayData.forEach(row => {
        [row.tto_2, row.tto_3].forEach(v => {
            if (v !== null && v !== undefined && !isNaN(v)) allValues.push(v);
        });
    });
    const hMin = allValues.length > 0 ? Math.min(...allValues) : 0;
    const hMax = allValues.length > 0 ? Math.max(...allValues) : 1;

    const isPercentageMode = displayMode === 'percentage';

    const renderCell = (value, nValue) => renderReImpactMetricCell(value, nValue, {
        isPercentageMode,
        heatmapEnabled,
        hMin,
        hMax,
        reverse: RE_IMPACT_HEATMAP_REVERSE,
        reliabilityStats
    });

    headEl.innerHTML = `
        <tr>
            <th class="bg-primary text-start align-middle fs-5">Runners</th>
            <th class="bg-primary text-start align-middle fs-5">Handedness</th>
            <th class="bg-primary text-center align-middle fs-5">1st Time</th>
            <th class="bg-primary text-center align-middle fs-5">2nd Time</th>
            <th class="bg-primary text-center align-middle fs-5">3rd+ Time</th>
        </tr>
    `;

    const grouped = {};
    displayData.forEach(row => {
        const runners = row.runners || row.scenario;
        if (!grouped[runners]) grouped[runners] = [];
        grouped[runners].push(row);
    });

    const runnerOrder = ['000', '100', '010', '001', '110', '101', '011', '111'];
    const handednessOrder = ['Same', 'Platoon'];
    const runnersWithData = runnerOrder.filter(runners => grouped[runners] && grouped[runners].length > 0);
    const groupsPerPage = Math.max(1, Math.floor(reImpactTtoPagination.rowsPerPage / 2));
    const totalPages = Math.max(1, Math.ceil(runnersWithData.length / groupsPerPage));

    if (reImpactTtoPagination.currentPage > totalPages) {
        reImpactTtoPagination.currentPage = totalPages;
    }

    const startGroup = (reImpactTtoPagination.currentPage - 1) * groupsPerPage;
    const pageRunners = runnersWithData.slice(startGroup, startGroup + groupsPerPage);

    let bodyHtml = '';
    pageRunners.forEach(runners => {
        const rows = grouped[runners];
        if (!rows || rows.length === 0) return;

        const orderedRows = handednessOrder
            .map(h => rows.find(r => (r.handedness || r.handedness_split) === h))
            .filter(Boolean);
        const fallbackRows = rows.filter(r => !orderedRows.includes(r));
        const finalRows = [...orderedRows, ...fallbackRows].slice(0, 2);
        if (finalRows.length === 0) return;

        const svgString = getBaseStateSVGMarkup(runners);
        const first = finalRows[0];

        bodyHtml += `
            <tr>
                <td class="text-start align-middle" rowspan="2">${svgString} ${first.label || runners}</td>
                <td class="text-start align-middle">${first.handedness || first.handedness_split || ''}</td>
                <td class="text-center">N/A</td>
                <td class="text-center">${renderCell(first.tto_2, first.tto_2_n)}</td>
                <td class="text-center">${renderCell(first.tto_3, first.tto_3_n)}</td>
            </tr>
        `;

        const second = finalRows[1];
        if (second) {
            bodyHtml += `
                <tr>
                    <td class="text-start align-middle">${second.handedness || second.handedness_split || ''}</td>
                    <td class="text-center">N/A</td>
                    <td class="text-center">${renderCell(second.tto_2, second.tto_2_n)}</td>
                    <td class="text-center">${renderCell(second.tto_3, second.tto_3_n)}</td>
                </tr>
            `;
        } else {
            bodyHtml += `
                <tr>
                    <td class="text-start align-middle">N/A</td>
                    <td class="text-center">N/A</td>
                    <td class="text-center">N/A</td>
                    <td class="text-center">N/A</td>
                </tr>
            `;
        }
    });
    bodyEl.innerHTML = bodyHtml;

    reImpactTtoPagination.totalRows = runnersWithData.length * 2;
    renderReImpactTtoPagination(totalPages);
}

function renderReImpactTtoPagination(totalPages) {
    renderPaginationControls({
        containerId: 're-impact-pagination',
        currentPage: reImpactTtoPagination.currentPage,
        totalPages,
        changeHandlerName: 'changeReImpactTtoPage',
        labels: {
            first: '⏮',
            previous: '◀',
            next: '▶',
            last: '⏭'
        }
    });
}

function changeReImpactTtoPage(newPage) {
    const groupsPerPage = Math.max(1, Math.floor(reImpactTtoPagination.rowsPerPage / 2));
    const totalPages = Math.max(1, Math.ceil((reImpactTtoPagination.totalRows / 2) / groupsPerPage));
    setPaginationPage(reImpactTtoPagination, newPage, totalPages, () => {
        renderReImpactPage();
    });
}

window.changeReImpactTtoPage = changeReImpactTtoPage;

function renderPaginationControls({
    containerId,
    currentPage,
    totalPages,
    changeHandlerName,
    labels,
    useDisabledVariant = true,
    hideWhenSinglePage = true
}) {
    const paginationEl = document.getElementById(containerId);
    if (!paginationEl) return;

    if (!Number.isFinite(totalPages) || (hideWhenSinglePage && totalPages <= 1)) {
        paginationEl.innerHTML = '';
        return;
    }

    const firstDisabled = currentPage === 1;
    const lastDisabled = currentPage === totalPages;

    const enabledClass = 'btn-primary-custom';
    const disabledClass = useDisabledVariant ? 'btn-pagination-custom' : 'btn-primary-custom';
    const prevClass = firstDisabled ? disabledClass : enabledClass;
    const nextClass = lastDisabled ? disabledClass : enabledClass;

    const html = `
        <button
            class="btn btn-sm ${prevClass}"
            data-accion="${changeHandlerName}" data-pagina="1"
            ${firstDisabled ? 'disabled' : ''}>
            ${labels.first}
        </button>
        <button
            class="btn btn-sm ${prevClass}"
            data-accion="${changeHandlerName}" data-pagina="${currentPage - 1}"
            ${firstDisabled ? 'disabled' : ''}>
            ${labels.previous}
        </button>
        <span class="text-white small">Page ${currentPage} of ${totalPages}</span>
        <button
            class="btn btn-sm ${nextClass}"
            data-accion="${changeHandlerName}" data-pagina="${currentPage + 1}"
            ${lastDisabled ? 'disabled' : ''}>
            ${labels.next}
        </button>
        <button
            class="btn btn-sm ${nextClass}"
            data-accion="${changeHandlerName}" data-pagina="${totalPages}"
            ${lastDisabled ? 'disabled' : ''}>
            ${labels.last}
        </button>
    `;

    paginationEl.innerHTML = html;
}

function setPaginationPage(paginationState, newPage, totalPages, rerenderFn) {
    if (newPage < 1 || newPage > totalPages) return;

    paginationState.currentPage = newPage;
    if (typeof rerenderFn === 'function') {
        rerenderFn();
    }
}

// Registro de las acciones delegadas.
//
// Va al final del fichero a proposito: las declaraciones `function` se elevan,
// asi que ya existen aqui, y de este modo la lista queda en un solo sitio en vez
// de repartida por los renderizadores.
//
// Si se anade una tabla paginada nueva, su changeXPage TIENE que aparecer aqui o
// sus botones no haran nada -- en silencio, que es como se rompio la primera vez.
// El test test_todas_las_paginaciones_estan_registradas lo comprueba.
registrarAccion('changeFIPPage', changeFIPPage);
registrarAccion('changeLeverageIndexPage', changeLeverageIndexPage);
registrarAccion('changeLinearWeightsPage', changeLinearWeightsPage);
registrarAccion('changeParkFactorsPage', changeParkFactorsPage);
registrarAccion('changePythagExponentsPage', changePythagExponentsPage);
registrarAccion('changeReImpactTtoPage', changeReImpactTtoPage);
registrarAccion('changeRunsPerWinPage', changeRunsPerWinPage);
registrarAccion('changeSBBreakevenPage', changeSBBreakevenPage);
registrarAccion('changeWOBAPage', changeWOBAPage);
registrarAccion('changeWinExpectancyPage', changeWinExpectancyPage);
registrarAccion('changeWinProbabilityAddedPage', changeWinProbabilityAddedPage);
registrarAccion('sortWOBA', sortWOBA);
registrarAccion('sortParkFactors', sortParkFactors);
