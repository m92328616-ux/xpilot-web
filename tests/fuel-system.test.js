const test = require('node:test');
const assert = require('node:assert/strict');
const { createFuelState, consumeFuel, tickFuelRegen } = require('../fuel-system.js');

test('fuel consumption lowers fuel and respects max cap', () => {
  const state = createFuelState(100, 100, 2, 3);
  const result = consumeFuel(state, 35);
  assert.equal(result.ok, true);
  assert.equal(result.remaining, 65);
  assert.equal(state.fuel, 65);
  assert.equal(state.fuel, Math.min(100, Math.max(0, 100 - 35)));
});

test('fuel regenerates after the idle delay and stops at the cap', () => {
  const state = createFuelState(100, 20, 2, 0.5);
  tickFuelRegen(state, 0.25, false);
  assert.equal(state.fuel, 20);
  tickFuelRegen(state, 0.5, false);
  assert.equal(state.fuel, 21);
  state.fuel = 100;
  tickFuelRegen(state, 1, false);
  assert.equal(state.fuel, 100);
});

test('active actions prevent regeneration until idle', () => {
  const state = createFuelState(100, 50, 2, 0.5);
  tickFuelRegen(state, 1, true);
  assert.equal(state.fuel, 50);
  assert.equal(state.idleTimer, 0);
});

test('zero-delay regeneration restores fuel immediately', () => {
  const state = createFuelState(200, 100, 5, 0);
  tickFuelRegen(state, 0.5, false);
  assert.equal(state.fuel, 102.5);
});
