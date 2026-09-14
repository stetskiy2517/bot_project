const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('web/location.js', 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

async function scenario(failFirst) {
  const events = new Map();
  const calls = [];
  const cleared = [];
  let fail = failFirst;
  let positionCallback = null;
  const position = {coords: {latitude: 55.78, longitude: 37.63, accuracy: 25}};
  const document = {
    visibilityState: 'visible',
    addEventListener(name, callback) {events.set(name, callback);},
  };
  const context = vm.createContext({
    Date,
    JSON,
    Math,
    navigator: {
      permissions: {query: async () => ({state: 'granted'})},
      geolocation: {
        watchPosition(success) {
          positionCallback = success;
          success(position);
          return 41;
        },
        clearWatch(id) {cleared.push(id);},
      },
    },
    document,
    window: {PlannerRequests: {request: async (path, options) => {
      calls.push({path, options});
      if (fail) {fail = false; throw Error('csrf_failed');}
      return {ok: true};
    }}},
    fetch() {throw Error('Location must use the authenticated request helper');},
  });
  vm.runInContext(source, context);
  await flush();
  await flush();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, '/api/location');
  assert.equal(calls[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(calls[0].options.body), {latitude: 55.78, longitude: 37.63, accuracy: 25});

  if (failFirst) {
    positionCallback(position);
    await flush();
    await flush();
    assert.equal(calls.length, 2);
  }

  document.visibilityState = 'hidden';
  events.get('visibilitychange')();
  assert.deepEqual(cleared, [41]);
}

(async () => {
  await scenario(false);
  await scenario(true);
  console.log('Location client tests passed (2 scenarios)');
})().catch(error => {console.error(error); process.exitCode = 1;});
