"""
Tests for pvp_system.py (desktop mirror of pvp-system.js).

Run with:  python -m unittest discover -s tests -p '*.py' -v
"""

import math
import random
import unittest

import pvp_system

WORLD_W, WORLD_H = 3200, 2400


class PlayerHpTest(unittest.TestCase):

    def test_new_players_start_with_full_health_and_are_alive(self):
        s = pvp_system.create_player_hp_state()
        self.assertEqual(s['hp'], pvp_system.PLAYER_MAX_HP)
        self.assertEqual(s['maxHp'], pvp_system.PLAYER_MAX_HP)
        self.assertFalse(s['dead'])
        self.assertEqual(s['hp'], 3)

    def test_damage_reduces_hp_without_killing_until_it_reaches_zero(self):
        s = pvp_system.create_player_hp_state()
        r = pvp_system.apply_player_damage(s, 1)
        self.assertEqual(s['hp'], 2)
        self.assertFalse(r['lethal'])
        r = pvp_system.apply_player_damage(s, 1)
        self.assertEqual(s['hp'], 1)
        self.assertFalse(r['lethal'])
        r = pvp_system.apply_player_damage(s, 1)
        self.assertEqual(s['hp'], 0)
        self.assertTrue(s['dead'])
        self.assertTrue(r['lethal'])

    def test_damage_never_drops_below_zero_and_ignores_damage_after_death(self):
        s = pvp_system.create_player_hp_state(1)
        pvp_system.apply_player_damage(s, 5)
        self.assertEqual(s['hp'], 0)
        self.assertTrue(s['dead'])
        # A dead player can no longer be damaged.
        pvp_system.apply_player_damage(s, 5)
        self.assertEqual(s['hp'], 0)

    def test_reset_restores_full_health_and_clears_death(self):
        s = pvp_system.create_player_hp_state()
        pvp_system.apply_player_damage(s, 2)
        self.assertFalse(s['dead'])
        pvp_system.reset_player_hp(s)
        self.assertEqual(s['hp'], pvp_system.PLAYER_MAX_HP)
        self.assertFalse(s['dead'])
        pvp_system.apply_player_damage(s, 3)
        self.assertTrue(s['dead'])
        pvp_system.reset_player_hp(s)
        self.assertEqual(s['hp'], pvp_system.PLAYER_MAX_HP)
        self.assertFalse(s['dead'])

    def test_ramming_uses_a_consistent_single_point_of_damage(self):
        self.assertEqual(pvp_system.RAM_DAMAGE, 1)
        self.assertGreaterEqual(pvp_system.RAM_COOLDOWN, 1.0,
                                'ram cooldown prevents burst damage')

    def test_bullet_hit_radius_is_a_fair_symmetric_collision_size(self):
        self.assertEqual(pvp_system.BULLET_HIT_RADIUS, 12)
        self.assertEqual(pvp_system.RAM_HIT_RADIUS, 18)
        self.assertGreater(pvp_system.RAM_HIT_RADIUS, pvp_system.BULLET_HIT_RADIUS)


class SpawnPointTest(unittest.TestCase):

    def test_spawn_point_is_deterministic_for_a_given_player_id(self):
        a = pvp_system.spawn_point_for_id('p_abc123', WORLD_W, WORLD_H)
        b = pvp_system.spawn_point_for_id('p_abc123', WORLD_W, WORLD_H)
        self.assertEqual(a, b)

    def test_spawn_point_lies_inside_the_world_and_away_from_the_centre(self):
        p = pvp_system.spawn_point_for_id('p_abc123', WORLD_W, WORLD_H)
        self.assertGreaterEqual(p['x'], 0)
        self.assertLess(p['x'], WORLD_W)
        self.assertGreaterEqual(p['y'], 0)
        self.assertLess(p['y'], WORLD_H)
        d_centre = math.hypot(p['x'] - WORLD_W / 2, p['y'] - WORLD_H / 2)
        self.assertGreater(d_centre, 300, 'spawn should not be at the world centre')

    def test_different_players_spawn_at_different_points(self):
        seen = set()
        for i in range(16):
            p = pvp_system.spawn_point_for_id(
                f'p_{i}_{random.random()}', WORLD_W, WORLD_H)
            seen.add((round(p['x']), round(p['y'])))
        self.assertGreaterEqual(len(seen), 8,
                                f'expected spread-out spawns, got {len(seen)}')


class GeometryTest(unittest.TestCase):

    def test_wrap_aware_distance_sees_across_the_world_edge(self):
        # Points just inside opposite edges are actually only 10px apart.
        d = pvp_system.distance_between(0, 1200, WORLD_W - 10, 1200, WORLD_W, WORLD_H)
        self.assertEqual(d, 10)

    def test_circle_collision_uses_wrap_aware_distances(self):
        hit = pvp_system.circle_collides(0, 1200, 12, WORLD_W - 10, 1200, 12,
                                         WORLD_W, WORLD_H)
        self.assertTrue(hit)
        miss = pvp_system.circle_collides(0, 1200, 12, WORLD_W - 50, 1200, 12,
                                          WORLD_W, WORLD_H)
        self.assertFalse(miss)

    def test_hash_string_is_stable_and_non_trivial(self):
        self.assertEqual(pvp_system.hash_string('p_aaa'),
                         pvp_system.hash_string('p_aaa'))
        self.assertNotEqual(pvp_system.hash_string('p_aaa'),
                            pvp_system.hash_string('p_bbb'))


if __name__ == '__main__':
    unittest.main()
