const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('web/location.js', 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function makeScenario({enabled, failFirstLocation = false}) {
  const events = new Map();
  const calls = [];
  const cleared = [];
  let failLocation = failFirstLocation;
  let positionCallback = null;
  let permissionQueries = 0;
  const position = {coords: {latitude: 55.78, longitude: 37.63, accuracy: 25}};
  const documentElement = {dataset: {}};
  const document = {
    documentElement,
    visibilityState: 'visible',
    addEventListener(name, callback) {events.set(name, callback);},
    removeEventListener(name, callback) {
      if (events.get(name) === callback) events.delete(name);
    },
    getElementById() {return null;},
  };
  const context = vm.createContext({
    Date,
    JSON,
    Math,
    navigator: {
      permissions: {query: async () => {
        permissionQueries += 1;
        return {state: 'granted', addEventListener() {}};
      }},
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
      if (path === '/api/status') return {navigation: {enabled}};
      if (path === '/api/location' && failLocation) {
        failLocation = false;
        throw Error('csrf_failed');
      }
      return {ok: true};
    }}},
    fetch() {throw Error('Location must use the authenticated request helper');},
  });
  vm.runInContext(source, context);
  return {
    events, calls, cleared, document, documentElement, context,
    position, getPositionCallback: () => positionCallback,
    getPermissionQueries: () => permissionQueries,
  };
}

async function enabledScenario(failFirstLocation) {
  const scenario = makeScenario({enabled: true, failFirstLocation});
  await flush();
  await flush();
  await flush();

  assert.equal(scenario.calls[0].path, '/api/status');
  const locationCalls = scenario.calls.filter(call => call.path === '/api/location');
  assert.equal(locationCalls.length, 1);
  assert.equal(locationCalls[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(locationCalls[0].options.body), {
    latitude: 55.78, longitude: 37.63, accuracy: 25,
  });
  assert.equal(scenario.getPermissionQueries(), 1);

  if (failFirstLocation) {
    scenario.getPositionCallback()(scenario.position);
    await flush();
    await flush();
    assert.equal(scenario.calls.filter(call => call.path === '/api/location').length, 2);
  }

  scenario.document.visibilityState = 'hidden';
  scenario.events.get('visibilitychange')();
  assert.deepEqual(scenario.cleared, [41]);
}

async function disabledScenario() {
  const scenario = makeScenario({enabled: false});
  await flush();
  await flush();

  assert.equal(scenario.calls.length, 1);
  assert.equal(scenario.calls[0].path, '/api/status');
  assert.equal(scenario.getPermissionQueries(), 0);
  assert.equal(scenario.getPositionCallback(), null);
  assert.equal(scenario.documentElement.dataset.navigationEnabled, 'false');

  scenario.events.get('planner-navigation-setting')({detail: {enabled: true}});
  await flush();
  await flush();
  assert.equal(scenario.getPermissionQueries(), 1);
  assert.equal(scenario.calls.filter(call => call.path === '/api/location').length, 1);

  scenario.events.get('planner-navigation-setting')({detail: {enabled: false}});
  assert.deepEqual(scenario.cleared, [41]);
  assert.equal(scenario.documentElement.dataset.navigationEnabled, 'false');
}

(async () => {
  await enabledScenario(false);
  await enabledScenario(true);
  await disabledScenario();
  console.log('Location client tests passed (navigation opt-in scenarios)');
})().catch(error => {console.error(error); process.exitCode = 1;});
