// Deterministic checks for loss of telemetry, paused inspection, and bounded storage.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = vm.createContext({ window: {} });
vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/component-reference/components/realtime-monitor-model.js'), 'utf8'), context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/component-reference/components/realtime-monitor-plot.js'), 'utf8'), context);
const create = context.window.ComponentReference.createRealtimeMonitorModel;
const zone = context.window.ComponentReference.realtimeMonitorZone;
const channels = [
  { id: 'cpu', label: 'CPU', kind: 'metric', unit: '%', max: 100 },
  { id: 'api', label: 'API', kind: 'status' }
];
let now = 100000;
const model = create(channels, { clock: () => now, windowMs: 5000, maxSamples: 3 });
const push = (timestamp, cpu = 0, api = 'good') => model.push({ timestamp, values: { cpu, api } });
assert.equal(model.view().feed, 'Waiting for data');
assert.equal(push(now), true);
assert.equal(model.view().latest.values.cpu, 0, 'A measured zero is available');
assert.equal(model.view().feed, 'Live');
const original = model.view().latest;
assert(Object.isFrozen(original) && Object.isFrozen(original.values));
const empty = create(channels, { clock: () => now });
assert.equal(empty.push({ timestamp: -Number.MAX_VALUE, values: {} }), false, 'Invalid dates cannot reach the timestamp readout');

now += 3000;
for (const timestamp of [NaN, Infinity, now + 1, original.timestamp, original.timestamp - 1]) {
  assert.equal(push(timestamp, 99, 'danger'), false);
}
assert.equal(model.view().latest, original, 'Rejected snapshots cannot overwrite readings');
assert.equal(model.view().feed, 'Stale', 'Rejected or duplicate timestamps cannot renew freshness');
model.setConnection('connected');
assert.equal(model.view().feed, 'Stale', 'An open socket is not evidence of fresh data');
model.setConnection('disconnected');
assert.equal(model.view().feed, 'Disconnected');
assert.equal(model.view().latest.values.api, 'good', 'Transport loss cannot invent a service outage');
model.setConnection('connecting');
assert.equal(model.view().feed, 'Reconnecting');

assert.equal(push(now, 40), true);
model.pause();
const frozen = model.view();
for (let i = 0; i < 20; i++) { now += 1000; push(now, i, i === 19 ? 'danger' : 'good'); }
assert.equal(model.view().latest, frozen.latest, 'Frozen readings cannot change as data arrives');
assert.equal(model.view().end, frozen.end, 'A paused time axis cannot keep moving');
assert.equal(model.view().samples.length, frozen.samples.length);
assert.equal(model.view().feed, 'Live', 'Paused inspection still reports current feed health');
now += 3000;
assert.equal(model.view().feed, 'Stale', 'Freshness still expires while paused');
model.resume();
assert.equal(model.view().latest.values.cpu, 19);
assert.equal(model.view().latest.values.api, 'danger');
assert(model.view().samples.length <= 3, 'Pause cannot allow unbounded buffering');
now += 6000;
assert.equal(model.view().samples.length, 0, 'Stalled history ages out of the live window');
assert.equal(model.view().latest.values.cpu, 19, 'Last-known readings survive history expiry');
assert.equal(push(now, null, 'mystery'), true);
assert.equal(model.view().feed, 'Live');
assert.equal(model.view().latest.values.cpu, null);
assert.equal(model.view().latest.values.api, null);

for (const cpu of [undefined, '', '42', NaN, Infinity, -1]) {
  now += 1000;
  assert(model.push({ timestamp: now, values: { cpu, api: null } }));
  assert.equal(model.view().latest.values.cpu, null);
}
now += 1000;
model.push({ timestamp: now, values: {} });
assert.equal(model.view().latest.values.cpu, null);
assert.equal(model.view().latest.values.api, null, 'Omitted channels cannot inherit stale values');

const independent = create(channels, { clock: () => now });
assert.equal(independent.view().latest, null);
independent.push({ timestamp: now, values: { cpu: 125, api: 'caution' } });
assert.equal(independent.view().latest.values.cpu, 125, 'A plot bound must not clamp raw data');
assert.equal(independent.view().latest.values.api, 'caution');
assert.equal(model.view().latest.values.cpu, null, 'Instances must not share samples');
for (const options of [{ interval: 0 }, { windowMs: Infinity }, { maxSamples: 0 }, { staleAfter: 10 }]) {
  assert.throws(() => create(channels, options));
}
assert.equal(zone({ kind: 'metric', max: 100, warning: 80, critical: 95 }, 10), 'good');
assert.equal(zone({ kind: 'metric', max: 100, warning: 80, critical: 95 }, 80), 'caution');
assert.equal(zone({ kind: 'metric', max: 100, warning: 80, critical: 95 }, 95), 'danger');
assert.equal(zone({ kind: 'metric', max: 20, decimals: 1 }, 6.5), 'nominal');
assert.equal(zone({ kind: 'metric', max: 20 }, null), 'unknown');
assert.equal(zone({ kind: 'status' }, 'good'), 'good');
assert.equal(zone({ kind: 'status' }, null), 'unknown');
assert.throws(() => create([channels[0], channels[0]]));
assert.throws(() => create([{ ...channels[0], decimals: 100 }]));
assert.throws(() => create([{ ...channels[0], warning: 90, critical: 80 }]));
console.log('Realtime monitor: telemetry validity, freshness, freeze/resume, bounded history, and isolation passed.');
