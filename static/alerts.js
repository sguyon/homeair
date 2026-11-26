// Home Air: the Alerts bell in the header, its own button left of the Home menu (mock 112), and its panel. On a phone the panel
// slides up from the bottom, turns web push alerts on and off and holds this phone's choices; on a computer a small
// panel under the bell says alerts go to phones, with the QR code for this page and the Phones list. The server says
// where alerts can work (window.APP.alerts, /api/alerts).
(function (root) {
  'use strict';

  // ---- the QR code for the computer's panel: byte mode, error correction M, versions 1 to 6 (up to 106 bytes) ----

  const QR_VERSIONS = [   // [total codewords, error correction codewords per block, blocks] at level M (ISO 18004, table 9)
    null, [26, 10, 1], [44, 16, 1], [70, 26, 1], [100, 18, 2], [134, 24, 2], [172, 16, 4]];
  const QR_ALIGN = [null, [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34]];   // alignment pattern centres per version
  const QR_LEVEL_M = 0;            // level M's two format bits: 15% of the code can be damaged and still read
  const QR_FORMAT_POLY = 0x537;    // the format bits' BCH generator
  const QR_FORMAT_MASK = 0x5412;   // XORed onto the format bits so they are never all light
  const QR_GF_POLY = 0x11d;        // the Reed-Solomon field's polynomial, GF(2^8)
  const QR_PAD = [0xec, 0x11];     // the pad bytes after the data, in turn

  function gfMul(x, y) {
    let z = 0;
    for (let i = 7; i >= 0; i--) { z = (z << 1) ^ ((z >>> 7) * QR_GF_POLY); z ^= ((y >>> i) & 1) * x; }
    return z;
  }
  function rsDivisor(degree) {
    const r = new Array(degree).fill(0); r[degree - 1] = 1;
    let rootV = 1;
    for (let i = 0; i < degree; i++) {
      for (let j = 0; j < degree; j++) { r[j] = gfMul(r[j], rootV); if (j + 1 < degree) r[j] ^= r[j + 1]; }
      rootV = gfMul(rootV, 2);
    }
    return r;
  }
  function rsRemainder(data, divisor) {
    const r = divisor.map(() => 0);
    for (const b of data) {
      const f = b ^ r.shift(); r.push(0);
      divisor.forEach((c, i) => { r[i] ^= gfMul(c, f); });
    }
    return r;
  }

  // The codewords for `bytes` at `version`: data, terminator, padding, then each block's error correction, interleaved.
  function qrCodewords(bytes, version) {
    const [total, ecLen, blocks] = QR_VERSIONS[version];
    const dataLen = total - ecLen * blocks;
    const bits = [];
    const put = (v, n) => { for (let i = n - 1; i >= 0; i--) bits.push((v >>> i) & 1); };
    put(4, 4); put(bytes.length, 8); bytes.forEach(b => put(b, 8));
    put(0, Math.min(4, dataLen * 8 - bits.length));
    while (bits.length % 8) bits.push(0);
    const data = [];
    for (let i = 0; i < bits.length; i += 8) data.push(bits.slice(i, i + 8).reduce((a, b) => (a << 1) | b, 0));
    for (let i = 0; data.length < dataLen; i++) data.push(QR_PAD[i % 2]);
    const per = dataLen / blocks, div = rsDivisor(ecLen), ds = [], es = [];
    for (let b = 0; b < blocks; b++) { const d = data.slice(b * per, (b + 1) * per); ds.push(d); es.push(rsRemainder(d, div)); }
    const out = [];
    for (let i = 0; i < per; i++) ds.forEach(d => out.push(d[i]));
    for (let i = 0; i < ecLen; i++) es.forEach(e => out.push(e[i]));
    return out;
  }

  const QR_MASKS = [
    (x, y) => (x + y) % 2 === 0, (x, y) => y % 2 === 0, x => x % 3 === 0, (x, y) => (x + y) % 3 === 0,
    (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0, (x, y) => x * y % 2 + x * y % 3 === 0,
    (x, y) => (x * y % 2 + x * y % 3) % 2 === 0, (x, y) => ((x + y) % 2 + x * y % 3) % 2 === 0];

  // The penalty of ISO 18004 section 8.8.2: runs, 2×2 blocks, finder-like patterns, dark balance. The lowest mask wins.
  function qrPenalty(m) {
    const n = m.length, lines = [];
    for (let i = 0; i < n; i++) { lines.push(m[i]); lines.push(m.map(row => row[i])); }
    let p = 0;
    for (const line of lines) {
      let run = 1;
      for (let i = 1; i <= n; i++) {
        if (i < n && line[i] === line[i - 1]) run++;
        else { if (run >= 5) p += run - 2; run = 1; }
      }
      const s = '0000' + line.map(v => (v ? '1' : '0')).join('') + '0000';   // the light margin counts as light
      for (const pat of ['010111010000', '000010111010']) for (let i = s.indexOf(pat); i >= 0; i = s.indexOf(pat, i + 1)) p += 40;
    }
    for (let y = 0; y < n - 1; y++) for (let x = 0; x < n - 1; x++) {
      const c = m[y][x];
      if (c === m[y][x + 1] && c === m[y + 1][x] && c === m[y + 1][x + 1]) p += 3;
    }
    const dark = m.reduce((a, row) => a + row.filter(Boolean).length, 0);
    p += Math.floor(Math.abs(dark * 20 - n * n * 10) / (n * n)) * 10;
    return p;
  }

  // The modules (true: dark) of the QR code for `text`, or null when it is longer than version 6 holds.
  // `mask` forces one of the eight masks (for the tests); otherwise the one with the lowest penalty.
  function qrMatrix(text, mask) {
    const bytes = Array.from(new TextEncoder().encode(text));
    let version = 1;
    while (version < QR_VERSIONS.length && (QR_VERSIONS[version][0] - QR_VERSIONS[version][1] * QR_VERSIONS[version][2]) * 8 < 12 + bytes.length * 8) version++;
    if (version >= QR_VERSIONS.length) return null;
    const n = version * 4 + 17;
    const base = Array.from({ length: n }, () => new Array(n).fill(false));
    const fixed = Array.from({ length: n }, () => new Array(n).fill(false));
    const set = (x, y, v) => { base[y][x] = v; fixed[y][x] = true; };
    for (let i = 0; i < n; i++) { set(6, i, i % 2 === 0); set(i, 6, i % 2 === 0); }
    for (const [cx, cy] of [[3, 3], [n - 4, 3], [3, n - 4]]) {
      for (let dy = -4; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++) {
        const x = cx + dx, y = cy + dy, d = Math.max(Math.abs(dx), Math.abs(dy));
        if (x >= 0 && x < n && y >= 0 && y < n) set(x, y, d !== 2 && d !== 4);
      }
    }
    const al = QR_ALIGN[version], last = al.length - 1;
    al.forEach((cx, i) => al.forEach((cy, j) => {
      if ((i === 0 && j === 0) || (i === 0 && j === last) || (i === last && j === 0)) return;
      for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) set(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    }));
    const formatAt = (m, k) => {   // both copies of the 15 format bits, and the dark module
      let rem = (QR_LEVEL_M << 3) | k;
      for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * QR_FORMAT_POLY);
      const bits = ((((QR_LEVEL_M << 3) | k) << 10) | rem) ^ QR_FORMAT_MASK, bit = i => ((bits >>> i) & 1) === 1;
      const put = (x, y, v) => { m[y][x] = v; fixed[y][x] = true; };
      for (let i = 0; i <= 5; i++) put(8, i, bit(i));
      put(8, 7, bit(6)); put(8, 8, bit(7)); put(7, 8, bit(8));
      for (let i = 9; i < 15; i++) put(14 - i, 8, bit(i));
      for (let i = 0; i < 8; i++) put(n - 1 - i, 8, bit(i));
      for (let i = 8; i < 15; i++) put(8, n - 15 + i, bit(i));
      put(8, n - 8, true);
    };
    formatAt(base, 0);   // reserves the format areas before the data goes in
    const words = qrCodewords(bytes, version), data = [];
    let i = 0;
    for (let right = n - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (let v = 0; v < n; v++) for (let j = 0; j < 2; j++) {
        const x = right - j, up = ((right + 1) & 2) === 0, y = up ? n - 1 - v : v;
        if (fixed[y][x]) continue;
        base[y][x] = i < words.length * 8 && ((words[i >>> 3] >>> (7 - (i & 7))) & 1) === 1;
        data.push([x, y]); i++;
      }
    }
    const build = k => {
      const m = base.map(row => row.slice());
      for (const [x, y] of data) if (QR_MASKS[k](x, y)) m[y][x] = !m[y][x];
      formatAt(m, k);
      return m;
    };
    if (mask !== undefined) return build(mask);
    let best = null, bestP = Infinity;
    for (let k = 0; k < 8; k++) { const m = build(k), p = qrPenalty(m); if (p < bestP) { best = m; bestP = p; } }
    return best;
  }

  // ---- when each phone was added, as the Phones list says it ----

  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const NB = '\u00a0';   // a space that never breaks: on a narrow row "25 Nov" and "2:02 AM" each stay whole
  const day = iso => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || ''); return m ? `${+m[3]}${NB}${MONTHS[+m[2] - 1]}` : ''; };
  const clock = hm => { const m = /^(\d{2}):(\d{2})$/.exec(hm || ''); return m ? `${+m[1] % 12 || 12}:${m[2]}${NB}${+m[1] < 12 ? 'AM' : 'PM'}` : ''; };
  // "25 Nov", or "25 Nov, 2:02 AM" when another phone in the list has the same name and day: the time tells the two
  // apart, so the person removes the right one. `p.since` and `p.time` are the home's date and "HH:MM" (/api/alerts).
  function addedText(p, phones) {
    const twin = phones.some(q => q !== p && q.device === p.device && q.since === p.since);
    const t = twin ? clock(p.time) : '';
    return t ? `${day(p.since)}, ${t}` : day(p.since);
  }

  // Where alerts can't run, the one line that says why: on the bell (for a screen reader) and at the top of the panel,
  // whose rows are then all disabled. "local" is Home Air on this computer or the home network, "setup" over HTTPS without
  // push keys (alerts.available); any other value counts as "local", as the panel's state does.
  const STATES = ['online', 'setup', 'local', 'demo'];
  const WHY = { demo: 'Install Home Air for your own home first. This page is a demo.',
    local: 'Set up Home Air on a server with HTTPS first: phones get alerts only from an HTTPS address.',
    setup: 'Set up alerts first: the alert keys are missing.' };
  const disabledWhy = st => (st === 'online' ? '' : WHY[st] || WHY.local);

  // An action's switch shows on only while Allow Alerts is on (its switch, as drawn) and the action isn't muted. Display
  // only: the saved choices stay as they are and show again once Allow Alerts is on.
  const itemOn = (allowOn, muted, k) => !!allowOn && !muted.includes(k);

  const api = { qrMatrix, addedText, disabledWhy, itemOn };
  if (typeof module !== 'undefined' && module.exports) { module.exports = api; return; }
  Object.assign(root, { AlertsQR: api });
  if (typeof document === 'undefined') return;

  // ---- the bell and the panel ----

  const STORE_KEY = 'aq-alerts';        // localStorage: this phone's {id, secret, muted, quiet} once alerts are on
  const SUBSCRIBE_TIMEOUT_MS = 20000;   // the service worker, the push service and Home Air together: past this, something is stuck
  const SUB_CHECK_TIMEOUT_MS = 3000;    // reading the browser's own push subscription: past this, it is treated as none
  const POP_GAP_PX = 8;                 // the computer's panel sits this far under the bell
  const IOS_PUSH_MIN = [16, 4];         // the first iOS with web push, for Home Screen apps only (WebKit)
  const ITEMS = [['co2', 'CO₂ inside'], ['rh', 'Humidity inside'], ['pm', 'PM2.5 outside'], ['pollen', 'Pollen today'],
    ['offline', 'Monitor offline']];    // the Summary's item ids and labels (summary.py), and the Offline alert
  const APP = root.APP || {};
  const QUIET_FALLBACK = [22, 7];       // alert_rules.QUIET_DEFAULT, for a page served without it
  const QUIET = Array.isArray(APP.alerts_quiet) ? APP.alerts_quiet : QUIET_FALLBACK;   // the default quiet hours, from the server

  // The phone's panel moves like an iPhone sheet (mock 112, round 5): it follows the finger, closes on a flick or a long
  // pull, and springs back otherwise.
  const FLICK_PX_MS = 0.4;              // a release moving down faster than this (px per ms) closes the panel: a light flick
  const CLOSE_SHARE = 0.3;              // a release past this share of the panel's height closes it, unless moving back up
  const BACK_UP_PX_MS = -0.1;           // ... where moving back up means faster than this (px per ms, up is negative)
  const RUBBER_MAX_PX = 28;             // pulled up past its place, the panel gives this much at most
  const RUBBER_GAIN = 6;                // and gives RUBBER_GAIN × √(px pulled) until then: a little, then less
  const DRAG_PX = 6;                    // a move this far makes a gesture a drag: the tap that ends it is not a click
  const CAPTURE_PX = 4;                 // the title bar keeps the pointer after this much movement (a mouse leaving the bar)
  const SAMPLES = 6;                    // finger positions kept to measure the speed at release
  const OPEN_SPRING = [0.9, 16];        // the opening spring: damping ratio (under 1: a very small overshoot), stiffness (rad/s)
  const BACK_SPRING = [0.78, 18];       // the spring back after a short pull
  const SPRING_MAX_S = 1.1;             // a spring's curve is cut here (s): it has settled long before
  const SPRING_POINTS = 40;             // points in a spring's CSS linear() curve
  const EASE_IOS = 'cubic-bezier(.32,.72,0,1)';   // the iPhone's sheet curve, where linear() isn't supported
  const OPEN_FALLBACK_MS = 500;         // the opening, with that curve
  const BACK_FALLBACK_MS = 450;         // the spring back, with that curve
  const CLOSE_MS = [160, 320];          // closing slides down at the release speed, within these times (ms)
  const CLOSE_MIN_PX_MS = 1.4;          // ... and never slower than this (px per ms)
  const CLOSE_EASE = 'cubic-bezier(.3,.6,.4,1)';
  const DIM_MAX_MS = 350;               // the dimming fades with the panel, at most this long
  const OFFSCREEN_PX = 20;              // closed, the panel rests this far below the screen's edge

  const btn = document.getElementById('alerts-btn'), dlg = document.getElementById('alerts-panel');
  const btnState = document.getElementById('alerts-btn-state');
  if (!btn || !dlg) return;
  if (APP.state === 'no_token') { btn.hidden = true; return; }   // the token card: no home yet

  const ua = navigator.userAgent || '';
  const IOS = /iPhone|iPod/.test(ua);
  const ANDROID = /Android/.test(ua);
  // A phone only when its browser says iPhone or Android: an iPad, a Mac or a Windows computer (touch screen or not) gets
  // the computer's panel and never subscribes.
  const PHONE = IOS || ANDROID;
  const computer = () => !PHONE;
  const installed = () => navigator.standalone === true || matchMedia('(display-mode: standalone)').matches;
  const pushOk = () => 'serviceWorker' in navigator && 'PushManager' in root && 'Notification' in root;
  const perm = () => ('Notification' in root ? Notification.permission : 'default');
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  function oldIos() {
    if (!IOS) return false;
    if (installed() && !pushOk()) return true;
    const v = ua.match(/OS (\d+)_(\d+)/);
    return !!v && (+v[1] < IOS_PUSH_MIN[0] || (+v[1] === IOS_PUSH_MIN[0] && +v[2] < IOS_PUSH_MIN[1]));
  }

  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const hour = h => `${h % 12 || 12} ${h < 12 ? 'AM' : 'PM'}`;
  const ICON = {
    bell: '<path d="M6 9a6 6 0 1 1 12 0c0 6 2.5 7.5 2.5 7.5h-17S6 15 6 9"/><path d="M10 20a2 2 0 0 0 4 0"/>',
    bellFill: '<path fill="currentColor" d="M6 9a6 6 0 1 1 12 0c0 6 2.5 7.5 2.5 7.5h-17S6 15 6 9"/><path d="M10 20a2 2 0 0 0 4 0"/>',
    bellOff: '<path d="M6 9a6 6 0 0 1 9.5-4.9M18 9c0 6 2.5 7.5 2.5 7.5h-13"/><path d="M10 20a2 2 0 0 0 4 0"/><path d="M3 3l18 18"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>', r: '<path d="M9 6l6 6-6 6"/>', l: '<path d="M15 6l-6 6 6 6"/>',
    phone: '<rect x="7" y="3" width="10" height="18" rx="2"/><path d="M11 18h2"/>',
    share: '<path d="M12 3v12"/><path d="M8 7l4-4 4 4"/><path d="M7 11H5v10h14V11h-2"/>',
    add: '<rect x="4" y="4" width="16" height="16" rx="4"/><path d="M12 8v8M8 12h8"/>',
    vdots: '<circle cx="12" cy="5.5" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="12" cy="18.5" r="1.3"/>',
    co2: '<path d="M7 18a4 4 0 0 1-.6-7.95A6 6 0 0 1 17.9 9.1 4.5 4.5 0 0 1 17.5 18z"/>',
    rh: '<path d="M12 3.5s6 6.4 6 10.9a6 6 0 0 1-12 0c0-4.5 6-10.9 6-10.9z"/>',
    pm: '<circle cx="7" cy="8" r="1.7"/><circle cx="15.5" cy="6.5" r="1.3"/><circle cx="12" cy="13" r="2.2"/><circle cx="18" cy="15.5" r="1.5"/><circle cx="6.5" cy="17" r="1.3"/>',
    pollen: '<circle cx="12" cy="11" r="2.6"/><path d="M12 8.4V4.5M12 13.6V20M9.4 11H5.5M18.5 11h-3.9M10.2 9.2 7.6 6.6M13.8 12.8l2.6 2.6M13.8 9.2l2.6-2.6M10.2 12.8l-2.6 2.6"/>',
    offline: '<path d="M8.5 16.4a5 5 0 0 1 7 0"/><path d="M5 12.8a10 10 0 0 1 3.6-2.3M13.6 10.3A10 10 0 0 1 19 12.8"/><path d="M2 9.2a15 15 0 0 1 4.3-2.7M10.4 5.6A15 15 0 0 1 22 9.2"/><path d="M12 20h.01"/><path d="M3 3l18 18"/>',
    moon: '<path d="M20 14.6A8 8 0 0 1 9.4 4 8.1 8.1 0 1 0 20 14.6z"/>',
    send: '<path d="M21 3 10.5 13.5"/><path d="M21 3l-6.5 18-4-7.5L3 9.5z"/>' };
  const I = n => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${ICON[n]}</svg>`;

  // The texts the panel shows.
  const T = {
    main: 'Allow Alerts', mainSub: 'On this phone only. Each phone has its own settings.',   // the iOS Settings label
    mainSubComputer: 'On each phone, from Home Air on its Home Screen.',   // the disabled panel on a computer
    lead: 'Sent when it’s time to act: what to do, when and why.',
    stepsHead: 'First, add Home Air to the Home Screen',
    stepShare: 'Share', stepShareSub: 'In Safari’s bar, or under ··· if the bar is small',
    stepAdd: 'Add to Home Screen',
    stepOpen: 'Open Home Air from its icon', stepOpenSub: 'Then tap the bell',
    stepsWhy: 'An iPhone shows alerts only from apps on its Home Screen.',
    androidMenu: 'in Chrome', androidAdd: 'Tap Add to home screen › Install',
    notYet: IOS ? 'Open Home Air from its Home Screen icon, tap the bell, then turn on Allow Alerts.'
      : 'Open Home Air from its home screen icon, tap the bell, then turn on Allow Alerts.',
    refused: 'Notifications are off for Home Air. Turn them on in Settings › Notifications › Home Air.',
    refusedAndroid: 'Notifications are off for Home Air. Turn them on in this phone’s settings. Then open Home Air again and turn on Allow Alerts.',
    on: '<b>On.</b> Home Air just sent a test notification.',
    onNoTest: 'Alerts are on for this phone.',   // the second made the line under the switch three lines long
    sendFor: 'Send alerts for',
    quiet: 'Quiet hours',
    test: 'Send a test',
    testFailed: 'Home Air could not confirm the test was sent. Tap Send a test again.',
    focus: 'No banner? Check Do Not Disturb and Focus in your phone’s settings.',
    quietText: `No alerts on this phone between these hours. At the end, one notification lists the actions still due.`,
    from: 'From', to: 'To',
    phones: 'Phones', thisPhone: 'This phone', off: 'Off', since: d => `since ${d}`,
    stop: 'A phone you remove gets one notification saying its alerts are off.',   // remove from any device
    remove: 'Remove', cancel: 'Cancel',
    removeLabel: (d, w) => `Remove ${d}, since ${w}`,   // the button's name for a screen reader
    removeFailed: 'Home Air did not answer. Tap Remove again.',
    none: 'No phone has alerts on yet.',
    count: k => (k === 1 ? '1 phone' : `${k} phones`),
    limit: k => `${k} phones have alerts, the most Home Air allows. Remove one below.`,
    old: 'Needs iOS 16.4 or later: Settings › General › Software Update.',
    failed: 'Home Air did not answer. Tap the switch again.',
    browser: 'Home Air can’t send alerts to this browser. On iPhone, open Home Air from its Home Screen icon; on Android, use Chrome.',   // a 400 "push service"
    tooMany: 'Too many tries. Try again in a minute.',   // a 429
    offFailed: 'Alerts are still on. Tap the switch again to turn them off.',
    onlineTitle: 'Alerts go to your phone',
    scan: "Scan this code with your phone's camera. Add the page to the Home Screen, open it from there, then tap the bell and turn on alerts.",
    on1: 'Turned on', off1: 'Not turned on',
    have: k => (k ? (k === 1 ? '1 phone has alerts' : `${k} phones have alerts`) : 'No phone has alerts'),
  };

  function load() {
    try {
      const v = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
      return v && typeof v.id === 'string' && typeof v.secret === 'string' ? v : null;
    } catch (e) { return null; }
  }
  function save() {
    try { if (S.reg) localStorage.setItem(STORE_KEY, JSON.stringify(S.reg)); else localStorage.removeItem(STORE_KEY); } catch (e) { /* kept for this visit only */ }
  }

  const S = {
    st: STATES.includes(APP.alerts) ? APP.alerts : 'local',
    key: null, phones: [], max: null, loaded: false,
    reg: load(),          // this phone's id and secret, and its choices
    view: 'main',         // or 'quiet'
    busy: false,          // 'on', 'off' or 'test' while that call is on its way
    just: false, sentTest: true, failed: '', limitHit: false, testFailed: '', offFailed: false,
    confirm: null,        // the id of the phone whose Remove was tapped once: its row asks Cancel or Remove
    removing: null,       // that id while the removal is on its way
    rmFailed: '',         // 'yes' or 'many' (a 429) when the last removal failed
    want: null,           // choices shown but not yet acknowledged by the server; S.reg holds the acknowledged ones
    hasSub: false,        // this browser still has a push subscription (alerts on before, the stored id lost)
  };
  const shown = () => S.want || S.reg;
  const quietOf = () => (shown() && Array.isArray(shown().quiet) ? shown().quiet : QUIET);
  const mutedOf = () => (shown() && Array.isArray(shown().muted) ? shown().muted : []);
  const registered = () => !!S.reg && (!S.loaded || S.phones.some(p => p.id === S.reg.id));
  const shut = () => S.st !== 'online';   // the demo, "setup" or "local": the bell and the panel are drawn disabled
  const alertsOn = () => S.st === 'online' && registered() && perm() === 'granted';
  const full = () => S.limitHit || (S.max !== null && S.phones.length >= S.max);

  function call(method, url, body) {
    return fetch(url, { method, credentials: 'same-origin', cache: 'no-store',
      headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  }
  async function refresh() {
    try {
      const r = await call('GET', '/api/alerts');
      if (!r.ok) return;
      const d = await r.json();
      if (STATES.includes(d.state)) S.st = d.state;
      S.key = d.key || null; S.phones = Array.isArray(d.phones) ? d.phones : []; S.max = typeof d.max === 'number' ? d.max : null; S.loaded = true;
      if (S.reg && S.st === 'online' && !S.phones.some(p => p.id === S.reg.id)) { S.reg = null; S.want = null; save(); }   // removed on the server
      if (S.confirm && !S.phones.some(p => p.id === S.confirm)) S.confirm = null;
    } catch (e) { /* the panel keeps what it showed */ }
  }

  // ---- the bell: its own round button, left of the Home menu ----

  function paintButton() {
    // where alerts can't run: the crossed bell, as when alerts are off (it still opens the panel, which says why); a
    // computer never gets alerts: the plain outline
    const state = shut() ? 'dis' : computer() ? 'neutral' : alertsOn() ? 'on' : 'off';
    btn.className = `al-bell ${state}`;
    btn.querySelector('svg').innerHTML = ICON[{ on: 'bellFill', off: 'bellOff', neutral: 'bell', dis: 'bellOff' }[state]];
    btnState.textContent = shut() ? disabledWhy(S.st) : computer() ? T.have(S.st === 'online' ? S.phones.length : 0) : alertsOn() ? T.on1 : T.off1;
    btn.setAttribute('aria-expanded', dlg.open ? 'true' : 'false');
  }

  // ---- the phone's panel: grouped rows, as in the iPhone's Settings; every row stays in place ----

  // One row. A row that does something is a button; a step or a phone is not. `sw` makes it a switch.
  function row({ ic = '', num = 0, label, sub = '', right = '', act = '', sw = null, cls = '', dis = false, busy = false }) {
    const tag = act ? 'button' : 'div';
    const attrs = act ? ` type="button" data-act="${act}"${dis ? ' disabled' : ''}${busy ? ' aria-busy="true"' : ''}${sw !== null ? ` role="switch" aria-checked="${sw}"` : ''}` : '';
    const icon = num ? `<span class="al-ic num" aria-hidden="true">${num}</span>` : ic ? `<span class="al-ic">${I(ic)}</span>` : '';
    return `<${tag} class="al-row${cls ? ' ' + cls : ''}"${attrs}>${icon}<span class="al-lb">${label}${sub ? `<small>${sub}</small>` : ''}</span>${right}`
      + `${sw !== null ? `<span class="al-sw${sw ? ' on' : ''}" aria-hidden="true"></span>` : ''}</${tag}>`;
  }
  const kbd = n => `<span class="al-kbd">${I(n)}</span>`;
  const foot = (text, cls = '') => `<p class="al-ft${cls}">${text}</p>`;

  function steps() {
    const st = IOS
      ? [[`Tap ${kbd('share')} ${T.stepShare}`, T.stepShareSub], [`Tap ${kbd('add')} ${T.stepAdd}`, ''], [T.stepOpen, T.stepOpenSub]]
      : [[`Tap ${kbd('vdots')} ${T.androidMenu}`, ''], [T.androidAdd, ''], [T.stepOpen, T.stepOpenSub]];
    return `<h3 class="al-gh">${T.stepsHead}</h3><ol class="al-grp al-steps">${st.map(([l, s], i) => `<li>${row({ num: i + 1, label: l, sub: s })}</li>`).join('')}</ol>`
      + (IOS ? foot(T.stepsWhy) : '') + '<div class="al-gap"></div>';
  }

  function phoneMain() {
    const showSteps = S.st === 'online' && !oldIos() && !installed();
    // why the switch can't be used: one line under it
    let block = '';
    if (oldIos()) block = T.old;
    else if (!installed()) block = T.notYet;
    else if (perm() === 'denied') block = ANDROID ? T.refusedAndroid : T.refused;
    else if (!registered() && full() && !S.hasSub) block = T.limit(S.max || S.phones.length);   // a phone that had alerts takes its own place back
    const reg = !block && registered();
    const on = S.busy === 'on' ? true : S.busy === 'off' ? false : reg;   // the switch moves at once; the call follows
    let line = T.lead;
    if (block) line = block;
    else if (S.offFailed) line = T.offFailed;
    else if (S.failed) line = S.failed === 'many' ? T.tooMany : S.failed === 'browser' ? T.browser : T.failed;
    else if (S.just && reg) line = S.sentTest ? T.on : T.onNoTest;
    // In a browser tab the steps say what to do; a switch that can't be used there is left out.
    let h = showSteps ? steps() : `<div class="al-grp">${row({ ic: on ? 'bell' : 'bellOff', label: T.main, sub: T.mainSub, act: 'toggle', sw: on, cls: 'main', dis: !!block || !!S.busy, busy: S.busy === 'on' || S.busy === 'off' })}</div>`
      + foot(line, ' al-live');
    if (!showSteps) {   // the choices: always drawn, dimmed and out of reach while alerts are off, so nothing moves when the switch changes
      const off = reg ? '' : ' off" inert="';
      const muted = mutedOf(), [a, b] = quietOf();
      const testLine = S.testFailed ? (S.testFailed === 'many' ? T.tooMany : T.testFailed) : IOS ? T.focus : '';
      h += `<h3 class="al-gh">${T.sendFor}</h3><div class="al-grp${off}">${ITEMS.map(([k, n]) => row({ ic: k, label: n, act: 'item:' + k, sw: itemOn(on, muted, k) })).join('')}</div>`
        + `<div class="al-grp al-sep${off}">${row({ ic: 'moon', label: T.quiet, act: 'quiet', right: `<span class="al-val">${hour(a)}–${hour(b)}${I('r')}</span>` })}`
        + `${row({ ic: 'send', label: T.test, act: 'test', cls: 'act', dis: S.busy === 'test', busy: S.busy === 'test' })}</div>`
        + (testLine ? foot(testLine) : '');
    }
    return h + phonesGroup(reg);
  }

  // Where alerts can't run (the demo, "setup", "local"): the same panel on a phone and a computer, every row disabled and greyed,
  // with one line on why at the top (DESIGN.md, "A missing or disabled part"). The phones Home Air lists, without
  // Remove; none: the empty list's text.
  function shutMain() {
    const [a, b] = quietOf();
    const phones = S.phones.length
      ? S.phones.map(p => row({ ic: 'phone', label: esc(p.device), sub: T.since(addedText(p, S.phones)) })).join('')
      : row({ label: T.none, cls: 'noic' });
    return `<p class="al-why">${disabledWhy(S.st)}</p><div class="al-dis">`
      + `<div class="al-grp">${row({ ic: 'bellOff', label: T.main, sub: computer() ? T.mainSubComputer : T.mainSub, act: 'toggle', sw: false, cls: 'main', dis: true })}</div>${foot(T.lead)}`
      + `<h3 class="al-gh">${T.sendFor}</h3><div class="al-grp">${ITEMS.map(([k, n]) => row({ ic: k, label: n, act: 'item:' + k, sw: itemOn(false, [], k), dis: true })).join('')}</div>`
      + `<div class="al-grp al-sep">${row({ ic: 'moon', label: T.quiet, act: 'quiet', right: `<span class="al-val">${hour(a)}–${hour(b)}${I('r')}</span>`, dis: true })}`
      + `${row({ ic: 'send', label: T.test, act: 'test', cls: 'act', dis: true })}</div>`
      + `<h3 class="al-gh">${T.phones}</h3><div class="al-grp">${phones}</div></div>`;
  }

  // A phone's Remove: red text that asks once, on its own row (Cancel or Remove), before anything is sent. Only these
  // are buttons: the rows stay plain text.
  function removeButtons(p) {
    const label = esc(T.removeLabel(p.device, addedText(p, S.phones))), id = esc(p.id);
    if (S.confirm !== p.id) return `<button type="button" class="al-rm" data-act="rm:${id}" aria-label="${label}">${T.remove}</button>`;
    const busy = S.removing === p.id;
    return `<span class="al-cf"><button type="button" class="al-cancel" data-act="rmno"${busy ? ' disabled' : ''}>${T.cancel}</button>`
      + `<button type="button" class="al-rm yes" data-act="rmyes:${id}" aria-label="${label}"${busy ? ' disabled aria-busy="true"' : ''}>${T.remove}</button></span>`;
  }
  const removeNote = () => (S.rmFailed ? (S.rmFailed === 'many' ? T.tooMany : T.removeFailed) : T.stop);

  // This phone first ("Off" while off; its switch turns it off), then the others the server lists, each with Remove.
  function phonesGroup(reg) {
    const mine = reg ? S.phones.find(p => p.id === S.reg.id) : null;
    const others = S.phones.filter(p => p !== mine);
    const name = mine ? esc(mine.device) : IOS ? 'iPhone' : ANDROID ? 'Android phone' : 'Phone';
    const val = t => `<span class="al-val">${t}</span>`;
    return `<h3 class="al-gh">${T.phones}</h3><div class="al-grp">`
      + row({ ic: 'phone', label: name, sub: mine ? `${T.thisPhone} · ${T.since(addedText(mine, S.phones))}` : T.thisPhone, right: mine || reg ? '' : val(T.off) })
      + others.map(p => row({ ic: 'phone', label: esc(p.device), sub: T.since(addedText(p, S.phones)), right: removeButtons(p) })).join('')
      + `</div>${others.length ? foot(removeNote()) : ''}`;
  }

  function quietBody() {
    const [a, b] = quietOf();
    const opts = sel => Array.from({ length: 24 }, (_, h) => `<option value="${h}"${h === sel ? ' selected' : ''}>${hour(h)}</option>`).join('');
    return `<div class="al-grp al-first"><label class="al-row noic"><span class="al-lb">${T.from}</span><select data-q="0">${opts(a)}</select></label>`
      + `<label class="al-row noic"><span class="al-lb">${T.to}</span><select data-q="1">${opts(b)}</select></label></div>${foot(T.quietText)}`;
  }

  const navHTML = () => `<span class="al-grab" aria-hidden="true"></span>`
    + (S.view === 'quiet' ? `<button type="button" class="al-back" data-act="back">${I('l')}Alerts</button>` : '')
    + `<h2 class="al-t" id="alerts-title">${S.view === 'quiet' ? T.quiet : 'Alerts'}</h2>`
    + `<button type="button" class="al-x" data-act="close" aria-label="Close"><span>${I('x')}</span></button>`;

  // ---- the computer's panel: alerts go to phones ----

  function qrSvg(text) {
    const m = qrMatrix(text);
    if (!m) return '';
    const q = 2, n = m.length + q * 2;   // a two-module margin inside the white tile
    let d = '';
    m.forEach((r, y) => r.forEach((on, x) => { if (on) d += `M${x + q} ${y + q}h1v1h-1z`; }));
    return `<svg class="al-qr" viewBox="0 0 ${n} ${n}" shape-rendering="crispEdges" role="img" aria-label="QR code for ${esc(location.host)}"><rect width="${n}" height="${n}" class="al-qr-bg"/><path d="${d}" class="al-qr-ink"/></svg>`;
  }
  const closeX = () => `<button type="button" class="al-px" data-act="close" aria-label="Close">${I('x')}</button>`;
  function computerBody() {
    const x = closeX();
    if (shut()) return `<div class="al-ph"><h2 class="al-t" id="alerts-title">Alerts</h2>${x}</div>${shutMain()}`;   // the phone's panel, disabled
    const url = location.origin + '/';
    const list = S.phones.length
      ? S.phones.map(p => `<div class="al-ln"><span class="al-who">${esc(p.device)}<small>${T.since(addedText(p, S.phones))}</small></span>${removeButtons(p)}</div>`).join('')
        + `<p class="al-note">${removeNote()}</p>`
      : `<p class="al-note al-first">${T.none}</p>`;
    return `<div class="al-ph"><h2 class="al-t" id="alerts-title">${T.onlineTitle}</h2>${x}</div>
      <div class="al-scan">${qrSvg(url)}<p>${T.scan}<br><a class="al-host" href="${esc(url)}">${esc(location.host)}</a></p></div>
      <p class="al-ph-gh">${S.phones.length ? T.count(S.phones.length) : T.phones}</p>${list}`;
  }

  // ---- drawing ----

  let sh = null, nav = null, sc = null, dim = null;   // the phone's panel: built once, so a drag or the opening isn't cut by a redraw
  if (!computer()) {
    dlg.className = 'al-panel sheet';
    dlg.innerHTML = '<div class="al-dim" data-act="close"></div><div class="al-sh"><div class="al-nav"></div><div class="al-sc"></div></div>';
    [dim, sh, nav, sc] = ['.al-dim', '.al-sh', '.al-nav', '.al-sc'].map(q => dlg.querySelector(q));
  } else {
    dlg.className = 'al-panel pop';
  }

  function render(anim) {
    const focused = dlg.contains(document.activeElement) && document.activeElement.dataset ? document.activeElement.dataset.act || document.activeElement.dataset.q : null;
    if (computer()) {
      dlg.classList.toggle('dis', shut());
      dlg.innerHTML = computerBody();
      place();
    } else {
      const keep = sc.scrollTop;
      nav.innerHTML = navHTML();
      sc.innerHTML = S.view === 'quiet' ? quietBody() : shut() ? shutMain() : phoneMain();
      sc.scrollTop = anim ? 0 : keep;
      sc.classList.remove('in', 'out');
      if (anim && !reduced()) { void sc.offsetWidth; sc.classList.add(anim); }
    }
    if (focused) { const el = dlg.querySelector(`[data-act="${focused}"]:not(.al-dim), [data-q="${focused}"]`); if (el && !el.disabled) el.focus({ preventScroll: true }); }
    paintButton();
  }

  // The computer's panel: under the bell, its right edge on the Home menu's right edge.
  function place() {
    const b = btn.getBoundingClientRect(), g = (btn.parentElement.querySelector('summary') || btn).getBoundingClientRect();
    dlg.style.top = `${b.bottom + scrollY + POP_GAP_PX}px`;
    dlg.style.left = `${Math.max(scrollX + 16, g.right + scrollX - dlg.offsetWidth)}px`;
  }

  // ---- the phone panel's motion ----

  // A spring as a CSS linear() curve (Safari 17.2+, Chrome 113+), with its length in ms.
  function spring([zeta, omega]) {
    const T1 = Math.min(SPRING_MAX_S, 7 / (zeta * omega)), wd = omega * Math.sqrt(1 - zeta * zeta), pts = [];
    for (let i = 0; i <= SPRING_POINTS; i++) {
      const t = T1 * i / SPRING_POINTS;
      pts.push(+(1 - Math.exp(-zeta * omega * t) * (Math.cos(wd * t) + (zeta * omega / wd) * Math.sin(wd * t))).toFixed(4));
    }
    return { css: `linear(${pts.join(',')})`, ms: Math.round(T1 * 1000) };
  }
  const LINEAR_OK = typeof CSS !== 'undefined' && CSS.supports && CSS.supports('transition-timing-function', 'linear(0, 1)');
  const springTo = (k, fallbackMs) => { const s = spring(k); return LINEAR_OK ? { ms: s.ms, ease: s.css } : { ms: fallbackMs, ease: EASE_IOS }; };

  // y: px below its place; how: null to follow the finger, else {ms, ease}. The dimming follows the panel.
  function moveSheet(y, how) {
    const H = sh.getBoundingClientRect().height || 1, anim = how && !reduced();
    sh.style.transition = anim ? `transform ${how.ms}ms ${how.ease}` : 'none';
    dim.style.transition = anim ? `opacity ${Math.min(how.ms, DIM_MAX_MS)}ms ease` : 'none';
    sh.style.transform = `translateY(${y}px)`;
    dim.style.opacity = String(Math.max(0, Math.min(1, 1 - y / H)));
  }
  const lockPage = on => { document.documentElement.classList.toggle('al-locked', on); };   // the page behind stays put
  const lockHeight = on => { sh.style.height = on ? `${sh.getBoundingClientRect().height}px` : ''; };   // Quiet hours keeps the panel's height

  let closing = 0;
  async function open() {
    clearTimeout(closing); closing = 0;
    S.view = 'main';
    render();
    if (!dlg.open) dlg.showModal();
    if (computer()) place();
    else {
      lockPage(true); sh.style.height = '';
      moveSheet(sh.getBoundingClientRect().height + OFFSCREEN_PX, null); void sh.offsetWidth;
      moveSheet(0, springTo(OPEN_SPRING, OPEN_FALLBACK_MS));
    }
    const first = dlg.querySelector('[data-act="close"]:not(.al-dim)');
    if (first) first.focus({ preventScroll: true });
    paintButton();
    await refresh();
    if (!computer() && S.st === 'online') await Promise.all([loadChoices(), checkSub()]);
    if (dlg.open && !closing) render();
  }

  // v: the release speed (px per ms) when a drag closes it.
  function close(v = 0) {
    if (!dlg.open || closing) return;
    if (computer() || reduced()) { dlg.close(); return; }
    const H = sh.getBoundingClientRect().height, at = new DOMMatrixReadOnly(getComputedStyle(sh).transform).m42;
    const ms = Math.round(Math.max(CLOSE_MS[0], Math.min(CLOSE_MS[1], (H - at) / Math.max(v, CLOSE_MIN_PX_MS))));
    moveSheet(H + OFFSCREEN_PX, { ms, ease: CLOSE_EASE });
    closing = setTimeout(() => { closing = 0; if (dlg.open) dlg.close(); }, ms + 20);
  }
  dlg.addEventListener('cancel', e => { if (!computer()) { e.preventDefault(); close(); } });   // Escape slides it down too
  dlg.addEventListener('close', () => {
    clearTimeout(closing); closing = 0;
    S.just = false; S.testFailed = ''; S.failed = ''; S.limitHit = false; S.offFailed = false; S.view = 'main';
    S.confirm = null; S.rmFailed = '';
    if (!computer()) { lockPage(false); sh.style.height = ''; }
    paintButton();
    btn.focus({ preventScroll: true });
  });

  // The drag: from the title bar always; from the rows when they are scrolled to the top and the finger goes down.
  const drag = { just: false };
  if (!computer()) {
    let g = null;   // {y0, dy, pts: [[t, y]], from: 'nav' | 'rows', claimed, decided}
    const rubber = d => -Math.min(RUBBER_MAX_PX, RUBBER_GAIN * Math.sqrt(-d));
    const start = (y, from) => { g = { y0: y, dy: 0, pts: [[performance.now(), y]], from, claimed: from === 'nav' }; };
    const move = y => {
      g.dy = y - g.y0; g.pts.push([performance.now(), y]); if (g.pts.length > SAMPLES) g.pts.shift();
      if (Math.abs(g.dy) > DRAG_PX) drag.just = true;
      moveSheet(g.dy >= 0 ? g.dy : rubber(g.dy), null);
    };
    const end = () => {
      if (!g) return;
      const [t1, y1] = g.pts[g.pts.length - 1], [t0, y0] = g.pts[0], v = (y1 - y0) / Math.max(1, t1 - t0);   // down is positive
      const H = sh.getBoundingClientRect().height, dy = g.dy; g = null;
      if (dy > 0 && (v > FLICK_PX_MS || (dy > H * CLOSE_SHARE && v > BACK_UP_PX_MS))) close(v);
      else moveSheet(0, springTo(BACK_SPRING, BACK_FALLBACK_MS));
      setTimeout(() => { drag.just = false; }, 400);   // the tap that ends a drag is not a click on what is under the finger
    };
    // the bar: pointer events (touch-action: none in the CSS, so the browser never takes the gesture)
    nav.addEventListener('pointerdown', e => { if (e.button > 0 || closing) return; start(e.clientY, 'nav'); g.id = e.pointerId; });
    nav.addEventListener('pointermove', e => {
      if (!g || g.from !== 'nav') return;
      if (!g.cap && Math.abs(e.clientY - g.y0) > CAPTURE_PX) { g.cap = true; try { nav.setPointerCapture(g.id); } catch (err) { /* gone */ } }
      move(e.clientY);
    });
    nav.addEventListener('pointerup', end); nav.addEventListener('pointercancel', end);
    // the rows: touch events, so the list still scrolls; the first move decides (down at the top: the panel; else the list)
    sc.addEventListener('touchstart', e => { if (closing) return; start(e.touches[0].clientY, 'rows'); g.decided = false; }, { passive: true });
    sc.addEventListener('touchmove', e => {
      if (!g || g.from !== 'rows') return;
      const y = e.touches[0].clientY, d = y - g.y0;
      if (!g.decided) {
        if (Math.abs(d) < 1) { e.preventDefault(); return; }   // no direction yet: hold the gesture rather than let the page take it
        g.decided = true; g.claimed = d > 0 && sc.scrollTop <= 0;
      }
      if (g.claimed) { e.preventDefault(); if (d >= 0) move(y); else { moveSheet(0, null); sc.scrollTop = -d; } }
      else if (sc.scrollHeight <= sc.clientHeight + 1) e.preventDefault();   // a list too short to scroll: the page behind never moves
    }, { passive: false });
    sc.addEventListener('touchend', () => { if (g && g.from === 'rows') { if (g.claimed) end(); else g = null; } });
    sc.addEventListener('touchcancel', () => { if (g && g.claimed) end(); g = null; });
  }

  // This phone's choices as the server keeps them: they win over the copy in localStorage, which can be lost or old.
  // A read that started before the latest acknowledged change, or answers while a change is waiting or on its way, is
  // older than what the panel holds: it is dropped (acked counts the acknowledged PATCHes; the registration must match).
  async function loadChoices() {
    const reg = S.reg;
    if (!reg || !registered() || S.want || sending) return;
    const start = acked;
    const current = () => S.reg === reg && acked === start && !S.want && !sending;
    try {
      const r = await call('POST', `/api/alerts/phones/${encodeURIComponent(reg.id)}/choices`, { secret: reg.secret });
      if (r.status === 403) { if (S.reg === reg) { S.reg = null; S.want = null; save(); await refresh(); } return; }
      if (!r.ok) return;
      const d = await r.json();
      if (current() && Array.isArray(d.muted) && Array.isArray(d.quiet)) { reg.muted = d.muted; reg.quiet = d.quiet; save(); }
    } catch (e) { /* the panel shows the copy it has */ }
  }

  // Whether this browser still holds a push subscription: then the switch sends it again (the server keeps the phone's
  // row and its choices, with a new id and secret), which works even with every place taken.
  async function checkSub() {
    S.hasSub = false;
    if (S.reg || !installed() || !pushOk() || perm() !== 'granted') return;
    try {
      const reg = await within(navigator.serviceWorker.ready, SUB_CHECK_TIMEOUT_MS);
      S.hasSub = !!(await reg.pushManager.getSubscription());
    } catch (e) { /* no answer: treated as none */ }
  }

  // ---- what the rows do ----

  function b64url(s) {
    const p = '='.repeat((4 - (s.length % 4)) % 4), raw = atob((s + p).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, ch => ch.charCodeAt(0));
  }
  const within = (promise, ms) => Promise.race([promise, new Promise((_, no) => setTimeout(() => no(new Error('timeout')), ms))]);

  // The subscription made for this server's key: the browser's own one, when it was made for that key (sending its
  // address again replaces this phone's row instead of taking a new place), else a new one.
  function madeForKey(sub) {
    try {
      const k = sub.options && sub.options.applicationServerKey;
      if (!k) return true;   // a browser that doesn't say: Home Air's key never changes (setup makes it once)
      const a = new Uint8Array(k), b = b64url(S.key);
      return a.length === b.length && a.every((v, i) => v === b[i]);
    } catch (e) { return true; }
  }
  async function subscribe() {
    const reg = await navigator.serviceWorker.ready;
    const old = await reg.pushManager.getSubscription();
    if (old && madeForKey(old)) return old;
    if (old) await old.unsubscribe().catch(() => {});
    return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64url(S.key) });
  }

  // Called straight from the tap: iOS shows its prompt only when requestPermission runs in the tap itself, before any wait.
  function turnOn() {
    if (S.busy || computer() || S.st !== 'online' || !installed()) return;   // a phone's Home Screen app, state "online" only
    let asked;
    try {
      asked = Notification.permission === 'granted' ? Promise.resolve('granted') : Promise.resolve(Notification.requestPermission());
    } catch (e) { asked = Promise.reject(e); }
    S.busy = 'on'; S.failed = ''; S.limitHit = false; S.offFailed = false; render();
    asked.then(async answer => {
      if (answer !== 'granted') return;   // refused: the panel says where to allow it; dismissed: the switch again
      if (!S.key) await refresh();
      if (!S.key) throw new Error('no key');
      const sub = await within(subscribe(), SUBSCRIBE_TIMEOUT_MS);
      const r = await call('POST', '/api/alerts/phones', { subscription: sub.toJSON() });
      if (r.status === 201) {
        const d = await r.json();
        S.reg = { id: d.id, secret: d.secret, muted: [], quiet: QUIET }; save();
        S.want = null; S.just = true; S.sentTest = !!d.sent; S.hasSub = false;
        await refresh();
        await loadChoices();   // a phone added again keeps its choices on the server: the panel shows those, not the defaults
      } else if (r.status === 409) {
        S.limitHit = true; S.hasSub = false;
        await refresh();
      } else if (r.status === 429) {
        S.failed = 'many';
      } else if (r.status === 400 && ((await r.json().catch(() => ({}))).error === 'push service')) {
        S.failed = 'browser';   // a push service Home Air doesn't send to: tapping again won't help
      } else {
        S.failed = 'yes';
        if (r.status === 410) sub.unsubscribe().catch(() => {});   // the push service dropped it: make a new one next time
      }
    }).catch(() => { S.failed = 'yes'; }).then(() => { S.busy = false; if (dlg.open) render(); else paintButton(); });
  }

  async function turnOff() {
    if (S.busy || !S.reg) return;
    S.busy = 'off'; S.offFailed = false; S.failed = ''; S.just = false; render();
    let done = false;
    try {
      const r = await call('DELETE', `/api/alerts/phones/${encodeURIComponent(S.reg.id)}`, { secret: S.reg.secret });
      if (r.status === 204 || r.status === 403) {   // 403: this phone was already removed
        done = true;
        S.reg = null; S.want = null; save(); S.testFailed = '';
        navigator.serviceWorker.ready.then(reg => reg.pushManager.getSubscription()).then(sub => sub && sub.unsubscribe()).catch(() => {});
        await refresh();
      }
    } catch (e) { /* no answer */ }
    S.offFailed = !done;   // the switch goes back on, with one line saying so
    S.busy = false; render();
  }

  // Choices go to the server one PATCH at a time, the newest wins: S.want is what the panel shows, S.reg what the server
  // acknowledged (the only copy saved). A failure shows the acknowledged choices again.
  let sending = false, acked = 0;
  function choose(change) {
    if (!S.reg) return;
    S.want = { muted: mutedOf().slice(), quiet: quietOf().slice(), ...change };
    S.just = false; render();
    sendChoices();
  }
  async function sendChoices() {
    if (sending || !S.reg || !S.want) return;
    const out = S.want, reg = S.reg;
    sending = true;
    let status = 0;
    try {
      status = (await call('PATCH', `/api/alerts/phones/${encodeURIComponent(reg.id)}`, { secret: reg.secret, muted: out.muted, quiet: out.quiet })).status;
    } catch (e) { /* no answer */ }
    sending = false;
    if (S.reg !== reg) return;   // turned off or replaced meanwhile
    if (status === 403) { S.reg = null; S.want = null; save(); await refresh(); render(); return; }
    if (status === 204) {
      reg.muted = out.muted; reg.quiet = out.quiet; acked++; save();
      if (S.want === out) S.want = null;
    } else {
      S.want = null;   // back to what the server has
    }
    render();
    if (S.want) sendChoices();
  }

  // Remove another phone from the list: no secret; the server sends that phone one notice first. A 404
  // means it was removed already.
  async function removePhone(id) {
    if (S.removing) return;
    S.removing = id; S.rmFailed = ''; render();
    let status = 0;
    try { status = (await call('DELETE', `/api/alerts/phones/${encodeURIComponent(id)}`, {})).status; } catch (e) { /* no answer */ }
    S.removing = null;
    if (status === 204 || status === 404) { S.confirm = null; await refresh(); }
    else S.rmFailed = status === 429 ? 'many' : 'yes';
    if (dlg.open) render(); else paintButton();
  }

  async function test() {
    if (S.busy || !S.reg) return;
    S.busy = 'test'; S.testFailed = ''; S.just = false; render();
    try {
      const r = await call('POST', `/api/alerts/phones/${encodeURIComponent(S.reg.id)}/test`, { secret: S.reg.secret });
      if (r.status === 410 || r.status === 403) { S.reg = null; S.want = null; save(); await refresh(); }
      else if (r.status === 429) S.testFailed = 'many';
      else if (r.status !== 202) S.testFailed = 'yes';
    } catch (e) { S.testFailed = 'yes'; }
    S.busy = false; render();
  }

  btn.addEventListener('click', () => { if (dlg.open) close(); else open(); });
  dlg.addEventListener('click', e => {
    if (drag.just) { drag.just = false; e.preventDefault(); return; }   // a drag that started on a row is not a tap on it
    if (e.target === dlg) {   // the computer panel's own padding, or its backdrop: a click outside the panel closes it
      const r = dlg.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) close();
      return;
    }
    const t = e.target.closest('[data-act]');
    if (!t || t.disabled) return;
    const a = t.dataset.act;
    if (a === 'close') close();
    else if (a === 'toggle') { if (registered()) turnOff(); else turnOn(); }
    else if (a.startsWith('item:')) { const k = a.slice(5), m = mutedOf(); choose({ muted: m.includes(k) ? m.filter(x => x !== k) : [...m, k] }); }
    else if (a === 'quiet') {
      lockHeight(true); S.view = 'quiet'; render('in');
      const back = dlg.querySelector('[data-act="back"]'); if (back) back.focus({ preventScroll: true });
    } else if (a === 'back') {
      S.view = 'main'; render('out'); lockHeight(false);
      const q = dlg.querySelector('[data-act="quiet"]'); if (q) q.focus({ preventScroll: true });
    } else if (a === 'test') test();
    else if (a.startsWith('rm:')) {   // asks once: the row now offers Cancel, focused, and Remove
      S.confirm = a.slice(3); S.rmFailed = ''; render();
      const c = dlg.querySelector('[data-act="rmno"]'); if (c) c.focus({ preventScroll: true });
    } else if (a === 'rmno') {
      const id = S.confirm; S.confirm = null; render();
      const r = id && dlg.querySelector(`[data-act="rm:${CSS.escape(id)}"]`); if (r) r.focus({ preventScroll: true });
    } else if (a.startsWith('rmyes:')) removePhone(a.slice(6));
  });
  dlg.addEventListener('change', e => {
    const sel = e.target.closest('select[data-q]');
    if (!sel) return;
    const q = quietOf().slice(); q[+sel.dataset.q] = +sel.value;
    choose({ quiet: q });
  });
  addEventListener('resize', () => { if (dlg.open && computer()) place(); });

  // A tapped notification, when the service worker can't move this window itself (WindowClient.navigate missing or
  // refused, as on some iPhones): it posts {type: "open", url} and the page goes to that place on itself.
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', e => {
      const d = e.data;
      if (!d || d.type !== 'open' || typeof d.url !== 'string' || (e.origin && e.origin !== location.origin)) return;
      let u;
      try { u = new URL(d.url, location.href); } catch (err) { return; }
      if (u.origin !== location.origin) return;
      if (dlg.open) dlg.close();
      if (u.pathname !== location.pathname) { location.assign(u.href); return; }
      if (location.hash === u.hash) { const el = u.hash && document.getElementById(u.hash.slice(1)); if (el) el.scrollIntoView(); }
      else location.hash = u.hash;
    });
    try { navigator.serviceWorker.startMessages(); } catch (e) { /* older browsers start them with the listener */ }
  }

  paintButton();
  if (S.st === 'online' && (computer() || S.reg)) refresh().then(paintButton);   // the computer's phone count; this phone still listed
})(typeof window !== 'undefined' ? window : globalThis);
