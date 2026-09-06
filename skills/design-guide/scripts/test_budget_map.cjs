// Verify forecast arithmetic and missing-data behavior independently of rendering.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = vm.createContext({ window: {} });
vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/component-reference/components/budget-map-model.js'), 'utf8'), context);
const api = context.window.ComponentReference.budgetMapModel;
const at = (day, hour = 0) => new Date(Date.UTC(2026, 8, day, hour)).toISOString();
const sample = (day, value, hour = 0) => ({ at: at(day, hour), value });
const fixture = (item = {}, period = {}) => api.prepare({
  start: at(1), end: at(31), asOf: at(16), ...period,
  items: [{ id: 'compute', label: 'Compute', unit: 'USD', limit: 10000, current: 9000, forecast: 13200,
    history: [sample(13, 7500), sample(1, 0), sample(9, 5100)], ...item }]
});
const analysis = model => api.analyze(model, model.items[0]);
const close = (actual, expected) => assert(Math.abs(actual - expected) < 1e-8, `${actual} != ${expected}`);

const model = fixture();
const original = JSON.stringify(model);
const csv = api.csv(model);
const result = analysis(model);
assert.equal(result.elapsedDays, 15);
assert.equal(result.remainingDays, 15);
assert.equal(result.average.value, 600);
close(result.seven.value, 3900 / 7);
assert.equal(result.three.value, 500);
close(result.sustainable, 1000 / 15);
close(result.change, -100 / 6);
close(result.reduction, 100 * (1 - (1000 / 15) / 500));
assert.equal(result.forecasts[0].total, 13200);
assert.equal(result.forecasts[0].rate, 280);
assert.equal(result.forecasts[1].total, 18000);
assert.equal(result.forecasts[1].percent, 180);
assert.equal(result.forecasts[1].headroom, -8000);
close(result.forecasts[2].total, 9000 + (3900 / 7) * 15);
assert.equal(result.forecasts[3].total, 16500);
assert.equal(new Date(result.forecasts[3].limitAt).toISOString(), at(18));
assert.equal(JSON.stringify(model), original, 'Analysis cannot replace the supplied forecast or mutate observations');
assert.equal(api.csv(model), csv, 'Raw exports cannot acquire synthetic forecasts or observations');
assert.equal(model.maximum, 132, 'Alternative projections cannot change the map scale');

const interpolated = analysis(fixture({ history: [sample(1, 0), sample(8, 4800), sample(12, 7200)] }));
assert.equal(interpolated.seven.interpolated, true);
assert.equal(interpolated.three.interpolated, true);
assert.equal(interpolated.three.value, 450);
close(interpolated.seven.value, 3600 / 7);
assert.equal(analysis(fixture({ history: [sample(1, 1500), sample(13, 7500)] })).average.value, 500,
  'A non-zero starting observation must not be replaced by an assumed zero');

const gap = analysis(fixture({ history: [sample(1, 0), sample(9, 5100), sample(11, null), sample(13, 7500)] }));
assert.equal(gap.average.value, null);
assert.equal(gap.seven.value, null);
assert.equal(gap.three.value, 500, 'A gap outside the window cannot invalidate the recent rate');
const boundaryGap = analysis(fixture({ history: [sample(1, 0), sample(12, null)] }));
assert.equal(boundaryGap.three.value, null, 'An estimated boundary cannot bridge an explicit gap');
const reset = analysis(fixture({ history: [sample(1, 0), sample(9, 5100), sample(13, 8000), sample(14, 7000)] }));
assert.equal(reset.average.value, null);
assert.equal(reset.three.value, null, 'A positive net delta cannot hide a counter reset');
for (const input of [{ history: [] }, { current: null }, { history: [sample(15, 8800)] }]) {
  const missing = analysis(fixture(input));
  assert.equal(missing.average.value, null);
  assert.equal(missing.three.value, null);
  assert.equal(missing.forecasts[3].total, null);
  assert.equal(missing.forecasts[0].total, 13200, 'Supplied forecasts survive unavailable usage rates');
}
const early = analysis(fixture({ history: [sample(1, 0)] }, { asOf: at(3, 12) }));
assert.equal(early.elapsedDays, 2.5);
assert.equal(early.average.value, 3600);
assert.equal(early.three.value, null, 'A partial three-day window cannot be labelled a three-day rate');
const first = analysis(fixture({ history: [] }, { asOf: at(1) }));
assert.equal(first.average.value, null);

const zero = analysis(fixture({ current: 0, forecast: 0, history: [sample(1, 0), sample(9, 0), sample(13, 0)] }));
assert.equal(zero.three.value, 0, 'Measured zero is available');
assert.equal(zero.forecasts[3].total, 0);
assert.equal(zero.forecasts[3].limitState, 'not-reached');
assert.equal(zero.change, null, 'Zero baseline cannot produce an infinite percent change');
assert.equal(zero.reduction, 0);
const over = analysis(fixture({ current: 11000 }));
assert.equal(over.remaining, -1000);
assert.equal(over.sustainable, 0);
assert.equal(over.reduction, 100);
assert(over.forecasts.every(row => row.limitState === 'reached'));
const under = analysis(fixture({ limit: 20000 }));
assert(under.forecasts.every(row => row.limitState === 'after-period'));
const boundary = analysis(fixture({ limit: 16500 }));
assert.equal(boundary.forecasts[3].limitAt, model.end, 'Reaching the limit at period end is a valid date');
const declining = analysis(fixture({ forecast: 8000 }));
assert.equal(declining.forecasts[0].total, 8000);
assert.equal(declining.forecasts[0].limitState, 'not-reached');
assert.equal(analysis(fixture({ forecast: null })).forecasts[0].total, null);
assert.equal(analysis(fixture({ stale: true })).three.value, 500, 'Stale snapshots use their own as-of time');
const overflow = analysis(fixture({ current: Number.MAX_VALUE, history: [sample(1, 0)] }));
assert.equal(overflow.forecasts[1].total, null, 'Numeric overflow is unavailable, never Infinity');
console.log('Budget map: rates, projections, limit dates, gaps, resets, zero usage, and supplied-data isolation passed.');
