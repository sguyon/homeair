import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

test('API cache fallbacks are marked while network responses stay unmarked', async () => {
  const cached = new Response('{"weather":{"weather_code":95}}', { headers: { 'Content-Type': 'application/json' } });
  const context = vm.createContext({ self: { addEventListener() {} }, URL, Request, Response, Headers,
    fetch: async () => { throw Error('offline'); }, caches: { match: async () => cached.clone() } });
  vm.runInContext(readFileSync(new URL('../sw.js', import.meta.url), 'utf8'), context);
  const response = await vm.runInContext('networkFirst("/api/context", "/api/context", () => Response.error())', context);
  assert.equal(response.headers.get('X-Home-Air-Cache'), 'fallback');
  assert.deepEqual(await response.json(), { weather: { weather_code: 95 } });
  context.fetch = async () => cached.clone();
  context.caches.open = async () => ({ put() {} });
  const live = await vm.runInContext('networkFirst("/api/context", "/api/context", () => Response.error())', context);
  assert.equal(live.headers.get('X-Home-Air-Cache'), null);
});

// Exercise the real worker cache path: a setup form must not be saved for offline reuse.
test('the service worker never caches a no-store setup page', async () => {
  const stored = [];
  const context = vm.createContext({
    self: { addEventListener() {} }, URL, Request, Response,
    caches: { open: async () => ({ put: async (...args) => stored.push(args) }) },
  });
  vm.runInContext(readFileSync(new URL('../sw.js', import.meta.url), 'utf8'), context);
  context.setup = new Response('<form>one-use value</form>', { headers: { 'Cache-Control': 'no-store' } });
  vm.runInContext('putInCache("/", setup)', context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(stored.length, 0);
  context.normal = new Response('readings');
  vm.runInContext('putInCache("/", normal)', context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(stored.length, 1);
});


test('installation also leaves a no-store setup page out of the shell cache', async () => {
  const stored = [];
  const listeners = {};
  const cache = {
    add: async url => stored.push(url),
    put: async (url) => stored.push(url),
  };
  const context = vm.createContext({
    self: { addEventListener: (name, callback) => { listeners[name] = callback; }, skipWaiting() {} },
    URL, Request, Response,
    caches: { open: async () => cache },
    fetch: async url => new Response('shell', { headers: url === '/' ? { 'Cache-Control': 'no-store' } : {} }),
  });
  vm.runInContext(readFileSync(new URL('../sw.js', import.meta.url), 'utf8'), context);
  let done;
  listeners.install({ waitUntil: promise => { done = promise; } });
  await done;
  assert.equal(stored.includes('/'), false);
  assert.equal(stored.length, 2);
});


test('activation removes v5 pages and keeps the new worker cache', async () => {
  const listeners = {};
  const removed = [];
  const opened = [];
  const context = vm.createContext({
    self: { addEventListener: (name, callback) => { listeners[name] = callback; }, clients: { claim() {} } },
    caches: {
      open: async name => { opened.push(name); return { put: async () => {} }; },
      keys: async () => ['aq-dashboard-v5', ...opened],
      delete: async name => { removed.push(name); },
    },
    URL, Request, Response,
  });
  vm.runInContext(readFileSync(new URL('../sw.js', import.meta.url), 'utf8'), context);
  context.page = new Response('new page');
  await vm.runInContext('putInCache("/", page)', context);
  let done;
  listeners.activate({ waitUntil: promise => { done = promise; } });
  await done;
  assert.deepEqual(removed, ['aq-dashboard-v5']);
  assert.equal(opened.length, 1);
  assert.equal(removed.includes(opened[0]), false);
});


// Safari revokes a subscription whose push shows nothing, so every push path shows a notification.
function pushWorker() {
  const listeners = {};
  const shown = [];
  const context = vm.createContext({
    self: {
      addEventListener: (name, callback) => { listeners[name] = callback; },
      registration: { showNotification: (title, options) => { shown.push([title, options]); return Promise.resolve(); } },
      location: { origin: 'https://home.example' },
    },
    URL, Request, Response,
  });
  vm.runInContext(readFileSync(new URL('../sw.js', import.meta.url), 'utf8'), context);
  return { listeners, shown };
}

test('push shows a notification in every path', async () => {
  for (const data of [
    { json: () => ({ title: 'CO2 high', body: 'Open a window', url: '/#summary' }) },
    { json: () => { throw new SyntaxError('bad JSON'); } },
    null,
  ]) {
    const { listeners, shown } = pushWorker();
    let done;
    listeners.push({ data, waitUntil: promise => { done = promise; } });
    await done;
    assert.equal(shown.length, 1);
  }
  const { listeners, shown } = pushWorker();
  listeners.push({ data: { json: () => { throw new Error('bad'); } }, waitUntil: () => {} });
  assert.equal(shown[0][0], 'Home Air');
  assert.equal(shown[0][1].body, '');
});


// A tapped notification brings the Summary up: by navigate where it works, else by a message the page acts on (alerts.js).
function clickWorker(windows) {
  const listeners = {};
  const opened = [];
  const context = vm.createContext({
    self: {
      addEventListener: (name, callback) => { listeners[name] = callback; },
      location: { origin: 'https://home.example' },
      clients: { matchAll: async () => windows, openWindow: async url => { opened.push(url); } },
    },
    URL, Request, Response,
  });
  vm.runInContext(readFileSync(new URL('../sw.js', import.meta.url), 'utf8'), context);
  const click = async url => {
    let done;
    listeners.notificationclick({ notification: { close() {}, data: { url } }, waitUntil: promise => { done = promise; } });
    await done;
  };
  return { click, opened };
}
function fakeWindow(navigate) {
  const w = { url: 'https://home.example/', posted: [], focused: 0, focus: async () => { w.focused++; return w; },
    postMessage: m => w.posted.push(m) };
  if (navigate) w.navigate = navigate;
  return w;
}

test('a click moves an open window by navigate when it works', async () => {
  const w = fakeWindow(async url => { w.to = url; return w; });
  await clickWorker([w]).click('/#summary');
  assert.equal(w.to, 'https://home.example/#summary');
  assert.deepEqual(w.posted, []);
});

test('a click posts the address when navigate is missing or refuses, and focuses the window', async () => {
  for (const navigate of [undefined, async () => { throw new TypeError('not allowed'); }]) {
    const w = fakeWindow(navigate);
    await clickWorker([w]).click('/#summary');
    assert.equal(w.posted.length, 1);
    assert.equal(w.posted[0].type, 'open');
    assert.equal(w.posted[0].url, 'https://home.example/#summary');
    assert.ok(w.focused >= 1);
  }
});

test('a click opens a window when none is open, and never follows another site', async () => {
  const { click, opened } = clickWorker([]);
  await click('https://evil.example/#x');
  assert.deepEqual(opened, ['https://home.example/#summary']);
});
