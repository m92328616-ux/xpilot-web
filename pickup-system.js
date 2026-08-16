(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.PickupSystem = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const FUEL_CELL_BLAST_RADIUS = 90;

  const PICKUP_DEFS = {
    shield: {
      label: '🛡 Shield',
      color: '#4488ff',
      bg: 'rgba(30,60,200,0.25)',
      duration: 30,
      hp: 2,
      respawn: 16,
      collectLabel: 'Shield charge',
      effect: true,
      r: 10,
      deflectionDmg: 1,
    },
    score_mult: {
      label: '🌟 2× Score',
      color: '#ffcc00',
      bg: 'rgba(180,140,0,0.25)',
      duration: 18,
      hp: 1,
      respawn: 15,
      collectLabel: 'Score booster',
      effect: true,
      r: 10,
    },
    fuel_cell: {
      label: '⛽ Fuel Cell',
      color: '#7ef0a0',
      bg: 'rgba(40,180,120,0.25)',
      hp: 3,
      respawn: 24,
      collectLabel: 'Fuel cell',
      effect: false,
      r: 12,
      explosionDelay: 1.4,
      blastRadius: FUEL_CELL_BLAST_RADIUS,
    },
  };

  function getPickupDef(kind) {
    return PICKUP_DEFS[kind] || null;
  }

  function createPickup(kind, options = {}) {
    const def = getPickupDef(kind) || {};
    const radius = options.r ?? def.r ?? 10;
    return {
      id: options.id || `pickup-${Math.random().toString(36).slice(2, 10)}`,
      kind: kind || 'shield',
      x: options.x ?? 0,
      y: options.y ?? 0,
      r: radius,
      hp: options.hp ?? def.hp ?? 1,
      maxHp: options.maxHp ?? def.hp ?? 1,
      active: options.active ?? true,
      respawnTimer: options.respawnTimer ?? 0,
      pulse: options.pulse ?? 0,
      hitFlash: options.hitFlash ?? 0,
      destroyFlash: options.destroyFlash ?? 0,
      blastRadius: options.blastRadius ?? (kind === 'fuel_cell' ? FUEL_CELL_BLAST_RADIUS : 0),
      respawnKind: options.respawnKind ?? (kind === 'fuel_cell' ? 'fuel_cell' : null),
      fuseActive: options.fuseActive ?? false,
      fuseTimer: options.fuseTimer ?? 0,
      explosionDelay: options.explosionDelay ?? (kind === 'fuel_cell' ? def.explosionDelay ?? 1.4 : 0),
    };
  }

  function applyBulletDamageToPickup(pickup, damage = 1, options = {}) {
    if (!pickup || !pickup.active) {
      return { pickup, damaged: false, destroyed: false, shouldConsumeBullet: false };
    }

    const def = getPickupDef(pickup.kind) || {};
    const bulletDamage = Math.max(0, Number(damage) || 1);
    const currentHp = Number(pickup.hp ?? def.hp ?? 1);
    const nextHp = Math.max(0, currentHp - bulletDamage);

    pickup.hp = nextHp;
    pickup.hitFlash = Math.max(pickup.hitFlash || 0, options.hitFlash ?? 0.18);

    if (nextHp <= 0) {
      pickup.active = false;
      pickup.respawnTimer = options.respawnTimer ?? (def.respawn ?? 18);
      pickup.destroyFlash = Math.max(pickup.destroyFlash || 0, options.destroyFlash ?? 0.24);
      return { pickup, damaged: true, destroyed: true, shouldConsumeBullet: options.consumeOnImpact !== false };
    }

    return { pickup, damaged: true, destroyed: false, shouldConsumeBullet: options.consumeOnImpact !== false };
  }

  function serializePickup(pickup) {
    return {
      id: pickup.id,
      kind: pickup.kind,
      x: Number(pickup.x),
      y: Number(pickup.y),
      r: Number(pickup.r),
      hp: Number(pickup.hp),
      maxHp: Number(pickup.maxHp),
      active: !!pickup.active,
      respawnTimer: Number(pickup.respawnTimer || 0),
      blastRadius: Number(pickup.blastRadius || 0),
      respawnKind: pickup.respawnKind || null,
      fuseActive: !!pickup.fuseActive,
      fuseTimer: Number(pickup.fuseTimer || 0),
      explosionDelay: Number(pickup.explosionDelay || 0),
    };
  }

  function hydratePickup(pickup, entry) {
    if (!pickup || !entry) return pickup;
    pickup.id = entry.id || pickup.id;
    pickup.kind = entry.kind || pickup.kind;
    pickup.x = Number(entry.x ?? pickup.x);
    pickup.y = Number(entry.y ?? pickup.y);
    pickup.r = Number(entry.r ?? pickup.r);
    pickup.hp = Number(entry.hp ?? pickup.hp);
    pickup.maxHp = Number(entry.maxHp ?? pickup.maxHp);
    pickup.active = !!entry.active;
    pickup.respawnTimer = Number(entry.respawnTimer ?? pickup.respawnTimer ?? 0);
    pickup.blastRadius = Number(entry.blastRadius ?? pickup.blastRadius ?? (pickup.kind === 'fuel_cell' ? FUEL_CELL_BLAST_RADIUS : 0));
    pickup.respawnKind = entry.respawnKind ?? pickup.respawnKind ?? (pickup.kind === 'fuel_cell' ? 'fuel_cell' : null);
    pickup.fuseActive = !!(entry.fuseActive ?? pickup.fuseActive ?? false);
    pickup.fuseTimer = Number(entry.fuseTimer ?? pickup.fuseTimer ?? 0);
    pickup.explosionDelay = Number(entry.explosionDelay ?? pickup.explosionDelay ?? (pickup.kind === 'fuel_cell' ? 1.4 : 0));
    pickup.hitFlash = Math.max(pickup.hitFlash || 0, 0.08);
    return pickup;
  }

  function getPickupRespawnKind(pickup) {
    return pickup?.respawnKind || (pickup?.kind === 'fuel_cell' ? 'fuel_cell' : null);
  }

  function getPickupBlastRadius(pickup) {
    return Number(pickup?.blastRadius ?? (pickup?.kind === 'fuel_cell' ? FUEL_CELL_BLAST_RADIUS : 0));
  }

  function getPickupExplosionDelay(pickup) {
    return Number(pickup?.explosionDelay ?? (pickup?.kind === 'fuel_cell' ? 1.4 : 0));
  }

  function monitorFuelExplosion(pickup, player, shield) {
    if (!pickup || pickup.kind !== 'fuel_cell' || !player) {
      return { inBlast: false, shielded: false };
    }
    const blastRadius = getPickupBlastRadius(pickup);
    if (blastRadius <= 0) {
      return { inBlast: false, shielded: false };
    }
    const inBlast = Math.hypot(player.x - pickup.x, player.y - pickup.y) < blastRadius;
    if (!inBlast) {
      return { inBlast: false, shielded: false };
    }
    const shielded = !!(shield && typeof shield.isActive === 'function' && shield.isActive());
    if (shielded && typeof shield.absorbHit === 'function') {
      shield.absorbHit(1);
    }
    return { inBlast: true, shielded };
  }

  return {
    PICKUP_DEFS,
    FUEL_CELL_BLAST_RADIUS,
    getPickupDef,
    createPickup,
    applyBulletDamageToPickup,
    serializePickup,
    hydratePickup,
    getPickupRespawnKind,
    getPickupBlastRadius,
    getPickupExplosionDelay,
    monitorFuelExplosion,
  };
});
