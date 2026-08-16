"""
Tests for the desktop in-game death log (issue #32).

Covers the log helpers added to xpilot.NetworkClient:

* add_log inserts newest-first with a lifetime and caps the buffer.
* tick_log ages entries and removes expired ones.
* The buffer is bounded (LOG_MAX_ENTRIES) and thread-safe.
* The standalone LogOverlay powers the offline single-player log and can be
  shared with a NetworkClient.
* _short_id formats/truncates connection ids for display.

Run with:  python -m unittest discover -s tests -p '*.py' -v
"""

import threading
import unittest

import xpilot


def make_client():
    """Build a NetworkClient pointed at a dead UDP port (no server needed)."""
    return xpilot.NetworkClient(
        "127.0.0.1", 9,
        player_ref=xpilot.Player(100, 100),
        bullets_ref=[],
        others_ref={},
        enemies_ref=[],
    )


class ShortIdTest(unittest.TestCase):

    def test_formats_common_cases(self):
        self.assertEqual(xpilot._short_id("abc12345"), "abc12345")
        self.assertEqual(xpilot._short_id(None), "?")
        self.assertEqual(xpilot._short_id(""), "?")

    def test_truncates_long_ids(self):
        self.assertEqual(xpilot._short_id("0123456789abcdef"), "01234567")
        self.assertEqual(len(xpilot._short_id("x" * 100)), 8)


class LogAddTest(unittest.TestCase):

    def setUp(self):
        self.client = make_client()

    def tearDown(self):
        self.client.close()

    def test_add_log_inserts_newest_first(self):
        self.client.add_log("first", xpilot.LOG_COLOR_JOIN)
        self.client.add_log("second", xpilot.LOG_COLOR_DEATH)
        entries = list(self.client.game_log)
        self.assertEqual(entries[0]["text"], "second")
        self.assertEqual(entries[1]["text"], "first")

    def test_add_log_stores_color_and_lifetime(self):
        self.client.add_log("hello", xpilot.LOG_COLOR_SELF)
        entry = self.client.game_log[0]
        self.assertEqual(entry["text"], "hello")
        self.assertEqual(entry["color"], xpilot.LOG_COLOR_SELF)
        self.assertEqual(entry["t"], xpilot.LOG_LIFETIME)

    def test_log_buffer_is_bounded(self):
        for i in range(xpilot.LOG_MAX_ENTRIES + 25):
            self.client.add_log(f"msg-{i}", xpilot.LOG_COLOR_JOIN)
        self.assertLessEqual(len(self.client.game_log), xpilot.LOG_MAX_ENTRIES)
        # Newest entries are kept.
        self.assertEqual(self.client.game_log[0]["text"], f"msg-{xpilot.LOG_MAX_ENTRIES + 24}")


class LogTickTest(unittest.TestCase):

    def setUp(self):
        self.client = make_client()

    def tearDown(self):
        self.client.close()

    def test_tick_log_removes_expired_entries(self):
        self.client.add_log("old", xpilot.LOG_COLOR_JOIN)
        self.client.add_log("new", xpilot.LOG_COLOR_DEATH)
        # Age everything past the lifetime.
        self.client.tick_log(xpilot.LOG_LIFETIME + 1)
        self.assertEqual(len(self.client.game_log), 0)

    def test_tick_log_keeps_fresh_entries(self):
        self.client.add_log("fresh", xpilot.LOG_COLOR_JOIN)
        self.client.tick_log(0.5)
        self.assertEqual(len(self.client.game_log), 1)
        self.assertEqual(self.client.game_log[0]["text"], "fresh")

    def test_tick_log_removes_only_expired(self):
        self.client.add_log("a", xpilot.LOG_COLOR_JOIN)
        self.client.game_log[0]["t"] = 0.2
        self.client.add_log("b", xpilot.LOG_COLOR_DEATH)
        self.client.tick_log(1.0)
        texts = [e["text"] for e in self.client.game_log]
        self.assertEqual(texts, ["b"])


class LogThreadSafetyTest(unittest.TestCase):

    def setUp(self):
        self.client = make_client()

    def tearDown(self):
        self.client.close()

    def test_concurrent_add_log_is_safe(self):
        errors = []

        def worker(n):
            try:
                for i in range(100):
                    self.client.add_log(f"t{n}-{i}", xpilot.LOG_COLOR_JOIN)
            except Exception as e:  # pragma: no cover - only on failure
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.client.game_log), xpilot.LOG_MAX_ENTRIES)


class LogAutoHideTest(unittest.TestCase):
    """Overlay fade/inactivity state machine (issue #37)."""

    def setUp(self):
        self.client = make_client()

    def tearDown(self):
        self.client.close()

    def test_add_log_arms_activity_timer(self):
        self.client.add_log("hi", xpilot.LOG_COLOR_JOIN)
        self.assertAlmostEqual(self.client.log_activity, xpilot.LOG_INACTIVITY_TIMEOUT)
        self.assertTrue(self.client.log_visible)

    def test_visible_while_active(self):
        self.client.add_log("hi", xpilot.LOG_COLOR_JOIN)
        self.client.tick_log(xpilot.LOG_INACTIVITY_TIMEOUT - 1)
        self.assertTrue(self.client.log_visible)

    def test_fades_out_after_inactivity(self):
        self.client.add_log("hi", xpilot.LOG_COLOR_JOIN)
        self.client.tick_log(xpilot.LOG_INACTIVITY_TIMEOUT + 0.01)
        self.assertFalse(self.client.log_visible)
        self.assertLess(self.client.log_opacity, 1.0)

    def test_new_entry_wakes_the_overlay(self):
        self.client.add_log("hi", xpilot.LOG_COLOR_JOIN)
        self.client.tick_log(xpilot.LOG_INACTIVITY_TIMEOUT + 0.01)
        self.assertFalse(self.client.log_visible)
        self.client.add_log("wake up", xpilot.LOG_COLOR_DEATH)
        self.client.tick_log(0.01)
        self.assertTrue(self.client.log_visible)

    def test_fade_in_is_gradual(self):
        self.client.log_fade = 1.0
        self.client.add_log("hi", xpilot.LOG_COLOR_JOIN)
        self.client.tick_log(0.5)
        self.assertAlmostEqual(self.client.log_opacity, 0.5, places=3)
        self.client.tick_log(0.5)
        self.assertAlmostEqual(self.client.log_opacity, 1.0, places=3)

    def test_fade_out_is_gradual(self):
        self.client.log_fade = 1.0
        self.client.add_log("hi", xpilot.LOG_COLOR_JOIN)
        self.client.tick_log(5.5)       # fully visible, 0.5s of activity left
        self.client.tick_log(0.5)       # activity window expires: fade-out starts
        self.assertFalse(self.client.log_visible)
        self.assertAlmostEqual(self.client.log_opacity, 0.5, places=3)

    def test_always_visible_keeps_overlay_full(self):
        self.client.log_always_visible = True
        self.client.add_log("hi", xpilot.LOG_COLOR_JOIN)
        self.client.tick_log(xpilot.LOG_INACTIVITY_TIMEOUT * 3)
        self.assertTrue(self.client.log_visible)
        self.assertAlmostEqual(self.client.log_opacity, 1.0, places=3)

    def test_constructor_applies_log_opts(self):
        nc = xpilot.NetworkClient(
            "127.0.0.1", 9,
            player_ref=xpilot.Player(1, 1),
            bullets_ref=[],
            others_ref={},
            enemies_ref=[],
            log_opts={'inactivity': 3.0, 'fade': 0.5, 'lifetime': 5.0,
                      'max_entries': 10, 'always_visible': True},
        )
        try:
            self.assertEqual(nc.log_inactivity, 3.0)
            self.assertEqual(nc.log_fade, 0.5)
            self.assertEqual(nc.log_lifetime, 5.0)
            self.assertEqual(nc.log_max_entries, 10)
            self.assertTrue(nc.log_always_visible)
        finally:
            nc.close()


class LogOverlayTest(unittest.TestCase):
    """The standalone overlay used for the offline single-player log."""

    def test_standalone_overlay_works_without_network(self):
        overlay = xpilot.LogOverlay()
        overlay.add("You died", xpilot.LOG_COLOR_SELF)
        self.assertEqual(overlay.game_log[0]["text"], "You died")
        overlay.tick(0.1)
        self.assertTrue(overlay.visible)

    def test_overlay_can_be_shared_with_client(self):
        overlay = xpilot.LogOverlay({'inactivity': 4.0})
        nc = xpilot.NetworkClient(
            "127.0.0.1", 9,
            player_ref=xpilot.Player(1, 1),
            bullets_ref=[],
            others_ref={},
            enemies_ref=[],
            log_overlay=overlay,
        )
        try:
            nc.add_log("join", xpilot.LOG_COLOR_JOIN)
            self.assertIs(nc.log_overlay, overlay)
            self.assertEqual(nc.game_log[0]["text"], "join")
            # Both the client and the shared overlay see the same buffer.
            self.assertEqual(overlay.game_log[0]["text"], "join")
        finally:
            nc.close()


if __name__ == "__main__":
    unittest.main()
