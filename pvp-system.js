(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.PvPSystem = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  // ── PvP balance constants (shared by every client so combat is fair) ────
  const PLAYER_MAX_HP = 3;
  const RAM_DAMAGE = 1;
  const RAM_COOLDOWN = 2.0;
  const BULLET_HIT_RADIUS = 12; // ship body radius for bullet-vs-player hits
  const RAM_HIT_RADIUS = 18;    // hull-to-hull ramming distance
  const N_SPAWN_POINTS = 8;
  const SPAWN_RING_FACTOR = 0.38;

  // ── Player HP state ─────────────────────────────────────────────────────
  function createPlayerHPState(maxHp = PLAYER_MAX_HP) {
    const cap = Math.max(1, Number(maxHp) || PLAYER_MAX_HP);
    return { hp: cap, maxHp: cap, dead: false };
  }

  function resetPlayerHP(state, maxHp = PLAYER_MAX_HP) {
    const cap = Math.max(1, Number(maxHp) || PLAYER_MAX_HP);
    state.hp = cap;
    state.maxHp = cap;
    state.dead = false;
    return state;
  }

  // Applies damage to a player's HP record. Returns {lethal} so callers can
  // react to an elimination (kill feed, respawn flow, etc).
  function applyPlayerDamage(state, dmg = 1) {
    if (!state || state.dead) return { lethal: false };
    const dmgVal = Math.max(0, Number(dmg) || 1);
    state.hp = Math.max(0, state.hp - dmgVal);
    if (state.hp <= 0) state.dead = true;
    return { lethal: !!state.dead };
  }

  // ── Wrap-aware geometry (world edges are seamless) ──────────────────────
  function wrapDelta(a, b, size) {
    let d = ((b - a) % size + size) % size;
    if (d > size / 2) d -= size;
    return d;
  }

  function distanceBetween(ax, ay, bx, by, worldW, worldH) {
    return Math.hypot(wrapDelta(ax, bx, worldW), wrapDelta(ay, by, worldH));
  }

  function circleCollides(ax, ay, ar, bx, by, br, worldW, worldH) {
    return distanceBetween(ax, ay, bx, by, worldW, worldH) < ar + br;
  }

  // ── Deterministic PvP spawn points ──────────────────────────────────────
  // Every player derives a spawn slot from a hash of their client id. Spawns
  // sit on a ring at equal distance from the world centre so nobody gets a
  // positional advantage — each slot is symmetric under world wrap.
  function hashString(str) {
    let h = 5381;
    for (let i = 0; i < str.length; i++) {
      h = ((h << 5) + h) ^ str.charCodeAt(i);
      h |= 0;
    }
    return h >>> 0;
  }

  function spawnPointForId(id, worldW, worldH, nSpawns = N_SPAWN_POINTS) {
    const h = hashString(String(id));
    const slot = h % nSpawns;
    // Small deterministic jitter within the slot so two players that hash to
    // the same slot still land at slightly different (non-overlapping) spots.
    const jitter = (((h >>> 8) % 1000) / 1000 - 0.5) * ((Math.PI * 2) / nSpawns) * 0.9;
    const angle = (slot / nSpawns) * Math.PI * 2 + 0.3 + jitter;
    const radius = Math.min(worldW, worldH) * SPAWN_RING_FACTOR;
    const cx = worldW / 2, cy = worldH / 2;
    return {
      x: ((cx + Math.cos(angle) * radius) % worldW + worldW) % worldW,
      y: ((cy + Math.sin(angle) * radius) % worldH + worldH) % worldH,
    };
  }

  return {
    PLAYER_MAX_HP,
    RAM_DAMAGE,
    RAM_COOLDOWN,
    BULLET_HIT_RADIUS,
    RAM_HIT_RADIUS,
    N_SPAWN_POINTS,
    SPAWN_RING_FACTOR,
    createPlayerHPState,
    resetPlayerHP,
    applyPlayerDamage,
    wrapDelta,
    distanceBetween,
    circleCollides,
    hashString,
    spawnPointForId,
  };
});
