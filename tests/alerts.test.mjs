// The computer's Alerts panel draws a QR code for this page's address (static/alerts.js). A wrong module still draws a
// code-like picture, so the codes are pinned to Nayuki's reference encoder (qrcodegen, byte mode, level M), each row as hex.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { qrMatrix } = require('../static/alerts.js');
const rows = m => m.map(r => BigInt('0b' + r.map(v => (v ? '1' : '0')).join('')).toString(16));

const REFERENCE = [
  ['https://home-air.up.railway.app/', undefined, '1fcc9e7f 105c7b41 1748d15d 1744e65d 17594e5d 104d8e41 1fd5557f fd800 15456812 1fa46de9 a5189b7 11ae2d02 1ad61bcb 1a81b8c9 cd0757b a27de1a 1bd178cb a8165cd 14c68423 a911f1a 13443df0 1e8117 1fc25b5b 1047e91a 175249f1 17435b37 175c16b9 1048afa2 1fd38903'],
  ['https://a.b/', 5, '1fc17f 105041 17555d 17565d 174b5d 104d41 1fd57f 1300 1059ce 1d1d8e 1546e6 10b4bc 19f482 191d 1fcda6 10464d 17427b 1740f4 174fbf 1040bc 1fd16a'],
  ['https://my-home-air-online-with-a-long-name.example.com/', undefined, '1fd8d7a7f 105790741 174b1535d 17555a75d 174e42d5d 1045ce041 1fd55557f 124a500 16e65ca4b 4b41f26d 1e74542b9 1c9fe7428 36b2a7b8 f1d13006 4d777168 198dd5ccc 65d75adc f19a87db 1350f08f6 1a3c9f713 3646aa0d 13bd2f249 64d1979b a23d712a 10c16b7f1 1122b18 1fd34f352 105bf0d1c 174dcbff4 175b0922d 1756488e8 104639771 1fdbfea14'],
];

test('the QR code matches the reference encoder, mask chosen or forced', () => {
  for (const [text, mask, want] of REFERENCE) assert.deepEqual(rows(qrMatrix(text, mask)), want.split(' '), text);
});

test('an address too long for version 6 gets no QR code', () => {
  assert.equal(qrMatrix('https://' + 'x'.repeat(100) + '/'), null);
  assert.equal(qrMatrix('https://' + 'x'.repeat(97) + '/').length, 41);   // 106 bytes: the most version 6 holds
});

// The Phones list (static/alerts.js addedText): the time added shows only when two phones share a name and a day, so the
// person can tell which one to remove. "25 Nov" and "2:02 AM" never break inside (non-breaking spaces).
const { addedText } = require('../static/alerts.js');
const plain = (p, ps) => addedText(p, ps).replace(/\u00a0/g, ' ');

test('a phone shows its day, and its time too when another phone has the same name and day', () => {
  const a = { id: 'a', device: 'iPhone', since: '2026-11-25', time: '02:02' };
  const b = { id: 'b', device: 'iPhone', since: '2026-11-25', time: '14:30' };
  const c = { id: 'c', device: 'Android phone', since: '2026-11-25', time: '00:05' };
  const d = { id: 'd', device: 'iPhone', since: '2026-11-24', time: '12:00' };
  const all = [a, b, c, d];
  assert.equal(addedText(a, all), '25\u00a0Nov, 2:02\u00a0AM');
  assert.equal(plain(b, all), '25 Nov, 2:30 PM');
  assert.equal(plain(c, all), '25 Nov');   // the only Android phone that day
  assert.equal(plain(d, all), '24 Nov');   // another day
  assert.equal(plain(a, [a, d]), '25 Nov');
  assert.equal(plain({ ...a, time: undefined }, all), '25 Nov');   // no time from the server: the day alone
  assert.equal(plain({ id: 'e', device: 'Phone', since: '2026-01-01', time: '00:00' }, [{ id: 'f', device: 'Phone', since: '2026-01-01', time: '12:00' }]), '1 Jan, 12:00 AM');
});
