// Runs the actual inline browser code with controlled browser APIs. No network,
// audio hardware, credentials or third-party Node dependencies are used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.value = ''; this.children = []; this.dataset = {};
    this.style = {}; this.className = ''; this.textContent = ''; this.innerHTML = '';
    this.listeners = {}; this.disabled = false;
    const classes = new Set();
    this.classList = {
      add: value => classes.add(value), remove: value => classes.delete(value),
      toggle: (value, on) => on ? classes.add(value) : classes.delete(value),
      contains: value => classes.has(value),
    };
  }
  appendChild(child) { this.children.push(child); return child; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setAttribute(name, value) { this[name] = value; }
}

function browser(options = {}) {
  const elements = new Map(), timers = new Map(), events = {};
  const resources = { streams: [], contexts: [], sockets: [], nodes: [], urls: new Set(), requests: [] };
  let timerId = 0, uuid = 0;
  const storage = options.storage || new Map([
    ['jarvis_token', 'fixture-token'], ['jarvis_device_id', 'fixture-device'],
  ]);
  const element = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const makeStream = () => {
    const tracks = [0, 1].map(() => ({ stopped: 0, stop() { this.stopped++; } }));
    const stream = { getTracks: () => tracks };
    resources.streams.push(stream);
    return stream;
  };
  const makeNode = () => {
    const node = { port: {}, disconnected: 0, connect() {}, disconnect() { this.disconnected++; } };
    resources.nodes.push(node); return node;
  };
  class Socket {
    constructor(url) {
      if (options.socketFailure && url.includes('phone-audio')) throw new Error('fixture socket failure');
      this.url = url; this.readyState = 0; this.closed = 0; this.sent = [];
      resources.sockets.push(this);
    }
    async open() { this.readyState = 1; if (this.onopen) await this.onopen(); }
    close() { this.closed++; this.readyState = 3; if (this.onclose) this.onclose({}); }
    send(data) { this.sent.push(data); }
  }
  class Context {
    constructor() {
      if (options.contextFailure) throw new Error('fixture context failure');
      this.state = options.resume ? 'suspended' : 'running'; this.sampleRate = 16000;
      this.closed = 0; this.destination = {};
      this.audioWorklet = { addModule: () => options.module ? options.module.promise : Promise.resolve() };
      resources.contexts.push(this);
    }
    resume() { return options.resume.promise; }
    close() { this.closed++; return Promise.resolve(); }
    createMediaStreamSource() { return makeNode(); }
    createScriptProcessor() {
      if (options.processorFailure) throw new Error('fixture processor failure');
      return makeNode();
    }
  }
  const sandbox = {
    document: { getElementById: element, createElement: tag => new Element(tag) },
    window: { addEventListener: (name, fn) => {
      const previous = events[name]; events[name] = previous ? () => { previous(); fn(); } : fn;
    } },
    sessionStorage: {
      getItem(key) {
        if (options.storageReadFailure) throw new Error('fixture private storage failure');
        return storage.get(key) || null;
      },
      setItem(key, value) {
        if (options.storageFailure) throw new Error('fixture private quota failure');
        storage.set(key, value);
      },
    },
    location: { protocol: 'https:', host: 'fixture.invalid', replace() {} },
    navigator: { mediaDevices: { getUserMedia() {
      resources.permissionCalls = (resources.permissionCalls || 0) + 1;
      return options.permission ? options.permission.promise : Promise.resolve(makeStream());
    } } },
    WebSocket: Socket, AudioContext: Context,
    AudioWorkletNode: function () { return makeNode(); },
    URL: {
      createObjectURL() { const url = 'blob:' + resources.urls.size; resources.urls.add(url); return url; },
      revokeObjectURL(url) { resources.urls.delete(url); },
    },
    Blob, Uint8Array, Int16Array, Float32Array, AbortController,
    crypto: { randomUUID: () => `00000000-0000-4000-8000-${String(++uuid).padStart(12, '0')}` },
    setTimeout: (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId; },
    clearTimeout: id => timers.delete(id),
    fetch: async (url, init) => {
      resources.requests.push({ url, init });
      if (options.fetch) return options.fetch(url, init);
      const body = JSON.parse(init.body || '{}');
      return { ok: true, status: 200, json: async () => ({ ok: true, id: body.id, status: 'delivered' }) };
    },
    console, encodeURIComponent, btoa,
  };
  const context = vm.createContext(sandbox);
  const source = fs.readFileSync(process.env.CHLOE_WEB_SOURCE || path.join(__dirname, '../dashboard/static/app.html'), 'utf8');
  vm.runInContext(source.match(/<script>\s*([\s\S]*?)<\/script>/)[1], context);
  return {
    run: code => vm.runInContext(code, context), element, resources, events, timers, makeStream, storage,
    voiceSockets: () => resources.sockets.filter(socket => socket.url.includes('phone-audio')),
    fireTimer(delay) {
      const found = [...timers].find(([, timer]) => timer.delay === delay);
      assert.ok(found, `timer ${delay} exists`); timers.delete(found[0]); found[1].fn();
    },
  };
}

function assertReleased(app) {
  for (const stream of app.resources.streams)
    for (const track of stream.getTracks()) assert.equal(track.stopped, 1);
  for (const context of app.resources.contexts) assert.equal(context.closed, 1);
  for (const socket of app.voiceSockets()) assert.equal(socket.closed, 1);
  for (const node of app.resources.nodes) assert.ok(node.disconnected >= 1);
  assert.equal(app.resources.urls.size, 0);
}

test('microphone double click cancels one pending permission and closes late tracks', async () => {
  const permission = deferred(), app = browser({ permission });
  const starting = app.run('doMic()');
  await app.run('doMic()');
  await app.run('doMic()');
  assert.equal(app.resources.permissionCalls, 1);
  assert.equal(app.element('mic-btn').dataset.state, 'stopping');
  permission.resolve(app.makeStream()); await starting;
  assertReleased(app);
  assert.equal(app.resources.contexts.length, 0);
  assert.equal(app.element('mic-btn').dataset.state, 'idle');
});

test('permission denial gives a visible error and permits another attempt', async () => {
  const permission = deferred(), app = browser({ permission });
  const starting = app.run('doMic()');
  permission.reject(Object.assign(new Error('fixture'), { name: 'NotAllowedError' }));
  await starting;
  assert.equal(app.element('mic-btn').dataset.state, 'error');
  assert.ok(app.element('feed').children.some(child => child.textContent.includes('Permissão')));
  assertReleased(app);
});

test('repeated microphone cycles release all streams, contexts, sockets and nodes', async () => {
  const app = browser();
  for (let i = 0; i < 4; i++) {
    await app.run('doMic()'); await app.voiceSockets().at(-1).open();
    assert.equal(app.element('mic-btn').dataset.state, 'recording');
    await app.run('doMic()');
    assert.equal(app.element('mic-btn').dataset.state, 'idle');
  }
  assertReleased(app);
});

test('stop during audio context resume cannot open a late socket', async () => {
  const resume = deferred(), app = browser({ resume });
  const starting = app.run('doMic()'); await Promise.resolve();
  app.run('_stopVoice()'); resume.resolve(); await starting;
  assert.equal(app.voiceSockets().length, 0); assertReleased(app);
});

test('stop during worklet initialization releases blob and cannot reactivate capture', async () => {
  const module = deferred(), app = browser({ module });
  await app.run('doMic()');
  const opening = app.voiceSockets()[0].open(); await Promise.resolve();
  app.run('_stopVoice()'); module.resolve(); await opening;
  assert.equal(app.element('mic-btn').dataset.state, 'idle'); assertReleased(app);
});

test('callback from old voice connection does not stop the newer session', async () => {
  const app = browser(); await app.run('doMic()');
  const oldClose = app.voiceSockets()[0].onclose;
  app.run('_stopVoice()'); await app.run('doMic()');
  const current = app.voiceSockets()[1]; await current.open(); oldClose({});
  assert.equal(current.closed, 0);
  assert.equal(app.element('mic-btn').dataset.state, 'recording');
  app.run('_stopVoice()'); assertReleased(app);
});

for (const option of ['contextFailure', 'socketFailure']) {
  test(`microphone ${option} closes resources and reports recoverable error`, async () => {
    const app = browser({ [option]: true }); await app.run('doMic()');
    assert.equal(app.element('mic-btn').dataset.state, 'error'); assertReleased(app);
  });
}

test('voice connection timeout closes capture', async () => {
  const app = browser(); await app.run('doMic()'); app.fireTimer(10000);
  assert.equal(app.element('mic-btn').dataset.state, 'error'); assertReleased(app);
});

test('pagehide stops microphone and pending permission cannot later reactivate it', async () => {
  const permission = deferred(), app = browser({ permission });
  const starting = app.run('doMic()'); app.events.pagehide();
  permission.resolve(app.makeStream()); await starting; assertReleased(app);
});

test('removing active microphone stops the voice session', async () => {
  const app = browser(); await app.run('doMic()'); await app.voiceSockets()[0].open();
  app.resources.streams[0].getTracks()[0].onended();
  assert.equal(app.element('mic-btn').dataset.state, 'error'); assertReleased(app);
});

test('failed worklet falls back to the existing PCM processor and cleans it up', async () => {
  const module = deferred(), app = browser({ module });
  await app.run('doMic()'); const opening = app.voiceSockets()[0].open();
  module.reject(new Error('fixture worklet failure')); await opening;
  assert.equal(app.element('mic-btn').dataset.state, 'recording');
  app.run('_stopVoice()'); assertReleased(app);
});

test('failure of both audio processors closes the capture', async () => {
  const module = deferred(), app = browser({ module, processorFailure: true });
  await app.run('doMic()'); const opening = app.voiceSockets()[0].open();
  module.reject(new Error('fixture worklet failure')); await opening;
  assert.equal(app.element('mic-btn').dataset.state, 'error'); assertReleased(app);
});

function cards(app) {
  return app.element('feed').children.filter(child => child.dataset.commandId);
}

function response(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

test('command stays pending until real delivery acknowledgment and double click sends once', async () => {
  const delivery = deferred(), app = browser({ fetch: () => delivery.promise });
  app.element('inp').value = 'fixture message';
  const sending = app.run('doSend()'); await app.run('doSend()');
  assert.equal(app.resources.requests.length, 1);
  assert.equal(app.element('inp').value, 'fixture message');
  assert.equal(cards(app)[0].dataset.state, 'pending');
  assert.equal(app.element('send-btn').disabled, true);
  const body = JSON.parse(app.resources.requests[0].init.body);
  delivery.resolve(response(200, { ok: true, id: body.id, status: 'delivered' }));
  await sending;
  assert.equal(app.element('inp').value, '');
  assert.equal(cards(app).length, 1);
  assert.equal(cards(app)[0].dataset.state, 'delivered');
  assert.equal(app.element('send-btn').disabled, false);
});

test('delivery of the previous message preserves the next edited draft', async () => {
  const delivery = deferred(), app = browser({ fetch: () => delivery.promise });
  app.element('inp').value = 'first fixture'; const sending = app.run('doSend()');
  app.element('inp').value = 'next fixture';
  const body = JSON.parse(app.resources.requests[0].init.body);
  delivery.resolve(response(200, { ok: true, id: body.id, status: 'delivered' }));
  await sending;
  assert.equal(app.element('inp').value, 'next fixture');
});

test('HTTP 503 retains draft and retry reuses ID and timestamp without a duplicate card', async () => {
  let attempts = 0;
  const app = browser({ fetch: async (_, init) => {
    const body = JSON.parse(init.body);
    return ++attempts === 1 ? response(503, { ok: false, status: 'failed', id: body.id })
      : response(200, { ok: true, status: 'delivered', id: body.id });
  } });
  app.element('inp').value = 'fixture retry'; await app.run('doSend()');
  assert.equal(app.element('inp').value, 'fixture retry');
  assert.equal(cards(app)[0].dataset.state, 'failed');
  assert.ok(cards(app)[0].children[2].textContent.includes('preservado'));
  await cards(app)[0].children[3].listeners.click();
  const bodies = app.resources.requests.map(request => JSON.parse(request.init.body));
  assert.equal(bodies[0].id, bodies[1].id);
  assert.equal(bodies[0].created_at, bodies[1].created_at);
  assert.equal(cards(app).length, 1);
  assert.equal(cards(app)[0].dataset.state, 'delivered');
  assert.equal(app.element('inp').value, '');
});

test('network disconnection gives visible unconfirmed state and retry keeps the same identity', async () => {
  let attempts = 0;
  const app = browser({ fetch: async (_, init) => {
    if (++attempts === 1) throw new Error('private fixture transport detail');
    const body = JSON.parse(init.body);
    return response(200, { ok: true, status: 'delivered', id: body.id });
  } });
  app.element('inp').value = 'fixture offline'; await app.run('doSend()');
  assert.equal(app.element('inp').value, 'fixture offline');
  assert.equal(cards(app)[0].dataset.state, 'unknown');
  assert.ok(cards(app)[0].children[2].textContent.includes('não confirmada'));
  assert.ok(!cards(app)[0].children[2].textContent.includes('private'));
  await app.run('doSend()');
  const bodies = app.resources.requests.map(request => JSON.parse(request.init.body));
  assert.equal(bodies[0].id, bodies[1].id);
  assert.equal(cards(app).length, 1);
});

test('command timeout aborts the request but preserves draft and does not claim non-delivery', async () => {
  const app = browser({ fetch: (_, init) => new Promise((_, reject) => {
    init.signal.addEventListener('abort', () => reject(new Error('fixture abort')));
  }) });
  app.element('inp').value = 'fixture timeout'; const sending = app.run('doSend()');
  app.fireTimer(15000); await sending;
  assert.equal(app.element('inp').value, 'fixture timeout');
  assert.equal(cards(app)[0].dataset.state, 'unknown');
  assert.equal(app.element('send-btn').disabled, false);
});

for (const status of ['pending', 'unknown']) {
  test(`server ${status} result remains unconfirmed across same-ID status queries`, async () => {
    const app = browser({ fetch: async (_, init) => {
      const body = JSON.parse(init.body); return response(202, { ok: false, id: body.id, status });
    } });
    app.element('inp').value = 'fixture uncertain'; await app.run('doSend()'); await app.run('doSend()');
    assert.equal(app.element('inp').value, 'fixture uncertain');
    assert.equal(cards(app)[0].dataset.state, 'unknown');
    const bodies = app.resources.requests.map(request => JSON.parse(request.init.body));
    assert.equal(bodies[0].id, bodies[1].id);
    assert.equal(cards(app).length, 1);
  });
}

test('revoked authorization gives recovery message without discarding the draft', async () => {
  const app = browser({ fetch: async () => response(401, { error: 'fixture private detail' }) });
  app.element('inp').value = 'fixture auth'; await app.run('doSend()');
  assert.equal(app.element('inp').value, 'fixture auth');
  assert.equal(cards(app)[0].dataset.state, 'failed');
  assert.ok(cards(app)[0].children[2].textContent.includes('Reconecte'));
  assert.ok(!cards(app)[0].children[2].textContent.includes('fixture private'));
});

test('unmatched or malformed success response cannot confirm delivery', async () => {
  const app = browser({ fetch: async () => response(200, { ok: true, id: 'wrong-id', status: 'delivered' }) });
  app.element('inp').value = 'fixture malformed'; await app.run('doSend()');
  assert.equal(app.element('inp').value, 'fixture malformed');
  assert.equal(cards(app)[0].dataset.state, 'unknown');
});

test('empty draft sends nothing and a legitimate repeated message receives a new identity', async () => {
  const app = browser(); app.element('inp').value = '   '; await app.run('doSend()');
  assert.equal(app.resources.requests.length, 0);
  app.element('inp').value = 'fixture repeat'; await app.run('doSend()');
  app.element('inp').value = 'fixture repeat'; await app.run('doSend()');
  const bodies = app.resources.requests.map(request => JSON.parse(request.init.body));
  assert.notEqual(bodies[0].id, bodies[1].id);
  assert.equal(cards(app).length, 2);
});

const OUTBOX_KEY = 'chloe_command_outbox_v1';
function recoveryNotice(app) {
  return app.element('feed').children.find(child => child.id === 'recovery-notice');
}

test('reload after uncertain delivery restores draft and card, and consults the original identity', async () => {
  const first = browser({ fetch: async (_, init) => {
    const body = JSON.parse(init.body); return response(202, { ok: false, id: body.id, status: 'unknown' });
  } });
  first.element('inp').value = 'fixture recover'; await first.run('doSend()');
  first.resources.sockets[0].onclose({});
  const original = JSON.parse(first.resources.requests[0].init.body);
  const restored = browser({ storage: first.storage });
  assert.equal(restored.element('inp').value, 'fixture recover');
  assert.equal(cards(restored).length, 1);
  assert.equal(cards(restored)[0].dataset.state, 'unknown');
  assert.equal(restored.resources.requests.length, 0);
  await cards(restored)[0].children[3].listeners.click();
  const queried = JSON.parse(restored.resources.requests[0].init.body);
  assert.equal(queried.id, original.id);
  assert.equal(queried.created_at, original.created_at);
  assert.equal(cards(restored).length, 1);
});

test('navigation during pending transport restores as unknown without automatic replay', async () => {
  const delivery = deferred(), first = browser({ fetch: () => delivery.promise });
  first.element('inp').value = 'fixture interrupted'; first.run('doSend()'); first.events.pagehide();
  const restored = browser({ storage: first.storage });
  assert.equal(restored.element('inp').value, 'fixture interrupted');
  assert.equal(cards(restored)[0].dataset.state, 'unknown');
  assert.equal(restored.resources.requests.length, 0);
});

test('multiple old uncertainties remain recoverable and capacity blocks a new attempt', async () => {
  const first = browser({ fetch: async (_, init) => {
    const body = JSON.parse(init.body); return response(202, { ok: false, id: body.id, status: 'unknown' });
  } });
  for (let i = 0; i < 20; i++) {
    first.element('inp').value = `fixture uncertain ${i}`; await first.run('doSend()');
  }
  first.element('inp').value = 'fixture twenty first'; await first.run('doSend()');
  assert.equal(first.resources.requests.length, 20);
  assert.ok(recoveryNotice(first).children[0].textContent.includes('20 envios'));
  const restored = browser({ storage: first.storage });
  assert.equal(cards(restored).length, 20);
  await cards(restored)[0].children[3].listeners.click();
  const original = JSON.parse(first.resources.requests[0].init.body);
  const queried = JSON.parse(restored.resources.requests[0].init.body);
  assert.equal(original.id, queried.id);
  assert.equal(original.created_at, queried.created_at);
});

test('storage quota failure keeps RAM draft, shows error and prevents an unsafe new request', async () => {
  const app = browser({ storageFailure: true });
  app.element('inp').value = 'fixture storage failure'; await app.run('doSend()');
  assert.equal(app.element('inp').value, 'fixture storage failure');
  assert.equal(app.resources.requests.length, 0);
  assert.ok(recoveryNotice(app).children[0].textContent.includes('mantenha a aba aberta'));
  assert.equal(cards(app)[0].dataset.state, 'failed');
});

test('unreadable session storage produces a visible recovery error without losing RAM draft', async () => {
  const app = browser({ storageReadFailure: true });
  app.element('inp').value = 'fixture readable RAM'; await app.run('doSend()');
  assert.equal(app.element('inp').value, 'fixture readable RAM');
  assert.equal(app.resources.requests.length, 0);
  assert.ok(recoveryNotice(app).children[0].textContent.includes('recuperação'));
});

test('expired recovery drops retained contents and blocks new sends until explicit acknowledgment', async () => {
  const old = Date.now() / 1000 - 25 * 3600;
  const storage = new Map([
    ['jarvis_token', 'fixture-token'], ['jarvis_device_id', 'fixture-device'],
    [OUTBOX_KEY, JSON.stringify({ version: 1, scope: 'device:fixture-device',
      draft: 'fixture old draft', draftUpdatedAt: old,
      commands: [{ id: 'old-id', createdAt: old, text: 'fixture old command', status: 'unknown' }] })],
  ]);
  const app = browser({ storage });
  assert.equal(app.element('inp').value, '');
  assert.equal(cards(app).length, 0);
  const persisted = JSON.parse(storage.get(OUTBOX_KEY));
  assert.equal(persisted.commands.length, 0);
  assert.equal(persisted.draft, '');
  assert.equal(persisted.expiredHold, true);
  app.element('inp').value = 'fixture verified new'; await app.run('doSend()');
  assert.equal(app.resources.requests.length, 0);
  recoveryNotice(app).children[1].listeners.click();
  await app.run('doSend()'); assert.equal(app.resources.requests.length, 1);
});

test('record beyond server dedup TTL still queries its original timestamp', async () => {
  const created = Date.now() / 1000 - 2 * 3600;
  const storage = new Map([
    ['jarvis_token', 'fixture-token'], ['jarvis_device_id', 'fixture-device'],
    [OUTBOX_KEY, JSON.stringify({ version: 1, scope: 'device:fixture-device',
      draft: 'fixture old uncertain', draftUpdatedAt: created,
      commands: [{ id: 'two-hour-id', createdAt: created, text: 'fixture old uncertain', status: 'pending' }] })],
  ]);
  const app = browser({ storage, fetch: async (_, init) => {
    const body = JSON.parse(init.body); return response(202, { ok: false, id: body.id, status: 'unknown' });
  } });
  await app.run('doSend()');
  const body = JSON.parse(app.resources.requests[0].init.body);
  assert.equal(body.id, 'two-hour-id'); assert.equal(body.created_at, created);
  assert.equal(cards(app)[0].dataset.state, 'unknown');
});

test('different device cannot replay the previous device outbox', async () => {
  const first = browser({ fetch: async (_, init) => {
    const body = JSON.parse(init.body); return response(202, { ok: false, id: body.id, status: 'unknown' });
  } });
  first.element('inp').value = 'fixture other device'; await first.run('doSend()');
  first.storage.set('jarvis_device_id', 'different-device');
  const other = browser({ storage: first.storage });
  await other.run('doSend()'); await cards(other)[0].children[3].listeners.click();
  assert.equal(other.resources.requests.length, 0);
  assert.equal(other.element('inp').value, 'fixture other device');
  assert.equal(JSON.parse(first.storage.get(OUTBOX_KEY)).scope, 'device:fixture-device');
  assert.ok(recoveryNotice(other).children[0].textContent.includes('outro dispositivo'));
});

test('delivered command removes its text from recovery and an unsent draft survives navigation', async () => {
  const app = browser();
  app.element('inp').value = 'fixture delivered'; await app.run('doSend()');
  const saved = JSON.parse(app.storage.get(OUTBOX_KEY));
  assert.equal(saved.commands.length, 0); assert.equal(saved.draft, '');
  app.element('inp').value = 'fixture unsent'; app.element('inp').listeners.input();
  const restored = browser({ storage: app.storage });
  assert.equal(restored.element('inp').value, 'fixture unsent');
  assert.equal(restored.resources.requests.length, 0);
});

test('corrupt recovery is not silently overwritten or executed', async () => {
  const storage = new Map([['jarvis_token', 'fixture-token'], ['jarvis_device_id', 'fixture-device'], [OUTBOX_KEY, '{broken']]);
  const app = browser({ storage }); app.element('inp').value = 'fixture RAM'; await app.run('doSend()');
  assert.equal(app.resources.requests.length, 0);
  assert.equal(storage.get(OUTBOX_KEY), '{broken');
  assert.equal(app.element('inp').value, 'fixture RAM');
  assert.ok(recoveryNotice(app).children[1].hidden === false);
});

module.exports = { browser, deferred };
