// The page's wiring: index.html uses the new summary card, and every bare function call in app.js is defined
// in one of the page's scripts or is a browser global.
// Found by the branch review: two calls to deleted functions (renderAgenda, renderActionVis) broke the refresh loop.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';

const root = new URL('../', import.meta.url);
const strip = s => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:\\])\/\/.*$/gm, '$1')
  .replace(/'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"/g, "''");
const files = ['static/app.js', 'static/sky.js', 'static/sky-glass.js', 'static/sky-wet.js', 'static/summary.js', 'static/home.js', 'static/picker.js'].map(f => strip(readFileSync(new URL(f, root), 'utf8')));
const all = files.join('\n');

const declared = new Set();
const add = x => x && declared.add(x.replace(/^\.\.\./, '').replace(/=.*/, ''));
for (const m of all.matchAll(/\b(?:function\s*\*?\s*|const\s+|let\s+|var\s+|class\s+)([A-Za-z_$][\w$]*)/g)) add(m[1]);
for (const m of all.matchAll(/\b(?:const|let|var)\s*[{[]([^=]*?)[}\]]\s*=/g)) m[1].split(/[,:\s]+/).forEach(add);
for (const m of all.matchAll(/function\s*[\w$]*\s*\(([^)]*)\)|\(([^()]*)\)\s*=>|([A-Za-z_$][\w$]*)\s*=>/g)) (m[1] ?? m[2] ?? m[3]).split(/[,\s{}=[\]]+/).forEach(add);
for (const m of all.matchAll(/[{,]\s*([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{/g)) add(m[1]);   // method shorthand: get() { … }

const GLOBALS = new Set(('if for while switch catch return typeof new function await async import require super ' +
  'setTimeout clearTimeout setInterval clearInterval requestAnimationFrame fetch getComputedStyle matchMedia ' +
  'Number String Boolean Object Array Date Math JSON Promise Set Map WeakMap Symbol Proxy Reflect Error RegExp ' +
  'parseFloat parseInt isNaN isFinite encodeURIComponent decodeURIComponent structuredClone queueMicrotask btoa atob ' +
  'Intl URL URLSearchParams IntersectionObserver ResizeObserver MutationObserver PointerEvent alert confirm ' +
  // not code: CSS functions inside template strings, and a word inside a regex
  'calc var Yesterday AbortController').split(' '));

test('app.js calls only functions that exist', () => {
  const missing = new Set();
  for (const m of files[0].matchAll(/(^|[^.\w$])([A-Za-z_$][\w$]*)\s*\(/g)) if (!declared.has(m[2]) && !GLOBALS.has(m[2])) missing.add(m[2]);
  assert.deepEqual([...missing], []);
});

test('the page uses the new summary card, not the old headline and need cards', async () => {
  const { readFile } = await import('node:fs/promises');
  const html = await readFile(new URL('index.html', root), 'utf8');
  assert.match(html, /id="summary"/);
  assert.doesNotMatch(html, /id="s-cards"/);
  assert.doesNotMatch(html, /id="lead"/);
  // Production materials load in order; prototype URLs cannot install another controller or an inert Play control.
  assert.match(html, /href="\/static\/sky-air\.css"/);
  const scripts = [...html.matchAll(/<script\b[^>]*\ssrc="([^"]+)"/g)].map(m => m[1]);
  for (const src of ['/static/sky-glass.js', '/static/sky-wet.js', '/static/sky.js']) {
    assert.ok(scripts.includes(src), `${src} must be loaded`);
  }
  assert.ok(scripts.indexOf('/static/sky-glass.js') < scripts.indexOf('/static/sky-wet.js'));
  assert.ok(scripts.indexOf('/static/sky-wet.js') < scripts.indexOf('/static/sky.js'));
  const skyHeadings = [...html.matchAll(/<(h2|span)\b[^>]*\bdata-sky-heading(?=\s|>)[^>]*>([^<]+)<\/\1>/g)].map(m => m[2]);
  assert.deepEqual(skyHeadings, ['Home monitors', 'Local conditions', 'History', 'Neighborhood']);
  const sky = readFileSync(new URL('static/sky.js', root), 'utf8');
  assert.doesNotMatch(sky, /CONCEPT|outsideIn|fxArm|\/static\/concepts\/|import\(/);
  assert.doesNotMatch(html, /id="(?:fx-in(?:-play)?|sky-in(?:-back)?)"/);
  assert.match(html, /id="sky-front"/);
  assert.match(html, /id="fx-time"/);
  assert.match(html, /id="fx-rand"/);
});

test('sky ink samples a painted frame once, without following every steady rain frame', () => {
  const noop = () => {};
  const observer = class { observe() {} };
  const rect = { left: 24, top: 24, right: 64, bottom: 38, width: 40, height: 14 };
  const reads = [], frames = new Map(), nodes = new Map();
  let nextFrame = 0, time = 0, air, wetApi, outdoorFrame, wetFrame;
  const composition = [];
  let rearAir = () => [110, 110, 110];
  const $ = id => {
    if (!nodes.has(id)) {
      const el = { style: { setProperty(k, v) { this[k] = v; } }, dataset: { theme: 'dark' }, querySelectorAll: () => [],
        classList: { add: noop, remove: noop, toggle: noop, contains: () => true }, addEventListener: noop,
        setAttribute: noop, getBoundingClientRect: () => rect, hasAttribute: () => true };
      let layers = [], savedFilter;
      const ctx = new Proxy({ canvas: el, filter: 'none', clearRect() { layers = []; },
        save() { savedFilter = this.filter; }, restore() { this.filter = savedFilter; },
        drawImage(layer) { layers.push([layer, this.filter]); },
        getImageData(x, y, w, h) { composition.push([time, 'ink']); reads.push({ time, layers }); return { data: new Uint8ClampedArray(w * h * 4) }; },
        createRadialGradient: () => ({ addColorStop: noop }), createLinearGradient: () => ({ addColorStop: noop }) },
      { get: (obj, key) => key in obj ? obj[key] : noop });
      el.getContext = () => ctx;
      nodes.set(id, el);
    }
    return nodes.get(id);
  };
  const requestAnimationFrame = cb => (frames.set(++nextFrame, cb), nextFrame);
  const advance = now => {
    time = now;
    const pending = [...frames.values()]; frames.clear();
    for (const cb of pending) cb(now);
  };
  const document = { documentElement: $('html'), body: $('body'), querySelector: () => null,
    querySelectorAll: selector => selector.includes('[data-sky-heading]') ? [$('heading')] : [],
    createElement: () => $(Symbol()), createRange: () => ({ selectNodeContents: noop, getClientRects: () => [rect] }), addEventListener: noop };
  const window = { matchMedia: () => ({ matches: false }), addEventListener: noop };
  runInNewContext(readFileSync(new URL('static/sky.js', root), 'utf8'), {
    window, document, $, location: { search: '' }, APP: { place: { lat: 0, lon: 0 }, limits: { pm_high: 35 } }, state: {},
    TZ: 'UTC', appNow: () => new Date('2026-11-24T22:00:00Z'), has: v => v != null, pmOver: home.pmOver,
    innerWidth: 120, innerHeight: 100, scrollX: 0, scrollY: 0, performance: { now: () => time },
    setInterval: noop, clearTimeout: noop, addEventListener: noop, requestAnimationFrame,
    cancelAnimationFrame: id => frames.delete(id), IntersectionObserver: observer, MutationObserver: observer,
    getComputedStyle: el => ({ backgroundColor: 'rgb(14, 15, 18)', filter: el === $('sky-clouds') ? 'blur(2px)' : 'none',
      opacity: el.style.opacity, getPropertyValue: k => k.endsWith('on-light') ? '8 14 26' : '253 254 255' }),
    SkyGlass: { create(api) {
      air = api;
      return { redraw: noop, sampleRear: (rgb, x) => rearAir(x), sampleFront: rgb => rgb };
    } },
    SkyWet: { create: api => { wetApi = api; return { redraw: noop, refreshComposite: () => composition.push([time, 'composite']), sampleBackground: rgb => rgb }; } },
  });
  rearAir = () => [214, 194, 160];   // the exhaust plume's light edge behind the heading (the air effects draw the PM2.5 now)
  window.skyPreview({ minutes: 120, cloud: 95, rain: .6, pm: 48, dir: 135, spd: 9 });
  for (let t = 0; t <= 4800; t += 16) advance(t);
  const steady = reads.filter(read => read.time > 4000);
  assert.ok(steady.length > 0, 'steady rain is still sampled periodically');
  assert.equal(new Set(steady.map(read => read.time)).size, 1, 'steady rain must not drive readbacks at frame rate');
  const cloudRead = steady.flatMap(read => read.layers).find(([layer]) => layer === $('sky-clouds'));
  assert.equal(cloudRead?.[1], 'blur(2px)', 'read the clouds with the same blur as the painted canvas');
  assert.equal($('heading').dataset.skyInkLight, 'false', 'a light plume behind the heading makes dark ink safer');

  const readCount = reads.length;
  const drawOutdoor = now => {
    outdoorFrame = now;
    composition.push([now, 'outdoor']);
    rearAir = x => x < 44 ? [72, 72, 72] : [240, 240, 240];
    air.invalidateInk(); air.invalidateInk();
  };
  (air.requestOutdoorFrame || requestAnimationFrame)(drawOutdoor);
  wetApi.requestWetFrame(now => { wetFrame = now; composition.push([now, 'wet']); });
  advance(6400); // the bounded weather sample is also due
  const outdoorReads = reads.slice(readCount);
  assert.equal(outdoorFrame, 6400);
  assert.equal(wetFrame, 6400);
  assert.deepEqual([...new Set(composition.filter(([at]) => at === 6400).map(([, stage]) => stage))], ['outdoor', 'wet', 'composite', 'ink']);
  assert.equal(composition.filter(([at, stage]) => at === 6400 && stage === 'composite').length, 1);
  assert.ok(outdoorReads.length > 0, 'outdoor drawing triggers sampling on the same frame');
  assert.equal(outdoorReads.length, steady.length, 'the two invalidations are serviced by a single ink pass');
  assert.equal($('heading').style['--sky-heading-outline'], 'rgb(253,254,255)', 'mixed light uses an opaque stroke opposite the dark fill');
});

// Exercise the production controller with a clock and renderer contracts; no test-only production hooks.
function airPage({ search = '', reduced = false, weather = null, savedWeather = null, road = null, prefs = null,
  realGlass = false, realWet = false, appState = null, random = false, theme = 'light', width = 390, height = 800, panes = {},
  hidden = false, randomValue = null, wallTime = Date.now(), weatherCache = savedWeather && { at: wallTime - 60000, v: savedWeather }, beforeSky } = {}) {
  const noop = () => {}, nodes = new Map(), frames = new Map(), timers = new Map(), events = new Map(), calls = [];
  const intervals = new Map(), writes = [], tokens = {}, paneResizes = new Map(), allocations = [], animations = [], starPaints = [];
  const css = readFileSync(new URL('static/app.css', root), 'utf8');
  for (const [, dark, rules] of css.matchAll(/:root(\[data-theme="dark"\])?\s*\{([^{}]+)\}/g)) {
    if (!dark || theme === 'dark') for (const [, name, value] of rules.matchAll(/(--[\w-]+):\s*([^;]+)/g)) tokens[name] = value.trim();
  }
  let clock = 0, id = 0, mediaChange;
  let gpuCard = false;
  const gl = realWet === 'gpu' ? new Proxy({ MAX_VIEWPORT_DIMS: 0x0d3a, TEXTURE0: 0x84c0,
    getParameter: key => key === 0x0d3a ? [4096, 4096] : 4096,
    getShaderParameter: () => true, getProgramParameter: () => true, getUniformLocation: (_, name) => name,
    uniform1f(name, value) { if (name === 'card') gpuCard = !!value; },
    drawArrays() { calls.push(['gpu', 'draw', gpuCard ? 'pane' : 'viewport']); },
  }, { get: (o, k) => o[k] ?? noop }) : null;
  const rect = { left: 0, top: 0, width: 40, height: 20, right: 40, bottom: 20 };
  const listen = (type, cb) => { if (!events.has(type)) events.set(type, []); events.get(type).push(cb); };
  const node = key => {
    if (nodes.has(key)) return nodes.get(key);
    const attributes = {}, classes = new Set(), style = {};
    const bounds = panes[key] || rect;
    const el = { nodeType: 1, children: [], childNodes: [], dataset: {}, style: { opacity: '',
      setProperty(k, v) { style[k] = v; }, getPropertyValue(k) { return style[k] || ''; }, removeProperty(k) { delete style[k]; } }, textContent: '',
      classList: { add: k => classes.add(k), remove: k => classes.delete(k), contains: k => classes.has(k),
        toggle(k, on) { if (on) classes.add(k); else classes.delete(k); } },
      setAttribute(k, v) { attributes[k] = v; }, getAttribute: k => attributes[k], hasAttribute: () => false,
      animate() { const a = { playState: 'running', effect: { getComputedTiming: () => ({ progress: .1 }) }, cancel() { this.playState = 'idle'; } }; animations.push(a); return a; },
      addEventListener: (type, cb) => listen(`${String(key)}:${type}`, cb), querySelector: () => null, querySelectorAll: () => [],
      getBoundingClientRect: () => bounds, getClientRects: () => [bounds],
      matches: selector => selector === '.sum-card' && key === 'summary',
      closest: selector => panes[key] && selector.includes(key === 'summary' ? '.sum-card' : '.card') ? el : null,
      appendChild(child) { this.children.push(child); child.parentElement = this; return child; },
      insertBefore(child, before) { const old = this.children.indexOf(child); if (old >= 0) this.children.splice(old, 1); this.children.splice(before ? this.children.indexOf(before) : this.children.length, 0, child); child.parentElement = this; },
      remove() { this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1); },
      getContext(type) { if (type === 'webgl') { calls.push(['gpu', 'context']); return gl; } return this.ctx ||= new Proxy({
        clearRect() {
          if (key === 'sky-stars') starPaints.push(clock);
          if (key === 'sky-clouds') calls.push(['weather', 'draw', clock]);
          if (el.parentElement?.className === 'sky-wet-pane') calls.push(['pane', 'paint', clock, el.parentElement.parentElement]);
          if (el.className === 'sky-wet-viewport') calls.push(['2d', 'draw', 'viewport']);
        },
        fillRect(x, y, w, h) {
          if (el.className?.includes('sky-air-back-soft') && x === 0 && y === 0 && w === width && h === height) {
            calls.push(['wash', 'fill', clock, this.fillStyle?.stops?.at(-1)]);
          }
        },
        stroke() { if (key === 'sky-rain') calls.push(['rain', 'drop', clock]); },
        putImageData(im) { el.image = im; },
        drawImage(source) {
          if (el.parentElement?.className === 'sky-wet-pane' && source.image?.width >= width / 4 - 1) {
            el.fog = Uint8ClampedArray.from(source.image.data);
          }
        },
        getImageData(x, y, width, height) { calls.push(['canvas', 'read', clock, width, height]); return { width, height, data: new Uint8ClampedArray(width * height * 4) }; },
        createImageData: (width, height) => ({ width, height, data: new Uint8ClampedArray(width * height * 4) }),
        createRadialGradient: () => ({ addColorStop: noop }),
        createLinearGradient: () => ({ stops: [], addColorStop(at, color) { this.stops.push(color); } }),
      }, { get: (o, k) => o[k] || noop }); },
    };
    for (const axis of ['width', 'height']) Object.defineProperty(el, axis, {
      get() { return this['_' + axis]; }, set(value) { allocations.push([key, axis, value]); this['_' + axis] = value; }
    });
    nodes.set(key, el); return el;
  };
  const document = { hidden, documentElement: node('html'), body: node('body'), addEventListener: listen,
    getElementById: node,
    querySelector: selector => selector === '.sum-card' && panes.summary ? node('summary') : null,
    querySelectorAll: selector => selector.startsWith('.sum-card, .card') || selector.startsWith('.card, section.sum-card') ? Object.keys(panes).map(node)
      : selector === '[data-fx-air]' ? ['smoke', 'exhaust', 'particles', 'pollen', 'damp'].map(kind => Object.assign(node('fx-air-' + kind), { dataset: { fxAir: kind } })) : [],
    createElement: () => node(Symbol()),
    createRange: () => ({ selectNodeContents: noop, getClientRects: () => [] }) };
  document.documentElement.dataset.theme = theme;
  const factory = name => ({ create(api) {
    calls.push([name, 'create', api]);
    return Object.fromEntries(['redraw', 'resize', 'clear', 'stop', 'settle', 'still', 'setPaused', 'setConditions', 'burst', 'play', 'refreshComposite', 'pointer', 'glassFor'].map(method => [method, (...args) => {
      calls.push([name, method, ...args]);
      if (method === 'play') api.onPlaying(args[0]);
      if (method === 'settle' || method === 'still' || method === 'clear') api.onPlaying(null);
      if (method === 'pointer') return args[0].gesture || null;
    }]));
  } });
  const observer = class { observe() {} unobserve() {} disconnect() {} };
  const resizeObserver = class extends observer {
    constructor(cb) { super(); this.cb = cb; }
    observe(el) { paneResizes.set(el, this.cb); }
    unobserve(el) { paneResizes.delete(el); }
  };
  const env = { document, window: { matchMedia: () => ({ matches: reduced, addEventListener: (type, cb) => { mediaChange = cb; } }), addEventListener: listen },
    $, APP: { place: { lat: 0, lon: 0 }, limits: { pm: 15, pm_high: 35, rh_damp: 55, pollen: 3 } }, state: {},
    location: { search }, TZ: 'UTC', appNow: () => new Date('2026-11-24T12:00:00Z'),
    has: v => v != null, fmtTemp: v => `${v}°`, bearingIn: home.bearingIn, esc: escHtml, pmOver: home.pmOver, skyConditions: home.skyConditions, skyLeader: home.skyLeader, previewConditions: home.previewConditions,
    innerWidth: width, innerHeight: height, scrollX: 0, scrollY: 0, performance: { now: () => clock },
    setTimeout: (cb, delay) => (timers.set(++id, { cb, at: clock + delay }), id), clearTimeout: id => timers.delete(id),
    setInterval: (cb, delay) => (intervals.set(++id, { cb, delay }), id), clearInterval: id => intervals.delete(id),
    requestAnimationFrame: cb => (frames.set(++id, cb), id), cancelAnimationFrame: id => frames.delete(id), addEventListener: listen,
    Date: class extends Date { static now() { return wallTime + clock; } },
    IntersectionObserver: observer, ResizeObserver: resizeObserver, MutationObserver: observer,
    getComputedStyle: () => ({ backgroundColor: 'rgb(255,255,255)', filter: 'none', opacity: '0',
      getPropertyValue: name => tokens[name] || '22 33 46' }),
    SkyGlass: factory('glass'), SkyWet: factory('wet'),
    localStorage: { getItem: key => key === 'aq-fx' && prefs ? JSON.stringify(prefs)
      : key.startsWith('aq-sky-live') && weatherCache ? JSON.stringify(weatherCache) : null,
      setItem: (key, value) => writes.push([key, JSON.parse(value)]) } };
  env.APP.road = road; env.APP.state = appState;
  if (randomValue !== null) env.Math = Object.assign(Object.create(Math), { random: () => randomValue });
  if (weather) env.window.__wx = weather;
  if (realGlass) {
    env.CSS = { supports: () => false }; document.fonts = { ready: Promise.resolve() };
    runInNewContext(readFileSync(new URL('static/sky-glass.js', root), 'utf8'), env);
    const actual = env.SkyGlass;
    env.SkyGlass = { create: api => actual.create({ ...api, requestOutdoorFrame: draw => { calls.push(['glass', 'frame']); return api.requestOutdoorFrame(draw); } }) };
  }
  if (realWet) {
    runInNewContext(readFileSync(new URL('static/sky-wet.js', root), 'utf8'), env);
    const actual = env.SkyWet;
    env.SkyWet = { create: api => actual.create({ ...api, requestWetFrame: draw => api.requestWetFrame(now => { calls.push(['wet', 'tick', now]); draw(now); }) }, { force2D: realWet !== 'gpu' }) };
  }
  function $(key) { return node(key); }
  beforeSky?.(env);
  runInNewContext(readFileSync(new URL('static/sky.js', root), 'utf8'), env);
  if (random) {
    const app = readFileSync(new URL('static/app.js', root), 'utf8');
    const body = app.slice(app.indexOf('// Preview menu: "Random"'), app.indexOf('\n})();', app.indexOf('// Preview menu: "Random"')) + '\n})();'.length);
    runInNewContext(body, env);
  }
  return { sky: env.SKY, preview: env.window.skyPreview, calls, timers, intervals, frames, writes, allocations, animations, starPaints, document, node,
    viewport(width, height) { env.innerWidth = width; env.innerHeight = height; },
    advance(now, { runTimers = true } = {}) {
      clock = now;
      if (runTimers) for (const [id, t] of [...timers]) if (t.at <= now && timers.delete(id)) t.cb();
      for (const [id, cb] of [...frames]) if (frames.delete(id)) cb(now);
    },
    resizePane(key, bounds) { Object.assign(panes[key], bounds); paneResizes.get(node(key))(); },
    emit(type, props = {}) { const e = { type, target: node('body'), preventDefault: noop, stopPropagation: noop, ...props }; for (const cb of events.get(type) || []) cb(e); },
    motion(matches) { mediaChange({ matches }); },
  };
}

test('real wet glass schedules and composites once on the shared weather/outdoor frame', () => {
  const p = airPage({ realWet: true });
  p.sky.air({ rh: 66 });
  assert.equal(p.frames.size, 1, 'wet scheduling does not allocate a second RAF');
  const glassApi = p.calls.find(c => c[0] === 'glass' && c[1] === 'create')[2];
  glassApi.requestOutdoorFrame(now => p.calls.push(['outdoor', 'draw', now]));
  p.advance(16);
  const reads = p.calls.filter(c => c[0] === 'canvas' && c[1] === 'read' && c[2] === 16 && c[3] === 195 && c[4] === 400);
  assert.equal(reads.length, 1, 'one scene copy: wet tick must not composite privately as well');
  const stages = p.calls.filter(c => c[2] === 16 && ['weather', 'outdoor', 'wet'].includes(c[0])).map(c => c[0]);
  assert.deepEqual(stages, ['weather', 'outdoor', 'wet']);
  p.document.hidden = true; p.emit('visibilitychange');
  assert.equal(p.frames.size, 0, 'wet cancellation leaves no independent frame');
});

test('sky elects once from a fresh Summary and consumes skipped or cached loads', () => {
  const snapshot = { pm: 48, rh: 66, ctx: null, summary: { items: [{ id: 'pm', action: { text: 'Shut windows' } }] } };
  for (const mode of ['fresh', 'cached', 'skip', 'early-input', 'late']) {
    const p = airPage();
    p.sky.air(snapshot); p.advance(700);
    assert.equal(p.calls.filter(c => c[1] === 'play').length, 0, 'saved data never starts a pass');
    if (mode === 'skip') p.emit('wheel');
    if (mode === 'late') p.advance(4000);
    p.sky.air(snapshot, { initial: true, freshSummary: mode === 'cached' ? null : snapshot.summary, skipped: mode === 'early-input' });
    p.advance(mode === 'late' ? 4001 : 701);
    const skipped = ['cached', 'skip', 'early-input'].includes(mode);
    assert.deepEqual(p.calls.filter(c => c[1] === 'play').map(c => c.slice(0, 3)), skipped ? [] : [['glass', 'play', 'smoke']]);
    p.sky.air(snapshot, { initial: true, freshSummary: snapshot.summary }); p.advance(8000);
    assert.equal(p.calls.filter(c => c[1] === 'play').length, skipped ? 0 : 1);
  }
  const p = airPage();
  p.sky.air(snapshot, { initial: true, freshSummary: snapshot.summary });
  p.advance(599); assert.equal(p.calls.filter(c => c[1] === 'play').length, 0);
  p.advance(600); assert.equal(p.calls.filter(c => c[1] === 'play').length, 1);
});

// The iPhone missed its first-load pass (25 Nov): these rules decide it and fail silently when they break.
test('the load pass survives a load scroll, a hidden tab and slow data, timed from the content', () => {
  const snapshot = { pm: 48, rh: 66, ctx: null, summary: { items: [{ id: 'pm', action: { text: 'Shut windows' } }] } };
  const plays = p => p.calls.filter(c => c[1] === 'play').length;
  const elect = (p, at) => { p.advance(at); p.sky.air(snapshot, { initial: true, freshSummary: snapshot.summary }); };
  // Due 600 ms after the Summary first appears, not after navigation; a scroll event alone never cancels it.
  let p = airPage({ search: '?skydebug' });
  p.advance(900); p.sky.air(snapshot); p.emit('scroll'); elect(p, 1000);
  p.advance(1499); assert.equal(plays(p), 0, 'the clear page shows first');
  p.advance(1500); assert.equal(plays(p), 1);
  assert.match(p.document.body.children.at(-1).textContent, /^sky load: played smoke/);
  // Held back until then: the renderer is told the load pass is still coming, and stops being told once it played.
  const api = p.calls.find(c => c[0] === 'glass' && c[1] === 'create')[2];
  assert.equal(api.loadPending(), false);
  // Hidden when due: plays the first time it is shown within the window, from that moment; never after the window.
  for (const [shown, played] of [[4000, 1], [12000, 0]]) {
    p = airPage({ hidden: true });
    elect(p, 300); p.advance(2000); assert.equal(plays(p), 0, 'nothing plays hidden');
    p.advance(shown); p.document.hidden = false; p.emit('visibilitychange');
    p.advance(shown + 599); assert.equal(plays(p), 0);
    p.advance(shown + 600); assert.equal(plays(p), played, `shown at ${shown} ms`);
  }
  // Slow first data still plays when it arrives; a real input before it does not.
  for (const [input, played] of [[null, 1], ['wheel', 0], ['touchmove', 0], ['pointerdown', 0]]) {
    p = airPage();
    assert.equal(p.calls.find(c => c[0] === 'glass' && c[1] === 'create')[2].loadPending(), true, 'held back while the pass may come');
    p.sky.air(snapshot); p.advance(3000); if (input) p.emit(input, { target: { closest: () => null } });
    elect(p, 8000); p.advance(9000);
    assert.equal(plays(p), played, input || 'no input');
  }
});

test('sky replay, skips, damp gestures and lifecycle preserve a single accessible playing word', () => {
  const snapshot = { pm: 48, rh: 66, ctx: null, summary: null };
  const p = airPage(); p.sky.air(snapshot);
  const buttons = p.node('sky-conditions').children, [smoke, damp] = buttons;
  const target = button => ({ closest: selector => selector === '[data-sky-play]' ? button : null });
  p.emit('pointerdown', { target: target(smoke) }); p.emit('click', { target: target(smoke) });
  assert.equal(smoke.getAttribute('aria-pressed'), 'true');
  p.sky.weather({ wind_speed: 12 }); p.sky.theme(); p.sky.units();
  assert.equal(p.node('sky-conditions').children[0], smoke, 'weather changes preserve focusable nodes');
  assert.equal(p.calls.filter(c => c[1] === 'play').length, 1);
  p.emit('keydown', { key: 'Enter', target: target(damp) }); p.emit('click', { target: target(damp) });
  assert.equal(damp.getAttribute('aria-label'), 'Stop Damp inside effect');
  const wetStops = () => p.calls.filter(c => c[0] === 'wet' && c[1] === 'stop').length;
  const beforeWipe = wetStops();
  p.emit('pointerdown', { gesture: 'pending' }); p.emit('touchmove'); p.emit('pointermove', { gesture: 'wipe' });
  p.emit('pointerup', { gesture: 'wipe' }); p.emit('click');
  assert.equal(wetStops(), beforeWipe, 'horizontal wiping and its synthesized click do not skip');
  p.emit('pointerdown', { gesture: 'pending' }); p.emit('pointermove', { gesture: 'skip' });
  assert.equal(wetStops(), beforeWipe + 1, 'vertical movement skips while retaining normal scrolling');
  assert.equal(damp.getAttribute('aria-pressed'), 'false');
  for (const [type, props] of [['wheel', {}], ['keydown', { key: 'x' }], ['click', {}], ['pointerup', { gesture: 'skip' }]]) {
    p.emit('keydown', { key: ' ', target: target(smoke) }); p.emit('click', { target: target(smoke) });
    assert.equal(smoke.getAttribute('aria-pressed'), 'true');
    p.emit(type, props); assert.equal(smoke.getAttribute('aria-pressed'), 'false', type);
  }
  p.emit('keydown', { key: ' ', target: target(smoke) }); p.emit('click', { target: target(smoke) });
  p.emit('scroll'); assert.equal(smoke.getAttribute('aria-pressed'), 'true', 'a scroll event alone is not an input');
  p.emit('click', { target: target(smoke) });
  p.emit('click', { target: target(smoke) });
  p.emit('sky-quick:click'); assert.equal(smoke.disabled, true);
  const plays = () => p.calls.filter(c => c[1] === 'play').length;
  let before = plays(); p.emit('sky-quick:click');
  assert.equal(smoke.disabled, false); assert.equal(plays(), before, 'master-on only settles');
  p.emit('click', { target: target(smoke) }); p.motion(true);
  assert.equal(smoke.getAttribute('aria-pressed'), 'false'); assert.ok(p.calls.some(c => c[1] === 'still'));
  before = plays(); p.motion(false); assert.equal(plays(), before, 'motion preference does not replay');
  p.emit('click', { target: target(smoke) }); p.document.hidden = true; p.emit('visibilitychange');
  assert.equal(p.frames.size, 0); assert.equal(p.timers.size, 0); assert.equal(smoke.getAttribute('aria-pressed'), 'false');
  before = plays(); p.emit('click', { target: target(smoke) });
  p.document.hidden = false; p.emit('visibilitychange'); assert.equal(plays(), before, 'hidden input and tab return never replay');
  p.sky.air({ ...snapshot, ctx: { pollen: [{ index: 3 }] }, summary: { items: [{ id: 'pm', action: { text: 'Shut windows' } }] } });
  const pollen = p.node('sky-conditions').children[1];
  assert.equal(pollen.children[1].textContent, 'Pollen high', 'new conditions retain stable order without replacing existing buttons');
  p.emit('click', { target: target(pollen) });
  assert.equal(p.calls.filter(c => c[0] === 'glass' && c[1] === 'setConditions').at(-1)[3], 'pollen');
  p.emit('click', { target: target(pollen) });
  assert.equal(p.calls.filter(c => c[0] === 'glass' && c[1] === 'setConditions').at(-1)[3], 'pollen', 'the tapped kind stays shown after its pass');
  p.sky.air({ ...snapshot, pm: null, rh: null }); assert.equal(p.node('sky-conditions').children.length, 0);
  assert.equal(p.timers.size, 0);
  for (const search of ['?shot', '?shot=dark']) {
    const q = airPage({ search }); q.sky.air(snapshot, { initial: true, freshSummary: snapshot.summary });
    assert.equal(q.calls.length, 0); assert.equal(q.timers.size, 0); assert.equal(q.frames.size, 0);
  }
});

test('a completed wipe survives lost capture and a release over a condition control', () => {
  const p = airPage(); p.sky.air({ pm: 48, rh: 66 });
  const damp = p.node('sky-conditions').children[1];
  const target = { closest: selector => selector === '[data-sky-play]' ? damp : null };
  p.emit('click', { target });
  const stops = () => p.calls.filter(c => c[1] === 'stop').length;
  const before = stops();
  p.emit('pointerdown', { pointerId: 7, gesture: 'pending' });
  p.emit('pointermove', { pointerId: 7, gesture: 'wipe' });
  p.emit('pointerup', { pointerId: 7, target, gesture: 'wipe' });
  assert.equal(p.calls.filter(c => c[1] === 'pointer').at(-1)[2].type, 'pointerup', 'owner gets release even over a control');
  p.emit('lostpointercapture', { pointerId: 7 });
  p.emit('click', { pointerId: 7, target });
  assert.equal(stops(), before, 'completed pointer click does not stop or replay');
  p.emit('click', { pointerId: 8 });
  assert.equal(stops(), before + 1, 'another pointer click still skips');
});

test('wet tracks a null link down through up or cancel over a condition control', () => {
  for (const ending of ['pointerup', 'pointercancel', 'lostpointercapture']) {
    const p = airPage({ realWet: true }); p.sky.air({ rh: 66 }); p.sky.wet.play('damp');
    const [damp] = p.node('sky-conditions').children;
    const link = { closest: selector => selector.startsWith('a, button') ? link : null };
    const control = { closest: selector => selector === '[data-sky-play]' ? damp : null };
    p.emit('pointerdown', { pointerId: 1, button: 0, pointerType: 'mouse', target: link });
    p.emit(ending, { pointerId: 1, target: control });
    p.emit('pointerdown', { pointerId: 2, button: 0, pointerType: 'touch', isPrimary: true });
    assert.ok(p.document.documentElement.classList.contains('sky-wet-dragging'), ending);
    p.emit('lostpointercapture', { pointerId: 1 });
    assert.ok(p.document.documentElement.classList.contains('sky-wet-dragging'), 'an unrelated end leaves the new gesture alone');
  }
});

test('a desktop stroke clears the Summary fog while the cards are wet, and the dry cards keep no pane', () => {
  const panes = { summary: { left: 200, top: 40, right: 500, bottom: 280, width: 300, height: 240 },
    // Left of the stroke: the pass's drops run at x 720 and 1355 here, and one fog update damages the box around all its changes.
    other: { left: 20, top: 40, right: 180, bottom: 280, width: 160, height: 240 } };
  for (const theme of ['light', 'dark']) {
    // Runners start at the right edge or between the panes (fixed random), so no trail crosses the stroke.
    const p = airPage({ realWet: true, theme, width: 1440, height: 320, panes, randomValue: .99, prefs: { sky: true, wind: false, air: true } });
    p.sky.air({ rh: 66 }); p.advance(16);
    assert.equal(p.node('summary').children.length, 0, 'settled without a pass: dry cards, no pane');
    p.sky.wet.play('damp'); for (let t = 500; t <= 3500; t += 500) p.advance(t);
    for (let t = 3516; t <= 4000; t += 16) p.advance(t); // the beads finish forming
    const [canvas] = p.node('summary').children[0].children;
    const fog = (x, y) => canvas.fog[(Math.floor(y / 4) * 360 + Math.floor(x / 4)) * 4 + 3];
    const x = 244, y = 150, initial = fog(x, y), outside = fog(x, 70);
    assert.ok(initial > 90 && initial < 125, 'the settled pane has a veil without raising opacity');
    const mouse = { pointerId: 1, pointerType: 'mouse', clientY: y, cancelable: true };
    const paints = () => p.calls.filter(c => c[0] === 'pane' && c[3] === p.node('other')).length;
    const untouched = paints();
    p.emit('pointerdown', { ...mouse, button: 0, clientX: x, timeStamp: 4016 });
    p.emit('pointermove', { ...mouse, clientX: 320, timeStamp: 4040 }); p.advance(4040);
    assert.equal(fog(x, y), 0, 'inset desktop stroke clears the Summary fog texture');
    assert.equal(fog(x, 70), outside, 'the off-stroke portion retains the veil');
    p.advance(6000);
    assert.equal(fog(x, y), 0, 'the beginning of a slow stroke does not regrow mid-drag');
    p.emit('pointerup', { ...mouse, clientX: 450, timeStamp: 6000 }); p.advance(6016);
    assert.equal(fog(450, y), 0, 'release clears its final position too');
    p.advance(7399);
    assert.equal(fog(x, y), 0, 'the entire stroke is clear for 1.4 seconds from release');
    p.advance(8700);
    assert.ok(fog(x, y) > 0 && fog(x, y) < initial, 'fog returns after the release hold');
    p.advance(10000);
    assert.equal(fog(x, y), initial, 'the settled veil returns');
    assert.equal(paints(), untouched, 'localized wet damage does not repaint an untouched pane');
    // 10 s after play the cards' condensation fades out over 2 s: then no card has a pane, and a drag is the page's own.
    p.advance(11016); assert.ok(Number(p.node('summary').children[0].style.opacity) < 1, 'the cards dry slowly');
    p.advance(12100);
    assert.equal(p.node('summary').children.length, 0, 'dry cards keep no pane');
    assert.equal(p.document.documentElement.classList.contains('sky-wet-active'), false, 'horizontal touch gestures are the page\'s again');
    p.emit('pointerdown', { ...mouse, button: 0, clientX: x, timeStamp: 12116 });
    assert.equal(p.document.documentElement.classList.contains('sky-wet-dragging'), false, 'nothing on the cards to wipe');
  }
});

test('damp keeps its word playing while the cards are wet; a skip dries them at once and the word replays it', () => {
  const p = airPage({ realWet: true, randomValue: .99, width: 120, height: 80,
    panes: { summary: { left: 10, top: 10, right: 110, bottom: 70, width: 100, height: 60 } } });
  p.sky.air({ rh: 66 }); p.advance(16);
  const [damp] = p.node('sky-conditions').children, wet = () => p.node('summary').children.length;
  p.sky.wet.play('damp'); for (let t = 500; t <= 5000; t += 500) p.advance(t);
  assert.equal(wet(), 1, 'beads and drops stay on the cards after the pass');
  assert.equal(damp.getAttribute('aria-pressed'), 'true', 'the word can still stop it');
  p.emit('click', {}); p.advance(5016); p.advance(5300);
  assert.equal(wet(), 0, 'a tap dries the cards in the skip fade');
  assert.equal(damp.getAttribute('aria-pressed'), 'false');
  p.emit('click', { target: { closest: selector => selector === '[data-sky-play]' ? damp : null } }); p.advance(5316);
  assert.equal(wet(), 1, 'the word replays the whole thing');
});

test('rain-only frames do not copy the wet backdrop or repaint panes', () => {
  const p = airPage({ realWet: true, width: 120, height: 40, weather: { weather_code: 95, cloud_cover: 0, wind_speed: 0 },
    panes: { summary: { left: 10, top: 5, right: 110, bottom: 35, width: 100, height: 30 } } });
  p.sky.air({ rh: 66 }); p.advance(16);
  p.calls.length = 0;
  for (const now of [32, 48, 64]) p.advance(now);
  assert.ok(p.calls.some(c => c[0] === 'rain'), 'the front rain draws');
  assert.equal(p.calls.filter(c => c[0] === 'canvas' && c[3] === 30 && c[4] === 10).length, 0, 'no rear scene copy');
  assert.equal(p.calls.filter(c => c[0] === 'pane').length, 0);
});

test('a settled WebGL pane redraw restores the visible viewport', () => {
  const p = airPage({ realWet: 'gpu', width: 120, height: 40, prefs: { sky: true, wind: false, air: true },
    panes: { summary: { left: 10, top: 5, right: 110, bottom: 35, width: 100, height: 30 } } });
  p.sky.air({ rh: 66 }); p.advance(16); p.sky.wet.play('damp');
  for (let t = 32; t <= 5000; t += 16) p.advance(t);
  const gpuDraws = () => p.calls.filter(c => c[0] === 'gpu').map(c => c[2]);
  p.calls.length = 0;
  p.advance(5016);
  assert.deepEqual(gpuDraws(), [], 'the settled viewport needs no repaint');
  p.resizePane('summary', { right: 100, width: 90 }); p.advance(5032);
  assert.deepEqual(gpuDraws(), ['pane', 'viewport'], 'the final draw leaves no card shading on the viewport');
});

test('settled damp caches its composite across moving weather in both backends', () => {
  for (const realWet of [true, 'gpu']) {
    const p = airPage({ realWet, width: 120, height: 80, randomValue: .99,
      weather: { cloud_cover: 100, wind_speed: 18, wind_direction: 135 },
      panes: { summary: { left: 10, top: 10, right: 110, bottom: 70, width: 100, height: 60 } } });
    p.sky.air({ rh: 66 });
    for (const now of [16, 32, 1000, 5000]) p.advance(now);
    p.calls.length = 0;
    for (const now of [5016, 5032, 5048, 5064, 5080]) p.advance(now);
    assert.equal(p.calls.filter(c => c[0] === 'wet' && c[1] === 'tick').length, 0);
    assert.equal(p.calls.filter(c => c[0] === 'canvas' && c[3] === 30 && c[4] === 20).length, 0, 'settled scene has no readback');
    assert.equal(p.calls.filter(c => c[0] === 'pane' || c[0] === 'gpu').length, 0, 'settled panes keep their pixels');
    p.resizePane('summary', { width: 90, right: 100 }); p.advance(5096);
    assert.ok(!p.calls.some(c => c[0] === 'pane') && !p.node('summary').children.length, 'settled cards are dry: no pane to repaint');
    p.calls.length = 0; p.sky.wet.burst(['damp']); p.advance(5112);
    assert.ok(p.calls.some(c => c[1] === 'draw' && c[2] === 'viewport'), 'a wet burst still repaints the viewport');
    assert.ok(!p.calls.some(c => c[0] === 'pane'), 'drops outside the pane do not repaint it');
  }
});

test('a tab opened hidden defers wet grain and WebGL setup until it is visible', () => {
  for (const realWet of [true, 'gpu']) {
    const p = airPage({ hidden: true, realGlass: true, realWet, width: 120, height: 80 });
    p.sky.air({ rh: 66 }); p.advance(16);
    assert.equal(p.calls.filter(c => c[1] === 'context').length, 0, 'no hidden WebGL context');
    assert.equal(p.calls.filter(c => c[0] === 'canvas' && c[1] === 'read').length, 0, 'no hidden grain raster');
    assert.equal(p.allocations.length, 0, 'no hidden pixel buffers');
    p.document.hidden = false; p.emit('visibilitychange'); p.advance(32);
    assert.equal(p.calls.filter(c => c[1] === 'context').length, realWet === 'gpu' ? 1 : 0);
    assert.ok(p.calls.some(c => c[0] === (realWet === 'gpu' ? 'gpu' : '2d') && c[1] === 'draw' && c[2] === 'viewport'),
      'the selected backend paints after resume');
  }
});

test('quiet initialization and hidden resizes allocate no canvases or stars', () => {
  for (const options of [{ search: '?shot' }, { search: '?shot=dark' }, { appState: 'no_token' }, { hidden: true }]) {
    const p = airPage(options);
    assert.equal(p.allocations.length, 0, 'initialization defers pixel buffers');
    assert.equal(p.starPaints.length, 0);
    p.emit('resize');
    assert.equal(p.allocations.length, 0);
    assert.equal(p.starPaints.length, 0);
    if (options.hidden) {
      p.document.hidden = false; p.emit('visibilitychange');
      assert.ok(p.allocations.length > 0); assert.ok(p.starPaints.length > 0);
    }
  }
  const p = airPage({ realGlass: true, realWet: true });
  p.sky.air({ pm: 48, rh: 66 }); p.advance(16);
  p.document.hidden = true; p.emit('visibilitychange');
  p.allocations.length = 0; p.starPaints.length = 0;
  p.viewport(800, 600);
  p.emit('resize'); p.emit('resize');
  assert.equal(p.allocations.length, 0, 'all renderer buffers wait while hidden');
  assert.equal(p.starPaints.length, 0);
  p.document.hidden = false; p.emit('visibilitychange'); p.advance(32);
  assert.equal(p.allocations.filter(([key, axis]) => key === 'sky-stars' && axis === 'width').length, 1, 'resume applies one resize');
  const outdoor = p.node('sky').children.find(c => c.className?.includes('sky-air-back-soft'));
  assert.equal(outdoor.width, 200, 'outdoor buffers receive the resumed viewport');
  p.allocations.length = 0; p.emit('resize');
  const output = p.node('sky').children.find(c => c.className === 'sky-wet-viewport');
  assert.equal(p.allocations.filter(([key, axis]) => p.node(key) === output && axis === 'width').length, 1, 'one owner resizes wet glass');
});

test('a second contact on a condition button ends wiping without consuming its native click', () => {
  const p = airPage({ realWet: true }); p.sky.air({ pm: 48, rh: 66 }); p.advance(16); p.sky.wet.play('damp');
  const [smoke] = p.node('sky-conditions').children;
  const target = { closest: selector => selector === '[data-sky-play]' || selector.startsWith('a, button') ? smoke : null };
  const touch = { pointerType: 'touch', button: 0, clientY: 200, cancelable: true };
  p.emit('pointerdown', { ...touch, pointerId: 1, isPrimary: true, clientX: 50, timeStamp: 16 });
  p.emit('pointermove', { ...touch, pointerId: 1, clientX: 90, timeStamp: 32 });
  let consumed = 0;
  p.emit('pointerdown', { ...touch, pointerId: 2, isPrimary: false, target, preventDefault() { consumed++; } });
  assert.equal(p.document.documentElement.classList.contains('sky-wet-dragging'), false);
  p.emit('pointermove', { ...touch, pointerId: 1, clientX: 140, timeStamp: 48, preventDefault() { consumed++; } });
  assert.equal(consumed, 0, 'multi-touch moves and control presses retain native behavior');
  p.emit('pointerup', { ...touch, pointerId: 2, target }); p.emit('click', { pointerId: 2, target });
  assert.equal(smoke.getAttribute('aria-pressed'), 'true');
  p.emit('pointerup', { ...touch, pointerId: 1 });
  p.emit('pointerdown', { ...touch, pointerId: 3, isPrimary: true, clientX: 50, timeStamp: 64 });
  assert.equal(p.document.documentElement.classList.contains('sky-wet-dragging'), true, 'all contact endings were tracked');
});

test('storm expiry prevents new strikes and storm exit cancels active flashes and card motion', () => {
  const wallTime = Date.parse('2026-11-24T12:00:00Z');
  const p = airPage({ wallTime, weatherCache: { at: { thunder: wallTime - 6 * 3600e3 + 1000, rain: wallTime, cloud: wallTime },
    v: { thunder: true, rain: 1, cloud: 100 } } });
  p.advance(16); p.advance(1100); p.sky.air({});
  assert.match(p.node('sky-weather').innerHTML, /Heavy rain/);
  p.advance(1300);
  assert.equal(p.animations.length, 0, 'an expired thunder reading cannot schedule a strike');
  const q = airPage({ randomValue: .5, panes: { summary: { left: 10, top: 100, right: 380, bottom: 400, width: 370, height: 300 } } });
  q.preview({ minutes: 900, thunder: true, rain: 1, cloud: 100 });
  q.advance(16); q.advance(1300);
  assert.equal(q.animations.length, 4, 'front strike owns both flashes, card shadow and impact motion');
  q.preview({ minutes: 900, thunder: false, rain: 1, cloud: 100 });
  assert.ok(q.animations.every(a => a.playState !== 'running'), 'all storm-owned animations stop on exit');
  const before = q.animations.length; q.advance(1400); q.advance(10000);
  assert.equal(q.animations.length, before);
});

test('stopping a tapped pollen fades its card tint into its own settled sky, and smoke is no longer drawn', () => {
  for (const theme of ['light', 'dark']) {
    const p = airPage({ realGlass: true, theme, panes: { summary: { left: 10, top: 150, right: 380, bottom: 500, width: 370, height: 350 } } });
    p.sky.air({ pm: 48, ctx: { pollen: [{ index: 3 }] }, summary: { items: [{ id: 'pm', action: { text: 'Shut windows' } }] } }, { initial: true, freshSummary: null });
    const [, pollen] = p.node('sky-conditions').children;
    const target = { closest: () => pollen };
    const tint = () => p.node('summary').style.getPropertyValue('--glass-tint');
    const washes = () => p.calls.filter(c => c[0] === 'wash' && c[3]).map(c => c[3]);
    p.advance(16); const settled = tint(); const [smokeRgb] = washes()[0].split(' / ');
    p.emit('click', { target }); p.advance(32); p.advance(432);
    const outgoing = tint();
    assert.notEqual(outgoing, settled);
    p.calls.length = 0;
    p.emit('click', { target });
    assert.equal(pollen.getAttribute('aria-pressed'), 'false', 'the button goes idle immediately');
    assert.equal(tint(), outgoing, 'the current card tint does not snap');
    p.calls.length = 0;
    p.advance(557);
    assert.notEqual(tint(), outgoing); assert.notEqual(tint(), settled);
    p.calls.length = 0;
    p.advance(682);
    assert.equal(tint(), settled);
    assert.ok(washes().length, 'pollen stays in the sky');
    assert.ok(!washes().some(color => color.startsWith(smokeRgb)), 'smoke is back to its word only');
    assert.equal(p.sky.glass.smokeCover, 0);
  }
});

test('after a pass every card returns to the everyday glass, and the air stays in the sky', () => {
  const panes = { summary: { left: 10, top: 150, right: 380, bottom: 500, width: 370, height: 350 } };
  for (const theme of ['light', 'dark']) {
    const glass = page => ['--glass-tint', '--glass-alpha'].map(k => page.node('summary').style.getPropertyValue(k)).join(' / ');
    const clean = airPage({ realGlass: true, theme, panes });
    clean.sky.air({ pm: 5 }, { initial: true, freshSummary: null }); clean.advance(16);
    const p = airPage({ realGlass: true, theme, panes });
    p.sky.air({ pm: 48, summary: { items: [{ id: 'pm', action: { text: 'Shut windows' } }] } }, { initial: true, freshSummary: null });
    const washes = () => p.calls.filter(c => c[0] === 'wash' && c[3]);
    p.advance(16);
    assert.equal(glass(p), glass(clean), 'settled smoke leaves the everyday glass');
    assert.ok(washes().length, 'the smoke stays in the sky');
    const [smoke] = p.node('sky-conditions').children;
    p.emit('click', { target: { closest: () => smoke } });
    let now = 16;
    while (now < 1000) p.advance(now += 16);
    assert.notEqual(glass(p), glass(clean), 'the pass lends the card the smoke');
    while (now < 6000) p.advance(now += 16);
    assert.equal(glass(p), glass(clean), 'the card is back to the everyday glass');
    p.calls.length = 0; p.sky.glass.redraw(); p.advance(now += 16);
    assert.ok(washes().length, 'the smoke is still in the sky');
  }
});

test('hide and resume keep the tapped outdoor kind shown without replaying', () => {
  const p = airPage();
  p.sky.air({ pm: 48, ctx: { pollen: [{ index: 3 }] }, summary: { items: [{ id: 'pm', action: { text: 'Shut windows' } }] } });
  const pollen = p.node('sky-conditions').children[1];
  p.emit('click', { target: { closest: () => pollen } });
  const primary = () => p.calls.filter(c => c[0] === 'glass' && c[1] === 'setConditions').at(-1)[3];
  assert.equal(primary(), 'pollen');
  const before = p.calls.filter(c => c[1] === 'play').length;
  p.document.hidden = true; p.emit('visibilitychange');
  assert.equal(primary(), 'pollen');
  p.document.hidden = false; p.emit('visibilitychange');
  assert.equal(primary(), 'pollen');
  assert.equal(p.calls.filter(c => c[1] === 'play').length, before);
});

// One effect per layer (Sacha, 25 Nov): one outdoor kind in the sky, damp on the glass; any other outdoor condition is its word only.
test('only the shown outdoor kind and damp are drawn; a tap on another word plays it and makes it the one shown', () => {
  const road = [{ name: 'BQE', short: 'BQE', side: 'W', reaching: true, strength: 'peak' }];
  const ctx = { roads: road, pollen: [{ index: 3 }] };
  const last = (p, renderer) => p.calls.filter(c => c[0] === renderer && c[1] === 'setConditions').at(-1);
  const drawn = (p, renderer = 'glass') => last(p, renderer)[2].map(c => c.kind);
  const words = p => [...p.node('sky-conditions').children].filter(b => b.dataset.skyPlay).map(b => b.dataset.skyPlay);   // the exhaust's wind pill isn't a word
  const pm = { items: [{ id: 'pm', action: { text: 'Shut windows' } }] };
  const damp = { items: [{ id: 'rh', facts: { dry: false }, action: { text: 'Run the dehumidifier' } }] };
  // The Summary's leader is shown; damp is drawn beside it; the second outdoor kind is only its word.
  let p = airPage({ randomValue: .5 });
  p.sky.air({ pm: 48, rh: 66, ctx: { pollen: [{ index: 3 }] }, summary: pm });
  assert.deepEqual(words(p), ['smoke', 'pollen', 'damp']);
  assert.deepEqual(drawn(p), ['smoke', 'damp']); assert.equal(last(p, 'glass')[3], 'smoke');
  assert.deepEqual(drawn(p, 'wet'), ['smoke', 'damp']);
  // Damp leads: the strongest outdoor kind by priority (exhaust before pollen) is the one in the sky.
  p = airPage({ randomValue: .5 });
  p.sky.air({ pm: 20, rh: 66, ctx, summary: damp });
  assert.deepEqual(words(p), ['exhaust', 'pollen', 'damp']);
  assert.deepEqual(drawn(p), ['exhaust', 'damp']);
  // A tap on pollen plays it and keeps it shown after its pass, through new data; exhaust goes back to its word.
  const pollen = p.node('sky-conditions').children[1];
  p.emit('click', { target: { closest: selector => selector === '[data-sky-play]' ? pollen : null } });
  assert.deepEqual(p.calls.filter(c => c[1] === 'play').map(c => c.slice(0, 3)), [['glass', 'play', 'pollen']]);
  assert.deepEqual(drawn(p), ['pollen', 'damp']);
  p.sky.glass.settle(); p.sky.air({ pm: 20, rh: 66, ctx, summary: damp });
  assert.deepEqual(drawn(p), ['pollen', 'damp']);
  // Sparse events carry the shown kind only.
  p.calls.length = 0; p.advance(40001);
  assert.deepEqual(p.calls.filter(c => c[1] === 'burst').map(c => [c[0], ...c[2]]), [['glass', 'pollen']]);
  // When the tapped kind ends, the sky falls back to the priority order.
  p.sky.air({ pm: 20, rh: 66, ctx: { roads: road }, summary: damp });
  assert.deepEqual(drawn(p), ['exhaust', 'damp']);
  // Reduced motion: the still shows only the shown kinds, and a tap switches the still without a pass.
  p = airPage({ reduced: true });
  p.sky.air({ pm: 48, rh: 66, ctx: { pollen: [{ index: 3 }] }, summary: pm });
  assert.ok(p.calls.some(c => c[0] === 'glass' && c[1] === 'still'));
  assert.deepEqual(drawn(p), ['smoke', 'damp']);
  const still = p.node('sky-conditions').children[1];
  p.emit('click', { target: { closest: selector => selector === '[data-sky-play]' ? still : null } });
  assert.equal(p.calls.filter(c => c[1] === 'play').length, 0);
  assert.deepEqual(drawn(p), ['pollen', 'damp']);
});

// The display menu's air buttons: a home previews the kind over its own readings; the demo opens the kind's story.
test('an air preview shows its kind over the home, one per layer, and Back to live restores the home', () => {
  const last = (p, renderer) => p.calls.filter(c => c[0] === renderer && c[1] === 'setConditions').at(-1);
  const drawn = (p, renderer = 'glass') => last(p, renderer)[2].map(c => c.kind);
  const words = p => [...p.node('sky-conditions').children].filter(b => b.dataset.skyPlay).map(b => b.dataset.skyPlay);   // the exhaust's wind pill isn't a word
  const pressed = p => ['smoke', 'exhaust', 'particles', 'pollen', 'damp'].filter(k => p.node('fx-air-' + k).getAttribute('aria-pressed') === 'true');
  const pm = { items: [{ id: 'pm', action: { text: 'Shut windows' } }] };
  const home = { pm: 48, rh: 66, ctx: { pollen: [{ index: 3 }] }, summary: pm };
  let p = airPage({ randomValue: .5 });
  p.sky.air(home);
  assert.deepEqual(drawn(p), ['smoke', 'damp']);
  // Exhaust replaces the shown outdoor kind and plays; the home's smoke and pollen stay as words, damp stays on the glass.
  p.node('dsp').open = true;
  p.emit('fx-air-exhaust:click');
  assert.equal(p.node('dsp').open, false, 'the menu closes so the pass shows');
  assert.deepEqual(words(p), ['smoke', 'exhaust', 'pollen', 'damp']);
  assert.deepEqual(drawn(p), ['exhaust', 'damp']); assert.equal(last(p, 'glass')[3], 'exhaust');
  assert.deepEqual(p.calls.filter(c => c[1] === 'play').map(c => c.slice(0, 3)), [['glass', 'play', 'exhaust']]);
  assert.match(p.node('sky-weather').innerHTML, /cap-tag">Preview/);
  assert.equal(p.node('fx-mode').textContent, 'Preview');
  assert.deepEqual(pressed(p), ['exhaust']);
  // New readings keep the preview; the label names the road the page knows, else a plain one.
  p.sky.glass.settle(); p.sky.air(home);
  assert.deepEqual(drawn(p), ['exhaust', 'damp']);
  assert.equal(last(p, 'glass')[2].find(c => c.kind === 'exhaust').label, 'Road exhaust');
  // A weather preset keeps the air preview and its haze.
  p.preview({ minutes: 1380, dir: 200, spd: 8, cloud: 5, pm: 1 });
  assert.deepEqual(drawn(p), ['exhaust', 'damp']);
  // Damp plays on the glass beside the previewed outdoor kind.
  p.emit('fx-air-damp:click');
  assert.deepEqual(p.calls.filter(c => c[1] === 'play').at(-1).slice(0, 3), ['wet', 'play', 'damp']);
  assert.deepEqual(drawn(p, 'wet'), ['exhaust', 'damp']); assert.equal(last(p, 'glass')[3], 'exhaust', 'the previewed outdoor kind stays shown');
  assert.deepEqual(pressed(p), ['exhaust', 'damp']);
  // Back to live: the home's own conditions, no tag, nothing pressed.
  p.emit('fx-live:click');
  assert.deepEqual(words(p), ['smoke', 'pollen', 'damp']);
  assert.deepEqual(drawn(p), ['smoke', 'damp']);
  assert.doesNotMatch(p.node('sky-weather').innerHTML, /cap-tag/);
  assert.equal(p.node('fx-mode').textContent, 'Live');
  assert.deepEqual(pressed(p), []);
  // Reduced motion: the still of the previewed kind, no pass.
  p = airPage({ reduced: true });
  p.sky.air({ pm: 5, rh: 40, ctx: null, summary: null });
  p.emit('fx-air-pollen:click');
  assert.equal(p.calls.filter(c => c[1] === 'play').length, 0);
  assert.ok(p.calls.some(c => c[0] === 'glass' && c[1] === 'still'));
  assert.deepEqual(drawn(p), ['pollen']);
  // The demo: each button opens its story and previews nothing.
  const opened = [];
  p = airPage({ beforeSky(env) { env.APP.now = { moment: 'clear' }; env.location.assign = url => opened.push(url); } });
  p.sky.air({ pm: 5, rh: 40, ctx: null, summary: null });
  for (const kind of ['smoke', 'exhaust', 'particles', 'pollen', 'damp']) p.emit(`fx-air-${kind}:click`);
  assert.deepEqual(opened, ['?moment=sky-smoke', '?moment=rush', '?moment=sky-particles', '?moment=sky-pollen', '?moment=humid']);
  assert.equal(p.calls.filter(c => c[1] === 'play').length, 0);
  assert.equal(p.node('fx-mode').textContent, 'Live');
  assert.equal(p.node('fx-air-smoke').getAttribute('aria-pressed'), undefined, 'a link-like button, not a toggle');
});

test('ordinary outdoor settle clears the still latch for sparse events', () => {
  const p = airPage({ realGlass: true });
  p.sky.air({ pm: 48 });
  p.sky.glass.still();
  const count = () => p.calls.filter(c => c[1] === 'frame').length;
  let before = count(); p.sky.glass.burst(['smoke']);
  assert.equal(count(), before, 'still mode suppresses sparse events');
  p.sky.glass.settle(); before = count(); p.sky.glass.burst(['smoke']);
  assert.equal(count(), before + 1, 'ordinary settle restores sparse scheduling');
});

test('buffered weather hydrates rain, thunder and freshness through the live updater', () => {
  for (const weather_code of [61, 95]) {
    const weather = { temperature: 12, wind_speed: 9, wind_direction: 135, cloud_cover: 100, weather_code };
    const buffered = airPage({ weather });
    const live = airPage(); live.sky.weather(weather);
    assert.equal(buffered.node('sky-weather').innerHTML, live.node('sky-weather').innerHTML);
    assert.equal(buffered.node('sky-cap').title, '');
  }
});

test('weather failure leaves saved wind and thunder stale without renewing the cache', () => {
  const savedWeather = { dir: 135, spd: 9, cloud: 100, rain: 1, thunder: true, temp: 12 };
  const unavailable = [{}, { temperature: null, wind_speed: null, wind_direction: null, cloud_cover: null, weather_code: null },
    { weather_code: -1 }, { weather_code: 100 }, null];
  for (const weather of unavailable) {
    const p = airPage({ savedWeather, weather });
    p.sky.air({ pm: 24 }); p.sky.weather(weather); p.sky.air({ pm: 25 });
    assert.match(p.node('sky-cap').title, /Last known/);
    assert.match(p.node('sky-weather').innerHTML, /Thunderstorm.*SE 9 km\/h/);
    assert.equal(p.writes.length, 0, 'weather failure and PM cannot renew the cache');
  }
  const p = airPage({ savedWeather });
  p.sky.weather({ wind_speed: 12, weather_code: null });
  assert.match(p.node('sky-weather').innerHTML, /Thunderstorm.*SE 12 km\/h/, 'partial data updates wind but does not clear rain');
  p.sky.weather({ weather_code: 0 });
  assert.doesNotMatch(p.node('sky-weather').innerHTML, /Thunderstorm/);
  assert.equal(p.writes.at(-1)[1].v.rain, 0);
  assert.match(p.node('sky-cap').title, /Last known/, 'a fresh weather code does not refresh retained wind and temperature');
  p.sky.weather({ temperature: 12, wind_speed: 12, wind_direction: 135, cloud_cover: 0, weather_code: 0 });
  assert.equal(p.node('sky-cap').title, '');
  p.sky.weather({});
  assert.match(p.node('sky-cap').title, /Last known/, 'an error after live weather marks it stale again');
});

test('offline weather and context cannot renew weather, select live conditions or elect a load pass', () => {
  const wallTime = Date.parse('2026-11-24T12:00:00Z');
  const p = airPage({ wallTime, weatherCache: { at: wallTime - 6 * 3600e3, v: { thunder: true, rain: 1 } },
    road: { name: 'Legacy road', toward: [100, 180] } });
  const weather = { temperature: 12, wind_speed: 9, wind_direction: 135, cloud_cover: 100, weather_code: 95 };
  p.sky.weather(weather, { cached: true });
  assert.equal(p.writes.length, 0);
  assert.doesNotMatch(p.node('sky-weather').innerHTML, /Thunderstorm/);
  const summary = { items: [{ id: 'pollen', action: { text: 'Shut windows' } }] };
  p.sky.air({ ctx: { _offline: true, weather, roads: [{ reaching: true, short: 'Old road', strength: 'high' }], pollen: [{ index: 4 }] }, summary },
    { initial: true, freshSummary: summary }); p.advance(700);
  assert.equal(p.node('sky-conditions').children.length, 0);
  assert.doesNotMatch(p.node('sky-weather').innerHTML, /Old road|Legacy road/);
  assert.equal(p.calls.filter(c => c[1] === 'play').length, 0);
});

// Run the real reading, monitor rendering and refresh paths with only unrelated page work stubbed out.
function skyPmPage({ outdoor = null, live = false, estimate = false, ...options } = {}) {
  const src = readFileSync(new URL('static/app.js', root), 'utf8');
  const part = (from, to) => src.slice(src.indexOf(from), src.indexOf(to, src.indexOf(from)));
  let current, app;
  const page = airPage({ ...options, beforeSky(env) {
    const noop = () => {};
    env.state.outdoor = outdoor;
    Object.assign(env, { skyReadingsAvailable: live, shownSkySummary: null, skyInitialOutcome: null,
      skyEarlySkip: null, skyLoadSkipped: false, NO_INDOOR: false, NO_OUTDOOR: estimate,
      singleFlight: fn => fn, skyFirstRefresh: true, SRC_ON: { nearby: false },
      getJSON: async url => url === '/api/current' ? current : {}, pickEntries: home.pickEntries,
      sourceLine: home.sourceLine, estimateNow: home.estimateNow, readingsNow: env.appNow,
      monitorsHTML: () => '<article>Monitor reading</article>',
      updateStatus: noop, showUpdated: noop, showBootRead: noop, renderContext: noop, contextStatus: noop,
      loadRead: async () => ({ freshSummary: null }), loadCharts: async () => {},
      REFRESH_TIMEOUT_MS: 30000, forceNext: false, loadFailed: false, lastRefresh: 0, lastStamp: null });
    const monitors = env.$('monitors');
    Object.defineProperty(monitors, 'childElementCount', { get: () => monitors.innerHTML ? 1 : 0 });
    app = runInNewContext([
      part('const outSource =', '\nconst outEstimate ='), part('const pick = ', 'const clock ='), part('function reading(', '// ── Monitors'),
      part('const estimateAt =', '// ghost:'), part('const estimateKey =', '// ── Local conditions'),
      part('const refreshOnce = singleFlight(', '\nfunction refresh(opts)'),
      '({ refresh: refreshOnce, redraw: (read, readAt) => { Object.assign(state, { read, readAt }); redrawEstimate(); } })',
    ].join('\n'), env);
  } });
  return { ...page, redrawEstimate: app.redraw, refresh(outdoor, cached = false) {
    current = Object.assign([{ locationType: 'outdoor', ...outdoor }], { _offline: cached });
    return app.refresh();
  } };
}

// The air effects are the sky's only PM2.5 picture (the old haze tint went on 26 Nov): only fresh outdoor PM2.5 shows them.
const airWords = p => [...p.node('sky-conditions').children].filter(b => b.dataset.skyPlay).map(b => b.dataset.skyPlay);
test('sky startup paints only outdoor PM from the fresh snapshot', () => {
  const outdoor = { pm02_corrected: 55 };
  const weather = { cloud_cover: 30 };
  for (const live of [false, true]) {
    const p = skyPmPage({ outdoor, live, weather, savedWeather: { cloud: 20, pm: 60 } });
    assert.deepEqual(airWords(p), live ? ['smoke'] : []);
  }
  const reopened = skyPmPage({ savedWeather: { cloud: 20, pm: 60 } });
  assert.deepEqual(airWords(reopened), [], 'legacy sky cache is not a live PM source');
});

test('cached current on refresh never paints the air or writes PM to the sky cache', async () => {
  const p = skyPmPage({ weather: { cloud_cover: 30 } });
  await p.refresh({ pm02_corrected: 55 }, true);
  assert.deepEqual(airWords(p), []);
  assert.match(p.node('monitors').innerHTML, /Monitor reading/, 'cached monitor cards still render');
  await p.refresh({ pm02: 55 });
  assert.deepEqual(airWords(p), ['smoke'], 'fresh monitor data still paints the air');
  await p.refresh({ pm02_corrected: 44 }, true);
  assert.deepEqual(airWords(p), [], 'cached refresh clears the live air');
  p.sky.weather({ cloud_cover: 100 });
  assert.ok(p.writes.every(([, payload]) => !('pm' in payload.v)), 'sky cache holds weather only');
});

test('a saved Summary estimate can fill its monitor card without painting live air', async () => {
  const p = skyPmPage({ estimate: true });
  const outdoor = { source: { kind: 'model' } };
  const read = { summary: { timeline: { pm: { source: 'model', past: [['2026-11-24T12:00:00Z', 55]] } } } };
  await p.refresh(outdoor);
  p.redrawEstimate(read, 0);
  assert.deepEqual(airWords(p), [], 'fresh current cannot make a saved estimate live');
  p.redrawEstimate(read, Date.now());
  assert.deepEqual(airWords(p), ['smoke'], 'a newly loaded model estimate can paint');
  await p.refresh(outdoor, true);
  p.redrawEstimate(read, Date.now());
  assert.deepEqual(airWords(p), [], 'estimate redraw cannot bypass the offline current gate');
});

test('getJSON preserves the service worker fallback marker on objects and reading arrays', async () => {
  const src = readFileSync(new URL('static/app.js', root), 'utf8');
  const body = src.slice(src.indexOf('async function getJSON('), src.indexOf('\nfunction homeHour()'));
  for (const data of [{ weather: { weather_code: 95 } }, [{ locationId: 1 }]]) {
    const fn = new Function('fetch', 'forceNext', 'apiUrl', 'FETCH_TIMEOUT_MS', body + '\nreturn getJSON;')(
      async () => new Response(JSON.stringify(data), { headers: { 'X-Home-Air-Cache': 'fallback' } }), false, url => url, 1000);
    const result = await fn('/api/context');
    assert.equal(result._offline, true);
    assert.equal(JSON.stringify(result), JSON.stringify(data), 'metadata stays outside request payloads');
  }
});

test('temperature-only weather cannot make retained wind and thunder fresh', () => {
  const wallTime = Date.parse('2026-11-24T12:00:00Z');
  const p = airPage({ wallTime, savedWeather: { dir: 135, spd: 9, cloud: 100, rain: 1, thunder: true, temp: 12 } });
  p.sky.weather({ temperature: 16, weather_code: null });
  assert.match(p.node('sky-weather').innerHTML, /Thunderstorm.*16°.*SE 9 km\/h/);
  assert.match(p.node('sky-cap').title, /Last known/);
  const weatherCache = p.writes.at(-1)[1];
  const warm = airPage({ wallTime, weatherCache });
  assert.match(warm.node('sky-weather').innerHTML, /Thunderstorm.*16°.*SE 9 km\/h/, 'unexpired readings still survive reload');
  // The original observation (11:59) has expired, but the update (12:00) has not.
  const later = Date.parse('2026-11-24T17:59:30Z');
  const reopened = airPage({ wallTime: later, weatherCache });
  p.advance(later - wallTime); p.sky.units();
  for (const page of [reopened, p]) {
    assert.match(page.node('sky-weather').innerHTML, /16°/);
    assert.doesNotMatch(page.node('sky-weather').innerHTML, /Thunderstorm|9 km\/h|Clear sky|Calm/);
  }
});

test('updating surviving temperature does not clear the stale marker for expired weather', () => {
  const wallTime = Date.parse('2026-11-24T17:59:30Z');
  const weatherCache = { at: { cloud: wallTime - 6 * 3600e3, spd: wallTime - 6 * 3600e3, temp: wallTime - 30000 },
    v: { cloud: 100, spd: 9, temp: 16 } };
  const p = airPage({ wallTime, weatherCache });
  p.sky.weather({ temperature: 17 });
  assert.match(p.node('sky-weather').innerHTML, /17°/);
  assert.match(p.node('sky-cap').title, /Last known/);
  const reloaded = airPage({ wallTime, weatherCache: p.writes.at(-1)[1] });
  reloaded.sky.weather({ temperature: 18 });
  assert.match(reloaded.node('sky-cap').title, /Last known/, 'another reload must not forget the expired fields');
  reloaded.sky.weather({ temperature: 18, cloud_cover: 0, wind_speed: 0 });
  assert.equal(reloaded.node('sky-cap').title, '');
  assert.match(reloaded.node('sky-weather').innerHTML, /Clear sky.*18°.*Calm/, 'observed zeroes are real readings');
});

test('an open tab expires storm visuals with the weather line', () => {
  const wallTime = Date.parse('2026-11-24T12:00:00Z');
  const p = airPage({ wallTime, weatherCache: { at: wallTime - 6 * 3600e3 + 1000,
    v: { dir: 135, spd: 9, cloud: 100, rain: 1, thunder: true, temp: 12 } } });
  const api = p.calls.find(c => c[0] === 'glass' && c[1] === 'create')[2];
  p.advance(16);
  assert.match(p.node('sky-weather').innerHTML, /Thunderstorm/);
  assert.equal(p.document.documentElement.classList.contains('wx-rain'), true);
  assert.ok(p.calls.some(c => c[0] === 'rain'), 'unexpired storm rain is painted');

  p.advance(1000, { runTimers: false }); // A weather frame may run before the expiry timeout.
  assert.doesNotMatch(p.node('sky-weather').innerHTML, /Thunderstorm/);
  assert.equal(p.document.documentElement.classList.contains('wx-rain'), false);
  assert.equal(p.document.documentElement.classList.contains('wx-cloud'), false);
  assert.equal(api.sky().rain, 0);
  assert.equal(api.sky().thunder, false, 'air renderers see the same expired weather');
  p.calls.length = 0; p.advance(1016);
  assert.equal(p.calls.filter(c => c[0] === 'rain').length, 0, 'expired rain stops drawing');

  p.preview({ minutes: 900, dir: 135, spd: 9, cloud: 100, rain: 1, thunder: true });
  assert.match(p.node('sky-weather').innerHTML, /Preview.*Thunderstorm/);
  assert.equal(p.document.documentElement.classList.contains('wx-rain'), true, 'weather Preview stays independent of cache expiry');

  const still = airPage({ reduced: true, wallTime, weatherCache: { at: wallTime - 6 * 3600e3 + 1000,
    v: { cloud: 100, rain: 1, thunder: true } } });
  still.advance(16);
  assert.equal(still.frames.size, 0, 'reduced motion has no running weather loop');
  assert.match(still.node('sky-weather').innerHTML, /Thunderstorm/);
  still.advance(1000);
  assert.doesNotMatch(still.node('sky-weather').innerHTML, /Thunderstorm/);
});

test('partial weather displays only segments supported by their own readings', () => {
  const p = airPage({ weather: { temperature: 0, wind_speed: 9, weather_code: 0 } });
  assert.match(p.node('sky-weather').innerHTML, /0°/);
  assert.doesNotMatch(p.node('sky-weather').innerHTML, /Clear sky|Calm|km\/h/);
  p.sky.weather({ wind_direction: 135, weather_code: 61 });
  assert.match(p.node('sky-weather').innerHTML, /Rain.*0°.*SE 9 km\/h/);
});

test('Random creates no interval or preview in quiet mode', () => {
  for (const options of [{ search: '?shot' }, { search: '?shot=dark' }, { appState: 'no_token' }, {}]) {
    const p = airPage({ ...options, random: true });
    const quiet = Object.keys(options).length > 0;
    const before = p.intervals.size;
    p.emit('fx-rand:click');
    assert.equal(p.intervals.size, before + (quiet ? 0 : 1));
    p.preview({ minutes: 900, dir: 250, spd: 12, cloud: 5, pm: 5, rain: 0 });
    assert.equal(p.node('fx-mode').textContent, quiet ? 'Live' : 'Preview');
  }
});

test('quiet sky controls cannot enable effects', () => {
  for (const options of [{ search: '?shot' }, { search: '?shot=dark' }, { appState: 'no_token' }]) {
    const p = airPage(options);
    for (const type of ['sky-quick:click', 'fx-on-sky:change', 'fx-time:input']) p.emit(type, { target: { checked: true, value: 900 } });
    assert.equal(Number(p.node('sky-grad').style.opacity), 0);
    assert.equal(p.frames.size, 0); assert.equal(p.calls.length, 0);
  }
});

test('saved wind names a legacy road only after a successful legacy context', () => {
  const p = airPage({ savedWeather: { dir: 135, spd: 12 }, road: { name: 'Legacy road', toward: [100, 180] } });
  assert.doesNotMatch(p.node('sky-weather').innerHTML, /Legacy road/);
  p.sky.air({ ctx: null }); assert.doesNotMatch(p.node('sky-weather').innerHTML, /Legacy road/);
  p.sky.air({ ctx: {} }); assert.match(p.node('sky-weather').innerHTML, /Legacy road/);
  p.sky.air({ ctx: { roads: [] } }); assert.doesNotMatch(p.node('sky-weather').innerHTML, /Legacy road/);
});

test('the first sky snapshot waits for context and analysis but not charts or Nearby', async () => {
  const src = readFileSync(new URL('static/app.js', root), 'utf8');
  const body = src.slice(src.indexOf('const refreshOnce = singleFlight('), src.indexOf('\nfunction refresh(opts)'));
  for (const [chartFails, cachedCurrent, cachedContext] of [[false, false, false], [true, false, false], [false, true, false], [false, false, true]]) {
    let resolveContext, resolveRead, resolveCharts, rejectCharts, resolveNeighbors;
    const context = new Promise(r => { resolveContext = r; }), read = new Promise(r => { resolveRead = r; });
    const charts = new Promise((r, j) => { resolveCharts = r; rejectCharts = j; }), nearby = new Promise(r => { resolveNeighbors = r; });
    const elections = [], calls = [], summary = { items: [{ id: 'pm', action: { text: 'Shut windows' } }] };
    const env = { singleFlight: fn => fn, skyFirstRefresh: true, state: {}, SRC_ON: {}, NO_INDOOR: false, NO_OUTDOOR: false,
      getJSON: url => url === '/api/current' ? Promise.resolve(Object.assign([], { _offline: cachedCurrent })) : url === '/api/context' ? context : nearby,
      pickEntries: home.pickEntries, renderMonitors() {}, showBootRead() {}, renderNeighborhood() {}, renderContext() {}, contextStatus() {},
      loadRead: () => { calls.push('analysis'); return read; }, loadCharts: () => charts,
      syncSkyAir: opts => { if (opts?.initial) elections.push(opts); }, setBusy() {}, setPhase() {}, showUpdated() {},
      console: { error() {} }, REFRESH_TIMEOUT_MS: 30000, skyReadingsAvailable: false, forceNext: false, loadFailed: false, lastRefresh: 0 };
    const { refresh, available } = new Function(...Object.keys(env), body + '\nreturn {refresh: refreshOnce, available: () => skyReadingsAvailable};')(...Object.values(env));
    const pending = refresh();
    await new Promise(r => setImmediate(r)); assert.equal(calls.length, 0, 'analysis waits for context');
    resolveContext(cachedContext ? { _offline: true } : {}); await new Promise(r => setImmediate(r));
    assert.equal(calls.length, 1); assert.equal(elections.length, 0);
    assert.equal(available(), !cachedCurrent, 'cached readings cannot select live PM or damp');
    if (chartFails) {
      rejectCharts(new Error('chart unavailable')); await new Promise(r => setImmediate(r));
      assert.equal(elections.length, 0, 'chart failure cannot consume the pending fresh Summary');
      assert.deepEqual(env.state.ctx, {}, 'chart failure retains the valid context');
    }
    resolveRead({ freshSummary: summary }); await new Promise(r => setImmediate(r));
    assert.deepEqual(elections, [{ initial: true, freshSummary: cachedCurrent || cachedContext ? null : summary }], 'cached observations cannot elect a load pass, even with fresh analysis');
    resolveCharts(); resolveNeighbors([]); await pending;
    await refresh(); assert.equal(elections.length, 1, 'later refreshes never rearm the opportunity');
  }
});

test('loadRead explicitly distinguishes fresh, unchanged, cached, missing and failed Summary outcomes', async () => {
  const src = readFileSync(new URL('static/app.js', root), 'utf8');
  const body = src.slice(src.indexOf('async function loadRead('), src.indexOf('// ── Refresh'));
  const summary = { items: [{ id: 'pm', action: { text: 'Windows shut' } }] };
  for (const mode of ['fresh', 'unchanged', 'cached', 'missing', 'failed']) {
    const state = { read: { summary }, readAt: mode === 'cached' ? Date.now() : 0, readKey: 'key' };
    const env = { state, READ_MAX_AGE: 300000, readPayload: () => ({}), readKey: () => 'key',
      setBusy() {}, setPhase() {}, busyCount: 0, getJSON: async () => { if (mode === 'failed') throw Error('failed'); return mode === 'missing' ? {} : { summary }; },
      store: { set() {}, get: () => ({ read: { summary } }) }, revealNext: false, readSig: () => 'same', shownSig: mode === 'unchanged' ? 'same' : '',
      summaryPopOpen: () => false, SummaryCard: { renderSummary() {} }, $: () => ({ innerHTML: '' }), appNow: () => new Date(), SUMMARY_OPTS: () => ({}),
      redrawEstimate() {}, renderRead() {}, settle() {}, syncSkyAir() {}, navigator: { onLine: true }, console: { error() {} } };
    const fn = new Function(...Object.keys(env), body + '\nreturn loadRead;')(...Object.values(env));
    const outcome = await fn(false);
    assert.deepEqual(outcome, { freshSummary: mode === 'fresh' || mode === 'unchanged' ? summary : null }, mode);
  }
});

// Everything about the home comes from the server's config (window.APP), never from the page's own files.
test('no home is hardcoded in the page', async () => {
  const { readdir, readFile } = await import('node:fs/promises');
  const files = ['index.html', ...(await readdir(new URL('static/', root))).filter(f => f.endsWith('.js')).map(f => 'static/' + f)];
  const found = [];
  for (const f of files) {
    const text = await readFile(new URL(f, root), 'utf8');
    for (const s of ['Greenpoint', '40.73', '-73.95', 'America/New_York', '11222', 'Blowing toward']) if (text.includes(s)) found.push(`${f}: ${s}`);
  }
  assert.deepEqual(found, []);
});

// The road helpers app.js, sky.js and map.js share (static/home.js).
const require = createRequire(import.meta.url);
const home = require('../static/home.js');
const BQE = { name: 'BQE', kind: 'expressway', toward: [80, 200], rush: [[7, 10, 'morning'], [16, 20, 'evening']], distance_km: 1 };

// Catch guessed conditions, lost simultaneous kinds and copied household thresholds at the real helper boundary.
test('sky conditions use real readings and home limits', () => {
  const limits = { pm: 15, pm_high: 35, pollen: 3, rh_damp: 55 };
  const base = { pm: null, rh: null, ctx: null, limits, road: null };
  const mapped = { smoke: { checked: true, detected: true } };
  const cases = [
    ['missing inputs', {}, []],
    ['measured high PM without NOAA', { pm: 36 }, ['smoke']],
    ['PM at high limit', { pm: 35 }, ['particles']],
    ['rounded PM below high limit', { pm: 35.3 }, ['particles']],
    ['rounded PM above high limit', { pm: 35.6 }, ['smoke']],
    ['mapped smoke over PM limit', { pm: 16, ctx: mapped }, ['smoke']],
    ['mapped smoke at PM limit', { pm: 15, ctx: mapped }, []],
    ['mapped smoke with rounded PM at limit', { pm: 15.3, ctx: mapped }, []],
    ['mapped smoke without PM', { ctx: mapped }, []],
    ['no mapped smoke', { pm: 16, ctx: { smoke: { detected: false } } }, ['particles']],
    ['fine particles over the PM limit', { pm: 16 }, ['particles']],
    ['fine particles at the PM limit', { pm: 15 }, []],
    ['fine particles with rounded PM at the limit', { pm: 15.4 }, []],
    ['fine particles with rounded PM over the limit', { pm: 15.5 }, ['particles']],
    ['fine particles up to the high limit', { pm: 35 }, ['particles']],
    ['custom fine particles limit', { pm: 16, limits: { ...limits, pm: 20 } }, []],
    ['pollen at limit', { ctx: { pollen: [{ index: 2 }, { index: 3 }] } }, ['pollen']],
    ['pollen below limit', { ctx: { pollen: [{ index: 2 }] } }, []],
    ['pollen missing', { ctx: { pollen: [] } }, []],
    ['indoor damp', { rh: 56 }, ['damp']],
    ['RH at limit', { rh: 55 }, []],
    ['outdoor RH cannot substitute', { ctx: { weather: { humidity: 90 } } }, []],
    ['custom high PM limit', { pm: 36, limits: { ...limits, pm_high: 40 } }, ['particles']],
    ['custom smoke PM limit', { pm: 16, ctx: mapped, limits: { ...limits, pm: 20 } }, []],
    ['custom smoke PM limit, fine particles over it', { pm: 21, ctx: mapped, limits: { ...limits, pm: 20, pm_high: 40 } }, ['smoke']],
    ['custom pollen limit', { ctx: { pollen: [{ index: 3 }] }, limits: { ...limits, pollen: 4 } }, []],
    ['custom damp limit', { rh: 56, limits: { ...limits, rh_damp: 60 } }, []],
  ];
  for (const status of ['off', 'error', 'not_set_up']) {
    cases.push(
      [`smoke source ${status}`, { pm: 16, ctx: { ...mapped, status: { smoke: status } } }, ['particles']],
      [`high PM independent of NOAA ${status}`, { pm: 36, ctx: { ...mapped, status: { smoke: status } } }, ['smoke']],
      [`pollen source ${status}`, { ctx: { pollen: [{ index: 4 }], status: { pollen: status } } }, []],
    );
  }
  for (const value of [null, undefined, NaN, Infinity, -Infinity, '56']) {
    cases.push(
      [`invalid PM ${value}`, { pm: value, ctx: mapped }, []],
      [`invalid RH ${value}`, { rh: value }, []],
      [`invalid pollen ${value}`, { ctx: { pollen: [{ index: value }] } }, []],
    );
  }
  const reaching = { name: 'Test Expressway', short: 'Test Rd', side: 'E', reaching: true, strength: 'low' };
  cases.push(
    ['a reaching road takes fine PM as exhaust', { pm: 20, ctx: { roads: [reaching] } }, ['exhaust']],
    ['a road not reaching leaves fine particles', { pm: 20, ctx: { roads: [{ ...reaching, reaching: false }] } }, ['particles']],
    ['road source off leaves fine particles', { pm: 20, ctx: { roads: [reaching], status: { road: 'off' } } }, ['particles']],
    ['fine particles sit between exhaust and pollen', { pm: 20, rh: 56, ctx: { pollen: [{ index: 3 }] } }, ['particles', 'pollen', 'damp']],
  );
  for (const [name, input, want] of cases) {
    assert.deepEqual(home.skyConditions({ ...base, ...input }).map(c => c.kind), want, name);
  }
  assert.deepEqual(home.skyConditions({ ...base, pm: 20 }), [{ kind: 'particles', label: 'Fine particles', road: null }]);
  const road = { name: 'Test Expressway', short: 'Test Rd', side: 'E', reaching: true, strength: 'low' };
  assert.deepEqual(home.skyConditions({ ...base, pm: 36, rh: 56, ctx: { roads: [road], pollen: [{ index: 3 }] } }), [
    { kind: 'smoke', label: 'Smoke', road: null },
    { kind: 'exhaust', label: 'Test Rd exhaust', road },
    { kind: 'pollen', label: 'Pollen high', road: null },
    { kind: 'damp', label: 'Damp inside', road: null },
  ]);
});

test('an air preview adds its kind in the sky order, keeps one already there, and names the road the page knows', () => {
  const smoke = { kind: 'smoke', label: 'Smoke', road: null }, damp = { kind: 'damp', label: 'Damp inside', road: null };
  assert.deepEqual(home.previewConditions([smoke, damp], 'pollen').map(c => c.kind), ['smoke', 'pollen', 'damp']);
  assert.deepEqual(home.previewConditions([damp], 'smoke').map(c => c.kind), ['smoke', 'damp']);
  assert.equal(home.previewConditions([smoke], 'smoke')[0], smoke, 'a kind already there stays as the home has it');
  assert.deepEqual(home.previewConditions([smoke], 'nothing'), [smoke]);
  const roads = [{ name: 'Test Expressway', short: 'Test Rd', side: 'E', reaching: false, strength: 'low' }];
  const viaContext = home.previewConditions([], 'exhaust', { ctx: { roads }, road: { name: 'Legacy' } })[0];
  assert.equal(viaContext.label, 'Test Rd exhaust'); assert.equal(viaContext.road.side, 'E'); assert.equal(viaContext.road.reaching, true);
  assert.equal(home.previewConditions([], 'exhaust', { ctx: null, road: { name: 'Legacy' } })[0].label, 'Legacy exhaust');
  assert.equal(home.previewConditions([], 'exhaust')[0].label, 'Road exhaust');
});

// Catch legacy exhaust resurrecting when the authoritative roads list says none reaches the home.
test('sky roads prefer the new contract', () => {
  const limits = { pm: 15, pm_high: 35, pollen: 3, rh_damp: 55 };
  const road = { name: 'Test Expressway', short: 'Test Rd', side: 'NW', reaching: true, strength: 'low' };
  const legacy = { road: { toward: true }, bqe: { traffic: 'PEAK (morning rush)' }, weather: { wind_direction: 0 } };
  const base = { pm: null, rh: null, ctx: legacy, limits, road: BQE };
  const cases = [
    ['new low road', { roads: [{ ...road, why: 'Wind reaches the home', until: null }] }, [{ kind: 'exhaust', label: 'Test Rd exhaust', road }]],
    ['new road needs no hand-set road', { roads: [road] }, [{ kind: 'exhaust', label: 'Test Rd exhaust', road }], null],
    ['empty new roads', { roads: [] }, []],
    ['first road away', { roads: [{ ...road, reaching: false }, road] }, []],
    ['reaching must be true', { roads: [{ ...road, reaching: 'true' }] }, []],
    ['null new roads', { roads: null }, []],
    ['undefined new roads is still present', { roads: undefined }, []],
    ['legacy peak', {}, [{ kind: 'exhaust', label: 'BQE exhaust', road: { name: 'BQE', short: 'BQE', side: null, reaching: true, strength: 'peak' } }]],
    ['legacy high', { bqe: { traffic: 'HIGH' } }, [{ kind: 'exhaust', label: 'BQE exhaust', road: { name: 'BQE', short: 'BQE', side: null, reaching: true, strength: 'high' } }]],
    ['legacy low', { bqe: { traffic: 'LOW' } }, []],
    ['legacy not toward', { road: { toward: false } }, []],
    ['legacy missing road context', { road: null }, []],
    ['legacy missing traffic', { bqe: null }, []],
    ['legacy non-string traffic', { bqe: { traffic: 7 } }, []],
    ['legacy missing hand-set road', {}, [], null],
  ];
  for (const status of ['off', 'error']) {
    cases.push([`new road source ${status}`, { roads: [road], status: { road: status } }, []]);
    cases.push([`legacy road source ${status}`, { status: { road: status } }, []]);
  }
  for (const [name, ctx, want, handSet = BQE] of cases) {
    assert.deepEqual(home.skyConditions({ ...base, ctx: { ...legacy, ...ctx }, road: handSet }), want, name);
  }
});

// Catch a later action or an arbitrary active kind stealing the Summary's first action's pass.
test('sky leader follows the first Summary action', () => {
  const conditions = home.skyConditions({ pm: 36, rh: 56, limits: { pm: 15, pm_high: 35, pollen: 3, rh_damp: 55 }, road: null,
    ctx: { roads: [{ name: 'Test Road', short: 'Test', side: 'N', reaching: true, strength: 'low' }], pollen: [{ index: 3 }] } });
  const action = { text: 'Take the current action' };
  const pm = { id: 'pm', action }, pollen = { id: 'pollen', action };
  const particles = home.skyConditions({ pm: 20, rh: null, limits: { pm: 15, pm_high: 35, pollen: 3, rh_damp: 55 }, road: null,
    ctx: { roads: [], pollen: [{ index: 3 }] } });
  const cases = [
    ['pollen first', [pollen, pm], conditions, 'pollen'],
    ['damp RH first', [{ id: 'rh', facts: { dry: false }, action }, pm], conditions, 'damp'],
    ['dry RH first', [{ id: 'rh', facts: { dry: true }, action }, pm], conditions, null],
    ['RH without damp facts', [{ id: 'rh', action }, pm], conditions, null],
    ['PM first prefers smoke', [pm, pollen], conditions, 'smoke'],
    ['PM first without smoke', [pm, pollen], conditions.filter(c => c.kind !== 'smoke'), 'exhaust'],
    ['traffic first', [{ id: 'traffic', action }, pm], conditions, 'exhaust'],
    ['CO2 first', [{ id: 'co2', action }, pm], conditions, null],
    ['unmatched first kind', [pollen, pm], conditions.filter(c => c.kind !== 'pollen'), null],
    ['PM without smoke or exhaust', [pm, pollen], conditions.filter(c => c.kind === 'pollen'), null],
    ['PM first picks fine particles', [pm, pollen], particles, 'particles'],
    ['pollen first over fine particles', [pollen, pm], particles, 'pollen'],
    ['traffic first never picks fine particles', [{ id: 'traffic', action }, pm], particles, null],
    ['PM first prefers exhaust over fine particles', [pm], [...conditions.filter(c => c.kind === 'exhaust'), ...particles], 'exhaust'],
    ['PM first prefers smoke over fine particles', [pm], [...conditions.filter(c => c.kind === 'smoke'), ...particles], 'smoke'],
    ['no active conditions', [pm], [], null],
    ['first action missing', [{ id: 'pm' }, pollen], conditions, null],
    ['first action null', [{ id: 'pm', action: null }, pollen], conditions, null],
    ['first action without text', [{ id: 'pm', action: { text: null, when: null, when_end: null } }, pollen], conditions, null],
    ['first action blank', [{ id: 'pollen', action: { text: '  ' } }, pm], conditions, null],
    ['no Summary items', [], conditions, null],
  ];
  for (const [name, items, active, want] of cases) assert.equal(home.skyLeader(active, { items }), want, name);
  for (const summary of [null, undefined, {}, { items: null }]) assert.equal(home.skyLeader(conditions, summary), null);
});

test('bearingIn: a plain arc and one that crosses north', () => {
  assert.equal(home.bearingIn(100, [80, 200]), true);
  assert.equal(home.bearingIn(10, [80, 200]), false);
  assert.equal(home.bearingIn(250, [80, 200]), false);
  assert.equal(home.bearingIn(0, [300, 20]), true);
  assert.equal(home.bearingIn(350, [300, 20]), true);
  assert.equal(home.bearingIn(180, [300, 20]), false);
  assert.equal(home.bearingIn(360, [300, 20]), true);
});

test('compass words for an arc, clockwise', () => {
  assert.deepEqual(home.compassIn([80, 200]), ['east', 'south-east', 'south']);
  assert.deepEqual(home.compassAway([80, 200]), ['west', 'north']);
  assert.deepEqual(home.compassIn([300, 20]), ['north-west', 'north']);
  assert.deepEqual(home.compassIn([25, 40]), ['north-east']);   // narrower than the points: the nearest one
});

test('rush hours read as today', () => {
  assert.equal(home.hourRange(7, 10), '7 to 10 AM');
  assert.equal(home.hourRange(16, 20, '–'), '4–8 PM');
  assert.equal(home.hourRange(11, 13), '11 AM to 1 PM');
  assert.equal(home.hourRange(0, 6), '12 to 6 AM');
});

test("The reference home's road reads exactly as the page did before the config", () => {
  assert.equal(home.roadClockText(BQE), 'Traffic is only the clock. Low most of the day, peak from 7 to 10 AM and from 4 to 8 PM, and a step higher in the hours next to those.');
  assert.equal(home.roadWindText(BQE), 'Wind is Open-Meteo’s direction. The BQE expressway runs about 1 km east to south of here, so wind from the east, south-east or south blows its exhaust toward the block, and wind from the west or north blows it away.');
  assert.equal(home.roadWhyText(BQE), 'The BQE expressway runs about 1 km east to south of here, so it matters on east, south-east and south winds.');
});

test('a road without a known distance or rush still reads', () => {
  const r = { name: 'Cross Bronx', toward: [300, 20], rush: [], distance_km: null };
  assert.equal(home.roadWhyText(r), 'The Cross Bronx highway runs north-west to north of here, so it matters on north-west and north winds.');
  assert.equal(home.roadClockText(r), 'Traffic is only the clock: no rush hours are set, so it reads low all day.');
});

test('the summary is "For:" the profiles that are on, in the same words', () => {
  assert.equal(home.profilesFor({ toddler: true, sinus: true, runner: true }), 'toddler, sinus & allergy, running');
  assert.equal(home.profilesFor({ toddler: true, sinus: true, runner: false }), 'toddler, sinus & allergy');
  assert.equal(home.profilesFor({ toddler: false, sinus: false, runner: false }), '');
});

test('a region names the place in the smoke and pollen sentences; without one they read neutrally', () => {
  assert.equal(home.smokeText('New York'), 'Wildfire smoke over New York. When it is detected, outdoor PM2.5 is the risk.');
  assert.equal(home.smokeMapText('New York'), 'NOAA Hazard Mapping: wildfire smoke over New York.');
  assert.equal(home.pollenBarsTitle('New York'), "Bars: the usual New York season, peak in amber. Right: today's level from Google.");
  for (const r of [null, undefined, '']) {
    const all = [home.smokeText(r), home.smokeMapText(r), home.pollenBarsTitle(r)].join(' ');
    assert.doesNotMatch(all, /New York|undefined|null/);
    assert.match(all, /over this area/);
    assert.match(home.pollenBarsTitle(r), /^Bars: the usual season, peak/);
  }
});

test('a source that is off or not set up says so in plain words, with one line on what it adds', () => {
  assert.deepEqual(home.sourceOff('pollen', 'not_set_up'), { word: 'Off', line: 'Needs a Google Pollen key', calendar: true });
  assert.deepEqual(home.sourceOff('pollen', 'off'), { word: 'Off', line: 'Adds daily pollen levels from Google.' });
  assert.deepEqual(home.sourceOff('smoke', 'off'), { word: 'Off', line: 'Adds wildfire smoke from NOAA\u2019s satellite map.' });
  assert.deepEqual(home.sourceOff('nearby', 'off'), { word: 'Off', line: 'Adds readings from public AirGradient monitors nearby.' });
  assert.deepEqual(home.sourceOff('road', 'off'), { word: 'Off', line: 'No road set up.' });
  assert.deepEqual(home.sourceOff('smoke', 'needs_place'), { word: 'Off', line: 'Add your place to see wildfire smoke.', calendar: false });   // a home without a place yet
  assert.equal(home.sourceOff('road', 'needs_place'), null);
  for (const s of ['ok', 'error', undefined]) assert.equal(home.sourceOff('pollen', s), null);
});

test('the sources the page lists leave out what the config turned off', () => {
  const all = { smoke: true, pollen: true, nearby: true };
  assert.deepEqual(home.sourcesOn(all, true), ['one', 'openair', 'wx', 'pollen', 'smoke', 'bqe']);
  assert.deepEqual(home.sourcesOn({ ...all, smoke: false, pollen: false }, false), ['one', 'openair', 'wx']);
});

test('the Sources label names only the sources that are on', () => {
  assert.equal(home.sourcesLabel({ smoke: true, pollen: true }, true), 'Sources: your 2 monitors, weather, pollen, smoke and traffic. Open to see them.');
  assert.equal(home.sourcesLabel({ smoke: false, pollen: true }, false), 'Sources: your 2 monitors, weather and pollen. Open to see them.');
  assert.equal(home.sourcesLabel({ smoke: false, pollen: false }, false), 'Sources: your 2 monitors and weather. Open to see them.');
});

test('home menu has no editing controls (configuration is done in the config file, not the page)', () => {
  const html = readFileSync(new URL('index.html', root), 'utf8');
  const js = readFileSync(new URL('static/app.js', root), 'utf8');
  const css = readFileSync(new URL('static/app.css', root), 'utf8');
  for (const [name, text] of [['index.html', html], ['app.js', js]]) {
    for (const bad of ['Add a source', 'Disconnect', '>Edit<', 'class="x"', 'class="add"', 'class="edit"', 'pr-add', 'Coming later']) {
      assert.ok(!text.includes(bad), `${name} still contains ${bad}`);
    }
  }
  assert.doesNotMatch(css, /\.edit\b|button\.x\b|\.pr-add\b|button\.add\b/);
});

// AirGradient's public data is CC-BY-SA 4.0: the neighborhood's footer says so; each owner is named in their open row (Task 6d).
const escHtml = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

test('the neighborhood credits AirGradient and CC-BY-SA 4.0, with links, and lists no contributors', () => {
  const html = home.creditHtml(false);
  assert.equal(html, 'Data provided by <a href="https://www.airgradient.com" target="_blank" rel="noopener">AirGradient</a> · ' +
    'Licensed under <a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank" rel="noopener">CC-BY-SA 4.0</a>');
  assert.doesNotMatch(html, /Contributors/);
  const app = readFileSync(new URL('static/app.js', root), 'utf8');
  assert.doesNotMatch(app, /creditHtml\(others/);
});

test('in the demo the line says the monitors are made up and links nothing', () => {
  assert.equal(home.creditHtml(true), 'Made-up monitors for the demo');
});

test('the page has the credit line in the neighborhood section', () => {
  const html = readFileSync(new URL('index.html', root), 'utf8');
  assert.match(html, /id="nb-credit"/);
  assert.match(readFileSync(new URL('static/app.js', root), 'utf8'), /creditHtml\(/);
});

test('index.html makes no AirGradient claim in the static credit line', () => {
  const html = readFileSync(new URL('index.html', root), 'utf8');
  assert.match(html, /<p class="nb-credit" id="nb-credit"><\/p>/);
});

test("the Roads row names the roads followed and says when their exhaust is likely, not how much", () => {
  assert.equal(home.roadsFollowedText([{ name: 'the BQE' }]), 'Roads followed: the BQE.');
  assert.equal(home.roadsFollowedText([{ name: 'the BQE' }, { name: 'Kent Avenue' }]), 'Roads followed: the BQE and Kent Avenue.');
  assert.match(home.roadsWhenText(1), /^Its exhaust is likely to reach your home when the wind blows from the road toward it in the road’s busy hours, or in calm air before sunrise\.$/);
  assert.match(home.roadsWhenText(2), /^Their exhaust/);
  const html = readFileSync(new URL('index.html', root), 'utf8');
  assert.match(html, /<b>Roads<\/b><em>OpenStreetMap · traffic data<\/em>/);
  assert.match(html, /It shows when exhaust is likely, not how much\./);
  assert.doesNotMatch(html, /Hour × wind|Pressure is those two multiplied/);
});

test('the road label says what kind of road it is', async () => {
  assert.equal(home.roadLabel({ name: 'BQE', kind: 'expressway' }), 'BQE expressway');
  assert.equal(home.roadLabel({ name: 'I-95' }), 'I-95 highway');
  assert.equal(home.aroundLine({ name: 'the BQE', kind: 'highway', band: 'within 500 m', side: 'E', aadt: 119116, trucks_pct: 11 }),
    'the BQE · highway · within 500 m, to the east · about 119,000 vehicles a day, 11% trucks');
  assert.equal(home.aroundLine({ name: 'the Staten Island Expressway', kind: 'highway', band: '500 m to 1 km', side: 'S' }),
    'the Staten Island Expressway · highway · 500 m to 1 km, to the south · counted only on calm mornings');
  assert.equal(home.aroundLine({ name: 'Road', several: true }), 'Road · on several sides');
  assert.equal(home.aroundLine({ name: 'Road', aadt: 2001 }), 'Road · about 2,000 vehicles a day');
  assert.equal(home.aroundStatus('none'), 'Traffic · no main road within 1 km');
  assert.equal(home.aroundStatus('not_built'), "Traffic · road data isn't built for your area yet");
  assert.equal(home.aroundStatus('failed'), "Traffic · couldn't get the road data · tried again when Home Air next starts");
  for (const status of ['ok', 'manual']) assert.equal(home.aroundStatus(status), null);

  // Run the real redraw/switch functions: replacing inputs must preserve the key,
  // including reordered answers, without taking focus back after the user leaves.
  const document = { activeElement: {} };
  let inputs = [], answer;
  const box = {
    contains: input => inputs.includes(input),
    querySelectorAll: () => inputs,
    set innerHTML(html) {
      if (this.contains(document.activeElement)) document.activeElement = {};
      inputs = [...html.matchAll(/<input[^>]+>/g)].map(([tag]) => ({
        dataset: { roadKey: tag.match(/data-road-key="([^"]*)"/)[1] },
        getAttribute: () => tag.match(/aria-disabled="([^"]*)"/)[1],
        listeners: {},
        addEventListener(type, listener) { this.listeners[type] = listener; },
        focus() { document.activeElement = this; }
      }));
    }
  };
  const nodes = { 'around-roads': box, 'around-you': {}, 'around-note': {} };
  const roads = [{ key: 'first', name: 'First', on: true }, { key: 'second', name: 'Second', on: true }];
  const app = { roads, roads_status: 'ok' };
  const source = readFileSync(new URL('static/app.js', root), 'utf8');
  const runtime = runInNewContext(source.slice(source.indexOf('const ROAD_SWITCH_NOTE'), source.indexOf('\napplyConfig();')) +
    '\n({ renderAround, switchRoad })', {
    APP: app, PUB: false, location: { hostname: 'localhost' }, document,
    $: id => nodes[id], state: { ctx: null }, ...home, esc: String,
    AbortController, setTimeout, clearTimeout, FETCH_TIMEOUT_MS: 1000,
    store: { set() {} }, refresh: async () => {},
    fetch: () => new Promise(resolve => { answer = resolve; })
  });
  runtime.renderAround();
  inputs[1].focus();
  runtime.renderAround();
  assert.equal(document.activeElement, inputs[1]);
  const saving = runtime.switchRoad(1, false);
  assert.equal(document.activeElement, inputs[1]);
  assert.equal(inputs[1].getAttribute('aria-disabled'), 'true');
  let prevented = false;
  inputs[1].listeners.click({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  answer({ ok: true, json: async () => ({ roads: [roads[1], roads[0]], roads_status: 'ok' }) });
  await saving;
  assert.equal(document.activeElement, inputs[0]);
  assert.equal(document.activeElement.dataset.roadKey, 'second');
  const outside = {};
  const leaving = runtime.switchRoad(0, true);
  document.activeElement = outside;
  answer({ ok: false, status: 409, json: async () => ({ error: 'Try again' }) });
  await leaving;
  runtime.renderAround();
  assert.equal(document.activeElement, outside);
  inputs[0].focus();
  const refused = runtime.switchRoad(0, true);
  answer({ ok: false, status: 403, json: async () => ({ error: 'Read only' }) });
  await refused;
  assert.equal(document.activeElement, inputs[0]);
  assert.equal(inputs[0].getAttribute('aria-disabled'), 'true');
});

test('the top of the comfortable humidity band comes from the config', () => {
  const app = readFileSync(new URL('static/app.js', root), 'utf8');
  assert.match(app, /RH_GOOD = APP\.limits\.rh_good/);
  assert.doesNotMatch(app, /RH_HIGH/);
});

test("the pollen warning follows the config's pollen limit", () => {
  assert.equal(home.pollenHigh(3, { pollen: 3 }), true);
  assert.equal(home.pollenHigh(4, { pollen: 5 }), false);   // a home that set pollen = 5
  assert.equal(home.pollenHigh(5, { pollen: 5 }), true);
  assert.equal(home.pollenHigh(null, { pollen: 3 }), false);
  const app = readFileSync(new URL('static/app.js', root), 'utf8');
  assert.doesNotMatch(app, /(top|i) >= 3\b/);
});

test('the sky and the PM2.5 words read the configured PM2.5 limits', () => {
  const sky = readFileSync(new URL('static/sky.js', root), 'utf8');
  assert.match(sky, /APP\.limits\.pm_high/);
  assert.doesNotMatch(sky, /pm > 35\b/);
  const app = readFileSync(new URL('static/app.js', root), 'utf8');
  assert.doesNotMatch(app, /35\.4/);
});

// The server rounds PM2.5 before comparing (summary.py: round(v) > limit), and the page shows it rounded,
// so the page's words and colours compare the rounded value too.
test('PM2.5 words compare the rounded reading, like the server', () => {
  const limits = { pm: 15, pm_high: 35 };
  assert.equal(home.pmLevel(15.3, limits).word, 'Good');
  assert.equal(home.pmLevel(15.6, limits).word, 'Moderate');
  assert.equal(home.pmLevel(35.2, limits).word, 'Moderate');
  assert.equal(home.pmLevel(35.6, limits).word, 'Unhealthy for sensitive groups');
  assert.equal(home.pmLevel(55.4, limits).word, 'Unhealthy for sensitive groups');
  assert.equal(home.pmLevel(55.6, limits).word, 'Unhealthy');
  assert.equal(home.pmLevel(null, limits).word, 'No reading');
  assert.equal(home.pmOver(15.3, 15), false);
  assert.equal(home.pmOver(15.6, 15), true);
  const app = readFileSync(new URL('static/app.js', root), 'utf8');
  assert.doesNotMatch(app, /pm02 > PM_FINE|v > PM_FINE|median > PM_FINE/);
  const sky = readFileSync(new URL('static/sky.js', root), 'utf8');
  assert.doesNotMatch(sky, /pm > /);
  const map = readFileSync(new URL('static/map.js', root), 'utf8');
  assert.match(map, /\['round', \['get', 'pm'\]\]/);
});

// The Home menu's rows: no (i) buttons; a source row explains itself on hover, focus or a tap, like the profile chips.
test('home menu rows carry their explanation as a row tooltip, not an (i) button', () => {
  const html = readFileSync(new URL('index.html', root), 'utf8');
  const css = readFileSync(new URL('static/app.css', root), 'utf8');
  const lists = [...html.matchAll(/<ul class="plist">([\s\S]*?)<\/ul>/g)].map(m => m[1]);
  assert.equal(lists.length, 2);
  const rows = lists.flatMap(l => l.split(/(?=<li class="pr)/).filter(r => r.startsWith('<li')));
  assert.equal(rows.length, 7);
  for (const row of rows) assert.doesNotMatch(row, /class="q"|class="bubble"|pr-a/);
  const monitors = lists[0].split(/(?=<li class="pr)/).filter(r => r.startsWith('<li'));
  for (const row of monitors) assert.doesNotMatch(row, /tabindex|pr-tip/);   // nothing to explain, no empty tooltip
  const sources = lists[1].split(/(?=<li class="pr)/).filter(r => r.startsWith('<li'));
  assert.equal(sources.length, 5);
  for (const row of sources) {
    const id = (row.match(/^<li class="pr has-tip"[^>]* tabindex="0" aria-describedby="(tip-\w+)"/) || [])[1];
    assert.ok(id, `row not focusable or not described: ${row.slice(0, 80)}`);
    assert.match(row, new RegExp(`<span class="pr-tip" id="${id}" role="tooltip"`));
  }
  assert.match(css, /\.pr\.has-tip:hover \.pr-tip, \.pr\.has-tip:focus \.pr-tip \{ display: block; \}/);
  assert.doesNotMatch(css, /\.pr-a\b/);
});

// ── A public monitor's page (the demo's ?monitor=<id>): what the page draws when the view has no indoor monitor ──
const NOW = new Date('2026-11-09T12:00:00Z');
const FRESH = { timestamp: '2026-11-09T11:58:00Z' };

test('an indoor monitor the view does not have is disabled: tone off, Not set up, never Offline', () => {
  assert.deepEqual(home.freshness(null, NOW, true), { tone: 'off', label: 'Not set up' });
  assert.deepEqual(home.freshness(FRESH, NOW, true), { tone: 'off', label: 'Not set up' });
  assert.equal(home.freshness(null, NOW).label, 'Offline');   // a home's indoor monitor that stopped: still red
  const pill = home.monitorPill({ indoor: null, outdoor: 'Roof' }, null, FRESH, NOW, { one: '<svg id="one"/>', openair: '<svg id="oa"/>' });
  const [indoor, outdoor] = pill.split(/(?=<span class="vhw)/);
  assert.match(indoor, /^<span class="vhw off" data-tip="Indoor · AirGradient ONE: Not set up"><svg id="one"\/><i class="off"><\/i><\/span>$/);
  assert.match(outdoor, /class="vhw" data-tip="Outdoor · AirGradient Open Air: Online"/);
  assert.doesNotMatch(pill, /Offline|class="bad"/);
  const home2 = home.monitorPill({ indoor: 'Living room', outdoor: 'Roof' }, null, FRESH, NOW, {});
  assert.match(home2, /class="vhw" data-tip="Indoor · AirGradient ONE: Offline"><i class="bad">/);
});

test("a public monitor's Home menu shows the demo's three profiles, the toddler greyed with its reason, as an outdoor-only home's", () => {
  assert.equal(home.profileOff('toddler', false), 'Needs an indoor monitor');
  assert.equal(home.profileOff('sinus', false), null);
  assert.equal(home.profileOff('runner', false), null);
  assert.equal(home.profileOff('toddler', true), null);   // a home with an indoor monitor: nothing greyed
  const app = readFileSync(new URL('static/app.js', root), 'utf8');
  const cfg = app.slice(app.indexOf('function applyConfig'), app.indexOf('applyConfig();'));
  // the greying runs on every page, a public monitor's included (it used to skip PUB, when a monitor showed the runner only)
  assert.match(cfg, /\n  document\.querySelectorAll\('\.view\.profiles \[data-profile\]'\)\.forEach\(el => \{ const why = profileOff\(el\.dataset\.profile, place\.indoor !== null\);/);
  assert.doesNotMatch(cfg, /if \(!PUB\) document\.querySelectorAll\('\.view\.profiles/);
});

test('the menu counts only the monitors the view has', () => {
  assert.deepEqual(home.monitorsLive([{ tone: 'off' }, { tone: 'good' }]), { text: '1 monitor live', tone: 'good' });
  assert.deepEqual(home.monitorsLive([{ tone: 'good' }, { tone: 'good' }]), { text: '2 monitors live', tone: 'good' });
  assert.deepEqual(home.monitorsLive([{ tone: 'bad' }, { tone: 'good' }]), { text: '1 of 2 monitors live', tone: 'warn' });
  assert.deepEqual(home.monitorsLive([{ tone: 'bad' }, { tone: 'warn' }]), { text: '0 of 2 monitors live', tone: 'bad' });
  assert.deepEqual(home.monitorsLive([{ tone: 'off' }, { tone: 'bad' }]), { text: '1 monitor, not live', tone: 'bad' });
  assert.deepEqual(home.sourcesOn({ pollen: false, smoke: true }, false, false), ['openair', 'wx', 'smoke']);
});

test('pollen without a key (a public monitor, or a home with no key) is Off, keeps its calendar, and never names the config', () => {
  for (const o of [home.sourceOff('pollen', 'not_set_up'), home.sourceOff('pollen', 'off', true)]) {
    assert.deepEqual(o, { word: 'Off', line: 'Needs a Google Pollen key', calendar: true });
  }
  assert.deepEqual(home.sourceOff('pollen', 'off'), { word: 'Off', line: 'Adds daily pollen levels from Google.' });   // a home that turned it off
  const smoke = home.sourceOff('smoke', 'off', true);   // a public monitor outside North America
  assert.equal(smoke.word, 'Off');
  assert.doesNotMatch(smoke.line, /config/i);
  // the Off card keeps the season calendar: drawn with no level for today, "—" for each type
  const SEASONS = [{ name: 'Tree', start: [3, 1], end: [5, 31], peak: [[4, 1], [4, 30]] },
    { name: 'Grass', start: [5, 1], end: [7, 31], peak: [[6, 1], [6, 30]] }];
  const cal = home.pollenSeasonHTML(SEASONS, [], new Date(2026, 3, 15, 9), { pollen: 3 }, null, escHtml);
  assert.match(cal, /class="ps-grid"/);
  assert.equal((cal.match(/<\/div>—<\/div>/g) || []).length, 2);   // no level from Google
  assert.match(cal, /<b>Now: tree season<\/b>, at its peak/);
  assert.equal(home.pollenSeasonHTML([], [], new Date(2026, 3, 15), { pollen: 3 }, null, escHtml), '');   // a place without seasons: no calendar
  assert.match(home.pollenSeasonHTML(SEASONS, [{ name: 'Tree', index: 4 }], new Date(2026, 3, 15, 9), { pollen: 3 }, null, escHtml), /<b class="warn">High<\/b>/);
});

test('the page never mentions the config', () => {
  const html = readFileSync(new URL('index.html', root), 'utf8').replace(/<!--[\s\S]*?-->/g, '').replace(/<script>[\s\S]*?<\/script>/g, '')
    .replace(/<article class="card no-token-card"[\s\S]*?<\/article>/, '');   // the setup state ("Config loaded, add your token"): for whoever is setting it up, decided in the spec
  assert.doesNotMatch(html, /config/i);
  for (const id of ['pollen', 'smoke', 'nearby', 'road']) for (const st of ['off', 'not_set_up']) for (const pub of [false, true]) {
    const o = home.sourceOff(id, st, pub);
    if (o) assert.doesNotMatch(o.line, /config/i, `${id} ${st} ${pub}`);
  }
  const strings = s => (s.replace(/\/\/.*$/gm, '').match(/'[^'\n]*'|`[^`]*`|"[^"\n]*"/g) || []).join('\n');
  for (const f of ['static/app.js', 'static/home.js', 'static/picker.js', 'static/summary.js']) assert.doesNotMatch(strings(readFileSync(new URL(f, root), 'utf8')), /\bconfig\b/i, f);
});

test('a public monitor has no road: its card reads Off with a plain line', () => {
  assert.deepEqual(home.sourceOff('road', 'off', true), { word: 'Off', line: 'Not available for public monitors yet.' });
});

test("a public monitor's card credits its owner, linked when there's a link, escaped", () => {
  const c = home.monitorCredit({ contributor: 'Dana <b>K</b>', url: 'www.example.org/roof' }, escHtml);
  assert.equal(c, 'Shared by <a href="https://www.example.org/roof" target="_blank" rel="noopener"><b>Dana &lt;b&gt;K&lt;/b&gt;</b></a> on ' +
    `<a href="https://www.airgradient.com/map/" target="_blank" rel="noopener">AirGradient's public map</a> · CC BY-SA`);
  assert.match(home.monitorCredit({ contributor: 'Ann', url: 'javascript:alert(1)' }, escHtml), /^Shared by <b>Ann<\/b> on /);
  assert.match(home.monitorCredit({ contributor: null, url: null }, escHtml), /^Shared on <a /);
});

test('the indoor card on a public monitor is the same card, empty and marked as missing', () => {
  const src = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
  assert.match(src, /monitorCard\(state\.outdoor, false\) \+ monitorCard\(null, true, true\)/);
  assert.match(src, /mc-ghost/);
});

test("a public monitor with no history: one line, not three empty charts", () => {
  const html = home.noHistoryHTML();
  assert.match(html, /<b>No past readings for this monitor<\/b>/);
  assert.match(html, /<span>AirGradient's map has none for it right now\. Today's forecast is in the summary\.<\/span>/);
  assert.match(readFileSync(new URL('index.html', root), 'utf8'), /id="hist-none"[^>]* hidden/);
});

test('the neighborhood calls the shown monitor This on a public page, You at home', () => {
  assert.deepEqual(home.selfWords(true), { row: 'This', map: 'This monitor', legend: 'This monitor', block: 'this monitor', near: 'this monitor' });
  assert.deepEqual(home.selfWords(false), { row: 'You', map: 'You', legend: 'Your monitor', block: 'your block', near: 'home' });
  const f = { esc: escHtml, pm: v => (v >= 10 ? String(Math.round(v)) : v.toFixed(1)), num: v => v.toFixed(1) };   // app.js pmNum, num
  const others = [{ pm02: 5, distance_km: 1, locationName: 'A' }, { pm02: 6, distance_km: 2, locationName: 'B' }, { pm02: 7, distance_km: 3, locationName: 'C' }];
  const lead = home.nbLead({ pm02: 40 }, others, 15, home.selfWords(true), f);
  assert.match(lead, /This monitor reads well above the area.*a source close to this monitor, such as construction.*road exhaust barely changes PM2\.5/);
  const area = [{ pm02: 30, distance_km: 1, locationName: 'A' }, { pm02: 31, distance_km: 2, locationName: 'B' }];
  assert.match(home.nbLead(null, area, 15, home.selfWords(true), f), /not a source near this monitor\./);
  assert.match(home.nbLead(null, area, 15, home.selfWords(false), f), /not a source near home\./);
  assert.doesNotMatch(lead, /Your block|your block/);
  assert.match(home.nbLead({ pm02: 40 }, others, 15, home.selfWords(false), f), /Your block reads well above the area/);
  const one = home.nbLead({ pm02: 5 }, [{ pm02: 30, distance_km: 1.1, locationName: 'X' }, ...others], 15, home.selfWords(true), f);
  assert.match(one, /the nearest is X, 1\.1 km away\. This monitor is still clean\./);
  assert.match(readFileSync(new URL('static/map.js', root), 'utf8'), /selfWords\(/);
});

test('the model past hours are a dotted line over a pale fill, labeled as the model', () => {
  const summary = require('../static/summary.js');
  const col = { muted: '#777', card: '#fff', fill: '#7f93a6' };
  const model = summary.pmPastSvg('model', 'M0 50 L100 30', 'M0 50 L100 30 L 100 96 L 0 96 Z', { x: 2, y: 22 }, col);
  assert.match(model, />PM2\.5 outside · model<\/text>/);
  assert.match(model, /stroke-dasharray="2 3"/);
  assert.match(model, /fill="#7f93a6" fill-opacity="\.14"/);
  const measured = summary.pmPastSvg(undefined, 'M0 50 L100 30', 'M0 50 L100 30 L 100 96 L 0 96 Z', { x: 2, y: 22 }, col);
  assert.equal(measured, '<path d="M0 50 L100 30 L 100 96 L 0 96 Z" fill="url(#sx-fp)"/>');
});

test("the open menu's indoor row on a public monitor's page: greyed (off), Not set up, no reading time", () => {
  const r = home.monitorRow(null, NOW, true, 'America/New_York');
  assert.deepEqual(r, { tone: 'off', text: 'Not set up', title: 'Not set up', off: true });
  const live = home.monitorRow(FRESH, NOW, false, 'America/New_York');
  assert.equal(live.off, false); assert.equal(live.tone, 'good'); assert.match(live.text, /^Live · cloud · (just now|\d+ min ago)$/);
  assert.match(home.monitorRow({ ...FRESH, via: 'local' }, NOW, false, 'UTC').text, /^Live · on your Wi-Fi · (just now|\d+ min ago)$/);
  const stale = home.monitorRow({ ...FRESH, via: 'local', stale: true, fix: 'home_server' }, NOW, false, 'UTC');
  assert.equal(stale.text, 'Not answering · 2 min ago'); assert.match(stale.title, /Run \.\/homeair home-server again on your computer$/);
  assert.match(live.title, /^Last reading Nov 9, 6:58\sAM$/);
  assert.deepEqual(home.monitorRow(null, NOW, false, 'UTC'), { tone: 'bad', text: 'Offline', title: 'No reading', off: false });
});

test('the neighborhood row for the shown monitor reads This on a public page, You at home', () => {
  const f = { esc: escHtml, pm: v => String(v), num: v => v.toFixed(1), temp: v => `${v}°C` };
  const scale = { limit: 15, barMax: 55, limPct: '27.3' };
  const n = { locationId: 5, locationName: 'Oak <b>st</b>', pm02: 22, distance_km: 0, atmp: 15, rhum: 92.6 };
  const row = home.nbRowHTML(n, true, home.selfWords(true), 'Roof & co', scale, f);
  assert.match(row, /^<tr class="you" data-id="5">/);
  // the id is AirGradient's number: anything else in it can't reach the markup (as in the open band's id)
  assert.match(home.nbRowHTML({ ...n, locationId: '5"><b>x' }, false, home.selfWords(false), 'x', scale, f), /^<tr class="" data-id="NaN">/);
  assert.match(row, /<td class="dist">This<\/td>/);
  assert.match(row, /<td class="loc" title="Oak &lt;b&gt;st&lt;\/b&gt;">Roof &amp; co<\/td>/);
  assert.match(row, /<td class="r pm warn">22<\/td><td class="r">15°C<\/td><td class="r">93%<\/td>/);
  assert.match(home.nbRowHTML(n, true, home.selfWords(false), 'Home', scale, f), /<td class="dist">You<\/td>/);
  assert.match(home.nbRowHTML({ ...n, atmp: null }, false, home.selfWords(true), 'x', scale, f), /<td class="dist">0\.0 km<\/td>.*<td class="r">—<\/td>/);
});

// ── Neighbors on the corrected PM2.5, like the home's own monitors (Task 6c) ──
const NB_F = { esc: escHtml, pm: v => (v >= 10 ? String(Math.round(v)) : v.toFixed(1)), num: v => v.toFixed(1), temp: v => `${v}°C` };

test("a neighbor's PM2.5 is the corrected one, the raw only without it", () => {
  assert.equal(home.nbPm({ pm02: 22, pm02_corrected: 12.4 }), 12.4);
  assert.equal(home.nbPm({ pm02: 22 }), 22);
  assert.equal(home.nbPm({ pm02: 22, pm02_corrected: null }), 22);
  assert.equal(home.nbPm({ pm02: 0, pm02_corrected: 0 }), 0);   // zero is a reading
  assert.equal(home.nbPm({}), null);
});

test('the neighborhood row shows the corrected PM2.5, its tone and its bar', () => {
  const scale = { limit: 15, barMax: 50, limPct: '30.0' };
  const row = home.nbRowHTML({ locationId: 1, locationName: 'A', pm02: 22, pm02_corrected: 10, distance_km: 1 }, false, home.selfWords(false), 'Home', scale, NB_F);
  assert.match(row, /<td class="r pm good">10<\/td>/);
  assert.match(row, /<i style="width:20\.0%" class="good">/);
  assert.doesNotMatch(row, />22</);
});

test('the lead counts the monitors over the limit on the corrected PM2.5', () => {
  const self = home.selfWords(false);
  // raw over the limit, corrected under it: nothing to say
  const clean = [{ pm02: 22, pm02_corrected: 12, distance_km: 1, locationName: 'A' }, { pm02: 4, pm02_corrected: 4.5, distance_km: 2, locationName: 'B' },
    { pm02: 5, pm02_corrected: 5, distance_km: 3, locationName: 'C' }];
  assert.equal(home.nbLead({ pm02: 6, pm02_corrected: 6 }, clean, 15, self, NB_F), '');
  // raw under, corrected over: counted, with the shown monitor still clean on its corrected reading
  const over = [{ pm02: 14, pm02_corrected: 18, distance_km: 1.2, locationName: 'Near' }, { pm02: 4, pm02_corrected: 4.5, distance_km: 2, locationName: 'B' },
    { pm02: 5, pm02_corrected: 5, distance_km: 3, locationName: 'C' }];
  assert.match(home.nbLead({ pm02: 20, pm02_corrected: 9 }, over, 15, self, NB_F),
    /One nearby monitor is over the 15 µg\/m³ limit<\/b>: the nearest is Near, 1\.2 km away\. Your block is still clean\./);
  // the median and "well above the area" read the corrected values too
  const area = [{ pm02: 40, pm02_corrected: 5, distance_km: 1, locationName: 'A' }, { pm02: 40, pm02_corrected: 6, distance_km: 2, locationName: 'B' }];
  assert.match(home.nbLead({ pm02: 10, pm02_corrected: 30 }, area, 15, self, NB_F), /Your block reads well above the area<\/b> \(30 against a typical 6\.0\)/);
});

test("each real neighbor row links to AirGradient's map in a new tab; made-up ones don't", () => {
  const scale = { limit: 15, barMax: 55, limPct: '27.3' };
  const url = 'https://www.airgradient.com/map?zoom=15&long=100.5&lat=13.9&meas=ug_m3';
  const row = home.nbRowHTML({ locationId: 3, locationName: 'Soi <b>7</b>', pm02: 5, distance_km: 2, map_url: url }, false, home.selfWords(false), 'Home', scale, NB_F);
  assert.match(row, /<a class="nb-ext" href="https:\/\/www\.airgradient\.com\/map\?zoom=15&amp;long=100\.5&amp;lat=13\.9&amp;meas=ug_m3" target="_blank" rel="noopener" title="Open on AirGradient's map" aria-label="Open Soi &lt;b&gt;7&lt;\/b&gt; on AirGradient's map">/);
  const fake = home.nbRowHTML({ locationId: 4, locationName: 'Demo monitor · X', pm02: 5, distance_km: 2 }, false, home.selfWords(false), 'Home', scale, NB_F);
  assert.doesNotMatch(fake, /<a /);
  const bad = home.nbRowHTML({ locationId: 5, locationName: 'Y', pm02: 5, distance_km: 2, map_url: 'javascript:alert(1)' }, false, home.selfWords(false), 'Home', scale, NB_F);
  assert.doesNotMatch(bad, /<a /);
});

test("the neighborhood header has no map button; the map reads the corrected PM2.5", () => {
  const html = readFileSync(new URL('index.html', root), 'utf8');
  assert.doesNotMatch(html, /Open the map/);
  assert.doesNotMatch(html, /data-app="map-area"/);
  const map = readFileSync(new URL('static/map.js', root), 'utf8');
  assert.doesNotMatch(map, /\.pm02\b/);
  assert.match(map, /nbPm\(/);
  // an unknown monitor's page ({id, error}) shows the default sample: the map calls the home's monitor "You"
  assert.match(map, /selfWords\(!!\(APP\.public && !APP\.public\.error\)\)/);
  const app = readFileSync(new URL('static/app.js', root), 'utf8');
  const nb = app.slice(app.indexOf('function renderNeighborhood'), app.indexOf('// ── Charts'));
  assert.doesNotMatch(nb, /\.pm02\b/);
});

// ── A neighbor row opens in place (Task 6d) ──
const OPEN_SCALE = { limit: 15, high: 35, barMax: 55, limPct: '27.3' };
const OPEN_F = { ...NB_F, when: ts => home.lastReadingText(ts, new Date('2026-11-09T12:45:00Z'), 'America/New_York') };
const REAL = { locationId: 5107, locationName: 'Oak <b>&</b> Elm', publicContributorName: ' Rosa <i>D</i> ', pm02: 30, pm02_corrected: 25,
  rhum: 90.6, atmp: 15.2, distance_km: 1.9, timestamp: '2026-11-09T12:38:00Z', map_url: 'https://www.airgradient.com/map?zoom=15&long=-73.9&lat=40.7&meas=ug_m3' };

test('a real neighbor row has a chevron toggle with aria-expanded; open, the band follows the row', () => {
  const closed = home.nbRowHTML(REAL, false, home.selfWords(false), 'Home', OPEN_SCALE, OPEN_F);
  assert.match(closed, /^<tr class="nb-can" data-id="5107">/);
  assert.match(closed, /<td class="nb-tg"><button type="button" class="nb-tgb" aria-expanded="false" aria-controls="nbd-5107" aria-label="Details: Oak &lt;b&gt;&amp;&lt;\/b&gt; Elm">/);
  assert.doesNotMatch(closed, /nb-detail/);
  const open = home.nbRowHTML(REAL, false, home.selfWords(false), 'Home', OPEN_SCALE, OPEN_F, true);
  assert.match(open, /^<tr class="nb-can nb-open" data-id="5107">/);
  assert.match(open, /aria-expanded="true"/);
  assert.match(open, /<\/tr><tr class="nb-detail" id="nbd-5107"><td colspan="8">/);   // the id the toggle's aria-controls names
  assert.equal(open.split('<tr').length - 1, 2);
});

test('the shown monitor and the demo samples\' made-up neighbors do not open', () => {
  const you = home.nbRowHTML(REAL, true, home.selfWords(false), 'Home', OPEN_SCALE, OPEN_F, true);
  assert.match(you, /^<tr class="you" data-id="5107">/);
  assert.match(you, /<td class="nb-tg"><\/td><\/tr>$/);
  const fake = home.nbRowHTML({ locationId: 4, locationName: 'Demo monitor · X', pm02: 5, distance_km: 2 }, false, home.selfWords(false), 'Home', OPEN_SCALE, OPEN_F, true);
  assert.match(fake, /^<tr class="" data-id="4">/);
  assert.doesNotMatch(fake, /<button|nb-detail|aria-expanded/);
});

test("the open band: PM2.5 now with its word, humidity, temperature, the owner, the last reading, and the map action only", () => {
  for (const self of [home.selfWords(false), home.selfWords(true)]) {   // a real home, and a public monitor's page on the demo
    const band = home.nbDetailHTML(REAL, OPEN_SCALE, OPEN_F);
    assert.equal(home.nbRowHTML(REAL, false, self, 'x', OPEN_SCALE, OPEN_F, true).endsWith(band), true);
    assert.match(band, /<dt>PM2\.5 now<\/dt><dd class="warn">25 <small>µg\/m³<\/small> · Moderate<\/dd>/);
    assert.match(band, /<dt>Humidity<\/dt><dd>91%<\/dd>/);
    assert.match(band, /<dt>Temperature<\/dt><dd>15\.2°C<\/dd>/);
    assert.match(band, /<p class="nbd-meta"><b class="nbd-band warn">Moderate<\/b><span class="nbd-sep"> · <\/span>Shared by Rosa &lt;i&gt;D&lt;\/i&gt; · last reading 7:38\sAM, 7 min ago<\/p>/);
    // one action: a quiet link to the monitor on AirGradient's map, in a new tab
    assert.equal(band.split('<a ').length - 1, 1);
    assert.match(band, /<a class="nbd-q" href="https:\/\/www\.airgradient\.com\/map\?zoom=15&amp;long=-73\.9&amp;lat=40\.7&amp;meas=ug_m3" target="_blank" rel="noopener"><svg[^>]*>.*<\/svg>Open on AirGradient's map<\/a>/);
    assert.doesNotMatch(band, /Switch|\?monitor=|<button/);
    assert.doesNotMatch(band, /<b>&|<i>D/);
  }
  // no owner: the reading time starts the line; no time: the owner alone
  assert.match(home.nbDetailHTML({ ...REAL, publicContributorName: null }, OPEN_SCALE, OPEN_F), /<span class="nbd-sep"> · <\/span>Last reading 7:38\sAM, 7 min ago<\/p>/);
  assert.match(home.nbDetailHTML({ ...REAL, timestamp: null }, OPEN_SCALE, OPEN_F), /<span class="nbd-sep"> · <\/span>Shared by Rosa &lt;i&gt;D&lt;\/i&gt;<\/p>/);
  assert.match(home.nbDetailHTML({ ...REAL, pm02_corrected: 9 }, OPEN_SCALE, OPEN_F), /<dd class="good">9\.0 <small>µg\/m³<\/small> · Good<\/dd>/);
});

test("a neighbor's last reading: its time in the home's timezone and how long ago", () => {
  const now = new Date('2026-11-09T12:45:00Z'), tz = 'America/New_York';
  assert.match(home.lastReadingText('2026-11-09T12:38:00Z', now, tz), /^7:38\sAM, 7 min ago$/);
  assert.match(home.lastReadingText('2026-11-09T12:44:40Z', now, tz), /^7:44\sAM, just now$/);
  assert.match(home.lastReadingText('2026-11-09T11:20:00Z', now, tz), /^6:20\sAM, 1 h 25 min ago$/);
  assert.match(home.lastReadingText('2026-11-09T11:45:00Z', now, tz), /^6:45\sAM, 1 h ago$/);
  assert.equal(home.lastReadingText(null, now, tz), '');
  assert.equal(home.lastReadingText('nope', now, tz), '');
});

test('the page opens one row at a time, in place, and the neighborhood links to no other monitor page', () => {
  const app = readFileSync(new URL('static/app.js', root), 'utf8');
  const nb = app.slice(app.indexOf('// ── Neighborhood'), app.indexOf('// ── Charts'));
  assert.match(nb, /state\.nbOpen = state\.nbOpen === id \? null : id/);
  assert.match(nb, /closest\('a'\)/);   // the row's ↗ opens the map, it doesn't toggle the row
  assert.match(nb, /nbRowHTML\([^)]*n\.locationId === state\.nbOpen\)/);
  const h = readFileSync(new URL('static/home.js', root), 'utf8'), rowsJs = h.slice(h.indexOf('const NB_COLS'), h.indexOf('function lastReadingText'));
  for (const text of [nb, rowsJs]) assert.doesNotMatch(text, /\?monitor=|Switch to this monitor/);
  const html = readFileSync(new URL('index.html', root), 'utf8');
  assert.match(html, /<th class="nbbar">.*<\/th><th class="nb-tg"><\/th><\/tr><\/thead><tbody id="nb">/);
});

test("on a phone the row's ↗ lives only in the open band, a full-width action at least 44 px tall", () => {
  const css = readFileSync(new URL('static/app.css', root), 'utf8');
  assert.match(css, /@media \(max-width: 700px\) \{\s*\.nbt td\.loc \.nb-ext \{ display: none; \}/);
  const q = css.match(/\.nbd-q \{[^}]*\}/)[0];
  assert.match(q, /width: 100%/);
  assert.match(q, /min-height: 44px/);
  assert.doesNotMatch(q, /background: var\(--accent\)/);   // quiet: no fill
});

test("at home, the You row reads the outdoor card's PM2.5 (the row, the lead and the map label alike)", () => {
  const you = { locationId: 7, pm02: 20, pm02_corrected: 9, rhum: 80 };
  assert.deepEqual(home.nbYou(you, { locationId: 7, pm02: 21, pm02_corrected: 11.2 }, 11.2), { ...you, pm02_corrected: 11.2 });
  assert.equal(home.nbYou(you, { locationId: 8 }, 3), you);   // the indoor monitor's row, or another: as it came
  assert.equal(home.nbYou(you, null, null), you);
  assert.equal(home.nbYou(undefined, { locationId: 7 }, 3), undefined);
  const app = readFileSync(new URL('static/app.js', root), 'utf8');
  assert.match(app, /const you = nbYou\(split\.you \|\| undefined, state\.outdoor, pick\(state\.outdoor, 'pm02_corrected', 'pm02'\)\)/);
});

test('neighborhood: the header says only Neighborhood, the findings sit in the card, the count is in Show more', () => {
  const app = readFileSync(new URL('static/app.js', root), 'utf8');
  const html = readFileSync(new URL('index.html', root), 'utf8');
  assert.match(html, /<h2\b[^>]*>Neighborhood<\/h2>/);
  assert.doesNotMatch(html + app, /nb-sub|nb-foot|nb-why/);
  // mock 114's order: the headline, the map and its key (the wind first), one line per road, the neighbors' line, the table
  const at = k => html.indexOf(k);
  assert.ok(at('id="nb-headline"') < at('id="nb-map"') && at('id="nb-map"') < at('id="nb-key-wind"') && at('id="nb-key-wind"') < at('lg-you')
    && at('lg-you') < at('id="nb-roads"') && at('id="nb-roads"') < at('id="nb-find"') && at('id="nb-find"') < at('class="nbt"'));
  assert.doesNotMatch(html, /nb-wind|roads-map-legend|data-app="road-why"/);
  assert.match(html, /PM2\.5<span class="nb-unit">µg\/m³<\/span>/);   // the unit moved from the header to the column
  // nearby off: the one-line note sits outside the hidden card
  assert.match(app, /\$?.*getElementById\('nb-off'\)/);
  assert.match(app, /\$\('nb-find'\)\.hidden = lead\.hidden/);
  assert.match(app, /nbMoreText\(extra, state\.nbCount\)/);
});

test('nbMoreText: the collapsed button says how many more and how many monitors in all', () => {
  assert.equal(home.nbMoreText(5, 12), 'Show 5 more of 12 within 15 km');
  assert.equal(home.nbMoreText(1, 9), 'Show 1 more of 9 within 15 km');
});


// ── The map's first view: the nearest neighbors in view, centered on the block (map.js create) ──
// map.js runs in a sandbox with a fake MapLibre that records the camera it's asked for.
function mapWith(others, width) {
  const vm = require('node:vm');
  const rec = { jumps: [], asks: [] };
  class LngLatBounds {
    constructor(a, b) { this.w = a[0]; this.s = a[1]; this.e = b[0]; this.n = b[1]; }
    extend([x, y]) { this.w = Math.min(this.w, x); this.e = Math.max(this.e, x); this.s = Math.min(this.s, y); this.n = Math.max(this.n, y); return this; }
  }
  class Map {
    constructor(o) { rec.opts = o; this.scrollZoom = { disable() {} }; }
    addControl() {} on() {} once() {}
    // a stand-in for MapLibre's fit: one zoom level out for each doubling of the box past 0.02°
    cameraForBounds(b, o) { rec.asks.push({ b, o }); return { zoom: Math.min(o.maxZoom, 12.7 - Math.log2(Math.max(b.e - b.w, b.n - b.s) / 0.02)) }; }
    jumpTo(c) { rec.jumps.push(c); }
  }
  const el = () => ({ set onload(f) { this._ok = f; }, get onload() { return this._ok; } });
  const box = { clientWidth: width, querySelectorAll: () => [], innerHTML: '' };
  const document = { documentElement: { dataset: {} }, body: { dataset: {} }, createElement: el, addEventListener() {},
    head: { appendChild: x => x.onload && x.onload() }, querySelectorAll: () => [] };
  const ctx = { window: {}, document, maplibregl: { Map, LngLatBounds, NavigationControl: class {}, AttributionControl: class {} },
    $: id => (id === 'nb-map' ? box : null), APP: { place: { lat: 13.75, lon: 100.48 }, road: null },
    apiUrl: x => x, selfWords: home.selfWords, bearingIn: home.bearingIn, nbPm: home.nbPm, Promise };
  vm.createContext(ctx);
  vm.runInContext(readFileSync(new URL('static/map.js', root), 'utf8') + ';this.NBMAP = NBMAP;', ctx);
  const you = { locationId: 1, longitude: 100.48, latitude: 13.75, pm02: 9 };
  ctx.NBMAP.update(you, others, null);
  return new Promise(r => setTimeout(() => r(rec), 0));
}
const east = km => ({ locationId: 100 + km, longitude: 100.48 + km / 108, latitude: 13.75, pm02: 5 });   // ~108 km per degree here

test("the map opens with the six nearest neighbors in view, centered on the block, never past the city's scale", async () => {
  const map = readFileSync(new URL('static/map.js', root), 'utf8');
  assert.match(map, /const FIT_NEAREST = 6, FIT_PAD = 40, FIT_MIN_ZOOM = 10\.5;/);
  // Bangkok-like: the nearest is 1 km away, the sixth 6 km, the seventh 14 km (left out)
  const rec = await mapWith([1, 2, 3, 4, 5, 6, 14].map(east), 1200);
  assert.equal(rec.opts.zoom, 12.7);
  assert.equal(rec.asks.length, 1);
  const { b, o } = rec.asks[0];
  assert.deepEqual({ ...o }, { padding: 40, maxZoom: 12.7 });
  assert.ok(Math.abs((b.w + b.e) / 2 - 100.48) < 1e-9 && Math.abs(b.e - (100.48 + 6 / 108)) < 1e-9);   // centered; the 6th, not the 7th
  assert.equal(rec.jumps.length, 1);
  assert.deepEqual([...rec.jumps[0].center], [100.48, 13.75]);
  assert.ok(rec.jumps[0].zoom < 12.7 && rec.jumps[0].zoom >= 10.5);
  // neighbors far out: no wider than FIT_MIN_ZOOM; all close by: the usual zoom (the phone's under 600 px)
  assert.equal((await mapWith([20, 30].map(east), 1200)).jumps[0].zoom, 10.5);
  assert.equal((await mapWith([0.2].map(east), 390)).jumps[0].zoom, 12.1);
  assert.equal((await mapWith([], 390)).jumps.length, 0);   // no neighbors: the map stays where it opened
});


test("a public monitor's empty week isn't kept, so a slow first answer from the map doesn't hide History", () => {
  const app = readFileSync(new URL('static/app.js', root), 'utf8');
  const week = app.slice(app.indexOf('function loadWeek'), app.indexOf('function chartsLoading'));
  assert.match(week, /if \(!ih \|\| !oh \|\| \(mapWeek\(\) && !oh\.length\)\) throw new Error\('history'\);\s*hist\.week = \{ ih, oh, to \}/);
  // the map's week: a public monitor's page, and the nearby public monitor an indoor-only home chose (not an estimate,
  // which has no locationId and so no week of its own)
  assert.match(app, /const mapWeek = \(\) => PUB \|\| \(NO_OUTDOOR && !noOutLine\(\)\);/);
});




// ── A home with one monitor: where an indoor-only home's outdoor PM2.5 comes from (/api/current's "source") ──
const PINNED = { kind: 'monitor', id: 900002, name: 'Cedar & 2nd Ave', distance_km: 1.1, area_count: 0, fallback: false };

test("an indoor-only home's outdoor source in words: the card's name line and the Home menu's", () => {
  assert.deepEqual(home.sourceLine(PINNED), { name: 'Nearby public monitor', sub: 'Cedar & 2nd Ave, 1.1 km', menu: 'Nearby public monitor, 1.1 km', estimate: false, down: null });
  assert.deepEqual(home.sourceLine({ kind: 'area', area_count: 4 }), { name: 'Estimate for your area', sub: 'from 4 public monitors', menu: 'Estimate for your area', estimate: true, down: null });
  assert.equal(home.sourceLine({ kind: 'area', area_count: 1 }).sub, 'from 1 public monitor');
  assert.equal(home.sourceLine({ kind: 'area', area_count: 0 }).sub, '');
  assert.deepEqual(home.sourceLine({ kind: 'model' }), { name: 'Estimate for your area (Open‑Meteo)', sub: '', menu: 'Estimate for your area', estimate: true, down: null });
  assert.equal(home.sourceLine(null), null);
  assert.equal(home.sourceLine({}), null);
});

test('a source distance has at most one decimal and no trailing .0, like the summary', () => {
  assert.equal(home.sourceLine({ ...PINNED, distance_km: 2 }).sub, 'Cedar & 2nd Ave, 2 km');
  assert.equal(home.sourceLine({ ...PINNED, distance_km: 2.0 }).menu, 'Nearby public monitor, 2 km');
  assert.equal(home.sourceLine({ ...PINNED, distance_km: 1.04 }).sub, 'Cedar & 2nd Ave, 1 km');
  assert.equal(home.sourceLine({ ...PINNED, distance_km: 0.46 }).sub, 'Cedar & 2nd Ave, 0.5 km');
});

test('a public monitor without a name or a distance still reads', () => {
  assert.equal(home.sourceLine({ ...PINNED, name: null }).sub, '1.1 km');
  assert.equal(home.sourceLine({ ...PINNED, name: '  ' }).sub, '1.1 km');
  assert.deepEqual(home.sourceLine({ kind: 'monitor', name: null, distance_km: null }), { name: 'Nearby public monitor', sub: '', menu: 'Nearby public monitor', estimate: false, down: null });
});

test("the chosen monitor isn't reporting: the card shows the estimate and says why; the menu keeps the chosen monitor", () => {
  const s = home.sourceLine({ kind: 'area', name: 'Cedar & 2nd Ave', distance_km: 1.1, area_count: 3, fallback: true });
  assert.deepEqual(s, { name: 'Estimate for your area', sub: 'from 3 public monitors', menu: 'Nearby public monitor, 1.1 km', estimate: true,
    down: "Cedar & 2nd Ave isn't reporting · showing an estimate for your area" });
  assert.equal(home.sourceLine({ kind: 'model', name: null, distance_km: null, fallback: true }).down,
    "The nearby public monitor isn't reporting · showing an estimate for your area");
});

test("AirGradient's list didn't answer: the card shows the estimate without blaming the chosen monitor", () => {
  const s = home.sourceLine({ kind: 'model', name: 'Cedar & 2nd Ave', distance_km: 1.1, area_count: 0, fallback: 'list' });
  assert.deepEqual(s, { name: 'Estimate for your area (Open‑Meteo)', sub: '', menu: 'Nearby public monitor, 1.1 km', estimate: true,
    down: "AirGradient didn't answer · showing an estimate for your area" });
  const st = home.sourceStatus({ kind: 'model', name: 'Cedar & 2nd Ave', distance_km: 1.1, fallback: 'list' }, { locationType: 'outdoor' }, NOW, 'UTC');
  assert.deepEqual(st.card, { tone: 'warn', label: "AirGradient didn't answer · showing an estimate for your area" });
  assert.deepEqual(st.row, { tone: 'warn', text: 'No answer', title: "AirGradient didn't answer · showing an estimate for your area", off: false });
  assert.doesNotMatch(JSON.stringify(st), /reporting/);
  assert.equal(home.sourceLine({ kind: 'area', name: 'X', area_count: 2, fallback: 'monitor' }).down, "X isn't reporting · showing an estimate for your area");
});

test('the source words never say "your outdoor monitor" nor a config word', () => {
  for (const s of [PINNED, { kind: 'area', area_count: 2 }, { kind: 'model' }, { kind: 'area', name: 'X', distance_km: 1, area_count: 2, fallback: true }]) {
    const w = home.sourceLine(s), all = [w.name, w.sub, w.menu, w.down].join(' ');
    assert.doesNotMatch(all, /your outdoor monitor|outdoor_from|\bmodel\b|config/i, JSON.stringify(s));
  }
});

test("the source's status: a public monitor's freshness, an estimate in grey, a monitor that isn't reporting in amber, never red", () => {
  const live = home.sourceStatus(PINNED, { ...FRESH, source: PINNED }, NOW, 'UTC');
  assert.deepEqual(live.card, { tone: 'good', label: 'Online' });
  assert.deepEqual(live.row, home.monitorRow(FRESH, NOW, false, 'UTC'));
  const est = home.sourceStatus({ kind: 'model' }, { locationType: 'outdoor' }, NOW, 'UTC');
  assert.deepEqual(est.card, { tone: 'neutral', label: 'Estimate' });
  assert.deepEqual(est.row, { tone: 'neutral', text: 'Estimate', title: 'Estimate for your area (Open‑Meteo)', off: false });
  const down = home.sourceStatus({ kind: 'area', name: 'Cedar & 2nd Ave', distance_km: 1.1, area_count: 3, fallback: true }, { locationType: 'outdoor' }, NOW, 'UTC');
  assert.deepEqual(down.card, { tone: 'warn', label: "Cedar & 2nd Ave isn't reporting · showing an estimate for your area" });
  assert.deepEqual(down.row, { tone: 'warn', text: 'Not reporting', title: "Cedar & 2nd Ave isn't reporting · showing an estimate for your area", off: false });
});

test('no history request for an outdoor entry without a locationId (an estimate)', () => {
  const from = new Date('2026-11-09T11:00:00Z'), to = new Date('2026-11-09T12:00:00Z');
  assert.equal(home.histUrl({ locationType: 'outdoor', source: { kind: 'area' } }, from, to), null);
  assert.equal(home.histUrl(null, from, to), null);
  assert.equal(home.histUrl({ locationId: 900002 }, from, to), '/api/history/900002?from=2026-11-09T11:00:00.000Z&to=2026-11-09T12:00:00.000Z');
  assert.equal(home.histUrl({ locationId: 0 }, from, to), '/api/history/0?from=2026-11-09T11:00:00.000Z&to=2026-11-09T12:00:00.000Z');
  const app = readFileSync(new URL('static/app.js', root), 'utf8');
  const fetchHist = app.slice(app.indexOf('const fetchHist'), app.indexOf('function loadWeek'));
  assert.match(fetchHist, /histUrl\(m, from, to\)/);
  assert.doesNotMatch(app, /\/api\/history\/\$\{m\.locationId\}/);
});

test("an estimate's PM2.5 now is the summary's model hour, only when it is this hour's", () => {
  const tl = { pm: { source: 'model', past: [['2026-11-09T10:00:00+00:00', 8.2], ['2026-11-09T11:00:00+00:00', 9.4]] } };
  assert.deepEqual(home.estimateNow(tl, NOW), { pm: 9.4, at: '2026-11-09T11:00:00+00:00' });   // the hour, for the ring's title
  assert.equal(home.estimateNow(tl, new Date('2026-11-09T14:00:00Z')), null);   // an old summary: no number
  assert.equal(home.estimateNow({ pm: { past: tl.pm.past } }, NOW), null);         // a measured past isn't an estimate
  assert.equal(home.estimateNow({ pm: { source: 'model', past: [] } }, NOW), null);
  assert.equal(home.estimateNow(null, NOW), null);
});

test("an indoor-only home's Home pill greys the outdoor picture and names the source", () => {
  const pill = home.monitorPill({ indoor: 'Bedroom', outdoor: null }, FRESH, { locationType: 'outdoor' }, NOW, {}, 'Estimate for your area');
  assert.match(pill, /class="vhw" data-tip="Indoor · AirGradient ONE: Online"/);
  assert.match(pill, /class="vhw off" data-tip="Outdoor PM2.5: Estimate for your area"><i class="off">/);
  assert.doesNotMatch(pill, /Offline|class="bad"/);
});

test('the Sources label counts the monitors the home has', () => {
  assert.equal(home.sourcesLabel({ smoke: false, pollen: false }, false, true, false), 'Sources: your monitor and weather. Open to see them.');
  assert.equal(home.sourcesLabel({ smoke: false, pollen: false }, false, false, true, false), 'Sources: your monitor and weather. Open to see them.');
  assert.equal(home.sourcesLabel({ smoke: false, pollen: false }, false, false), 'Sources: this monitor and weather. Open to see them.');   // a public monitor's page
});

test("an outdoor-only home: Toddler stays, greyed, with its reason; the indoor card says it isn't set up", () => {
  assert.equal(home.profileOff('toddler', false), 'Needs an indoor monitor');
  assert.equal(home.profileOff('toddler', true), null);
  assert.equal(home.profileOff('sinus', false), null);
  assert.equal(home.profileOff('runner', false), null);
  assert.deepEqual(home.ghostWords(false), { status: 'Not set up', aria: 'Indoor: no indoor monitor' });
  assert.deepEqual(home.ghostWords(true), { status: 'Public monitors are outdoor only', aria: 'Indoor: none, this is a public outdoor monitor' });
});

test("an estimate's card: one plain line on what it covers", () => {
  assert.equal(home.estimateNote(true), 'The estimate covers PM2.5 only. Temperature and humidity are Open‑Meteo’s weather.');
  assert.equal(home.estimateNote(false), 'The estimate covers PM2.5 only.');
});

test("a home with one monitor reads its own entries, whatever else the token sees", () => {
  const IN = { locationId: 1, locationType: 'indoor' }, OUT = { locationId: 2, locationType: 'outdoor' };
  const SRC = { locationType: 'outdoor', source: { kind: 'model' } };
  assert.deepEqual(home.pickEntries([IN, OUT], false, false), { indoor: IN, outdoor: OUT });   // two monitors: as before
  assert.deepEqual(home.pickEntries([IN, OUT, SRC], false, true), { indoor: IN, outdoor: SRC });   // indoor-only: the source
  assert.deepEqual(home.pickEntries([IN], false, true), { indoor: IN, outdoor: null });           // no source entry yet
  assert.deepEqual(home.pickEntries([IN, OUT], true, false), { indoor: null, outdoor: OUT });     // outdoor-only
  assert.deepEqual(home.pickEntries(null, false, false), { indoor: null, outdoor: null });
});

test('a greyed menu row stays greyed: tone off, or Off / Not set up in grey', () => {
  assert.equal(home.rowOff('off', 'Not set up'), true);
  assert.equal(home.rowOff('neutral', 'Off'), true);
  assert.equal(home.rowOff('neutral', 'Not set up'), true);
  assert.equal(home.rowOff('neutral', 'Estimate'), false);
  assert.equal(home.rowOff('good', 'Live · just now'), false);
  assert.equal(home.rowOff('bad', 'Offline'), false);
});

test("an indoor-only home without its source entry: the outdoor card is greyed with one line, the menu row grey, never red", () => {
  const st = home.sourceStatus(null, null, NOW, 'UTC');
  assert.deepEqual(st.card, { tone: 'off', label: 'No outdoor reading yet' });
  assert.deepEqual(st.row, { tone: 'neutral', text: 'No reading yet', title: 'No outdoor reading yet', off: false });
});

test("the neighborhood on an indoor-only home: no You row; the pinned monitor stays with the others, tagged", () => {
  const A = { locationId: 10, locationName: 'A' }, B = { locationId: 11, locationName: 'B' }, OWN = { locationId: 2, locationName: 'Yard' };
  // a two-monitor home: its outdoor monitor is You, as before
  assert.deepEqual(home.nbSplit([OWN, A, B], [1, 2], null), { you: OWN, others: [A, B] });
  // an indoor-only home pinned to A: no You, A stays among the others with its tag
  const s = home.nbSplit([A, B], [1], 10);
  assert.equal(s.you, null);
  assert.deepEqual(s.others, [{ ...A, used: true }, B]);
  const f = { esc: escHtml, pm: v => v.toFixed(1), num: v => v.toFixed(1), temp: v => v + '°' };
  const row = home.nbRowHTML({ ...s.others[0], pm02_corrected: 4, distance_km: 1.1 }, false, home.selfWords(false), '', { limit: 15, barMax: 55, limPct: 27 }, f);
  assert.match(row, /<td class="loc" title="A">A<span class="nb-used"> · used for your outdoor PM2\.5<\/span><\/td>/);
  assert.doesNotMatch(home.nbRowHTML({ ...B, pm02_corrected: 4, distance_km: 2 }, false, home.selfWords(false), '', { limit: 15, barMax: 55, limPct: 27 }, f), /nb-used/);
  assert.equal(home.NB_USED, 'used for your outdoor PM2.5');
});

test('nbCountText: with no Show more button, the count leads the credit line', () => {
  assert.equal(home.nbCountText(6), '6 public monitors within 15 km');
  assert.equal(home.nbCountText(1), '1 public monitor within 15 km');
  assert.equal(home.nbCountText(0), '');
});

test("monitorPill: an indoor-only home's chosen public monitor shows live, an estimate greyed", () => {
  const now = new Date('2026-11-10T03:30:00Z'), place = { indoor: 'apartment', outdoor: null };
  const indoor = { timestamp: '2026-11-10T03:28:00Z' };
  const pinned = { timestamp: '2026-11-10T03:25:00Z', source: { kind: 'monitor', name: 'Long Island City' } };
  const live = home.monitorPill(place, indoor, pinned, now, {}, 'Nearby public monitor, 1.4 km');
  assert.equal((live.match(/class="vhw off"/g) || []).length, 0);
  const est = home.monitorPill(place, indoor, { locationType: 'outdoor', source: { kind: 'model' } }, now, {}, 'Estimate for your area');
  assert.equal((est.match(/class="vhw off"/g) || []).length, 1);
});

test('the demo has one top area above the page, empty and hidden until the demo draws it; the band and the dark bar are gone', () => {
  const html = readFileSync(new URL('index.html', root), 'utf8');
  assert.match(html, /<header class="demo-top" id="demo-top" hidden><\/header>/);
  assert.ok(html.indexOf('id="demo-top"') < html.indexOf('<div class="page">'));
  assert.doesNotMatch(html, /entry-band|demo-bar/);
});


// ── The logo (mock 94, round 8 a): its dot says whether the readings are fresh, green while they are, grey otherwise ──
test('readingsState: fresh up to 10 min, otherwise when the readings are from, in words for the logo and the summary', () => {
  const NOW = new Date('2026-11-13T12:20:00Z'), at = m => new Date(NOW - m * 60000).toISOString();
  assert.deepEqual(home.readingsState(at(4), NOW, { tz: 'UTC' }), { fresh: true, status: 'Live readings', meta: '' });
  assert.deepEqual(home.readingsState(at(10), NOW, { tz: 'UTC' }).fresh, true);
  assert.deepEqual(home.readingsState(at(25), NOW, { tz: 'UTC' }), { fresh: false, status: 'Readings as of 11:55 AM', meta: '25 min old' });
  assert.deepEqual(home.readingsState(at(180), NOW, { tz: 'UTC' }), { fresh: false, status: 'Readings as of 9:20 AM', meta: '3 h old' });
  assert.deepEqual(home.readingsState(at(3000), NOW, { tz: 'UTC' }), { fresh: false, status: 'Readings as of Nov 11, 10:20 AM', meta: '2 days old' });
  assert.deepEqual(home.readingsState(at(4), NOW, { tz: 'UTC', offline: true }), { fresh: false, status: 'Offline · last reading 12:16 PM', meta: 'Last reading 12:16 PM' });
  assert.deepEqual(home.readingsState(null, NOW, { tz: 'UTC' }), { fresh: false, status: 'Loading readings', meta: '' });
  assert.deepEqual(home.readingsState(null, NOW, { tz: 'UTC', failed: true }), { fresh: false, status: 'Readings did not load', meta: 'Readings did not load' });
  assert.deepEqual(home.readingsState(null, NOW, { tz: 'UTC', offline: true }), { fresh: false, status: 'Offline', meta: 'Offline' });   // in words on screen, not only the grey dot
});

test('the logo follows freshness: green while fresh, grey when stale, offline or not loaded; never amber or red', () => {
  const app = readFileSync(new URL('static/app.js', root), 'utf8');
  const css = readFileSync(new URL('static/app.css', root), 'utf8');
  const html = readFileSync(new URL('index.html', root), 'utf8');
  const show = app.slice(app.indexOf('function showUpdated'), app.indexOf('function startSpin'));
  assert.match(show, /readingsState\(lastStamp, readingsNow\(\), \{ offline, failed: loadFailed, tz: TZ \}\)/);
  assert.match(show, /mark\.classList\.toggle\('is-stale', !s\.fresh\)/);
  assert.match(show, /mark\.title = name/);
  assert.match(show, /\$\('mark-name'\)\.textContent = name/);
  assert.doesNotMatch(show, /title\.textContent = (?!fresh)/);   // the hidden heading stays Today or Tonight
  // the dot and its glow take --logo-dot: green by default, grey on .is-stale
  assert.match(css, /--logo-live: #4fd68f;/);
  assert.match(css, /--logo-idle: #a7adb6;/);
  assert.match(css, /svg\.mark, svg\.dt-mark \{[^}]*--logo-dot: var\(--logo-live\);/);
  assert.match(css, /\.now-mark\.is-stale svg\.mark \{ --logo-dot: var\(--logo-idle\); --logo-glow-a: 0; \}/);   // stale: flat grey, no glow (not hue alone)
  assert.match(css, /\.lg-dot, svg\.mark \.lg-glow[^{]*\{ fill: var\(--logo-dot\); \}/);
  const marks = css.slice(css.indexOf('/* The logo'), css.indexOf('/* The logo') + 3000);
  assert.doesNotMatch(marks, /--(?:warn|bad)/);
  // the logo on the page: the house, the window bar, the roof's sky, the glow and the dot; no moon, no live sky gradient
  const svg = html.match(/<div class="site now-mark[^"]*"[^>]*>(<svg class="mark".*?<\/svg>)/)[1];
  assert.match(svg, /<path d="M13\.8 3\.2Q16 1\.4 18\.2 3\.2L30\.1 12\.8Q31 13\.5 31 14\.7V25Q31 31 25 31H7Q1 31 1 25V14\.7Q1 13\.5 1\.9 12\.8Z" fill="#fff"\/>/);
  assert.match(svg, /<path d="M0 17\.5h32M16 17\.5v15" stroke="#000" stroke-width="2\.6"\/>/);
  assert.match(svg, /<path class="lg-glow" d="M8\.6 16\.2A7\.4 7\.4 0 0 1 23\.4 16\.2Z"\/><path class="lg-dot" d="M11\.6 16\.2A4\.4 4\.4 0 0 1 20\.4 16\.2Z"\/>/);
  assert.doesNotMatch(html + app + css, /mk-g|mk-sky|mk-fr|mk-br|mk-dot|--mark-top|--mark-hor/);
  assert.match(html, /<span class="vh" id="mark-name">Home Air<\/span>/);
});

test('the weather line has no time and no live dot; the "Today" heading is hidden but still read out', () => {
  const sky = readFileSync(new URL('static/sky.js', root), 'utf8');
  const css = readFileSync(new URL('static/app.css', root), 'utf8');
  assert.doesNotMatch(sky, /cap-live/);
  assert.doesNotMatch(css, /cap-live/);
  assert.match(css, /\.now-h \{ position: absolute !important; width: 1px; height: 1px;[^}]*clip: rect\(0 0 0 0\)/);
  assert.doesNotMatch(css, /\.now-h \{[^}]*display: none/);
});

test('the summary line shows one status: the readings\' age or "Updated …", never both, on one line on a phone', () => {
  const css = readFileSync(new URL('static/app.css', root), 'utf8');
  // stale or offline: the age ("40 min old", "Last reading 3:16 PM", "Offline") replaces "Updated just now", which would contradict it
  assert.match(css, /#updated:not\(:empty\) ~ #s-ago \{ display: none !important; \}/);
  // the dot before the age only where the line goes on after "from 6 sources" (a computer); a phone starts a new row with it
  assert.match(css, /@media \(min-width: 481px\) \{\s*:root \.sum-card > \.s-bar #updated:not\(:empty\)::before \{ content: "· "; color: var\(--muted\); \}/);
  assert.doesNotMatch(css, /^:root \.sum-card > \.s-bar #updated:not\(:empty\)::before/m);
  // the status never wraps inside itself (Sacha's phone, offline: "Updated / just now" on two lines)
  assert.match(css, /:root \.sum-card > \.s-bar #updated, :root \.sum-card > \.s-bar #s-ago \{ white-space: nowrap; \}/);
  // the age sits on the same baseline as "Updated just now" and "from 6 sources": the same font box (design check, 1 px high before)
  assert.match(css, /:root \.sum-card > \.s-bar #updated \{ font: 400 13px\/1\.2 Inter, sans-serif; line-height: 20px !important; display: inline-block; margin: 0 !important; padding: 0 !important; \}/);
  assert.match(css, /:root \.sum-card > \.s-bar #updated:empty \{ display: none; \}/);   // fresh: takes no slot in the row
});

test('on a phone the summary header is two rows: title and profile icons, then the status across the full width', () => {
  const css = readFileSync(new URL('static/app.css', root), 'utf8');
  // the longest status ("Last reading Nov 12, 1:23 PM") pushed the refresh button under the icons when row 2 shared their column
  const phone = css.slice(css.indexOf('/* Summary profile icons and phone byline layout */'));
  const block = phone.slice(phone.indexOf('@media (max-width: 480px)'), phone.indexOf('.blocks .pop-head'));
  assert.match(block, /:root \.sum-card > \.s-bar \{ display: grid !important; grid-template-columns: minmax\(0, 1fr\) auto;/);
  assert.match(block, /\.s-meta \{ display: contents; \}/);
  assert.match(block, /:root \.sum-card > \.s-bar \.s-tools \{ grid-row: 2; grid-column: 1 \/ -1;/);
  assert.match(block, /\.s-profiles \{ grid-row: 1; grid-column: 2;/);
});

test('the header logo is 40 px on a computer and 32 on a phone, as tall as the menu beside it (mock 94, round 21)', () => {
  const css = readFileSync(new URL('static/app.css', root), 'utf8');
  assert.match(css, /\n\.site svg\.mark \{ flex: none; width: 40px !important; height: 40px !important; \}/);
  assert.match(css, /@media \(max-width: 640px\) \{\s*\.bar \.site svg\.mark, \.site svg\.mark \{ width: 32px !important; height: 32px !important; \}/);
  assert.match(css, /:root \{ --dt-row: 40px; --dt-mark: 32px; \}/);
});

test('the page starts in the home\'s unit (APP.temp_unit, °F for a US home); a viewer\'s own pick, °C or °F, wins and is kept', () => {
  const app = readFileSync(new URL('static/app.js', root), 'utf8');
  // decision 8 (Sacha, 2026-11-12): the home's unit first, the viewer's saved choice over it
  assert.match(app, /let tempPref = \(typeof APP !== 'undefined' && APP\.temp_unit === 'F'\) \? 'F' : 'C';/);
  assert.match(app, /try \{ const saved = localStorage\.getItem\(TEMP_KEY\); if \(saved === 'C' \|\| saved === 'F'\) tempPref = saved; \} catch \{\}/);
  // picking °C in a °F home must stick, so both units are saved (before, °C removed the key: the default was always °C)
  assert.match(app, /try \{ localStorage\.setItem\(TEMP_KEY, u\); \} catch \{\}/);
  assert.doesNotMatch(app, /localStorage\.removeItem\(TEMP_KEY\)/);
});

test('switching °C/°F redraws the summary card too, so its chart follows the weather line', () => {
  const app = readFileSync(new URL('static/app.js', root), 'utf8');
  const at = app.indexOf('function setTempUnit'), set = app.slice(at, app.indexOf('\n}\n', at));
  assert.match(set, /if \(state\.read && state\.read\.summary\) SummaryCard\.renderSummary\(\$\('summary'\), state\.read\.summary, appNow\(\), SUMMARY_OPTS\(\)\);/);
});

// The Connected card waits for the home (rehearsals, 13 Nov 2026: after setup restarted the app, the tab still showed the
// token card until a reload). waitForHome asks /api/setup and opens the home once the app no longer waits for setup.
test('the Connected card opens the home by itself once setup has restarted the app', async () => {
  const src = readFileSync(new URL('static/app.js', root), 'utf8');
  const start = src.indexOf('function waitForHome(');
  assert.ok(start > 0, 'waitForHome exists');
  const body = src.slice(start, src.indexOf('\n}\n', start) + 2);
  const answers = [new Error('restarting'), { setup: true }, { setup: false }];
  const asked = [], timers = [];
  const env = {
    fetch: async (url, opts) => { asked.push([url, opts && opts.cache]); const a = answers.shift(); if (a instanceof Error) throw a; return { ok: true, json: async () => a }; },
    location: { replaced: null, replace(u) { this.replaced = u; } },
    setTimeout: (f) => timers.push(f),
    document: { hidden: false },
    SETUP_POLL_MS: 3000,
  };
  const run = new Function(...Object.keys(env), body + '\nreturn waitForHome;')(...Object.values(env));
  run();
  for (let i = 0; i < 3 && !env.location.replaced; i++) { await timers.shift()(); }
  assert.equal(env.location.replaced, '/');
  assert.deepEqual(asked.map(a => a[0]), ['/api/setup', '/api/setup', '/api/setup']);   // a failed ask (the app restarting) is asked again
  assert.ok(asked.every(a => a[1] === 'no-store'));
});

// The server fetches AirGradient at most every 5 minutes for every tab (docs/api-usage.md). The
// page asks it again when someone comes back to it, on Refresh, and every 5 minutes while the tab is on screen.
test('the one timer that asks for readings is the visible-tab re-check, every 5 minutes', () => {
  const app = files[0];
  const asking = [...app.matchAll(/setInterval\(([^;]*)/g)].filter(m => /refresh|getJSON|fetch\(|loadCharts|loadRead|recheck/.test(m[1]));
  assert.equal(asking.length, 0);   // no free-running timer asks for readings: recheckTimer arms one tick at a time
  assert.match(app, /recheck = recheckTimer\(\{ refresh, visibility: \(\) => document\.visibilityState \}\);\s*recheck\.arm\(\);/);
  assert.equal(home.RECHECK_MS, 5 * 60 * 1000 + 10 * 1000);   // just past the server's 5-minute cache
  assert.match(app, /addEventListener\('', \(\) => \{ recheck\.arm\(\); refreshIfDue\(\); \}\)/);   // strings are blanked in `files`
  assert.equal(home.recheckDue({ visibility: 'visible' }), true);
  assert.equal(home.recheckDue({ visibility: 'hidden' }), false);   // a hidden tab never polls
  assert.equal(home.recheckDue({ visibility: 'prerender' }), false);
});

// A refresh whose request never answers used to hold the page's one-refresh-at-a-time guard forever, so a wall
// tablet stopped re-checking. Now each fetch gives up after 20 s, a refresh after 60 s, and the timer re-arms itself.
test('a refresh that hangs does not stop the re-checks at 5, 10 and 15 minutes', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] });
  const timers = { setTimeout, clearTimeout };
  let calls = 0;
  const hangsFirst = () => { calls++; return calls === 1 ? new Promise(() => {}) : Promise.resolve(); };
  const refresh = home.singleFlight(hangsFirst, { timers });
  let visibility = 'visible';
  const timer = home.recheckTimer({ refresh, visibility: () => visibility, timers });
  timer.arm();
  const at = [];
  const flush = () => new Promise(r => setImmediate(r));
  const minutes = async n => { for (let i = 0; i < n * 6; i++) { t.mock.timers.tick(10 * 1000); await flush(); } };
  await minutes(1);
  for (const min of [6, 11, 16]) {
    await minutes(5);
    at.push([min, calls]);
  }
  assert.deepEqual(at, [[6, 1], [11, 2], [16, 3]]);   // asks at 5:10, 10:20, 15:30: the first hung, the next two still asked
  assert.equal(home.REFRESH_TIMEOUT_MS, 60 * 1000);
  assert.equal(home.FETCH_TIMEOUT_MS, 20 * 1000);
  visibility = 'hidden';
  timer.arm();                                          // what visibilitychange does
  assert.equal(timer.armed(), false);
  await minutes(30);
  assert.equal(calls, 3);                               // hidden: no asks
  visibility = 'visible';
  timer.arm();
  await minutes(6);
  assert.equal(calls, 4);                               // shown again: re-armed
});

test('a call during a refresh shares it; a stuck one is dropped after the timeout', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let calls = 0;
  const run = home.singleFlight(() => { calls++; return new Promise(() => {}); }, { timeoutMs: 1000, timers: { setTimeout, clearTimeout } });
  const a = run(), b = run();
  assert.equal(a, b);
  await new Promise(r => setImmediate(r));
  assert.equal(calls, 1);
  t.mock.timers.tick(1000);
  await assert.rejects(a, /timed out/);
  run().catch(() => {});
  await new Promise(r => setImmediate(r));
  assert.equal(calls, 2);
});

test('every API read gives up after the fetch timeout, and the refresh guard is the shared helper', () => {
  const app = readFileSync(new URL('static/app.js', root), 'utf8');
  assert.match(app, /const ctl = new AbortController\(\), timer = setTimeout\(\(\) => ctl\.abort\(\), FETCH_TIMEOUT_MS\);/);
  assert.match(app, /fetch\(apiUrl\(url\), \{ \.\.\.opts, signal: ctl\.signal \}\)/);
  assert.match(app, /const refreshOnce = singleFlight\(/);
  assert.match(app, /if \(recheck\) recheck\.arm\(\);/);   // any refresh restarts the re-check, before it can hang
  assert.doesNotMatch(app, /inFlight/);
});

test('coming back to the page refreshes only when what it shows is older than the server interval', () => {
  const app = readFileSync(new URL('static/app.js', root), 'utf8');
  assert.match(app, /addEventListener\('visibilitychange', \(\) => \{ recheck\.arm\(\); refreshIfDue\(\); \}\)/);
  assert.match(app, /addEventListener\('focus', refreshIfDue\)/);
  assert.match(app, /\$\('read-refresh'\)\.addEventListener\('click', \(\) => refresh\(\{ manual: true \}\)\)/);
  const now = 1_000_000_000;
  assert.equal(home.REFRESH_STALE_MS, 5 * 60 * 1000);
  assert.equal(home.refreshDue({ hidden: false, last: now - 60 * 1000, now }), false);       // a minute old: the cache would answer the same
  assert.equal(home.refreshDue({ hidden: false, last: now - 5 * 60 * 1000, now }), true);
  assert.equal(home.refreshDue({ hidden: true, last: 0, now }), false);                      // a hidden tab never asks
});


// The exhaust (mock 109 round 4, Sacha 26 Nov): its colour follows the sky, it comes from the road's compass side (north up,
// as on the map) and drifts with the wind, and its strength follows the pollution.
const exhaustRules = (() => {
  const env = { document: {}, window: {} };
  runInNewContext(readFileSync(new URL('static/sky-glass.js', root), 'utf8'), env);
  return env.SkyGlass;
})();
const near = (a, b, eps = 1e-6) => Math.abs(a - b) < eps;

test("the exhaust's colour: LA's brown on a clear day, a little dusk at sunrise and sunset, grey under cloud and in rain", () => {
  const { exhaustPalette } = exhaustRules;
  const clear = exhaustPalette({ el: 40, cloud: 5 });
  assert.equal(clear.dusk, 0); assert.equal(clear.grey, 0);
  assert.deepEqual([...clear.pal.body[0]], [164, 144, 116]);   // LA's lid, by day
  const dusk = exhaustPalette({ el: 4, cloud: 5 });
  assert.ok(near(dusk.dusk, .55) && dusk.grey === 0, 'at most a little of the 80s dusk');
  assert.ok(dusk.pal.edge[0][0] > clear.pal.edge[0][0], 'warmer at dusk');
  assert.equal(exhaustPalette({ el: 4 + 9, cloud: 5 }).dusk, 0, 'gone 9° from its peak');
  assert.equal(exhaustPalette({ el: 40, cloud: 90 }).grey, 1);
  assert.deepEqual([...exhaustPalette({ el: 40, cloud: 90 }).pal.body[0]], [134, 126, 116]);   // arrays from the script's own context
  assert.ok(near(exhaustPalette({ el: 40, cloud: 0, rain: .5 }).grey, .3));
  assert.equal(exhaustPalette({ el: 40, cloud: 0, thunder: true }).grey, .4);
});

test("the exhaust comes from the road's side, north up, and drifts the way the wind blows", () => {
  const { roadBearing, plumeFrame } = exhaustRules;
  assert.equal(roadBearing({ side: 'SE' }, [66, 207], 300), 135);
  assert.equal(roadBearing({ side: null }, [66, 207], 300), 136.5, 'no side: the middle of the arc the wind must blow from');
  assert.equal(roadBearing(null, [300, 60], 90), 0, 'an arc across north');
  assert.equal(roadBearing(null, null, 200), 200);
  assert.equal(roadBearing(null, null, null), 135);
  // southeast on a phone: the bottom-right corner; a southeast wind carries it up and to the left
  const se = plumeFrame({ bearing: 135, windDir: 135, windSpeed: 9, W: 390, H: 844 });
  assert.ok(near(se.origin.x, 390) && near(se.origin.y, 844));
  assert.ok(near(se.dx, -Math.SQRT1_2) && near(se.dy, -Math.SQRT1_2));
  assert.equal(se.speed, 8 + 2 * 9);
  const east = plumeFrame({ bearing: 90, windDir: 90, windSpeed: 9, W: 390, H: 844 });
  assert.ok(near(east.origin.x, 390) && near(east.origin.y, 422));
  const north = plumeFrame({ bearing: 0, windDir: 0, windSpeed: 9, W: 1440, H: 900 });
  assert.ok(near(north.origin.x, 720) && near(north.origin.y, 0) && near(north.dy, 1));
  // a calm: straight away from the road, whatever the vane says
  const calm = plumeFrame({ bearing: 135, windDir: 300, windSpeed: 2, W: 390, H: 844 });
  assert.ok(near(calm.dx, -Math.SQRT1_2) && near(calm.dy, -Math.SQRT1_2));
});

test("the exhaust's strength follows the traffic, the PM2.5 over the limit, the wind and the rain", () => {
  const { exhaustStrength } = exhaustRules, limits = { pm: 15, pm_high: 35 };
  assert.ok(near(exhaustStrength({ strength: 'peak', pm: 22, limits, windSpeed: 9 }), .55 + .45 * 7 / 20));
  assert.equal(exhaustStrength({ strength: 'peak', pm: 35, limits }), 1);
  assert.equal(exhaustStrength({ strength: 'high', pm: 15, limits }), .4, 'never weaker than its floor');
  assert.ok(near(exhaustStrength({ strength: 'peak', pm: 35, limits, windSpeed: 37 }), .7), 'a strong wind spreads it thinner');
  assert.ok(near(exhaustStrength({ strength: 'peak', pm: 35, limits, rain: 1 }), .55), 'rain washes it out');
  assert.ok(near(exhaustStrength({ strength: 'peak', pm: 35, limits: { pm: 10, pm_high: 20 } }), 1), "the home's own limits");
});

test("beside the exhaust's word, the wind that carries it; it leaves with the exhaust", () => {
  const road = [{ name: 'BQE', short: 'BQE', side: 'SE', reaching: true, strength: 'peak' }];
  const p = airPage({ randomValue: .5 });
  const kids = () => [...p.node('sky-conditions').children];
  p.sky.air({ pm: 22, rh: 47, ctx: { roads: road }, summary: { items: [{ id: 'pm', action: { text: 'Windows shut' } }] } });
  const pill = kids().find(el => el.className?.includes('cap-ex-wind'));
  assert.ok(pill, 'the wind pill is shown');
  assert.equal(kids().indexOf(pill), kids().findIndex(el => el.dataset.skyPlay === 'exhaust') + 1, 'right after the exhaust');
  const words = pill.children.find(el => el.className === 'ex-wind').textContent;
  assert.match(words, /^from (N|NNE|NE|ENE|E|ESE|SE|SSE|S|SSW|SW|WSW|W|WNW|NW|NNW) · \d+ km\/h$/);
  assert.match(pill.getAttribute('aria-label'), /^Wind from the [a-z-]+ at \d+ km\/h, carrying the exhaust/);
  assert.ok(p.node('html').classList.contains('has-ex-wind'), "the weather's own wind reading hides meanwhile");
  p.sky.air({ pm: 8, rh: 47, ctx: { roads: [] }, summary: { items: [] } });
  assert.ok(!kids().some(el => el.className?.includes('cap-ex-wind')));
  assert.ok(!p.node('html').classList.contains('has-ex-wind'));
});

// The Live sky switches (Sacha, 26 Nov): Sky, Wind, Air. Air alone turns the air effects on and off; a choice saved
// with the old Haze switch carries over to Air.
test('the Air switch alone turns the air effects on and off, and a saved Haze choice carries over', () => {
  const road = [{ name: 'BQE', short: 'BQE', side: 'SE', reaching: true, strength: 'peak' }];
  const shown = prefs => {
    const p = airPage({ randomValue: .5, prefs });
    p.sky.air({ pm: 22, rh: 47, ctx: { roads: road }, summary: { items: [{ id: 'pm', action: { text: 'Windows shut' } }] } });
    const word = [...p.node('sky-conditions').children].find(b => b.dataset.skyPlay === 'exhaust');
    return { enabled: !!word && !word.disabled, air: p.node('fx-on-air').checked };
  };
  assert.deepEqual(shown({ sky: true, wind: true, air: true }), { enabled: true, air: true });
  assert.deepEqual(shown({ sky: false, wind: false, air: true }), { enabled: true, air: true }, 'no sky or wind: the air still shows');
  assert.deepEqual(shown({ sky: true, wind: true, air: false }), { enabled: false, air: false }, 'Air off: no air effects, whatever the sky');
  assert.deepEqual(shown({ sky: true, wind: true, haze: false }), { enabled: false, air: false }, 'a saved Haze off is Air off');
  assert.deepEqual(shown({ sky: true, wind: true, haze: true }), { enabled: true, air: true });
  const html = readFileSync(new URL('index.html', root), 'utf8');
  assert.match(html, /<b>Air<\/b>/); assert.doesNotMatch(html, /fx-on-haze|sky-haze/);
});

test('the map\'s road words: headline, one line per road, the key (mock 114)', () => {
  const h = createRequire(import.meta.url)('../static/home.js'), tz = 'America/New_York';
  const bqe = { name: 'the BQE', short: 'BQE', reaching: true, why: 'slow', until: '2026-04-21T11:00:00-04:00', until_why: 'wind',
    wind_after: 290, toward_home: true, light_wind: false, band: 'within 500 m' };
  const se = { from: 135, kmh: 9.4 };
  assert.equal(h.roadsHeadline([bqe], se, tz), 'The BQE’s exhaust is likely to reach you until 11 AM.');
  assert.equal(h.roadLine(bqe, se, tz), 'its exhaust blows north-west, toward your home. At 11 AM the wind turns and carries it east, away from you.');
  assert.equal(h.roadsHeadline([{ ...bqe, reaching: false }], se, tz), 'Road exhaust is unlikely to reach you now.');
  assert.equal(h.roadsHeadline([bqe], null, tz), '');   // no wind: no claim either way
  assert.equal(h.roadsHeadline([{ ...bqe, why: 'calm', until: '2026-04-21T06:38:00-04:00', until_why: 'sunrise' }], { from: 180, kmh: 4 }, tz),
    'The BQE’s exhaust is likely to reach you until sunrise, about 6:45 AM.');
  assert.equal(h.roadLine({ ...bqe, until: '2026-04-21T20:00:00-04:00', until_why: 'busy_end' }, se, tz), 'its exhaust blows north-west, toward your home. After 8 PM the road is no longer busy.');
  assert.equal(h.roadLine({ ...bqe, reaching: false, busy_source: 'slow' }, se, tz), 'its exhaust blows north-west, toward your home, but there is no traffic jam on it now.');
  assert.equal(h.roadLine({ ...bqe, reaching: false, busy_source: 'busy' }, se, tz), 'its exhaust blows north-west, toward your home, but it’s outside its busy hours now.');
  assert.equal(h.roadLine({ ...bqe, reaching: false, toward_home: false, far_toward: true, band: '500 m to 1 km' }, se, tz),
    'its exhaust blows north-west. It is 500 m to 1 km away, so its exhaust thins out before it reaches you.');
  assert.equal(h.roadsHeadline([{ ...bqe, until: null, until_why: 'steady', horizon_h: 12 }], se, tz), 'The BQE’s exhaust is likely to reach you for the next 12 hours or more.');
  // a plume only where the words say the exhaust reaches, or goes away from home
  assert.equal(h.plumeKind(bqe), 'reach');
  assert.equal(h.plumeKind({ ...bqe, reaching: false, toward_home: false }), 'away');
  assert.equal(h.plumeKind({ ...bqe, reaching: false }), null);
  assert.equal(h.plumeKind({ ...bqe, reaching: false, toward_home: false, far_toward: true }), 'far');
  assert.equal(h.plumeKind({ ...bqe, reaching: false, light_wind: true }), 'near');
  assert.equal(h.roadLine({ ...bqe, reaching: false, light_wind: true }, se, tz), 'the wind is light, so its exhaust stays near the road.');
  assert.equal(h.roadLine({ ...bqe, reaching: false, toward_home: false }, { from: 290, kmh: 17 }, tz), 'its exhaust blows east, away from you.');
  assert.equal(h.clockAt('2026-04-21T12:00:00-04:00', tz), 'noon');
  assert.equal(h.roadKeyNames([{ short: 'McGuinness Boulevard' }, bqe, { short: 'FDR Drive' }]), 'McGuinness Blvd, BQE +1');
  assert.equal(h.windKey(se), 'Wind from the south-east · 9 km/h');
});

// A heading's ink and outline hold through a passing haze, fog or flash (Sacha, 26 Nov: "keeps changing between black
// font and black with white border"), and stay the whole time an air effect is shown ("On particles the home monitors
// still changes style").
function headingInk() {
  const noop = () => {}, observer = class { observe() {} };
  const rect = { left: 24, top: 24, right: 64, bottom: 38, width: 40, height: 14 };
  const frames = new Map(), nodes = new Map();
  let nextFrame = 0, time = 0, air, rear = [240, 240, 240];
  const $ = id => {
    if (!nodes.has(id)) {
      const el = { style: { setProperty(k, v) { this[k] = v; } }, dataset: { theme: 'light' }, querySelectorAll: () => [], children: [],
        classList: { add: noop, remove: noop, toggle: noop, contains: () => true }, addEventListener: noop, appendChild: noop, insertBefore: noop,
        setAttribute: noop, getAttribute: () => null, removeAttribute: noop, getBoundingClientRect: () => rect, hasAttribute: () => true };
      const ctx = new Proxy({ canvas: el, getImageData: (x, y, w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
        createRadialGradient: () => ({ addColorStop: noop }), createLinearGradient: () => ({ addColorStop: noop }) }, { get: (o, k) => k in o ? o[k] : noop });
      el.getContext = () => ctx; nodes.set(id, el);
    }
    return nodes.get(id);
  };
  const renderer = extra => new Proxy({ redraw: noop, ...extra }, { get: (o, k) => k in o ? o[k] : noop });
  const requestAnimationFrame = cb => (frames.set(++nextFrame, cb), nextFrame);
  const advance = now => { time = now; const pending = [...frames.values()]; frames.clear(); for (const cb of pending) cb(now); };
  const document = { documentElement: $('html'), body: $('body'), querySelector: () => null,
    querySelectorAll: selector => selector.includes('[data-sky-heading]') ? [$('heading')] : [],
    createElement: () => $(Symbol()), createRange: () => ({ selectNodeContents: noop, getClientRects: () => [rect] }), addEventListener: noop };
  const env = {
    window: { matchMedia: () => ({ matches: false }), addEventListener: noop }, document, $, location: { search: '' },
    APP: { place: { lat: 0, lon: 0 }, limits: { pm: 15, pm_high: 35, rh_damp: 60 } }, state: {}, TZ: 'UTC', appNow: () => new Date('2026-11-24T17:00:00Z'),
    has: v => v != null, pmOver: home.pmOver, pollenHigh: home.pollenHigh, skyConditions: home.skyConditions, skyLeader: home.skyLeader, previewConditions: home.previewConditions,
    innerWidth: 120, innerHeight: 100, scrollX: 0, scrollY: 0, performance: { now: () => time },
    setInterval: noop, setTimeout: noop, clearTimeout: noop, addEventListener: noop, requestAnimationFrame, cancelAnimationFrame: id => frames.delete(id),
    IntersectionObserver: observer, MutationObserver: observer,
    getComputedStyle: el => ({ backgroundColor: 'rgb(14, 15, 18)', filter: 'none', opacity: el.style.opacity, getPropertyValue: k => k.endsWith('on-light') ? '8 14 26' : '253 254 255' }),
    SkyGlass: { create(api) { air = api; return renderer({ sampleRear: () => rear, sampleFront: rgb => rgb }); } },
    SkyWet: { create: () => renderer({ sampleBackground: rgb => rgb }) },
  };
  runInNewContext(readFileSync(new URL('static/sky.js', root), 'utf8'), env);
  const heading = $('heading'), states = [];
  const step = (now, rgb) => { rear = rgb; air.invalidateInk(); advance(now); states.push(`${heading.dataset.skyInkLight}:${heading.style['--sky-heading-outline']}`); };
  return { step, states, sky: env.SKY };
}

test('a section heading keeps its ink and outline while the sky behind it flickers, and lets go once the sky settles', () => {
  const { step, states } = headingInk();
  for (let t = 0; t <= 600; t += 16) step(t, [240, 240, 240]);   // a light sky: dark ink, no outline needed
  const settled = states.at(-1);
  const before = states.length;
  for (let t = 616, i = 0; t <= 4000; t += 16, i++) step(t, i % 2 ? [20, 20, 24] : [240, 240, 240]);   // a flickering pass
  const during = states.slice(before);
  assert.equal(new Set(during.map(s => s.split(':')[0])).size, 1, 'the ink never flips during the flicker');
  assert.ok(during.slice(4).every(s => s === during.at(-1)), 'the outline settles at once and stays: no blinking');
  for (let t = 4016; t <= 12000; t += 16) step(t, [240, 240, 240]);   // the sky settles light again
  assert.equal(states.at(-1), settled, 'a few seconds after the sky settles, the heading is back to its plain ink');
});

test('while an air effect is shown, a section heading keeps its outline the whole time', () => {
  const { step, states, sky } = headingInk();
  for (let t = 0; t <= 600; t += 16) step(t, [240, 240, 240]);
  assert.match(states.at(-1), /:transparent$/, 'a plain light sky: no outline');
  sky.air({ pm: 24, rh: 47, ctx: {}, summary: { items: [{ id: 'pm', action: { text: 'Keep windows shut' } }] } });   // fine particles
  for (let t = 616; t <= 20000; t += 16) step(t, [240, 240, 240]);
  const outlined = states.slice(states.findIndex(s => !s.endsWith(':transparent')));
  assert.ok(outlined.length > 1000 && outlined.every(s => s === outlined[0]), 'outlined at once and unchanged for the next 19 s');
});
