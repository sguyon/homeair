// Home Air: the live sky behind the page and the Weather pill. Uses $, APP, TZ and appNow from app.js,
// and bearingIn and pmOver from home.js.
// ── Sky band: real sun position, cloud cover, wind and PM2.5. Preview overrides from the Customize panel. ──
var SKY = (() => {
  const HAZY_PM = 12;   // µg/m³ outdoor PM2.5: over this the Weather pill says "Hazy air" (the old US AQI "Good" edge); over pm_high, "Smoky"
  const WMO_CODE_MAX = 99; // highest numeric present-weather code in the two-digit WMO scheme.
  const WEATHER_CACHE_MS = 6 * 3600e3; // ms to keep each last-known weather reading across reloads.
  const WEATHER_FIELDS = ['dir', 'spd', 'cloud', 'rain', 'thunder', 'temp'];
  // Text on the sky (title, weather line, sky control): its ink follows what is actually behind it (gradient, sun, clouds, haze).
  const INK_ON_LIGHT = [22, 33, 46], INK_ON_DARK = [242, 246, 251];      // rgb: the ink over a light sky, over a dark one
  const HALO_ON_LIGHT = [255, 255, 255], HALO_ON_DARK = [8, 14, 26];    // rgb: the soft halo around that ink, the opposite tone
  const INK_CONTRAST = 4.5;     // WCAG AA for normal text, used for the weather line and its soft shadow
  const HEADING_CONTRAST = 5.4; // ratio for bare heading ink, with margin for nearby card shadows and weather between samples
  // ms a heading keeps its outline after its bare ink reads again: moving air or clouds behind it would otherwise switch the
  // outline on and off many times a second (Sacha, 26 Nov: "keeps changing between black font and black with white border").
  const HEADING_OUTLINE_HOLD_MS = 6000;
  const HEADING_KEEP_SHARE = .85; // share: a held heading ink, outlined, stays when it reads at least this well against the other ink outlined (dense smoke leaves neither at the full floor)
  const outlineSeen = new WeakMap();   // heading element → when its outline was last needed
  const flipWanted = new WeakMap();   // heading element → since when the other ink has read better
  const HALO_MIN = .35, HALO_MAX = .9, HALO_STEP = .05;   // halo opacity: always a little, at most nearly solid, searched in these steps
  const INK_FLIP = 1.15;        // the other ink must do 15% better at the worst spot before the text switches (no flicker as a cloud passes)
  const INK_EVERY = 1500;       // ms between two looks at the sky behind the text: clouds drift slowly
  const INK_GRID = [6, 3];      // points sampled across and down each piece of text
  const INK_SCROLL = 300;       // ms between looks while scrolling, with a trailing look when the page stops on a new patch of sky
  const FLASH_BACK_SIZE = [1.2, .8], FLASH_FRONT_SIZE = [.9, .7]; // viewport shares, match the native sheet-flash radial gradients.
  const FLASH_BACK_STOPS = [[0, [225, 235, 255], .95], [.6, [200, 215, 255], .4], [1, [200, 215, 255], 0]]; // radius/rgb/opacity, rear flash before front matter.
  const FLASH_FRONT_STOPS = [[0, [214, 226, 255], .85], [.55, [170, 190, 255], .25], [.8, [170, 190, 255], 0]]; // radius/rgb/opacity, screen-blended flash above the page.
  const FLASH_BACK_LEVELS = [0, .6, .1, .4, 0], FLASH_BACK_FRONT_LEVELS = [0, .9, .1, .7, 0], FLASH_FRONT_LEVELS = [0, .55, .05, .4, 0]; // opacity, same keyframes for paint and sampling.
  const HEADING_SAMPLE_PAD = 4; // CSS px around glyph bounds to include their immediate backdrop
  const LOAD_DELAY_MS = 600; // ms after the content first appears (first paint, the Summary's first render, or the tab first shown), so the clear page shows first.
  const LOAD_WINDOW_MS = 10000; // ms after navigation: the air waits at most this long for its load pass, and a tab opened hidden still plays it if shown by then.
  const SETTLED_GAP_MS = [20000, 40000]; // ms between finite sparse events; never a replay.
  const SPARSE_PARTICLE_MAX = 8, DAMP_PARTICLE_MAX = 2; // particles/event shared across renderers; wet events use two drops.
  const CAP_GAP_PX = 8; // CSS px between sky-line controls, matching sky-air.css when fitting weather words.
  const INK_TRANSIENT = '.sx-tip, .sx-hv, [data-chart-scrub], .sky-wet-pane'; // Chart scrub/pane nodes never change shared ink geometry.
  const CLOUD_BLUR_PAD = 3; // sigmas around a sample patch, so the CSS blur can pull in nearby cloud pixels
  // What app.css draws, rebuilt to judge the text's background: the sun's glow (.sky-sun: 70vmax wide; stops at a share of its
  // radius, [rgb, alpha]).
  const SUN_R = .35;            // the glow's radius, a share of the screen's longer side
  const SUN_GLOW = [[0, [255, 246, 220], .55], [.4, [255, 236, 200], .18], [.72, [255, 236, 200], 0]];
  const SUN_GLOW_WARM = [[0, [255, 196, 140], .6], [.42, [255, 170, 120], .2], [.72, [255, 170, 120], 0]];
  // A tap on the weather card's compass: the dial turns one way, the needle the other, then both settle on the reading.
  const COMPASS_MS = 800;        // ms, there and back
  const COMPASS_DIAL = -80;      // deg the dial (ring, ticks, N) turns: counter-clockwise
  const COMPASS_NEEDLE = 140;    // deg the needle swings the other way, on top of the wind's direction
  const COMPASS_TURN = .38;      // share of the time spent turning away; the rest settles back
  // The display menu's air buttons (Smoke, Exhaust, Fine particles, Pollen, Damp). In the demo each opens its story
  // (?moment=), so the readings and actions match the air; on a home each plays over the page as a preview.
  const AIR_STORIES = { smoke: 'sky-smoke', exhaust: 'rush', particles: 'sky-particles', pollen: 'sky-pollen', damp: 'humid' };
  const AIR_PREVIEW_PM = { smoke: 48, exhaust: 22, particles: 24 };   // µg/m³: the haze a preview of a PM2.5 kind paints, the demo stories' outdoor readings
  const COMPASS_EASE_AWAY = 'cubic-bezier(.4, 0, .2, 1)', COMPASS_EASE_BACK = 'cubic-bezier(.34, 1.4, .5, 1)';   // the way back overshoots a little
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const LAT = APP.place.lat ?? 0, LON = APP.place.lon;   // no place yet: the sun is placed on the equator, at the timezone's own longitude
  let reduce = motion.matches;
  // Start from the last real weather this browser saw, so a reload never shows made-up defaults.
  const LIVE_KEY = 'aq-sky-live' + (APP.now ? ':' + APP.now.moment : '');   // the demo keeps one per moment
  const live = { dir: 200, spd: 0, cloud: 0, rain: 0, thunder: false, temp: null, loaded: false, fresh: false };
  const weatherAt = {};
  const isWeatherValue = (key, value) => key === 'thunder' ? typeof value === 'boolean' : Number.isFinite(value);
  const weatherUnexpired = (key, now) => now - weatherAt[key] < WEATHER_CACHE_MS;
  try {
    const saved = JSON.parse(localStorage.getItem(LIVE_KEY) || 'null');
    const now = Date.now(), legacy = Number.isFinite(saved?.at);
    for (const key of WEATHER_FIELDS) {
      // Older entries used one timestamp for the whole weather response.
      const at = legacy ? saved.at : saved?.at?.[key], value = saved?.v?.[key];
      if (!Number.isFinite(at)) continue;
      const expired = now - at >= WEATHER_CACHE_MS;
      // Keep an expired field's timestamp so partial updates cannot declare it fresh.
      if (!isWeatherValue(key, value) && (legacy || !expired)) continue;
      weatherAt[key] = at;
      if (!expired) { live[key] = value; live.loaded = true; }
    }
  } catch {}
  function remember() {
    if (quiet) return;
    const now = Date.now();
    const v = Object.fromEntries(WEATHER_FIELDS.filter(key => weatherUnexpired(key, now)).map(key => [key, live[key]]));
    try { localStorage.setItem(LIVE_KEY, JSON.stringify({ at: weatherAt, v })); } catch {}
  }
  let preview = null;
  let stormExpiryAt = Infinity, stormExpiryTimer = 0;
  // Screenshots and the token page (plain light or dark, nothing moving behind the form) keep every effect off and save nothing.
  const quiet = /[?&]shot\b/.test(location.search) || APP.state === 'no_token';
  // The menu's sliders (time, wind, clouds, rain, PM2.5) are a development tool: dev and branch previews only, never the demo, a home or a screenshot.
  const sliders = !quiet && (APP.env === 'dev' || APP.env === 'preview');
  if (!sliders) document.querySelector('.fx-sliders')?.remove();
  // The Live sky switches: Sky (sun, clouds, stars, rain, thunder), Wind (its lines), Air (smoke, exhaust, fine particles,
  // pollen, damp). A choice saved before 26 Nov had Haze where Air is now: Haze off keeps the air off.
  const on = quiet ? { sky: false, wind: false, air: false }
    : (() => { try { const v = JSON.parse(localStorage.getItem('aq-fx') || 'null') || {};
      return { sky: v.sky ?? true, wind: v.wind ?? true, air: v.air ?? v.haze ?? true }; } catch { return { sky: true, wind: true, air: true }; } })();
  const save = () => { if (quiet) return; try { localStorage.setItem('aq-fx', JSON.stringify(on)); } catch {} };
  // At most two effects at once, one per layer (Sacha, 25 Nov): one outdoor kind in the sky (primaryKind) and damp on the glass.
  // Any other outdoor condition is only its word in the sky line; a tap on it plays it and makes it the one shown (chosenKind).
  // Air previews, one per layer (airPreview: an outdoor kind and damp): each adds its kind to the home's conditions
  // (homeConditions), and the previewed outdoor kind is the one shown.
  let homeConditions = [], airPreview = { outdoor: null, damp: false };
  let conditions = [], airContext = null, airPm = null, leadingKind = null, playingKind = null, primaryKind = null, chosenKind = null;
  let loadConsumed = false, loadDecided = false, loadTimer = 0, loadLeader = null, contentAt = null, shownAt = document.hidden ? null : 0;
  let sparseTimer = 0, masterEnabled = !!on.air;
  // ?skydebug: one small line on the page with the load pass's outcome and its reason (a phone test can read it).
  const debugLine = !quiet && /[?&]skydebug\b/.test(location.search) ? document.createElement('div') : null;
  if (debugLine) { debugLine.className = 'sky-debug'; debugLine.setAttribute('aria-hidden', 'true'); document.body.appendChild(debugLine); }
  function loadDebug(text) { if (debugLine) debugLine.textContent = `sky load: ${text} · ${Math.round(performance.now())} ms`; }
  loadDebug('waiting for a fresh Summary');
  // The held-back air shows by itself once the window ends; a pass from slow data can still play over it. Armed by the first snapshot.
  let veilTimer = 0, veilArmed = false;
  function armVeil() {
    if (veilArmed || loadConsumed) return;
    veilArmed = true;
    veilTimer = setTimeout(() => { veilTimer = 0; if (!loadConsumed) { glass?.redraw(); if (!loadLeader) loadDebug('no fresh summary yet, air shown'); } }, Math.max(0, LOAD_WINDOW_MS - performance.now()));
  }
  const airButtons = new Map();
  const airEnabled = () => !quiet && masterEnabled;
  const rendererFor = kind => kind === 'damp' ? wet : glass;
  const activeKind = kind => conditions.some(c => c.kind === kind);

  function syncAirButtons() {
    const host = $('sky-conditions');
    for (const [kind, parts] of airButtons) if (!activeKind(kind)) { parts.button.remove(); airButtons.delete(kind); }
    if (!activeKind('exhaust')) dropWindPill();
    for (const [index, condition] of conditions.entries()) {
      let parts = airButtons.get(condition.kind);
      if (!parts) {
        const button = document.createElement('button'), icon = document.createElement('span'), label = document.createElement('span');
        button.type = 'button'; button.className = 'cap-i cap-air'; button.dataset.skyPlay = condition.kind;
        icon.className = 'cap-air-icon'; icon.setAttribute('aria-hidden', 'true'); label.className = 'cap-air-label';
        button.appendChild(icon); button.appendChild(label); host.appendChild(button);
        parts = { button, icon, label }; airButtons.set(condition.kind, parts);
      }
      const { button, icon, label } = parts, playing = playingKind === condition.kind;
      const order = [...host.children].filter(el => !el.classList.contains('cap-ex-wind'));   // the exhaust's wind pill sits outside the conditions' order
      if (order[index] !== button) host.insertBefore(button, order[index] || null);
      if (label.textContent !== condition.label) label.textContent = condition.label;
      const symbol = playing ? '■' : '▶'; if (icon.textContent !== symbol) icon.textContent = symbol;
      const name = `${playing ? 'Stop' : 'Play'} ${condition.label} effect`;
      if (button.getAttribute('aria-label') !== name) button.setAttribute('aria-label', name);
      if (button.getAttribute('aria-pressed') !== String(playing)) button.setAttribute('aria-pressed', String(playing));
      button.disabled = !airEnabled();
      if (condition.kind === 'exhaust') exhaustWind(button, host);
    }
    fitCap();
  }
  // The exhaust's wind, beside its word (mock 109 round 4): an arrow pointing where the wind blows (north up, as on the map),
  // where it comes from and how fast. With a map on the page, a map icon, and a tap scrolls to the map. The weather's own
  // wind reading hides meanwhile (html.has-ex-wind), so the wind isn't shown twice.
  const COMPASS = { N: 'north', NNE: 'north-northeast', NE: 'northeast', ENE: 'east-northeast', E: 'east', ESE: 'east-southeast', SE: 'southeast',
    SSE: 'south-southeast', S: 'south', SSW: 'south-southwest', SW: 'southwest', WSW: 'west-southwest', W: 'west', WNW: 'west-northwest', NW: 'northwest', NNW: 'north-northwest' };
  const COMPASS_POINTS = Object.keys(COMPASS), COMPASS_STEP = 360 / COMPASS_POINTS.length;   // degrees per named point
  const WIND_ARROW_SVG = '<svg viewBox="0 0 24 24" width="15" height="15"><path d="M12 19V5M6 11l6-6 6 6"/></svg>';
  const MAP_ICON_SVG = '<svg viewBox="0 0 24 24" width="14" height="14"><path d="M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2zM9 4v14M15 6v14"/></svg>';
  let windPill = null;
  function dropWindPill() {
    if (windPill && windPill.el.parentElement) windPill.el.remove();
    document.documentElement.classList.remove('has-ex-wind');
  }
  function exhaustWind(button, host) {
    const v = st();
    if (!Number.isFinite(v.dir)) { dropWindPill(); return; }
    if (!windPill) {
      const map = !!document.querySelector('.nb-card'), el = document.createElement(map ? 'button' : 'span');
      const arrow = document.createElement('span'), words = document.createElement('span');
      el.className = 'cap-i cap-air cap-ex-wind'; if (map) el.type = 'button';
      arrow.className = 'ex-arrow'; arrow.setAttribute('aria-hidden', 'true'); arrow.innerHTML = WIND_ARROW_SVG;
      words.className = 'ex-wind'; el.appendChild(arrow); el.appendChild(words);
      if (map) {
        const icon = document.createElement('span'); icon.className = 'ex-mapicon'; icon.setAttribute('aria-hidden', 'true'); icon.innerHTML = MAP_ICON_SVG;
        el.appendChild(icon);
        el.addEventListener('click', e => { e.stopPropagation(); document.querySelector('.nb-card')?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'center' }); });
      }
      windPill = { el, arrow, words, map };
    }
    const { el, arrow, words, map } = windPill, kids = [...host.children], next = kids[kids.indexOf(button) + 1];
    if (next !== el) host.insertBefore(el, next || null);
    const point = COMPASS_POINTS[Math.round(v.dir / COMPASS_STEP) % COMPASS_POINTS.length], speed = Math.round(v.spd || 0);
    arrow.style.transform = `rotate(${Math.round((v.dir + 180) % 360)}deg)`;
    const text = `from ${point} · ${speed} km/h`, label = `Wind from the ${COMPASS[point]} at ${speed} km/h, carrying the exhaust${map ? '. Show it on the map' : ''}`;
    if (words.textContent !== text) words.textContent = text;
    if (el.getAttribute('aria-label') !== label) { el.setAttribute('aria-label', label); el.title = label; }
    document.documentElement.classList.add('has-ex-wind');
  }
  function withPreview() {
    const kinds = [airPreview.outdoor, airPreview.damp && 'damp'].filter(Boolean), home = { ctx: airContext, road: APP.road || null };
    return kinds.reduce((list, kind) => previewConditions(list, kind, home), homeConditions);
  }
  // The outdoor kind shown: the one playing, else the one last tapped, else the one previewed, else the Summary's leader,
  // else the strongest by skyConditions' order (smoke, exhaust, fine particles, pollen), as when damp leads.
  function shownOutdoor() {
    for (const kind of [playingKind, chosenKind, airPreview.outdoor, leadingKind]) if (kind && kind !== 'damp' && activeKind(kind)) return kind;
    return conditions.find(c => c.kind !== 'damp')?.kind || null;
  }
  // What the renderers draw: the shown outdoor kind and damp; nothing of the other outdoor conditions (no haze, specks or tint).
  const drawnConditions = () => conditions.filter(c => c.kind === 'damp' || c.kind === primaryKind);
  function setPrimary() {
    const next = shownOutdoor();
    if (next !== primaryKind) { primaryKind = next; if (airEnabled()) glass?.setConditions(drawnConditions(), primaryKind); }
  }
  // A tap on an outdoor word makes it the kind shown from then on; the previous one goes back to its word only.
  function choose(kind) {
    if (kind === 'damp' || !activeKind(kind)) return;
    chosenKind = kind; setPrimary();
  }
  function reportPlaying(renderer, kind) {
    if (kind) playingKind = kind;
    else if (playingKind && rendererFor(playingKind) === renderer) { playingKind = null; setPrimary(); }
    syncAirButtons();
  }
  // The load pass is used up: played, skipped by a real input, or decided against. The held-back air then settles in view.
  function consumeLoad(reason) {
    const waiting = loadPending();
    if (!loadConsumed && reason) loadDebug(reason);
    loadConsumed = true; loadLeader = null; clearTimeout(loadTimer); loadTimer = 0; clearTimeout(veilTimer); veilTimer = 0;
    if (waiting) glass?.redraw();
  }
  // While the load pass may still come, the renderer holds the air back so the clear page shows first.
  function loadPending() { return !loadConsumed && airEnabled() && !reduce && performance.now() < LOAD_WINDOW_MS; }
  // When the content first appeared: the Summary's first render (the first paint is only the page's frame), or later if the tab was first shown later.
  function firstContent() {
    let paint = null;
    try { paint = performance.getEntriesByName?.('first-contentful-paint')?.[0]?.startTime ?? null; } catch {}
    return Math.max(paint ?? 0, contentAt ?? 0, shownAt ?? 0);
  }
  // Due LOAD_DELAY_MS after the content first appears. A hidden tab waits to be shown; scroll alone (iOS fires one at load) never cancels it.
  function armLoad() {
    clearTimeout(loadTimer); loadTimer = 0;
    if (!loadLeader || loadConsumed) return;
    if (document.hidden) { loadDebug('hidden, waiting to be shown'); return; }
    const delay = Math.max(0, firstContent() + LOAD_DELAY_MS - performance.now());
    loadDebug(`due in ${Math.round(delay)} ms`);
    loadTimer = setTimeout(() => {
      loadTimer = 0;
      if (!loadLeader || loadConsumed || document.hidden) return;
      playAir(loadLeader, `played ${loadLeader}`);
    }, delay);
  }
  function stopPass() {
    const renderer = playingKind && rendererFor(playingKind);
    playingKind = null; renderer?.stop(); setPrimary(); syncAirButtons();
  }
  function armSparse() {
    clearTimeout(sparseTimer); sparseTimer = 0;
    if (!airEnabled() || reduce || document.hidden || !conditions.length) return;
    const delay = SETTLED_GAP_MS[0] + Math.random() * (SETTLED_GAP_MS[1] - SETTLED_GAP_MS[0]);
    sparseTimer = setTimeout(() => {
      sparseTimer = 0;
      if (!airEnabled() || reduce || document.hidden) return;
      if (!playingKind) {
        const outdoor = primaryKind ? [primaryKind] : [];
        // One renderer per event shares the cap without relying on either renderer accepting a particle-count override.
        const damp = activeKind('damp') && (!outdoor.length || Math.random() < DAMP_PARTICLE_MAX / SPARSE_PARTICLE_MAX);
        if (damp) wet?.burst(['damp']); else if (outdoor.length) glass?.burst(outdoor);
      }
      armSparse();
    }, delay);
  }
  function reconcileAir() {
    if (!glass || !wet) return;
    if (!airEnabled()) {
      playingKind = null; glass.clear(); wet.clear(); clearTimeout(sparseTimer); sparseTimer = 0;
    } else {
      if (playingKind && !activeKind(playingKind)) stopPass();
      if (chosenKind && !activeKind(chosenKind)) chosenKind = null;
      setPrimary(); glass.setConditions(drawnConditions(), primaryKind); wet.setConditions(drawnConditions(), primaryKind);
      if (reduce) { playingKind = null; glass.still(); wet.still(); }
      if (!conditions.length) { clearTimeout(sparseTimer); sparseTimer = 0; }
      else if (!sparseTimer) armSparse();
    }
    syncAirButtons(); invalidateInk();
  }
  // The pass starts before the load is marked used, so held-back air rises from nothing instead of flashing its settled cast first.
  function playAir(kind, reason = `skipped by a tap on ${kind}`) {
    if (!airEnabled() || reduce || document.hidden || !activeKind(kind)) { consumeLoad(reason); return; }
    stopPass(); playingKind = kind; setPrimary();
    rendererFor(kind)?.play(kind); consumeLoad(reason); syncAirButtons(); armSparse();
  }
  function air(snapshot, { initial = false, freshSummary = null, skipped = false } = {}) {
    if (quiet) return;
    if (skipped) consumeLoad(`skipped by ${typeof skipped === 'string' ? skipped : 'an input'} before the sky loaded`);
    if (snapshot.summary && contentAt == null) contentAt = performance.now();
    armVeil();
    const cachedContext = !!snapshot.ctx?._offline;
    airContext = cachedContext ? null : snapshot.ctx || null;
    const pm = Number.isFinite(snapshot.pm) ? snapshot.pm : null;
    const pmChanged = pm !== airPm;
    airPm = pm;
    homeConditions = skyConditions({ ...snapshot, ctx: airContext, limits: APP.limits, road: APP.road || null });
    conditions = withPreview();
    leadingKind = skyLeader(homeConditions, snapshot.summary);
    reconcileAir();
    if (pmChanged && !preview) paint(); else renderCap();
    if (!initial || loadDecided) return;
    loadDecided = true;
    // Slow first data still plays when it arrives, unless the person has really interacted (loadConsumed).
    const leader = freshSummary && !cachedContext ? skyLeader(homeConditions, freshSummary) : null;
    if (loadConsumed) return;
    if (!airEnabled()) consumeLoad('effects off');
    else if (reduce) consumeLoad('reduced motion');
    else if (cachedContext) consumeLoad('offline (saved data)');
    else if (!freshSummary) consumeLoad('no fresh summary');
    else if (!leader) consumeLoad('no condition leads the Summary');
    else { loadLeader = leader; armLoad(); }
  }
  function syncAirMaster() {
    const enabled = !!on.air;
    if (masterEnabled === enabled) return;
    masterEnabled = enabled; consumeLoad('effects switched'); stopPass(); reconcileAir();
    if (enabled) { if (reduce) { glass?.still(); wet?.still(); } else { glass?.settle(); wet?.settle(); } }
  }
  const nyMinutes = () => { const p = new Intl.DateTimeFormat('en-US', { timeZone: TZ, hour: 'numeric', minute: 'numeric', hour12: false }).formatToParts(appNow()); const g = t => Number(p.find(x => x.type === t).value); return (g('hour') % 24) * 60 + g('minute'); };
  function sunPos(minutes) {
    const now = appNow();
    const tz = new Intl.DateTimeFormat('en-US', { timeZone: TZ, timeZoneName: 'shortOffset' }).formatToParts(now).find(x => x.type === 'timeZoneName').value;
    const offset = -60 * Number((tz.match(/GMT([+-]\d+)/) || [0, -5])[1]);
    const day = Math.floor((now - Date.UTC(now.getUTCFullYear(), 0, 0)) / 864e5);
    const g = 2 * Math.PI / 365 * (day - 1 + (minutes / 60 - 12) / 24);
    const decl = 0.006918 - 0.399912 * Math.cos(g) + 0.070257 * Math.sin(g) - 0.006758 * Math.cos(2 * g) + 0.000907 * Math.sin(2 * g) - 0.002697 * Math.cos(3 * g) + 0.00148 * Math.sin(3 * g);
    const eqt = 229.18 * (0.000075 + 0.001868 * Math.cos(g) - 0.032077 * Math.sin(g) - 0.014615 * Math.cos(2 * g) - 0.040849 * Math.sin(2 * g));
    const ha = ((minutes + offset + eqt + 4 * (LON ?? -offset / 4)) / 4 - 180) * Math.PI / 180, lat = LAT * Math.PI / 180;
    const cosz = Math.sin(lat) * Math.sin(decl) + Math.cos(lat) * Math.cos(decl) * Math.cos(ha), zen = Math.acos(Math.max(-1, Math.min(1, cosz)));
    let az = Math.acos(Math.max(-1, Math.min(1, (Math.sin(lat) * Math.cos(zen) - Math.sin(decl)) / (Math.cos(lat) * Math.sin(zen) || 1e-6)))) * 180 / Math.PI;
    az = ha > 0 ? (az + 180) % 360 : (540 - az) % 360;   // degrees clockwise from north
    return { el: 90 - zen * 180 / Math.PI, az };
  }
  const STOPS = [[-18, [12, 18, 34], [22, 30, 52]], [-8, [22, 34, 70], [70, 72, 112]], [-2, [52, 72, 128], [236, 146, 112]],
                 [3, [104, 146, 204], [252, 196, 140]], [10, [126, 178, 232], [214, 228, 240]], [40, [110, 170, 236], [222, 236, 248]]];
  const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
  const gray = (c, k) => { const l = Math.round(c[0] * .3 + c[1] * .59 + c[2] * .11); return mix(c, [l, l, l], k); };
  function skyAt(el) {
    const i = STOPS.findIndex(s => el < s[0]);
    if (i === -1) return [STOPS[5][1], STOPS[5][2]];
    if (i === 0) return [STOPS[0][1], STOPS[0][2]];
    const [e0, t0, h0] = STOPS[i - 1], [e1, t1, h1] = STOPS[i], t = (el - e0) / (e1 - e0);
    return [mix(t0, t1, t), mix(h0, h1, t)];
  }
  const st = (now = Date.now()) => {
    if (preview) return preview;
    // The weather line and visuals expire each observation together, even in a tab left open.
    const weather = Object.fromEntries(WEATHER_FIELDS.map(key => [key,
      weatherUnexpired(key, now) ? live[key] : key === 'thunder' ? false : 0]));
    return { minutes: nyMinutes(), ...live, ...weather, pm: airPm ?? 0 };
  };
  let glass = null, wet = null; // Outdoor and wet compositors join the shared ink samples in drawing order.
  let elev = 0, azim = 180;
  // Colors and wind ease toward their targets instead of snapping.
  const cur = { top: null, hor: null, dir: null, spd: 0 }, tgt = { top: [12, 18, 34], hor: [22, 30, 52] };
  const ease = (a, b, k) => a + (b - a) * k;
  const angEase = (a, b, k) => { const d = ((b - a + 540) % 360) - 180; return (a + d * k + 360) % 360; };
  const ICO = {
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    rise: '<path d="M12 2v6M8.5 5.5 12 2l3.5 3.5"/><path d="M5 18a7 7 0 0 1 14 0"/><path d="M2 22h20"/>',
    set: '<path d="M12 2v6M8.5 4.5 12 8l3.5-3.5"/><path d="M5 18a7 7 0 0 1 14 0"/><path d="M2 22h20"/>',
    moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    cloud: '<path d="M17.5 19H9a7 7 0 1 1 6.7-9h1.8a4.5 4.5 0 1 1 0 9Z"/>',
    therm: '<path d="M14 4v10.5a4 4 0 1 1-4 0V4a2 2 0 0 1 4 0Z"/>',
    arrow: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    rain: '<path d="M17.5 15H9a6 6 0 1 1 5.8-7.5h1.7a3.75 3.75 0 0 1 1 7.5Z"/><path d="M8 19l-1 2M12 19l-1 2M16 19l-1 2"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    alert: '<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>',
    bolt: '<path d="M17.5 14H9a6 6 0 1 1 5.8-7.5h1.7a3.75 3.75 0 0 1 1 7.5Z"/><path d="m12 14-2 4h3l-2 4"/>',
  };
  const ic = (d, extra = '') => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true"${extra}>${d}</svg>`;
  const words = (v, now) => {
    const observed = key => preview ? isWeatherValue(key, v[key]) : weatherUnexpired(key, now);
    // golden hour: sun below ~6° (the usual definition); dawn and dusk: just below the horizon
    const morning = azim < 180;
    const phase = elev > 6 ? 'Day' : elev > 0 ? (morning ? 'Sunrise' : 'Golden hour') : elev > -6 ? (morning ? 'Dawn' : 'Dusk') : 'Night';
    const air = pmOver(v.pm, APP.limits.pm_high) ? ['Smoky air', 'bad'] : pmOver(v.pm, HAZY_PM) ? ['Hazy air', 'warn'] : ['Air clear', 'good'];
    // drop: the order items hide in when the line is too narrow (1 first). The sky itself already shows time of day and clouds.
    const seg = (icon, text, cls = '', drop = 0, title = '') => `<span class="cap-i ${cls}"${drop ? ` data-drop="${drop}"` : ''}${title ? ` title="${title}"` : ''}>${icon}${text}</span>`;
    const out = [];
    // no time and no live dot (Sacha, 2026-11-13: "we don't need to show the time in this app, only the weather")
    if (preview) out.push('<span class="cap-tag">Preview</span>');
    out.push(seg(ic(elev > 6 ? ICO.sun : elev > -6 ? (morning ? ICO.rise : ICO.set) : ICO.moon), phase, elev > 6 ? 'sun' : elev > -6 ? 'dusk' : 'night', 1));
    if (observed('thunder') && v.thunder) out.push(seg(ic(ICO.bolt), 'Thunderstorm', 'storm', 6));
    else if (observed('rain') && v.rain > .05) out.push(seg(ic(ICO.rain), v.rain > .65 ? 'Heavy rain' : v.rain > .4 ? 'Rain' : 'Drizzle', 'rainy', 6));
    else if (observed('cloud')) {
      const c = v.cloud, skyW = c >= 90 ? 'Overcast' : c >= 60 ? 'Mostly cloudy' : c >= 25 ? 'Partly cloudy' : 'Clear sky';
      out.push(seg(ic(ICO.cloud), skyW, '', 2));
    }
    if (!preview && observed('temp')) out.push(seg(ic(ICO.therm), fmtTemp(v.temp, { tight: true }), '', 3));   // the viewer's unit (fmtTemp is in app.js)
    if (observed('spd')) {
      if (v.spd < 2) out.push(seg(ic(ICO.arrow, ' style="opacity:.4"'), 'Calm', '', 5));
      else if (observed('dir')) {
        const firstRoad = Array.isArray(airContext?.roads) ? airContext.roads[0] : null;
        const roadName = !airContext ? null : 'roads' in airContext ? (firstRoad?.reaching === true ? firstRoad.short : null)
          : APP.road && bearingIn(v.dir, APP.road.toward) ? APP.road.name : null;
        out.push(seg(ic(ICO.arrow, ` style="transform:rotate(${(v.dir + 180) % 360}deg)"`), `${card(v.dir)} ${v.spd} km/h${roadName ? ` · ${esc(roadName)}` : ''}`, 'wind', 5, `Wind from the ${card(v.dir)}${roadName ? `, carrying ${esc(roadName)} exhaust this way` : ''}`));
      }
    }
    // The condition's own word (Smoke, Fine particles) already says it; the pill would repeat it.
    if (air[1] !== 'good' && !conditions.some(c => c.kind === 'smoke' || c.kind === 'particles')) out.push(seg(ic(ICO.alert), air[0], 'air ' + air[1], 4));
    return out.join('');
  };
  function renderCap(now = Date.now(), v) {
    const host = $('sky-weather');
    const html = preview || live.loaded ? words(v || st(now), now) : '<span class="cap-i" data-drop="1">Loading the weather…</span>';
    if (host.innerHTML !== html) host.innerHTML = html;
    const stale = !live.fresh || WEATHER_FIELDS.some(key => key in weatherAt && !weatherUnexpired(key, now));
    $('sky-cap').title = !preview && live.loaded && stale ? 'Last known weather; live data is loading' : '';
    syncAirButtons();
  }
  // Keep the weather on one line: hide items by their drop order until it fits.
  function fitCap() {
    const cap = $('sky-cap'), items = [...cap.querySelectorAll('[data-drop]')].sort((a, b) => a.dataset.drop - b.dataset.drop);
    items.forEach(e => { if (e.classList.contains('drop')) e.classList.remove('drop'); });
    const segments = [...cap.querySelectorAll('.cap-i, .cap-tag')];
    let width = segments.reduce((sum, el) => sum + el.getBoundingClientRect().width + CAP_GAP_PX, -CAP_GAP_PX);
    for (const e of items) { if (width <= cap.clientWidth + 1) break; width -= e.getBoundingClientRect().width + CAP_GAP_PX; e.classList.add('drop'); }
  }
  // A still sky has no weather frame to notice expiry. Both paths repaint words and storm visuals together.
  function scheduleStormExpiry(v) {
    const at = !quiet && !document.hidden && !preview && v?.thunder ? weatherAt.thunder + WEATHER_CACHE_MS : Infinity;
    if (at === stormExpiryAt) return;
    clearTimeout(stormExpiryTimer); stormExpiryAt = at;
    if (at < Infinity) stormExpiryTimer = setTimeout(paint, Math.max(0, at - Date.now()));
  }
  function paint(now = Date.now()) {
    const v = st(now), sp = sunPos(v.minutes); elev = sp.el; azim = sp.az;
    scheduleStormExpiry(v);
    const sky = $('sky'), dark = document.documentElement.dataset.theme === 'dark';
    sky.classList.toggle('off', !on.sky && !on.wind && !on.air);
    let [top, hor] = skyAt(elev);
    const k = (v.cloud || 0) / 100 * .32; top = gray(top, k); hor = gray(hor, k);
    const rain = on.sky ? (v.rain || 0) : 0;
    if (rain) { top = mix(top, [34, 40, 54], rain * .72); hor = mix(hor, [72, 80, 96], rain * .62); }
    if (dark) { top = mix(top, [10, 16, 26], .5); hor = mix(hor, [14, 22, 34], .5); }
    tgt.top = top; tgt.hor = hor;
    // the monitor cards read these: the outdoor card shows this sky, the indoor card's window looks out on it
    const rs = document.documentElement.style, outLum = (top[0] * .3 + top[1] * .59 + top[2] * .11 + hor[0] * .3 + hor[1] * .59 + hor[2] * .11) / 2;
    rs.setProperty('--sky-top', `rgb(${top.map(Math.round)})`); rs.setProperty('--sky-hor', `rgb(${hor.map(Math.round)})`);
    // a light pastel of the same hue, for tints: keeps the sky's color (indigo at night, warm at dusk) without its darkness
    const pastel = (c, L) => { const [r, g, b] = c.map(v => v / 255), mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
      let h = 0; if (d) h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4; h = (h * 60 + 360) % 360;
      const sat = Math.max(.45, Math.min(.75, d / (1 - Math.abs(mx + mn - 1) || 1))); return `hsl(${Math.round(h)} ${Math.round(sat * 100)}% ${L}%)`; };
    rs.setProperty('--sky-tint', pastel(top, 90)); rs.setProperty('--sky-tint2', pastel(hor, 93));
    // the header scenes follow the sky in the page's soft palette: same hue, lighter; a little deeper at night
    rs.setProperty('--scene-top', pastel(top, elev > -2 ? 84 : 72)); rs.setProperty('--scene-hor', pastel(hor, elev > -2 ? 90 : 80));
    document.documentElement.classList.toggle('outside-dark', outLum < 140);
    document.documentElement.classList.toggle('outside-night', elev < -6);
    // the header scenes follow the weather too (rain on the glass, clouds, light behind the doors at night)
    document.documentElement.classList.toggle('wx-rain', (v.rain || 0) > .05 || !!v.thunder);
    document.documentElement.classList.toggle('wx-cloud', (v.cloud || 0) >= 60);
    cloudCover = on.sky ? (v.cloud || 0) / 100 : 0;
    cloudTint = elev > 8 ? [255, 255, 255] : elev > -2 ? [255, 214, 196] : elev > -8 ? [150, 150, 180] : (dark ? [58, 68, 92] : [74, 84, 110]);
    if (rain) cloudTint = mix(cloudTint, [68, 74, 90], rain * .8);
    rainAmt = on.sky ? rain : 0; syncStorm(v);
    if (!cur.top || reduce) { cur.top = top.slice(); cur.hor = hor.slice(); }
    $('sky-grad').style.opacity = on.sky ? 1 : 0;
    document.documentElement.classList.toggle('sky-on', on.sky);
    inkLayoutDirty = true; inkDue = true;   // the next frame looks at the new sky behind the text (measureInk)
    // sun glow: horizontal place from azimuth (east left, west right), height from elevation
    const clear = 1 - (v.cloud || 0) / 100 * .85;
    const glow = on.sky ? Math.max(0, Math.min(1, (elev + 5) / 6)) * (elev > 25 ? Math.max(.25, 1 - (elev - 25) / 40) : 1) * clear : 0;
    const sun = $('sky-sun');
    sunAt = { x: Math.max(4, Math.min(96, 50 + (azim - 180) / 120 * 50)), y: Math.max(6, Math.min(78, 70 - elev * 1.4)), a: glow, warm: elev < 12 };
    sun.style.left = `${sunAt.x}%`;
    sun.style.top = `${sunAt.y}%`;
    sun.style.opacity = `calc(${glow.toFixed(2)} * (1 - var(--sky-smoke-cover, 0)))`;
    sun.classList.toggle('warm', sunAt.warm);
    // stars: only when it is dark and the sky is not covered
    $('sky-stars').style.opacity = on.sky ? (Math.max(0, Math.min(1, (-elev - 4) / 8)) * (1 - (v.cloud || 0) / 100 * .65)).toFixed(2) : 0;
    starsOn = on.sky && elev < -4;
    $('needle') && ($('needle').style.transform = `rotate(${(live.dir + 180) % 360}deg)`);
    renderCap(now, v);
    fitCap();   // the weather line stays when the effects are off, so the layout never shifts
    syncPanel();
    if (glass) glass.redraw();
    if (wet) wet.redraw();
    kick();
  }
  // layers
  const cv = $('sky-wind'), ctx = cv.getContext('2d'), sc = $('sky-stars'), sctx = sc.getContext('2d'), cc = $('sky-clouds'), cctx = cc.getContext('2d');
  let clouds = [], sprites = [], cloudCover = 0, cloudTint = [255, 255, 255];
  const rc = $('sky-rain'), rctx = rc.getContext('2d'), bc = $('sky-bolt'), bctx = bc.getContext('2d'); let drops = [], rainAmt = 0, thunder = false, nextBolt = 0, bolt = null;
  let rearFlash = null, frontFlash = null, flashX = .5, rearFlashLevels = FLASH_BACK_LEVELS;
  const stormAnimations = new Set();
  let rearSceneDirty = true, wetMaterialDirty = false, rearGradient = '', windPainted = false, cloudsPainted = false;
  let W = 0, H = 0, lines = [], stars = [], starsOn = false, last = 0, raf = 0, visible = true, resizeDue = false;
  const spawn = (p, anywhere) => {
    p.z = .35 + Math.random() * .65;                  // depth: far lines are thinner, slower, fainter
    p.x = Math.random() * W; p.y = Math.random() * H;
    p.l = (110 + Math.random() * 150) * p.z; p.life = anywhere ? Math.random() : 0; p.span = 4 + Math.random() * 6; p.seed = Math.random() * 1000;
    return p;
  };
  function resize() {
    if (quiet) return;
    if (document.hidden) { resizeDue = true; return; }
    resizeDue = false;
    const dpr = window.devicePixelRatio || 1;
    W = innerWidth; H = innerHeight;
    for (const [c, x] of [[cv, ctx], [sc, sctx], [cc, cctx], [rc, rctx], [bc, bctx]]) { c.width = W * dpr; c.height = H * dpr; x.setTransform(dpr, 0, 0, dpr, 0, 0); }
    // cloud sprites: soft clusters of white puffs, drawn once and reused
    sprites = Array.from({ length: 5 }, () => {
      const w = 520 + Math.random() * 360, h = w * (.32 + Math.random() * .1), o = document.createElement('canvas'); o.width = w; o.height = h;
      const g = o.getContext('2d');
      for (let i = 0; i < 16; i++) {
        const px = w * (.15 + Math.random() * .7), py = h * (.45 + Math.random() * .3), r = h * (.22 + Math.random() * .3);
        const rg = g.createRadialGradient(px, py, 0, px, py, r);
        rg.addColorStop(0, 'rgba(255,255,255,.55)'); rg.addColorStop(.6, 'rgba(255,255,255,.22)'); rg.addColorStop(1, 'rgba(255,255,255,0)');
        g.fillStyle = rg; g.beginPath(); g.arc(px, py, r, 0, Math.PI * 2); g.fill();
      }
      return o;
    });
    clouds = Array.from({ length: 16 }, (_, i) => ({ s: sprites[i % sprites.length], x: Math.random() * (W + 800) - 400, y: Math.random() * H * .7 - 60,
      z: .5 + Math.random() * .5, k: (i + .5) / 16, a: 0 }));
    lines = Array.from({ length: Math.round(W * H / 11000) }, () => spawn({}, true));
    drops = Array.from({ length: Math.round(W * H / 1800) }, () => ({ x: Math.random() * W, y: Math.random() * H, z: .35 + Math.random() * .65, k: Math.random() }));
    stars = Array.from({ length: Math.round(W * H / 6500) }, () => {
      const big = Math.random() < .07;
      return { x: Math.random() * W, y: Math.random() * H * .8, r: big ? 1.4 + Math.random() * .5 : .6 + Math.random() * .5,
               a: big ? .85 : .4 + Math.random() * .45, tw: Math.random() < .35, ph: Math.random() * 6.28, sp: .0006 + Math.random() * .0012 };
    });
    drawStars(0);
  }
  function drawStars(t) {
    sctx.clearRect(0, 0, W, H);
    for (const st2 of stars) {
      const a = st2.tw && !reduce ? st2.a * (.6 + .4 * Math.sin(t * st2.sp + st2.ph)) : st2.a;
      sctx.fillStyle = `rgba(236,243,255,${a.toFixed(3)})`; sctx.beginPath(); sctx.arc(st2.x, st2.y, st2.r, 0, Math.PI * 2); sctx.fill();
      if (st2.r > 1.3) { sctx.fillStyle = `rgba(200,220,255,${(a * .18).toFixed(3)})`; sctx.beginPath(); sctx.arc(st2.x, st2.y, st2.r * 3, 0, Math.PI * 2); sctx.fill(); }
    }
  }
  // A jagged path by midpoint displacement: reads as lightning, not as a zigzag.
  const jag = (x1, y1, x2, y2, d, out) => {
    if (d < 6) { out.push([x2, y2]); return out; }
    const mx = (x1 + x2) / 2 + (Math.random() - .5) * d, my = (y1 + y2) / 2 + (Math.random() - .5) * d * .25;
    jag(x1, y1, mx, my, d / 2, out); jag(mx, my, x2, y2, d / 2, out); return out;
  };
  function strike(t) {
    for (const animation of stormAnimations) if (animation.playState !== 'running') stormAnimations.delete(animation);
    const animate = (el, frames, timing) => { const animation = el.animate(frames, timing); stormAnimations.add(animation); return animation; };
    const cards = [...document.querySelectorAll('.card, section.sum-card')].map(el => [el, el.getBoundingClientRect()])
      .filter(([, r]) => r.width > 0 && r.top > 70 && r.top < H * .75);
    const front = cards.length > 0 && Math.random() < .7;
    let x0 = W * (.15 + Math.random() * .7), ex = x0 + (Math.random() - .5) * 220, ey = H * (.3 + Math.random() * .3), hit = null;
    if (front) { const [el, r] = cards[Math.floor(Math.random() * cards.length)]; hit = el; ex = r.left + r.width * (.15 + Math.random() * .7); ey = r.top + 1; x0 = ex + (Math.random() - .5) * Math.min(420, W * .35); }
    const pts = jag(x0, -20, ex, ey, Math.max(120, ey * .35), [[x0, -20]]), branches = [];
    for (let i = 3; i < pts.length - 4; i += 2) if (Math.random() < .09) {
      const [bx, by] = pts[i], dir = Math.random() < .5 ? -1 : 1;
      branches.push(jag(bx, by, bx + dir * (40 + Math.random() * 110), by + 50 + Math.random() * 90, 40, [[bx, by]]));
    }
    // light: the sky flashes behind; a front strike also lights the page and throws the cards' shadows away from it
    rearFlashLevels = front ? FLASH_BACK_FRONT_LEVELS : FLASH_BACK_LEVELS;
    rearFlash = animate($('sky-flash'), rearFlashLevels.map(opacity => ({ opacity })), { duration: 700, easing: 'ease-out' });
    if (front) {
      const ff = $('front-flash'); flashX = ex / W; ff.style.setProperty('--bx', `${(flashX * 100).toFixed(1)}%`);
      frontFlash = animate(ff, FLASH_FRONT_LEVELS.map(opacity => ({ opacity })), { duration: 700, easing: 'ease-out' });
      for (const [el, r] of [...document.querySelectorAll('.card, section.sum-card, .bar .custom, .bar .dsp')].map(el => [el, el.getBoundingClientRect()])) {
        if (r.bottom < 0 || r.top > H) continue;
        const dx = Math.max(-18, Math.min(18, (r.left + r.width / 2 - ex) / 30)), dy = Math.max(6, Math.min(22, (r.top - ey) / 25 + 10));
        const lit = `${dx.toFixed(1)}px ${dy.toFixed(1)}px 26px rgba(4, 10, 30, .38)`, rest = getComputedStyle(el).boxShadow;
        const shadow = el === hit ? `0 -1px 0 rgba(225,235,255,.95), 0 -6px 34px rgba(170,195,255,.75), ${lit}` : lit;
        animate(el, [{ boxShadow: rest }, { boxShadow: shadow, offset: .08 }, { boxShadow: rest, offset: .2 }, { boxShadow: shadow, offset: .3 }, { boxShadow: rest }], { duration: 760, easing: 'ease-out' });
      }
      if (hit) animate(hit, [{ transform: 'none' }, { transform: 'translateY(1.5px)', offset: .25 }, { transform: 'none' }], { duration: 260, delay: 120, easing: 'ease-out' });
    }
    return { pts, branches, t0: t, front };
  }
  function cancelStorm() {
    for (const animation of stormAnimations) animation.cancel();
    stormAnimations.clear(); rearFlash = frontFlash = null;
    if (bolt) { bctx.clearRect(0, 0, W, H); rctx.clearRect(0, 0, W, H); }
    bolt = null; nextBolt = 0;
  }
  function syncStorm(v) {
    const next = !!v.thunder && on.sky && !reduce;
    if (!next && (thunder || bolt || nextBolt || stormAnimations.size)) cancelStorm();
    thunder = next;
  }
  function frame(t) {
    if (quiet || document.hidden) { raf = 0; return; }
    const now = Date.now();
    if (now >= stormExpiryAt) paint(now);
    prepareInk();
    const dt = Math.min(50, t - last || 16); last = t; const v = st(now);
    syncStorm(v);
    const kk = 1 - Math.exp(-dt / 450);                 // ~1 s to settle
    let moving = false;
    if (cur.top) {
      cur.top = cur.top.map((c, i) => ease(c, tgt.top[i], kk)); cur.hor = cur.hor.map((c, i) => ease(c, tgt.hor[i], kk));
      moving = cur.top.some((c, i) => Math.abs(c - tgt.top[i]) > .5) || cur.hor.some((c, i) => Math.abs(c - tgt.hor[i]) > .5);
      const r = a => a.map(Math.round).join(',');
      const background = `linear-gradient(180deg, rgb(${r(cur.top)}) 0%, rgb(${r(cur.hor)}) 100%)`;
      if (rearGradient !== background) { $('sky-grad').style.background = rearGradient = background; rearSceneDirty = true; }
    }
    cur.dir = cur.dir == null ? (v.dir || 0) : angEase(cur.dir, v.dir || 0, kk);
    cur.spd = ease(cur.spd, v.spd || 0, kk);
    let drawsWind = false;
    ctx.clearRect(0, 0, W, H);
    if (on.wind) {
      const lightSky = elev > -2 && document.documentElement.dataset.theme !== 'dark' && on.sky;
      const base = lightSky ? '255,255,255' : '214,228,255', boost = lightSky ? 2.6 : 1.15;
      ctx.shadowColor = 'transparent'; ctx.shadowBlur = 0;
      const strength = Math.min(1, .35 + cur.spd / 12);
      ctx.lineCap = 'round';
      const share = Math.min(1, .25 + cur.spd / 26);   // calm: a few lines; strong wind: all of them
      const head = (cur.dir + 180) * Math.PI / 180;
      // Side view: wind crosses the screen almost level, so it never reads as falling rain.
      let hx = Math.sin(head); if (Math.abs(hx) < .45) hx = hx < 0 ? -.45 : .45;
      const vy = -Math.cos(head) * .05, dark = !lightSky;
      for (let li = 0; li < lines.length; li++) {
        const p = lines[li]; if (li / lines.length > share) continue;
        const bend = Math.sin(p.y * .005 + p.seed + t * .0002) * .07;
        const ux = hx, uy = vy + bend;
        if (!reduce) { const step = cur.spd * .03 * dt * p.z; p.x += ux * step; p.y += uy * step; p.life += dt / 1000 / p.span; }
        if (p.life >= 1 || p.x < -260 || p.x > W + 260 || p.y < -150 || p.y > H + 150) { spawn(p, false); continue; }
        // gusts: a slow wave sweeps across the screen in the wind's direction
        const gust = .35 + .65 * Math.max(0, Math.sin(p.x * .0028 * Math.sign(hx) - t * .0011 + p.y * .0009));
        const fade = Math.sin(Math.PI * Math.min(1, p.life)), a = Math.min(.85, (.14 + .4 * p.z) * fade * strength * boost * gust * (dark ? .8 : 1));
        if (a < .01) continue;
        drawsWind = true;
        const len = p.l * Math.min(1, .35 + cur.spd / 16), n = 10, amp = (1.5 + p.z * 3) * Math.min(1, .4 + cur.spd / 20);
        // a ribbon with a gentle ripple that travels along it
        ctx.beginPath(); ctx.moveTo(p.x, p.y);
        let x = p.x, y = p.y;
        for (let s2 = 1; s2 <= n; s2++) {
          const f = s2 / n, rip = Math.sin(f * 5.2 + p.seed - t * .004) * amp * f;
          ctx.lineTo(p.x - ux * len * f, p.y - uy * len * f + rip); x = p.x - ux * len * f; y = p.y - uy * len * f;
        }
        const g = ctx.createLinearGradient(p.x, p.y, x, y);
        g.addColorStop(0, `rgba(${base},${(a * .35).toFixed(3)})`); g.addColorStop(.2, `rgba(${base},${a.toFixed(3)})`); g.addColorStop(1, `rgba(${base},0)`);
        if (lightSky) {   // a darker blue edge under the white core keeps ribbons visible on a bright sky
          const e = ctx.createLinearGradient(p.x, p.y, x, y);
          e.addColorStop(0, 'rgba(28,74,140,0)'); e.addColorStop(.2, `rgba(28,74,140,${(a * .38).toFixed(3)})`); e.addColorStop(1, 'rgba(28,74,140,0)');
          ctx.strokeStyle = e; ctx.lineWidth = (.7 + p.z) * 3; ctx.stroke();
        }
        ctx.strokeStyle = g; ctx.lineWidth = (.6 + p.z * .9) * (lightSky ? 1.4 : 1); ctx.stroke();
      }
    }
    if (drawsWind || windPainted) rearSceneDirty = true;
    windPainted = drawsWind;
    if (starsOn) { drawStars(t); rearSceneDirty = true; }
    // rain: slanted by the wind, three depths; only the share of drops that matches the intensity is drawn
    rctx.clearRect(0, 0, W, H);
    if (rainAmt > .02) {
      const tilt = Math.sin((cur.dir + 180) * Math.PI / 180) * Math.min(.6, cur.spd / 40);
      rctx.lineCap = 'round';
      for (const d of drops) {
        if (d.k > rainAmt) continue;
        const fall = (.9 + d.z * 1.3) * dt * 1.1;
        if (!reduce) { d.y += fall; d.x += fall * tilt; }
        if (d.y > H + 20) { d.y = -20 - Math.random() * 60; d.x = Math.random() * (W + 200) - 100; }
        if (d.x > W + 40) d.x -= W + 80; if (d.x < -40) d.x += W + 80;
        const len = 10 + d.z * 16;
        rctx.strokeStyle = `rgba(206,220,240,${(.18 + d.z * .38).toFixed(3)})`; rctx.lineWidth = .7 + d.z * .9;
        rctx.beginPath(); rctx.moveTo(d.x, d.y); rctx.lineTo(d.x - tilt * len, d.y - len); rctx.stroke();
      }
    }
    // lightning: sheet flashes behind the page, and now and then a strike in front of it that lands on a card
    if (bolt || thunder) bctx.clearRect(0, 0, W, H);
    if (thunder && visible) {
      if (!nextBolt) nextBolt = t + 1200;
      if (t > nextBolt) { nextBolt = t + 4000 + Math.random() * 6000; bolt = strike(t); }
    } else { nextBolt = 0; }
    if (bolt) {
      const age = t - bolt.t0;
      // a strike flickers: on, off, a brighter re-strike, then it fades
      const a = age < 90 ? 1 : age < 150 ? 0 : age < 520 ? 1 - (age - 150) / 370 : -1;
      if (a < 0) { bolt = null; bctx.clearRect(0, 0, W, H); }
      else if (a > 0) {
        const c = bolt.front ? bctx : rctx;
        c.save(); c.lineJoin = 'round'; c.lineCap = 'round';
        const path = pts => { c.beginPath(); pts.forEach(([px, py], i) => i ? c.lineTo(px, py) : c.moveTo(px, py)); };
        for (const [w, col, blur] of [[12, `rgba(150,180,255,${(.16 * a).toFixed(3)})`, 30], [4.5, `rgba(190,210,255,${(.5 * a).toFixed(3)})`, 12], [1.9, `rgba(255,255,255,${a.toFixed(3)})`, 4]]) {
          c.shadowColor = 'rgba(170,195,255,.9)'; c.shadowBlur = blur; c.strokeStyle = col;
          c.lineWidth = w; path(bolt.pts); c.stroke();
          c.lineWidth = w * .45; for (const br of bolt.branches) { path(br); c.stroke(); }
        }
        if (bolt.front) {   // the impact glows on the card's top edge
          const [ix, iy] = bolt.pts[bolt.pts.length - 1], rg = c.createRadialGradient(ix, iy, 0, ix, iy, 70);
          rg.addColorStop(0, `rgba(235,242,255,${(.9 * a).toFixed(3)})`); rg.addColorStop(1, 'rgba(200,215,255,0)');
          c.shadowBlur = 0; c.fillStyle = rg; c.beginPath(); c.arc(ix, iy, 70, 0, Math.PI * 2); c.fill();
        }
        c.restore();
      }
    }
    const cloudsMoving = cloudCover > .01;
    let drawsClouds = false;
    cctx.clearRect(0, 0, W, H);
    if (cloudCover > 0.01 || clouds.some(c => c.a > .01)) {
      const to = (cur.dir + 180) * Math.PI / 180, drift = Math.sin(to) * (cur.spd || 0) * .004 * dt;
      // an even veil when it is mostly or fully overcast
      if (cloudCover > .5) { drawsClouds = true; cctx.fillStyle = `rgba(${cloudTint},${((cloudCover - .5) * .5).toFixed(3)})`; cctx.fillRect(0, 0, W, H); }
      for (const c of clouds) {
        const want = Math.min(1, Math.max(0, Math.min(1, (cloudCover - c.k * .9) * 4)) * (1 + rainAmt * .6));   // more clouds as cover rises; heavier in rain
        const previousAlpha = c.a, previousX = c.x;
        c.a += (want - c.a) * kk;
        if (!reduce) c.x += drift * c.z;
        const w = c.s.width * c.z * 1.4, h = c.s.height * c.z * 1.4;
        if (c.x > W + 50) c.x = -w; if (c.x < -w - 50) c.x = W;
        if (c.a < .01) { if (previousAlpha >= .01) rearSceneDirty = true; continue; }
        drawsClouds = true;
        if (c.x !== previousX || previousAlpha < .01 || Math.round(c.a * 255) !== Math.round(previousAlpha * 255)) rearSceneDirty = true;
        cctx.globalAlpha = c.a * (.55 + .45 * c.z);
        cctx.drawImage(c.s, c.x, c.y, w, h);
      }
      cctx.globalAlpha = 1;
      // tint the white sprites to the light of the moment
      cctx.globalCompositeOperation = 'source-atop'; cctx.fillStyle = `rgb(${cloudTint})`; cctx.fillRect(0, 0, W, H); cctx.globalCompositeOperation = 'source-over';
    }
    if (drawsClouds !== cloudsPainted) rearSceneDirty = true;
    cloudsPainted = drawsClouds;
    const drawOutdoor = outdoorFrame, outdoorChangesRear = outdoorRear;
    outdoorFrame = null; outdoorRear = false;
    if (drawOutdoor) drawOutdoor(t);
    const drawWet = wetFrame; wetFrame = null;
    if (drawWet) drawWet(t);
    // Rain and flashes are drawn above wet glass and do not change what its lenses refract.
    wet?.refreshComposite(rearSceneDirty || (!!drawOutdoor && outdoorChangesRear), wetMaterialDirty);
    rearSceneDirty = wetMaterialDirty = false;
    const weatherChanging = moving || !!bolt || rearFlash?.playState === 'running' || frontFlash?.playState === 'running';
    if (inkDue || weatherChanging || inkWeatherChanging || t - inkAt > INK_EVERY) measureInk(t);
    inkWeatherChanging = weatherChanging;
    raf = 0;
    if (!document.hidden && (outdoorFrame || wetFrame ||
        (!reduce && visible && (on.wind || weatherChanging || starsOn || cloudsMoving || rainAmt > .02 || thunder)))) {
      raf = requestAnimationFrame(frame);
    }
  }
  // ── The ink of the text on the sky ──
  // Sample the weather and then the two production effect layers. Every piece of text chooses from its own samples.
  let sunAt = { x: 50, y: 50, a: 0, warm: false }, inkDue = true, inkAt = -1e9, inkScrollTimer = 0, inkPatch = null;
  let inkLayoutDirty = true, inkTargets = [], inkTokens = null, inkWeatherChanging = false, outdoorFrame = null, outdoorRear = false, wetFrame = null;
  const lin = c => { c /= 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; };
  const relLum = c => .2126 * lin(c[0]) + .7152 * lin(c[1]) + .0722 * lin(c[2]);
  const ratio = (a, b) => (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
  const over = (b, c, a) => b.map((v, i) => v + (c[i] - v) * a);
  const glowAt = r => {   // the sun's radial gradient: [rgb, alpha] at r, a share of its radius
    const s = sunAt.warm ? SUN_GLOW_WARM : SUN_GLOW;
    if (r >= s[2][0]) return null;
    const [a, b] = r < s[1][0] ? [s[0], s[1]] : [s[1], s[2]], k = (r - a[0]) / (b[0] - a[0]);
    return [mix(a[1], b[1], k), a[2] + (b[2] - a[2]) * k];
  };
  function pickInk(worst, darkInk, lightInk, previous, floor) {
    const dark = worst(darkInk), light = worst(lightInk);
    // Hysteresis is only allowed to keep ink that still meets the heading's contrast floor.
    const best = light > dark;
    if (previous != null && previous !== best) {
      const keep = previous ? light : dark, next = best ? light : dark;
      if (keep >= floor && next <= keep * INK_FLIP) return { light: previous, ink: previous ? lightInk : darkInk, bare: keep };
    }
    return { light: best, ink: best ? lightInk : darkInk, bare: best ? light : dark };
  }
  function weatherPixels(layers, x, y, w, h) {
    if (!layers.length) return null;
    // Read just the text patch once, not the full (possibly retina-sized) weather canvases.
    if (!inkPatch) inkPatch = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
    if (inkPatch.canvas.width < w) inkPatch.canvas.width = w;
    if (inkPatch.canvas.height < h) inkPatch.canvas.height = h;
    inkPatch.clearRect(0, 0, w, h);
    for (const layer of layers) {
      const blur = layer === cc ? inkTokens.cloudBlur : 0, pad = Math.ceil(blur * CLOUD_BLUR_PAD);
      const x0 = Math.max(0, x - pad), y0 = Math.max(0, y - pad);
      const sw = Math.min(W, x + w + pad) - x0, sh = Math.min(H, y + h + pad) - y0;
      const sx = layer.width / W, sy = layer.height / H;
      inkPatch.save();
      let offset = 0;
      if (blur && 'filter' in inkPatch) inkPatch.filter = `blur(${blur}px)`;
      else if (blur) {
        // The cloud canvas is uniformly tinted. If Canvas filters are unavailable, blur that tint by its alpha.
        offset = w + pad;
        inkPatch.shadowOffsetX = offset;
        inkPatch.shadowBlur = blur * 2; // Canvas shadows express twice the Gaussian sigma used by CSS blur.
        inkPatch.shadowColor = `rgb(${cloudTint})`;
      }
      inkPatch.drawImage(layer, x0 * sx, y0 * sy, sw * sx, sh * sy, x0 - x - offset, y0 - y, sw, sh);
      inkPatch.restore();
    }
    return inkPatch.getImageData(0, 0, w, h).data;
  }
  // Called before renderer writes on layout/theme/content invalidation, never once per particle frame.
  function prepareInk() {
    if (!inkLayoutDirty || !W || !H) return;
    inkLayoutDirty = false;
    const html = document.documentElement, style = getComputedStyle(html);
    inkTokens = {
      heading: ['--sky-heading-on-light', '--sky-heading-on-dark'].map(k => style.getPropertyValue(k).trim().split(/\s+/).map(Number)),
      body: getComputedStyle(document.body).backgroundColor.match(/\d+/g).slice(0, 3).map(Number),
      cloudBlur: Number(getComputedStyle(cc).filter.match(/blur\(([\d.]+)px\)/)?.[1]) || 0,
    };
    const range = document.createRange(); inkTargets = [];
    // Sample the individual weather words; empty flex space and hidden Today/mark are not text backdrops.
    for (const el of document.querySelectorAll('.now-bar .now-mark, .now-bar .now-h, .now-bar .cap-i, [data-sky-heading]')) {
      const heading = el.hasAttribute('data-sky-heading');
      const box = el.getBoundingClientRect();
      if (box.width < 2 || box.height < 2) continue;
      range.selectNodeContents(el);
      const rects = [...range.getClientRects()].filter(r => r.width >= 2 && r.height >= 2);
      if (!rects.length) continue;
      const points = [], pad = heading ? HEADING_SAMPLE_PAD : 0;
      for (const r of rects) {
        const x0 = Math.floor(r.left - pad), x1 = Math.ceil(r.right + pad);
        const y0 = Math.floor(r.top - pad), y1 = Math.ceil(r.bottom + pad);
        for (let i = 0; i < INK_GRID[0]; i++) for (let j = 0; j < INK_GRID[1]; j++) {
          points.push([x0 + (x1 - x0) * (i + .5) / INK_GRID[0] + scrollX,
            y0 + (y1 - y0) * (j + .5) / INK_GRID[1] + scrollY]);
        }
      }
      inkTargets.push({ el, heading, points, light: el.dataset.skyInkLight == null ? null : el.dataset.skyInkLight === 'true' });
    }
  }
  function flashOpacity(animation, levels) {
    const progress = animation?.effect.getComputedTiming().progress;
    if (progress == null) return 0;
    const at = Math.min(levels.length - 2, Math.floor(progress * (levels.length - 1)));
    return levels[at] + (levels[at + 1] - levels[at]) * (progress * (levels.length - 1) - at);
  }
  function sampleFlash(rgb, x, y, front, opacity) {
    if (!opacity || reduce) return rgb;
    const size = front ? FLASH_FRONT_SIZE : FLASH_BACK_SIZE, stops = front ? FLASH_FRONT_STOPS : FLASH_BACK_STOPS;
    const radius = Math.hypot((x / W - (front ? flashX : .5)) / size[0], y / H / size[1]);
    const index = stops.findIndex(stop => stop[0] > radius);
    if (index < 1) return rgb;
    const a = stops[index - 1], b = stops[index], t = (radius - a[0]) / (b[0] - a[0]);
    const color = mix(a[1], b[1], t), alpha = (a[2] + (b[2] - a[2]) * t) * opacity;
    return front ? rgb.map((v, i) => 255 - (255 - v) * (1 - color[i] / 255 * alpha)) : over(rgb, color, alpha);
  }
  function measureInk(t) {
    // Weather → outdoor → wet have drawn on this frame; sampling must not advance a renderer.
    // Geometry and tokens are cached. Read the current pixels.
    if (!inkTokens) return;
    inkDue = false; inkAt = t;
    clearTimeout(inkScrollTimer); inkScrollTimer = 0;
    const html = document.documentElement;
    if (!on.sky && !on.wind && !(airEnabled() && conditions.length)) { html.classList.remove('sky-ink', 'sky-dark'); return; }
    const R = SUN_R * Math.max(W, H), updates = [], weatherLayers = [];
    const sunAlpha = sunAt.a * (1 - (glass?.smokeCover ?? 0));
    if (cloudCover > .01 || clouds.some(c => c.a > .01)) weatherLayers.push(cc);
    if (on.wind) weatherLayers.push(cv);
    const rearOpacity = flashOpacity(rearFlash, rearFlashLevels), frontOpacity = flashOpacity(frontFlash, FLASH_FRONT_LEVELS);
    const screenPixel = (rgb, data, at) => data && data[at + 3] ? over(rgb, data.subarray(at, at + 3), data[at + 3] / 255) : rgb;
    for (const target of inkTargets) {
      const { el, heading, points } = target;
      const positions = points.map(([x, y]) => [x - scrollX, y - scrollY]).filter(([x, y]) => x >= 0 && x < W && y >= 0 && y < H);
      if (!positions.length) continue;
      const x0 = Math.floor(Math.min(...positions.map(p => p[0]))), x1 = Math.ceil(Math.max(...positions.map(p => p[0]))) + 1;
      const y0 = Math.floor(Math.min(...positions.map(p => p[1]))), y1 = Math.ceil(Math.max(...positions.map(p => p[1]))) + 1;
      const px = weatherPixels(weatherLayers, x0, y0, x1 - x0, y1 - y0);
      const rain = weatherPixels(rainAmt > .02 || (bolt && !bolt.front) ? [rc] : [], x0, y0, x1 - x0, y1 - y0);
      const strike = weatherPixels(bolt?.front ? [bc] : [], x0, y0, x1 - x0, y1 - y0);
      const samples = positions.map(([x, y]) => {
        let c = on.sky ? mix(cur.top || tgt.top, cur.hor || tgt.hor, y / H) : inkTokens.body;
        if (on.sky && sunAlpha > 0) {
          const g = glowAt(Math.hypot(x - sunAt.x / 100 * W, y - sunAt.y / 100 * H) / R);
          if (g) c = c.map((v, k) => 255 - (255 - v) * (1 - g[0][k] / 255 * g[1] * sunAlpha));
        }
        const p = (Math.floor(y - y0) * (x1 - x0) + Math.floor(x - x0)) * 4;
        c = screenPixel(c, px, p);
        if (glass) c = glass.sampleRear(c, x, y);
        // Rain follows rear outdoor air in #sky; the wet pane fogs over the rain; the flash is on top.
        c = screenPixel(c, rain, p);
        if (wet) c = wet.sampleBackground(c, x, y, el);
        c = sampleFlash(c, x, y, false, rearOpacity);
        // Foreground air/lightning are painted over the ink itself, not just over its neighboring pixels.
        const front = glass?.frontActive || frontOpacity || strike ? rgb => {
          if (glass?.frontActive) rgb = glass.sampleFront(rgb, x, y);
          return screenPixel(sampleFlash(rgb, x, y, true, frontOpacity), strike, p);
        } : null;
        return { c, front };
      });
      const [darkInk, lightInk] = heading ? inkTokens.heading : [INK_ON_LIGHT, INK_ON_DARK];
      const worst = (ink, backing, alpha = 1) => Math.min(...samples.map(({ c, front }) => {
        const bg = !backing ? c : alpha === 1 ? backing : over(c, backing, alpha);
        return front ? ratio(relLum(front(ink)), relLum(front(bg))) : ratio(relLum(ink), relLum(bg));
      }));
      let { light, ink, bare } = pickInk(worst, darkInk, lightInk, target.light, heading ? HEADING_CONTRAST : INK_CONTRAST);
      // A heading keeps its ink through a passing haze, fog or flash: the other ink takes over only once it has read better
      // for HEADING_OUTLINE_HOLD_MS, and meanwhile the kept ink gets its outline (which always reads).
      let held = false;
      const airShown = heading && airEnabled() && conditions.length > 0;   // an air effect is shown: the heading's look holds while it lasts
      if (heading && target.light != null && light !== target.light) {
        const now = performance.now(), since = flipWanted.get(el) ?? now;
        flipWanted.set(el, since);
        const keepInk = target.light ? lightInk : darkInk, keepOutline = target.light ? darkInk : lightInk;
        const kept = worst(keepInk, keepOutline), other = worst(ink, target.light ? lightInk : darkInk);
        if ((airShown || now - since < HEADING_OUTLINE_HOLD_MS) && (kept >= HEADING_CONTRAST || kept >= other * HEADING_KEEP_SHARE)) {
          light = target.light; ink = keepInk; bare = worst(keepInk); held = true;
        }
      } else if (heading) flipWanted.delete(el);
      target.light = light;
      if (heading) {
        // Only the opaque glyph stroke is a contrast partner. It never changes the sky behind a word.
        const now = performance.now();
        // While an air effect is shown, the outline stays on the whole time: the air moving behind a heading would otherwise
        // switch its look as the opening ends or the drift passes (Sacha, 26 Nov: "On particles the home monitors still changes style").
        let outline = airShown || held || bare < HEADING_CONTRAST ? (light ? darkInk : lightInk) : null;
        if (outline) outlineSeen.set(el, now);
        else if (now - (outlineSeen.get(el) ?? -Infinity) < HEADING_OUTLINE_HOLD_MS) outline = light ? darkInk : lightInk;
        const assisted = outline ? worst(ink, outline) : bare;
        updates.push({ el, heading, light, ink, outline, contrast: `${bare.toFixed(1)}/${assisted.toFixed(1)}` });
        continue;
      }
      const halo = light ? HALO_ON_DARK : HALO_ON_LIGHT;
      const withHalo = a => worst(ink, halo, a);
      let a = HALO_MIN;
      while (a < HALO_MAX && withHalo(a) < INK_CONTRAST) a = Math.min(HALO_MAX, a + HALO_STEP);
      updates.push({ el, heading, light, ink, halo, a, contrast: `${bare.toFixed(1)}/${withHalo(a).toFixed(1)}` });
    }
    // Finish every geometry/canvas read before writing any ink styles.
    for (const { el, heading, light, ink, outline, halo, a, contrast } of updates) {
      el.style.setProperty('--sky-ink', `rgb(${ink})`);
      el.dataset.skyInkLight = String(light);
      if (heading) el.style.setProperty('--sky-heading-outline', outline ? `rgb(${outline})` : 'transparent');
      else el.style.setProperty('--sky-halo', `rgba(${halo},${a.toFixed(2)})`);
      el.dataset.skyContrast = contrast;
    }
    if (!updates.length) return;
    html.classList.add('sky-ink');
    const header = updates.find(update => !update.heading);
    if (!header) return;
    html.classList.toggle('sky-dark', header.light);
    html.style.setProperty('--sky-ink', `rgb(${header.ink})`);
    html.style.setProperty('--sky-halo', `rgba(${header.halo},${header.a.toFixed(2)})`);
    html.style.setProperty('--sky-halo-rgb', `${header.halo}`);
    html.dataset.skyContrast = header.contrast;
  }
  function invalidateInk() { inkLayoutDirty = true; inkDue = true; kick(); }
  // Outdoor animation shares the paint frame with weather. Arbitrarily many invalidations become one ink pass.
  // rear: false when the frame only redraws pollen's settled drift, which wet glass need not refract again.
  function requestOutdoorFrame(draw, rear = true) { outdoorFrame = draw; outdoorRear = outdoorRear || rear; kick(); return draw; }
  function requestWetFrame(draw) { wetFrame = draw; kick(); return draw; }
  function invalidateOutdoorInk() { inkDue = true; kick(); }
  addEventListener('scroll', () => {
    if (quiet || inkDue || inkScrollTimer) return;
    const delay = INK_SCROLL - (performance.now() - inkAt);
    if (delay <= 0) invalidateInk();
    else inkScrollTimer = setTimeout(invalidateInk, delay);
  }, { passive: true });
  (() => {   // the compass's spin on a tap (COMPASS_*): transforms only, nothing under reduced motion, one at a time
    const svg = document.querySelector('.compass'), dial = svg && svg.querySelector('.c-dial'), needle = $('needle');
    if (!svg || !dial || !needle) return;
    let busy = false;
    const spin = (el, from, by) => el.animate([{ transform: `rotate(${from}deg)`, easing: COMPASS_EASE_AWAY },
      { transform: `rotate(${from + by}deg)`, offset: COMPASS_TURN, easing: COMPASS_EASE_BACK }, { transform: `rotate(${from}deg)` }], { duration: COMPASS_MS });
    svg.addEventListener('click', e => {
      e.preventDefault(); e.stopPropagation();   // a tap here never follows a link around it
      if (busy || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      busy = true;
      const deg = Number((needle.style.transform.match(/-?[\d.]+/) || [0])[0]);   // the needle's wind direction, set in paint()
      spin(needle, deg, COMPASS_NEEDLE);
      spin(dial, 0, COMPASS_DIAL).finished.then(() => { busy = false; }, () => { busy = false; });
    });
  })();
  function kick() { if (!quiet && !document.hidden && !raf) raf = requestAnimationFrame(t => { last = t; frame(t); }); }
  new IntersectionObserver(es => { visible = es[0].isIntersecting; if (visible) kick(); }).observe($('sky'));
  if (!quiet) document.addEventListener('visibilitychange', () => {
    clearTimeout(sparseTimer); sparseTimer = 0; clearTimeout(inkScrollTimer); inkScrollTimer = 0;
    // A load pass due while hidden plays the first time the tab is shown, within the window; never after it.
    clearTimeout(loadTimer); loadTimer = 0;
    if (!document.hidden && shownAt === null) shownAt = performance.now();
    if (!document.hidden && loadLeader && !loadConsumed) {
      if (performance.now() < LOAD_WINDOW_MS) armLoad(); else consumeLoad('hidden until after the window');
    }
    if (document.hidden) {
      cancelAnimationFrame(raf); raf = 0; outdoorFrame = wetFrame = null; playingKind = null;
      scheduleStormExpiry(); cancelStorm();
    }
    if (!document.hidden && resizeDue) { resize(); glass?.resize(); wet?.resize(); }
    glass?.setPaused(document.hidden); wet?.setPaused(document.hidden);
    setPrimary(); syncAirButtons();
    if (!document.hidden) { paint(); armSparse(); }
  });
  window.addEventListener('resize', () => {
    if (quiet) return;
    if (document.hidden) { resizeDue = true; return; }
    resize(); fitCap(); if (glass) glass.resize(); if (wet) wet.resize(); invalidateInk();
  });
  // measure again once the web font is in (it widens the text) and whenever the line's room changes (a bigger Weather pill)
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { fitCap(); invalidateInk(); });
  if (window.ResizeObserver && $('sky-cap')) new ResizeObserver(() => { fitCap(); invalidateInk(); }).observe($('sky-cap').parentElement);
  if (!quiet) {
    const inkContents = new MutationObserver(records => {
      if (records.some(record => {
        const node = record.target.nodeType === 1 ? record.target : record.target.parentElement;
        if (!node || node.closest(INK_TRANSIENT)) return false;
        return record.type !== 'childList' || [...record.addedNodes, ...record.removedNodes].some(n => n.nodeType !== 1 || !n.matches(INK_TRANSIENT));
      })) invalidateInk();
    });
    inkContents.observe(document.querySelector('.page') || document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['class', 'hidden'] });
    const inkTheme = new MutationObserver(invalidateInk);
    inkTheme.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  }
  // panel
  const label = m => new Date(2000, 0, 1, Math.floor(m / 60), m % 60).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  const card = d => ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'][Math.round(d / 22.5) % 16];
  function syncPanel() {
    const v = st();
    if (sliders) {
      $('fx-time').value = v.minutes; $('fx-dir').value = v.dir; $('fx-spd').value = v.spd; $('fx-cloud').value = v.cloud; $('fx-pm').value = v.pm; $('fx-rain').value = Math.round((v.rain || 0) * 100); $('fx-v-rain').textContent = !v.rain ? 'None' : v.thunder ? 'Thunderstorm' : v.rain > .65 ? 'Heavy' : v.rain > .4 ? 'Rain' : 'Drizzle';
      const phase = elev > 6 ? 'day' : elev > 0 ? (azim < 180 ? 'sunrise' : 'golden hour') : elev > -6 ? (azim < 180 ? 'dawn' : 'dusk') : 'night';
      $('fx-v-time').textContent = `${label(v.minutes)} · ${phase}`; $('fx-v-dir').textContent = card(v.dir);
      $('fx-v-spd').textContent = `${v.spd} km/h`; $('fx-v-cloud').textContent = `${Math.round(v.cloud)}%`; $('fx-v-pm').textContent = `${v.pm} µg/m³`;
    }
    $('fx-mode').textContent = preview ? 'Preview' : 'Live'; $('fx-mode').classList.toggle('is-preview', !!preview); $('fx-live').hidden = !preview;
    for (const k of ['sky', 'wind', 'air']) $('fx-on-' + k).checked = on[k];
    const any = on.sky || on.wind || on.air, q = $('sky-quick');
    q.setAttribute('aria-pressed', String(any)); q.title = any ? 'Turn the live sky off' : 'Turn the live sky on'; q.classList.toggle('off', !any);
  }
  const PRE = {
    dawn: { minutes: 395, dir: 40, spd: 2, cloud: 10, pm: 2 }, noon: { minutes: 750, dir: 300, spd: 22, cloud: 5, pm: 3 },
    dusk: { minutes: 1010, dir: 250, spd: 14, cloud: 30, pm: 6 }, night: { minutes: 1380, dir: 200, spd: 8, cloud: 5, pm: 1 },
    storm: { minutes: 1035, dir: 265, spd: 34, cloud: 100, pm: 4, rain: 1, thunder: true },
  };
  function setPreview(p) {
    if (quiet) return;
    preview = p;
    if (!p && (airPreview.outdoor || airPreview.damp)) { airPreview = { outdoor: null, damp: false }; conditions = withPreview(); reconcileAir(); syncAirPreview(); }
    paint(); kick();
  }
  // A weather preset or Random keeps a previewed air kind and its haze.
  function weatherPreview(p) { setPreview(airPreview.outdoor in AIR_PREVIEW_PM ? { ...p, pm: AIR_PREVIEW_PM[airPreview.outdoor] } : p); }
  window.skyPreview = weatherPreview;   // used by the menu's Random cycle (app.js)
  const airMenu = () => document.querySelectorAll('[data-fx-air]');
  // On a home the buttons are toggles (pressed: the kind previewed); in the demo they open a page.
  function syncAirPreview() {
    if (!APP.now) airMenu().forEach(b => b.setAttribute('aria-pressed', String(b.dataset.fxAir === airPreview.outdoor || (b.dataset.fxAir === 'damp' && airPreview.damp))));
  }
  // A home: the kind plays over the page, marked as a preview (the sky line's tag), with the weather shown now and the
  // kind's haze; the readings and the Summary stay the home's. Back to live ends it. Reduced motion shows the still.
  function previewAir(kind) {
    if (quiet || !(kind in AIR_STORIES)) return;
    const v = st();
    if (kind === 'damp') airPreview.damp = true; else { airPreview.outdoor = kind; chosenKind = null; }
    // A PM2.5 kind paints its haze; pollen takes the home's own PM2.5 back; damp keeps the outdoor air as it is.
    preview = { ...v, pm: kind in AIR_PREVIEW_PM ? AIR_PREVIEW_PM[kind] : kind === 'damp' ? v.pm : airPm ?? 0 };
    conditions = withPreview(); reconcileAir(); syncAirPreview(); paint(); kick();
    playAir(kind, `preview ${kind}`);
  }
  if (!quiet) {
    if (sliders) ['time', 'dir', 'spd', 'cloud', 'pm', 'rain'].forEach(id => $('fx-' + id).addEventListener('input', e => {
      const v = { ...st() }; v[id === 'time' ? 'minutes' : id] = id === 'rain' ? Number(e.target.value) / 100 : Number(e.target.value); if (id === 'rain') v.thunder = v.rain > .85; setPreview(v);
    }));
    document.querySelectorAll('[data-fx]').forEach(b => b.addEventListener('click', () => { weatherPreview({ rain: 0, thunder: false, ...PRE[b.dataset.fx] }); }));
    // The demo opens the kind's story the way its sample links do (a plain address, nothing else kept); a home previews it,
    // with the menu closed so the pass shows.
    airMenu().forEach(b => { b.title = APP.now ? 'Open a sample with this air' : 'Preview this air over the page'; });
    syncAirPreview();
    airMenu().forEach(b => b.addEventListener('click', () => {
      const kind = b.dataset.fxAir;
      if (APP.now) { location.assign(`?moment=${AIR_STORIES[kind]}`); return; }
      const menu = $('dsp'); if (menu) menu.open = false;
      previewAir(kind);
    }));
    $('fx-live').addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); setPreview(null); });
    ['sky', 'wind', 'air'].forEach(k => $('fx-on-' + k).addEventListener('change', e => { on[k] = e.target.checked; save(); syncAirMaster(); paint(); kick(); }));
    $('sky-quick').addEventListener('click', e => {
      e.preventDefault(); e.stopPropagation();
      const any = on.sky || on.wind || on.air;
      on.sky = on.wind = on.air = !any; save(); syncAirMaster(); paint(); kick();
    });
  }
  function updateWeather(w, { cached = false } = {}) {
    if (cached) { live.fresh = false; return; }
    const { temperature, wind_direction, wind_speed, cloud_cover, weather_code: code } = w || {};
    const validCode = Number.isInteger(code) && code >= 0 && code <= WMO_CODE_MAX;
    const next = { temp: temperature, dir: wind_direction, spd: Number.isFinite(wind_speed) ? Math.round(wind_speed * 2) / 2 : null, cloud: cloud_cover };
    if (validCode) {
      next.thunder = code >= 95;
      next.rain = code >= 95 ? 1 : code >= 80 ? .7 : code >= 61 ? (code >= 65 ? .9 : .6) : code >= 51 ? .3 : 0;
    }
    const at = Date.now();
    let updated = false, retained = false;
    for (const key of WEATHER_FIELDS) {
      if (isWeatherValue(key, next[key])) {
        live[key] = next[key]; weatherAt[key] = at; updated = true;
      } else if (key in weatherAt) retained = true;
    }
    live.fresh = updated && !retained;
    if (updated) { live.loaded = true; remember(); }
  }
  if (window.__wx) updateWeather(window.__wx);
  resize(); // Supply real viewport dimensions before factories allocate their soft/crisp buffers.
  if (!quiet) {
  const api = {
    back: $('sky'), front: $('sky-front'), reduce: () => reduce,
    size: () => ({ W, H, dpr: Math.min(2, window.devicePixelRatio || 1) }),
    wind: () => { const v = st(); return { dir: cur.dir == null ? v.dir : cur.dir, spd: cur.spd || v.spd }; },
    windNow: () => { const v = st(); return { dir: v.dir, spd: v.spd }; },   // the wind as read, not the one the sky eases toward it: the exhaust's direction holds still
    sun: () => ({ el: elev, az: azim }),
    sky: () => { const v = st(); return { top: cur.top || tgt.top, hor: cur.hor || tgt.hor, cloud: v.cloud, rain: v.rain, thunder: v.thunder, pm: v.pm }; },
    dark: () => document.documentElement.dataset.theme === 'dark',
    loadPending,
    prepareInk,
    requestOutdoorFrame, cancelOutdoorFrame: () => { outdoorFrame = null; },
    requestWetFrame, cancelWetFrame: () => { wetFrame = null; },
    invalidateInk: invalidateOutdoorInk,
  };
  glass = SkyGlass.create({ ...api, onPlaying: kind => reportPlaying(glass, kind),
    invalidateInk() { rearSceneDirty = true; invalidateOutdoorInk(); },
    invalidateWetMaterial() { if (wet) { wetMaterialDirty = true; kick(); } },
  });
  wet = SkyWet.create({ ...api, glassFor: element => glass.glassFor(element), onPlaying: kind => reportPlaying(wet, kind) });
  const skip = e => { consumeLoad(`skipped by ${e?.type || 'an input'}`); stopPass(); };
  const conditionButton = e => e.target.closest?.('[data-sky-play]');
  let dampPointer = null, wipeClick = null;
  const wetPointers = new Set();
  document.addEventListener('click', e => {
    if (wipeClick !== null && e.pointerId === wipeClick) { wipeClick = null; return; }
    wipeClick = null;
    const button = conditionButton(e);
    if (button) { const kind = button.dataset.skyPlay; if (playingKind === kind) skip(e); else { choose(kind); playAir(kind); } }
    else skip(e);
  }, true);
  document.addEventListener('keydown', e => {
    wipeClick = null;
    // Native button activation delivers one click (Enter on keydown, Space on keyup).
    if (conditionButton(e) && ['Enter', ' ', 'Spacebar'].includes(e.key)) { consumeLoad('skipped by keydown'); return; }
    skip(e);
  }, true);
  for (const type of ['pointerdown', 'pointermove', 'pointerup', 'pointercancel', 'lostpointercapture']) document.addEventListener(type, e => {
    const id = e.pointerId, down = type === 'pointerdown';
    const ending = type === 'pointerup' || type === 'pointercancel' || type === 'lostpointercapture';
    if (down) { consumeLoad('skipped by pointerdown'); wipeClick = null; }
    // Even a down on a link (null gesture) registered a contact that needs its own end event.
    const forwarded = wetPointers.has(id);
    const control = conditionButton(e);
    if (control && !forwarded && !down) return;
    const deliver = (ending && forwarded) || (airEnabled() && activeKind('damp'));
    if (down && deliver) { wetPointers.add(id); dampPointer = null; }
    const gesture = deliver ? wet.pointer(e) : null;
    if (control && down) { dampPointer = null; return; } // Track the contact without consuming the button's native activation.
    const wiping = gesture === 'pending' || gesture === 'wipe';
    if (!ending && wiping) dampPointer = id;
    else if (dampPointer === id) dampPointer = null;
    if (ending) {
      wetPointers.delete(id);
      // Implicit lost capture follows pointerup and can precede the completed pointer's click.
      if (type === 'pointerup' && gesture === 'wipe') wipeClick = id;
    }
    if (wiping) return;
    if (gesture === 'skip' || down) stopPass();
  }, { capture: true, passive: false });
  // A scroll event alone is no input: iOS fires one at load (scroll restoration, toolbar). Real scrolling comes with wheel, touch or keys.
  document.addEventListener('wheel', skip, { capture: true, passive: true });
  document.addEventListener('touchmove', () => { consumeLoad('skipped by touchmove'); if (dampPointer === null) stopPass(); }, { capture: true, passive: true });
  motion.addEventListener?.('change', e => {
    reduce = e.matches; consumeLoad('motion preference changed'); stopPass(); clearTimeout(sparseTimer); sparseTimer = 0;
    if (airEnabled()) {
      if (reduce) { glass.still(); wet.still(); } else { glass.settle(); wet.settle(); }
    }
    paint(); armSparse();
  });
  }
  paint(); kick();
  if (!quiet) setInterval(() => { if (!document.hidden && !preview) paint(); }, 60000);
  return {
    glass, wet, air,
    sunEl: m => sunPos(m).el,
    weather(w, options) { updateWeather(w, options); paint(); kick(); },
    units() { paint(); },   // °C / °F switched in the Home menu
    // A theme switch jumps straight to the new sky colors; the page-wide cross-fade does the easing.
    theme() { cur.top = null; paint(); const r = a => a.map(Math.round).join(','); $('sky-grad').style.background = `linear-gradient(180deg, rgb(${r(cur.top)}) 0%, rgb(${r(cur.hor)}) 100%)`; },
  };
})();
if (typeof syncSkyAir === 'function') syncSkyAir(); // Deliver a pending first-refresh outcome if app.js finished before the sky scripts.

// Display dropdown: same behavior as the Home panel; opening one closes the other.
(() => {
  const d = $('dsp'), u = document.querySelector('.bar details.custom');
  const close = (el, refocus) => { if (el && el.open) { el.open = false; if (refocus) el.querySelector('summary').focus(); } };
  $('dsp-close').addEventListener('click', e => { e.preventDefault(); close(d, true); });
  d.addEventListener('toggle', () => { d.querySelector('summary').setAttribute('aria-expanded', String(d.open)); if (d.open) close(u); });
  if (u) u.addEventListener('toggle', () => { if (u.open) close(d); });
  document.addEventListener('click', e => { if (d.open && !d.contains(e.target)) close(d); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && d.open) close(d, true); });
})();
