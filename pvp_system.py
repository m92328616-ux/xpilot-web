"""
PvP (player versus player) shared helpers for the desktop XPilot client.

This mirrors pvp-system.js exactly so every XPilot client (web, pyodide and
desktop) enforces identical combat rules: max HP, ram damage/cooldown, bullet
hit radius, and fair deterministic spawn points derived from the player id.

The desktop client cannot load the browser module, so these are re-implemented
in pure Python (issue #10).
"""

import math

PLAYER_MAX_HP = 3
RAM_DAMAGE = 1
RAM_COOLDOWN = 2.0
BULLET_HIT_RADIUS = 12   # ship body radius for bullet-vs-player hits
RAM_HIT_RADIUS = 18      # hull-to-hull ramming distance
N_SPAWN_POINTS = 8
SPAWN_RING_FACTOR = 0.38


# ── Player HP state ────────────────────────────────────────────────────────

def create_player_hp_state(max_hp=PLAYER_MAX_HP):
    cap = max(1, int(max_hp) if isinstance(max_hp, (int, float)) else PLAYER_MAX_HP)
    return {'hp': cap, 'maxHp': cap, 'dead': False}


def reset_player_hp(state, max_hp=PLAYER_MAX_HP):
    cap = max(1, int(max_hp) if isinstance(max_hp, (int, float)) else PLAYER_MAX_HP)
    state['hp'] = cap
    state['maxHp'] = cap
    state['dead'] = False
    return state


def apply_player_damage(state, dmg=1):
    """Apply damage to an HP state dict. Returns {'lethal': bool}."""
    if not state or state.get('dead'):
        return {'lethal': False}
    dmg_val = max(0, dmg if dmg else 1)
    state['hp'] = max(0, state['hp'] - dmg_val)
    if state['hp'] <= 0:
        state['dead'] = True
    return {'lethal': bool(state['dead'])}


# ── Wrap-aware geometry (world edges are seamless) ─────────────────────────

def wrap_delta(a, b, size):
    d = (b - a) % size
    if d > size / 2:
        d -= size
    return d


def distance_between(ax, ay, bx, by, world_w, world_h):
    return math.hypot(wrap_delta(ax, bx, world_w), wrap_delta(ay, by, world_h))


def circle_collides(ax, ay, ar, bx, by, br, world_w, world_h):
    return distance_between(ax, ay, bx, by, world_w, world_h) < ar + br


# ── Deterministic PvP spawn points ─────────────────────────────────────────
# Every player derives a spawn slot from a hash of their client id. Spawns sit
# on a ring at equal distance from the world centre so nobody gets a positional
# advantage — each slot is symmetric under world wrap. Mirrors pvp-system.js.

def hash_string(s):
    """djb2 hash (32-bit), identical to pvp-system.js hashString."""
    h = 5381
    for ch in str(s):
        h = ((h << 5) + h) ^ ord(ch)
        h &= 0xFFFFFFFF
    return h


def spawn_point_for_id(player_id, world_w, world_h, n_spawns=N_SPAWN_POINTS):
    h = hash_string(str(player_id))
    slot = h % n_spawns
    # Small deterministic jitter within the slot so two players that hash to
    # the same slot still land at slightly different (non-overlapping) spots.
    jitter = ((((h >> 8) % 1000) / 1000.0) - 0.5) * ((math.pi * 2) / n_spawns) * 0.9
    angle = (slot / n_spawns) * math.pi * 2 + 0.3 + jitter
    radius = min(world_w, world_h) * SPAWN_RING_FACTOR
    cx, cy = world_w / 2.0, world_h / 2.0
    return {
        'x': ((cx + math.cos(angle) * radius) % world_w + world_w) % world_w,
        'y': ((cy + math.sin(angle) * radius) % world_h + world_h) % world_h,
    }
