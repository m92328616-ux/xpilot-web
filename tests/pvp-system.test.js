const test = require('node:test');
const assert = require('node:assert/strict');
const {
  PLAYER_MAX_HP, RAM_DAMAGE, RAM_COOLDOWN, BULLET_HIT_RADIUS, RAM_HIT_RADIUS,
  createPlayerHPState, resetPlayerHP, applyPlayerDamage,
  wrapDelta, distanceBetween, circleCollides, hashString, spawnPointForId,
} = require('../pvp-system.js');

const WORLD_W = 3200, WORLD_H = 2400;

test('new players start with full health and are alive', () => {
  const s = createPlayerHPState();
  assert.equal(s.hp, PLAYER_MAX_HP);
  assert.equal(s.maxHp, PLAYER_MAX_HP);
  assert.equal(s.dead, false);
  assert.equal(s.hp, 3);
});

test('damage reduces hp without killing until it reaches zero', () => {
  const s = createPlayerHPState();
  let r = applyPlayerDamage(s, 1);
  assert.equal(s.hp, 2);
  assert.equal(r.lethal, false);
  r = applyPlayerDamage(s, 1);
  assert.equal(s.hp, 1);
  assert.equal(r.lethal, false);
  r = applyPlayerDamage(s, 1);
  assert.equal(s.hp, 0);
  assert.equal(s.dead, true);
  assert.equal(r.lethal, true);
});

test('damage never drops below zero and ignores damage after death', () => {
  const s = createPlayerHPState(1);
  applyPlayerDamage(s, 5);
  assert.equal(s.hp, 0);
  assert.equal(s.dead, true);
  // A dead player can no longer be damaged.
  applyPlayerDamage(s, 5);
  assert.equal(s.hp, 0);
});

test('reset restores full health and clears death', () => {
  const s = createPlayerHPState();
  applyPlayerDamage(s, 2);
  assert.equal(s.dead, false);
  resetPlayerHP(s);
  assert.equal(s.hp, PLAYER_MAX_HP);
  assert.equal(s.dead, false);
  applyPlayerDamage(s, 3);
  assert.equal(s.dead, true);
  resetPlayerHP(s);
  assert.equal(s.hp, PLAYER_MAX_HP);
  assert.equal(s.dead, false);
});

test('ramming uses a consistent, single point of damage', () => {
  assert.equal(RAM_DAMAGE, 1);
  assert.ok(RAM_COOLDOWN >= 1.0, 'ram cooldown prevents burst damage');
});

test('bullet hit radius is a fair, symmetric collision size', () => {
  assert.equal(BULLET_HIT_RADIUS, 12);
  assert.equal(RAM_HIT_RADIUS, 18);
  assert.ok(RAM_HIT_RADIUS > BULLET_HIT_RADIUS);
});

test('spawn point is deterministic for a given player id', () => {
  const a = spawnPointForId('p_abc123', WORLD_W, WORLD_H);
  const b = spawnPointForId('p_abc123', WORLD_W, WORLD_H);
  assert.deepEqual(a, b);
});

test('spawn point lies inside the world and away from the centre', () => {
  const p = spawnPointForId('p_abc123', WORLD_W, WORLD_H);
  assert.ok(p.x >= 0 && p.x < WORLD_W);
  assert.ok(p.y >= 0 && p.y < WORLD_H);
  const dCentre = Math.hypot(p.x - WORLD_W / 2, p.y - WORLD_H / 2);
  assert.ok(dCentre > 300, 'spawn should not be at the world centre');
});

test('different players spawn at different points', () => {
  const seen = new Set();
  for (let i = 0; i < 16; i++) {
    const p = spawnPointForId(`p_${i}_${Math.random()}`, WORLD_W, WORLD_H);
    seen.add(`${Math.round(p.x)},${Math.round(p.y)}`);
  }
  assert.ok(seen.size >= 8, `expected spread-out spawns, got ${seen.size}`);
});

test('wrap-aware distance sees across the world edge', () => {
  // Points just inside opposite edges are actually only 10px apart.
  const d = distanceBetween(0, 1200, WORLD_W - 10, 1200, WORLD_W, WORLD_H);
  assert.equal(d, 10);
});

test('circle collision uses wrap-aware distances', () => {
  const hit = circleCollides(0, 1200, 12, WORLD_W - 10, 1200, 12, WORLD_W, WORLD_H);
  assert.equal(hit, true);
  const miss = circleCollides(0, 1200, 12, WORLD_W - 50, 1200, 12, WORLD_W, WORLD_H);
  assert.equal(miss, false);
});

test('hashString is stable and non-trivial', () => {
  assert.equal(hashString('p_aaa'), hashString('p_aaa'));
  assert.notEqual(hashString('p_aaa'), hashString('p_bbb'));
});
