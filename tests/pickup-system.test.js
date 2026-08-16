const test = require('node:test');
const assert = require('node:assert/strict');
const { PICKUP_DEFS, createPickup, applyBulletDamageToPickup, getPickupRespawnKind, getPickupBlastRadius, getPickupExplosionDelay, FUEL_CELL_BLAST_RADIUS, monitorFuelExplosion } = require('../pickup-system.js');

test('power-up balancing keeps shields and score boosts in a fair range', () => {
  assert.equal(PICKUP_DEFS.shield.duration, 30);
  assert.equal(PICKUP_DEFS.shield.hp, 2);
  assert.equal(PICKUP_DEFS.score_mult.duration, 18);
  assert.equal(PICKUP_DEFS.score_mult.respawn, 15);
  assert.equal(PICKUP_DEFS.fuel_cell.respawn, 24);
});

test('fragile pickups are destroyed in one hit', () => {
  const pickup = createPickup('score_mult', { x: 100, y: 100, active: true });
  const result = applyBulletDamageToPickup(pickup, 1, { consumeOnImpact: true });
  assert.equal(result.destroyed, true);
  assert.equal(result.shouldConsumeBullet, true);
  assert.equal(pickup.active, false);
  assert.equal(pickup.hp, 0);
});

test('shield pickups survive multiple hits', () => {
  const pickup = createPickup('shield', { x: 100, y: 100, active: true });
  const first = applyBulletDamageToPickup(pickup, 1, { consumeOnImpact: true });
  assert.equal(first.destroyed, false);
  assert.equal(pickup.hp, 1);
  const second = applyBulletDamageToPickup(pickup, 1, { consumeOnImpact: true });
  assert.equal(second.destroyed, true);
  assert.equal(pickup.hp, 0);
  assert.equal(pickup.active, false);
});

test('durable pickups need multiple hits', () => {
  const pickup = createPickup('fuel_cell', { x: 120, y: 120, active: true });
  const first = applyBulletDamageToPickup(pickup, 1, { consumeOnImpact: true });
  const second = applyBulletDamageToPickup(pickup, 2, { consumeOnImpact: true });
  assert.equal(first.destroyed, false);
  assert.equal(second.destroyed, true);
  assert.equal(pickup.hp, 0);
});

test('fuel cells can be marked for respawn and expose a blast radius', () => {
  const pickup = createPickup('fuel_cell', { x: 120, y: 120, active: true });
  assert.equal(getPickupRespawnKind(pickup), 'fuel_cell');
  assert.equal(getPickupBlastRadius(pickup), FUEL_CELL_BLAST_RADIUS);
  assert.ok(FUEL_CELL_BLAST_RADIUS < 140, 'fuel cell blast radius should be smaller than before');
  assert.equal(getPickupExplosionDelay(pickup), 1.4);
});

test('fuel cell blast radius can be overridden per pickup', () => {
  const pickup = createPickup('fuel_cell', { x: 120, y: 120, active: true, blastRadius: 60 });
  assert.equal(getPickupBlastRadius(pickup), 60);
});

test('fuel cell explosion is monitored by an active shield', () => {
  const pickup = createPickup('fuel_cell', { x: 100, y: 100, active: true, blastRadius: 90 });
  const player = { x: 150, y: 100 };
  let absorbed = 0;
  const shield = {
    isActive: () => true,
    absorbHit: (dmg) => { absorbed += dmg; },
  };
  const result = monitorFuelExplosion(pickup, player, shield);
  assert.equal(result.inBlast, true);
  assert.equal(result.shielded, true);
  assert.equal(absorbed, 1);
});

test('fuel cell explosion is not shielded without an active shield', () => {
  const pickup = createPickup('fuel_cell', { x: 100, y: 100, active: true, blastRadius: 90 });
  const player = { x: 150, y: 100 };
  const result = monitorFuelExplosion(pickup, player, { isActive: () => false, absorbHit: () => {} });
  assert.equal(result.inBlast, true);
  assert.equal(result.shielded, false);
});

test('fuel cell explosion does not monitor players outside the blast radius', () => {
  const pickup = createPickup('fuel_cell', { x: 100, y: 100, active: true, blastRadius: 90 });
  const player = { x: 500, y: 500 };
  const result = monitorFuelExplosion(pickup, player, { isActive: () => true, absorbHit: () => {} });
  assert.equal(result.inBlast, false);
  assert.equal(result.shielded, false);
});

test('non-fuel-cell pickups are never monitored as explosions', () => {
  const pickup = createPickup('shield', { x: 100, y: 100, active: true });
  const player = { x: 100, y: 100 };
  let absorbed = 0;
  const result = monitorFuelExplosion(pickup, player, {
    isActive: () => true,
    absorbHit: (dmg) => { absorbed += dmg; },
  });
  assert.equal(result.inBlast, false);
  assert.equal(result.shielded, false);
  assert.equal(absorbed, 0);
});
