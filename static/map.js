// Home Air: the Neighborhood map. Loaded only when the section nears the screen.
// MapLibre (open source) draws OpenFreeMap tiles: free, no key. Each followed road uses /api/road/route?i=.
// OpenStreetMap routes are drawn when available. Each followed road has a plume (mock 114): its likely exhaust when
// /api/context says it reaches home, else the same shape carried away, paler and greyer.
// Uses $, APP, apiUrl, state, has, PM_FINE, TZ and the table rows (data-id) from app.js, and bearingIn, selfWords, nbPm and plumeKind from home.js.
var NBMAP = (() => {
  const MAPLIBRE = 'https://cdnjs.cloudflare.com/ajax/libs/maplibre-gl/4.7.1/maplibre-gl.min';
  const STYLE = { light: 'https://tiles.openfreemap.org/styles/positron', dark: 'https://tiles.openfreemap.org/styles/dark' };
  // the first view: the nearest FIT_NEAREST neighbors in view (the server sends them nearest first), FIT_PAD px inside
  // the map's edge, never wider than FIT_MIN_ZOOM (about the 15 km radius the server searches) and never closer than
  // the neighborhood scale the map always opened at (ZOOM_PHONE under 600 px wide, ZOOM_WIDE above)
  const FIT_NEAREST = 6, FIT_PAD = 40, FIT_MIN_ZOOM = 10.5;
  const ZOOM_PHONE = 12.1, ZOOM_WIDE = 12.7, PHONE_PX = 600;
  // the shown monitor's label: "You", or "This monitor" on a public monitor's page (not on an unknown monitor's, {id, error},
  // which shows the default sample instead)
  const YOU = selfWords(!!(APP.public && !APP.public.error)).map;
  // Routes stay in config order; only roads with `on` are drawn. Context decides which plumes reach home.
  const roadList = () => APP.public && !APP.public.error ? [] : APP.roads || (APP.road ? [{ ...APP.road, on: true }] : []);
  const routeKey = (road, i) => `${i}:${road.key || road.name}`;
  const routes = new Map();
  // The plume image: FRAME_KM around home, CANVAS_PX square (half on a phone: 8 m a pixel is enough at its zoom, and it
  // keeps the three working canvases near 4 MB each). Each road's exhaust is STEPS areas from the road to 1/STEPS,
  // 2/STEPS … of its reach (/api/context's reach_m: 500 m by day, 2 km in calm air before sunrise), each at STEP_ALPHA,
  // then TAIL areas out to TAIL_TO × the reach at TAIL_ALPHA, each blurred by BLUR_SHARE of the reach: a smooth fade,
  // about 0.74 at the road and 0.30 at the reach, then a tail that fades out (exhaust thins out rather than stopping at a
  // line: Karner 2010; 3 km before sunrise is near Hu 2009's 2.6 km). Carried away: the same fade at AWAY of the
  // strength, AWAY_GREY toward grey, AWAY_BLUR × softer.
  const FRAME_KM = 8, CANVAS_PX = { wide: 2048, phone: 1024 }, STEPS = 10, STEP_ALPHA = .105, TAIL = 6, TAIL_ALPHA = .04, BLUR_SHARE = .07;
  // On the dark map the carried-away haze mixes toward a lighter grey, a little stronger: grey 150 at 0.55 nearly vanishes on near-black.
  // NEAR_KM: in light wind the exhaust stays near the road: a haze this far on each side, blurred like a plume.
  const NEAR_KM = .1;
  const TAIL_TO = { day: 1.6, calm: 1.5 }, AWAY = { light: .55, dark: .8 }, AWAY_GREY = .55, AWAY_BLUR = 2, GREY = { light: 150, dark: 200 };
  // The map's exhaust colour, from the live sky's (--air-exhaust-map): the same hue, saturation × MAP_SAT_X (at most
  // MAP_SAT_MAX, bolder without turning neon), lightness per theme for contrast on the base map (MAP_LIGHT: about 2.5:1
  // on the light map's land at the road, 4.5:1 on the dark one). PLUME_FALLBACK: LA's tan, before the sky sets a colour.
  const MAP_SAT_X = 2.2, MAP_SAT_MAX = .75, MAP_LIGHT = { light: .42, dark: .6 }, PLUME_FALLBACK = 'rgb(179 159 129)';
  const KM_PER_DEG = 111.1; // km per latitude degree, sufficient for a neighborhood drawing.
  let map = null, data = null, hoverId = null, loading = null;
  const fc = f => ({ type: 'FeatureCollection', features: f });
  const pmLabel = v => (Math.round(v * 10) / 10 >= 10 ? String(Math.round(v)) : v.toFixed(1));   // as the table writes it (app.js pmNum)
  // the nearby monitors' dots, on the corrected PM2.5 like the table (nbPm, home.js)
  const monitors = () => fc(data.others.map(n => ({ type: 'Feature', id: n.id, properties: { pm: nbPm(n), label: pmLabel(nbPm(n)) }, geometry: { type: 'Point', coordinates: [n.longitude, n.latitude] } })));
  const theme = () => document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';

  function load() {
    if (loading) return loading;
    const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = MAPLIBRE + '.css'; document.head.appendChild(css);
    loading = Promise.all([
      new Promise((ok, no) => { const s = document.createElement('script'); s.src = MAPLIBRE + '.js'; s.onload = ok; s.onerror = no; document.head.appendChild(s); }),
      loadRoutes(),
    ]);
    return loading;
  }

  function hsl(rgbText) {
    const [r, g, b] = (rgbText.match(/[\d.]+/g) || [179, 159, 129]).slice(0, 3).map(v => v / 255), max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min, l = (max + min) / 2;
    let h = 0;
    if (d) h = max === r ? 60 * (((g - b) / d) % 6) : max === g ? 60 * ((b - r) / d + 2) : 60 * ((r - g) / d + 4);
    return [(h + 360) % 360, d ? d / (1 - Math.abs(2 * l - 1)) : 0, l];
  }
  function hslToRgb(text) {   // "hsl(36 54% 42%)" → [r, g, b] 0–255
    const [h, s, l] = text.match(/[\d.]+/g).map(Number), S = s / 100, L = l / 100, a = S * Math.min(L, 1 - L);
    const f = n => { const k = (n + h / 30) % 12; return 255 * (L - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))); };
    return [f(0), f(8), f(4)];
  }
  function mapExhaust(skyColour, mode) {
    const [h, s] = hsl(skyColour);
    return `hsl(${h.toFixed(0)} ${(Math.min(MAP_SAT_MAX, s * MAP_SAT_X) * 100).toFixed(0)}% ${(MAP_LIGHT[mode] * 100).toFixed(0)}%)`;
  }
  const skyColour = () => (getComputedStyle(document.documentElement).getPropertyValue('--air-exhaust-map') || '').trim() || PLUME_FALLBACK;
  async function loadRoutes() {
    await Promise.all(roadList().map(async (road, i) => {
      const key = routeKey(road, i);
      if (!road.on || routes.has(key)) return;
      try {
        const response = await fetch(apiUrl(`/api/road/route?i=${i}`));
        if (!response.ok) return;
        const points = await response.json();
        if (!Array.isArray(points) || !points.length) return;
        const lines = typeof points[0][0] === 'number' ? [points] : points;
        routes.set(key, lines.filter(line => line.length > 1));
      } catch { /* A missing route leaves the road named in Around you. */ }
    }));
  }
  function roadFeatures() {
    return fc(roadList().flatMap((road, i) => road.on ? (routes.get(routeKey(road, i)) || []).map(line => ({
      type: 'Feature', properties: { name: road.name.replace(/^the /, '') }, geometry: { type: 'LineString', coordinates: line }
    })) : []));
  }
  // Paint every followed road's plume into one canvas, placed on the map as an image: overlapping pieces of a road (two
  // carriageways, clipped ways) never stack into darker stripes, and the edge is soft. Painted again only when the wind,
  // the roads' flags, the colour or the theme change.
  let canvas = null, work = null, frame = null, painted = '';
  function paintPlume() {
    const colour = mapExhaust(skyColour(), theme());
    document.documentElement.style.setProperty('--map-exhaust', colour);   // the key's swatches
    const flags = (data.roads || []).map(r => [r.name, plumeKind(r), r.reach_m]);
    const sig = JSON.stringify([data.wind, flags, colour, [...routes.keys()]]);
    if (sig === painted && canvas) return false;
    painted = sig;
    const px_ = $('nb-map').clientWidth < PHONE_PX ? CANVAS_PX.phone : CANVAS_PX.wide;
    if (!canvas || canvas.width !== px_) {
      const make = () => Object.assign(document.createElement('canvas'), { width: px_, height: px_ });
      canvas = make(); work = { band: make(), acc: make(), tint: make() };
    }
    const N = canvas.width, ctx = canvas.getContext('2d'), b = work.band.getContext('2d'), A = work.acc.getContext('2d'), t = work.tint.getContext('2d');
    ctx.clearRect(0, 0, N, N);
    const lat = APP.place.lat, lon = APP.place.lon, kLat = 1 / KM_PER_DEG, kLon = kLat / Math.cos(lat * Math.PI / 180), half = FRAME_KM / 2;
    frame = { w: lon - half * kLon, e: lon + half * kLon, n: lat + half * kLat, s: lat - half * kLat };
    if (data.wind == null) return true;
    const px = ([lo, la]) => [(lo - frame.w) / (frame.e - frame.w) * N, (frame.n - la) / (frame.n - frame.s) * N];
    const mPerPx = FRAME_KM * 1000 / N, to = (data.wind + 180) * Math.PI / 180;
    const greyed = `rgb(${hslToRgb(colour).map(v => Math.round(v + (GREY[theme()] - v) * AWAY_GREY)).join(' ')})`;   // carried away: toward grey
    const lines = name => roadList().flatMap((road, i) => road.on && road.name === name ? routes.get(routeKey(road, i)) || [] : []);
    const sweep = (pieces, km) => pieces.forEach(line => {   // one piece of the road swept km downwind, as a closed path
      const moved = line.map(([lo, la]) => [lo + Math.sin(to) * km * kLon, la + Math.cos(to) * km * kLat]);
      b.beginPath(); [...line, ...moved.reverse()].map(px).forEach(([x, y], i) => i ? b.lineTo(x, y) : b.moveTo(x, y)); b.closePath();
      b.fill(); b.stroke();   // the swept shape leaves gaps where a line bends: a stroke of its outline closes them
    });
    (data.roads || []).forEach(r => {
      const pieces = lines(r.name), reach = (r.reach_m || 500) / 1000;
      const kind = plumeKind(r);
      if (!pieces.length || !kind) return;   // a quiet road blowing toward home: no plume (home.js plumeKind)
      A.clearRect(0, 0, N, N);
      if (kind === 'near') {   // light wind: one band along the road, NEAR_KM each side
        b.clearRect(0, 0, N, N); b.strokeStyle = '#000'; b.lineWidth = 2 * NEAR_KM * 1000 / mPerPx; b.lineCap = b.lineJoin = 'round';
        pieces.forEach(line => { b.beginPath(); line.map(px).forEach(([x, y], i) => i ? b.lineTo(x, y) : b.moveTo(x, y)); b.stroke(); });
        A.save(); A.filter = `blur(${NEAR_KM * 1000 * AWAY_BLUR * BLUR_SHARE * 4 / mPerPx}px)`; A.globalAlpha = STEPS * STEP_ALPHA * .6; A.drawImage(work.band, 0, 0); A.restore();
      } else {
        const blur = reach * 1000 * BLUR_SHARE * (r.reaching ? 1 : AWAY_BLUR) / mPerPx, tail = TAIL_TO[r.why === 'calm' ? 'calm' : 'day'];
        const steps = [...Array(STEPS).keys()].map(k => [reach * (k + 1) / STEPS, STEP_ALPHA])
          .concat(kind === 'far' ? [] : [...Array(TAIL).keys()].map(k => [reach * (1 + (tail - 1) * (k + 1) / TAIL), TAIL_ALPHA]));   // a far road: no tail toward the home
        steps.forEach(([km, alpha]) => {
          b.clearRect(0, 0, N, N); b.fillStyle = b.strokeStyle = '#000'; b.lineWidth = 2;
          sweep(pieces, km);
          A.save(); A.filter = `blur(${blur}px)`; A.globalAlpha = alpha; A.drawImage(work.band, 0, 0); A.restore();   // each step once: the pieces' union
        });
      }
      t.clearRect(0, 0, N, N); t.globalCompositeOperation = 'source-over'; t.drawImage(work.acc, 0, 0);
      t.globalCompositeOperation = 'source-in'; t.fillStyle = r.reaching ? colour : greyed;
      t.fillRect(0, 0, N, N); t.globalCompositeOperation = 'source-over';
      ctx.save(); ctx.globalAlpha = r.reaching ? 1 : AWAY[theme()]; ctx.drawImage(work.tint, 0, 0); ctx.restore();
    });
    return true;
  }
  const plumeCorners = () => [[frame.w, frame.n], [frame.e, frame.n], [frame.e, frame.s], [frame.w, frame.s]];
  function setPlume() {   // paint, then hand the image to the map's source (or add the source the first time)
    if (!paintPlume() && map.getSource('plume')) return;
    const url = canvas.toDataURL('image/png');
    if (map.getSource('plume')) map.getSource('plume').updateImage({ url, coordinates: plumeCorners() });
    else map.addSource('plume', { type: 'image', url, coordinates: plumeCorners() });
  }
  const recolourPlume = () => { if (map && map.getSource && map.getSource('plume')) setPlume(); };

  function addLayers() {
    const dark = theme() === 'dark', ink = dark ? '#ececef' : '#18181b', halo = dark ? '#17181c' : '#ffffff';
    const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    // The plume above every base-map layer that draws ground, water and roads, below the labels after them: roads never
    // cut through the exhaust, and street names stay on top (the styles put a few labels, such as water names, early).
    painted = ''; setPlume();
    const base = map.getStyle().layers || [], lastGround = base.reduce((k, l, i) => l.type !== 'symbol' ? i : k, -1);
    const firstLabel = base.slice(lastGround + 1).find(l => l.type === 'symbol');
    map.addLayer({ id: 'plume', type: 'raster', source: 'plume', paint: { 'raster-fade-duration': 0 } }, firstLabel && firstLabel.id);
    map.addSource('bqe', { type: 'geojson', data: roadFeatures() });   // empty road sources let switches update the lines without rebuilding the map
    map.addLayer({ id: 'bqe-case', type: 'line', source: 'bqe', paint: { 'line-color': halo, 'line-width': 8 }, layout: { 'line-cap': 'round', 'line-join': 'round' } });
    map.addLayer({ id: 'bqe', type: 'line', source: 'bqe', paint: { 'line-color': css('--muted'), 'line-width': 4 }, layout: { 'line-cap': 'round', 'line-join': 'round' } });
    map.addSource('mons', { type: 'geojson', data: monitors() });
    map.addLayer({ id: 'mons', type: 'circle', source: 'mons', paint: {
      'circle-radius': ['+', 6, ['min', 10, ['*', ['get', 'pm'], .9]]],
      // over the limit as the server counts it: the rounded reading (home.js pmOver)
      'circle-color': ['case', ['>', ['round', ['get', 'pm']], PM_FINE], '#f59e0b', '#22a55a'], 'circle-opacity': .9,
      'circle-stroke-color': ['case', ['boolean', ['feature-state', 'hl'], false], ink, halo],
      'circle-stroke-width': ['case', ['boolean', ['feature-state', 'hl'], false], 3, 2] } });
    map.addLayer({ id: 'mons-label', type: 'symbol', source: 'mons', layout: { 'text-field': ['get', 'label'], 'text-size': 11, 'text-font': ['Noto Sans Bold'], 'text-offset': [0, -1.6], 'text-allow-overlap': true },
      paint: { 'text-color': ink, 'text-halo-color': halo, 'text-halo-width': 2 } });
    if (data.you) {
      map.addSource('you', { type: 'geojson', data: { type: 'Feature', geometry: { type: 'Point', coordinates: [data.you.longitude, data.you.latitude] } } });
      map.addLayer({ id: 'you', type: 'circle', source: 'you', paint: { 'circle-radius': 9, 'circle-color': dark ? '#7fd3e6' : '#1c7488', 'circle-stroke-color': halo, 'circle-stroke-width': 3 } });
      map.addLayer({ id: 'you-label', type: 'symbol', source: 'you', layout: { 'text-field': YOU + ' · ' + pmLabel(nbPm(data.you)), 'text-size': 12, 'text-font': ['Noto Sans Bold'], 'text-offset': [0, 1.5], 'text-allow-overlap': true },
        paint: { 'text-color': dark ? '#7fd3e6' : '#1c7488', 'text-halo-color': halo, 'text-halo-width': 2.5 } });
    }
    // each road's short name ("BQE"), above the monitors' dots so they never cover it
    map.addLayer({ id: 'bqe-label', type: 'symbol', source: 'bqe', layout: { 'symbol-placement': 'line', 'text-field': ['get', 'name'], 'text-size': 12, 'text-font': ['Noto Sans Bold'], 'symbol-spacing': 500 },
      paint: { 'text-color': css('--soft'), 'text-halo-color': halo, 'text-halo-width': 2 } });
  }

  function create() {
    const box = $('nb-map');
    // centered on the block, at a neighborhood scale: the nearby road and the closest monitors in view
    const home = data.you ? [data.you.longitude, data.you.latitude] : [APP.place.lon, APP.place.lat];
    const zoom = box.clientWidth < PHONE_PX ? ZOOM_PHONE : ZOOM_WIDE;
    try {
      map = new maplibregl.Map({ container: box, style: STYLE[theme()], center: home, zoom, attributionControl: false, dragRotate: false });
      document.addEventListener('sky-exhaust-colour', recolourPlume);   // the sky's exhaust colour changes with the weather and the hour
    } catch (e) {
      box.innerHTML = '<p class="nb-map-msg">The map needs WebGL, which this browser has turned off. The table below has every monitor.</p>';
      document.body.dataset.mapReady = 'none'; map = null; return;
    }
    fitNearest(home, zoom);
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-left');
    map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: roadList().length ? 'Wind: Open-Meteo' : undefined }), 'bottom-right');
    map.on('load', () => box.querySelectorAll('.maplibregl-compact-show').forEach(el => el.classList.remove('maplibregl-compact-show')));   // credits folded into the ⓘ
    map.scrollZoom.disable();
    map.on('style.load', addLayers);
    map.once('idle', () => { document.body.dataset.mapReady = '1'; });   // scripts/screenshot.sh waits for this
    map.on('mousemove', 'mons', e => { const id = e.features[0].id; if (hoverId !== id) highlight(id); });
    map.on('mouseleave', 'mons', () => highlight(null));
  }

  // zoom out until the nearest neighbors are in view, still centered on the block: each one's mirror through home goes
  // in the box too, so the box stays centered. Only create() sets the camera; refresh() and a theme switch keep it.
  function fitNearest(home, zoom) {
    const near = data.others.slice(0, FIT_NEAREST).filter(n => Number.isFinite(n.longitude) && Number.isFinite(n.latitude));
    if (!near.length) return;
    const b = new maplibregl.LngLatBounds(home, home);
    near.forEach(n => { b.extend([n.longitude, n.latitude]); b.extend([2 * home[0] - n.longitude, 2 * home[1] - n.latitude]); });
    const cam = map.cameraForBounds(b, { padding: FIT_PAD, maxZoom: zoom });
    if (cam) map.jumpTo({ center: home, zoom: Math.max(FIT_MIN_ZOOM, cam.zoom) });
  }

  function highlight(id) {
    if (hoverId != null && map && map.getSource('mons')) map.setFeatureState({ source: 'mons', id: hoverId }, { hl: false });   // before the style loads there is no source
    document.querySelectorAll('#nb tr.hl').forEach(r => r.classList.remove('hl'));
    hoverId = id;
    if (id == null) return;
    if (map && map.getSource('mons')) map.setFeatureState({ source: 'mons', id }, { hl: true });
    const row = document.querySelector(`#nb tr[data-id="${id}"]`); if (row) row.classList.add('hl');
  }

  // called by renderNeighborhood with the monitors on the table and the wind
  function update(you, others, wind, roads = []) {
    if (APP.place.lat == null) return;   // no place: no map (the section says so)
    data = { you, others: others.map(n => ({ ...n, id: n.locationId })), wind, roads };
    const box = $('nb-map'); if (!box) return;
    if (!('IntersectionObserver' in window)) return draw();
    if (!update.watching) { update.watching = true; onVisible(box, draw); }
    else if (map) refresh();
  }
  function draw() { load().then(() => { if (!map) create(); else refresh(); }).catch(() => { $('nb-map').innerHTML = '<p class="nb-map-msg">The map did not load. The table below has every monitor.</p>'; }); }
  // every minute: new readings and wind, same map (no reload, no flash)
  function refresh() {
    if (!map || !map.getSource('mons')) return;
    if (map.getSource('plume')) setPlume();
    if (map.getSource('bqe')) map.getSource('bqe').setData(roadFeatures());
    map.getSource('mons').setData(monitors());
  }
  function theme_() { if (map) { map.setStyle(STYLE[theme()]); } }
  function roadsChanged() { if (!loading) return Promise.resolve(); if (map) refresh(); return loadRoutes().then(() => { if (map) refresh(); }); }
  return { update, highlight, theme: theme_, roadsChanged, mapExhaust, hslToRgb };
})();
