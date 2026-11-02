// Actual login script with synthetic sessions; no server or browser profile.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../dashboard/static/login.html'), 'utf8');
const code = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function login(fetch, remembered = false) {
  const local = new Map(remembered ? [['jarvis_device_token', 'fixture-device-token']] : []);
  const session = new Map();
  const elements = new Map();
  const document = { getElementById(id) {
    if (!elements.has(id)) elements.set(id, { value: '', textContent: '', style: {}, focus() {},
      addEventListener() {}, classList: { add() {}, remove() {} } });
    return elements.get(id);
  } };
  const store = map => ({ getItem: key => map.get(key) || null,
    setItem: (key, value) => map.set(key, String(value)), removeItem: key => map.delete(key) });
  const location = { href: '', replace(value) { this.href = value; } };
  const context = vm.createContext({ fetch, document, location,
    localStorage: store(local), sessionStorage: store(session) });
  vm.runInContext(code, context);
  return { local, session, elements, location, run: source => vm.runInContext(source, context) };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('rapid login attempts use one PIN request and preserve stable device identity', async () => {
  let release, calls = 0;
  const page = login(() => { calls++; return new Promise(resolve => { release = resolve; }); });
  page.elements.get('key').value = 'ABC234';
  const attempts = page.run('Promise.all([doLogin(), doLogin()])');
  assert.equal(calls, 1);
  release({ status: 200, json: async () => ({ ok: true, token: 'fixture-token',
    device_id: 'fixture-device', device_token: 'fixture-device-token' }) });
  await attempts;
  assert.equal(page.session.get('jarvis_device_id'), 'fixture-device');
  assert.equal(page.local.get('jarvis_device_token'), 'fixture-device-token');
  assert.equal(page.location.href, '/');
});

test('offline reconnect preserves the device credential and displays recovery', async () => {
  const page = login(async () => { throw new Error('offline'); }, true);
  await flush();
  assert.equal(page.local.get('jarvis_device_token'), 'fixture-device-token');
  assert.match(page.elements.get('err').textContent, /preservada/);
  assert.equal(page.run('_loginPending'), false);
});

test('503 reconnect preserves authorization rather than treating it as revoked', async () => {
  const page = login(async () => ({ status: 503, json: async () => ({ ok: false }) }), true);
  await flush();
  assert.equal(page.local.get('jarvis_device_token'), 'fixture-device-token');
  assert.match(page.elements.get('err').textContent, /tente novamente/);
});

test('explicitly rejected credential is removed and PIN form remains available', async () => {
  const page = login(async () => ({ status: 401, json: async () => ({ ok: false }) }), true);
  await flush();
  assert.equal(page.local.has('jarvis_device_token'), false);
  assert.equal(page.elements.get('reconnecting').style.display, 'none');
});

test('device reconnect rotates bearer while retaining canonical device identity', async () => {
  const page = login(async () => ({ status: 200, json: async () => ({ ok: true,
    token: 'fixture-rotated-token', key: 'fixture-key', device_id: 'fixture-device' }) }), true);
  await flush();
  assert.equal(page.session.get('jarvis_device_id'), 'fixture-device');
  assert.equal(page.session.get('jarvis_token'), 'fixture-rotated-token');
  assert.equal(page.local.get('jarvis_device_token'), 'fixture-device-token');
});
