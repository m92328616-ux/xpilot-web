"""
Integration tests for server-authoritative power-up synchronization.

These tests run a real ws_server.py relay together with
pickup_sync.PowerUpManager and connect real WebSocket clients to verify
the acceptance criteria from issue #29:

* All players see the same power-ups at the same locations.
* A power-up can only be collected once.
* Pickup events are synchronized for every connected client.
* Late-joining players receive the correct current power-up state.
* No duplicate or "ghost" power-ups appear.
* Client state resynchronizes via periodic authoritative snapshots.

Run with:  python -m unittest discover -s tests -p '*.py' -v
or:        python -m pytest tests/pickup-sync.test.py -v
"""

import asyncio
import json
import unittest

import websockets

import ws_server
from pickup_sync import PowerUpManager, PICKUP_DEFS

WORLD_W = 3200
WORLD_H = 2400


def far_position(p):
    """A position guaranteed far (under world wrap) from a pickup."""
    return {
        "x": (p["x"] + WORLD_W // 2) % WORLD_W,
        "y": (p["y"] + WORLD_H // 2) % WORLD_H,
    }


def norm_pickup(p):
    """Drop live countdown timers so states captured at different instants
    can be compared (they tick down between snapshots)."""
    out = dict(p)
    out.pop("respawnTimer", None)
    out.pop("fuseTimer", None)
    return out


class PickupSyncTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(cls.loop)

    @classmethod
    def tearDownClass(cls):
        cls.loop.close()

    def setUp(self):
        loop = self.loop
        ws_server.CLIENTS.clear()
        ws_server.CLIENTS_LOCK = asyncio.Lock()
        ws_server.POWERUP_MANAGER = PowerUpManager()
        self.manager = ws_server.POWERUP_MANAGER

        async def start_server():
            return await websockets.serve(
                ws_server.handler,
                "127.0.0.1",
                0,
                ping_interval=None,
                ping_timeout=None,
            )

        self.server = loop.run_until_complete(start_server())
        self.port = self.server.sockets[0].getsockname()[1]
        self.powerup_task = loop.create_task(ws_server.powerup_loop())

    def tearDown(self):
        loop = self.loop
        self.powerup_task.cancel()
        self.server.close()
        loop.run_until_complete(self.server.wait_closed())
        loop.run_until_complete(
            asyncio.gather(self.powerup_task, return_exceptions=True)
        )

    # -- helpers -------------------------------------------------------------
    def run_coro(self, coro):
        return self.loop.run_until_complete(coro)

    async def connect(self, cid):
        ws = await websockets.connect(f"ws://127.0.0.1:{self.port}")
        await ws.send(json.dumps({"type": "join", "id": cid, "name": cid}))
        return ws

    async def next_of_type(self, ws, mtype, timeout=5.0):
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout)
            msg = json.loads(raw)
            if msg.get("type") == mtype:
                return msg

    async def snapshot(self, ws, timeout=5.0):
        return await self.next_of_type(ws, "pickup_state", timeout)

    async def collect_for(self, ws, seconds, mtype=None):
        msgs = []
        end = self.loop.time() + seconds
        while True:
            remaining = end - self.loop.time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            if mtype is None or msg.get("type") == mtype:
                msgs.append(msg)
        return msgs

    async def send_state(self, ws, cid, pos):
        await ws.send(json.dumps({
            "type": "state", "id": cid,
            "x": pos["x"], "y": pos["y"], "angle": 0, "vx": 0, "vy": 0,
            "dead": False,
        }))

    # -- tests ---------------------------------------------------------------
    def test_initial_state_is_identical_for_all_clients(self):
        async def scenario():
            a = await self.connect("A")
            b = await self.connect("B")
            snap_a = await self.snapshot(a)
            snap_b = await self.snapshot(b)

            self.assertEqual(len(snap_a["pickups"]), 80)
            ids = [p["id"] for p in snap_a["pickups"]]
            self.assertEqual(len(ids), len(set(ids)), "power-up IDs must be unique")
            self.assertTrue(all(p["active"] for p in snap_a["pickups"]),
                            "fresh power-ups must all be active")

            # Every client sees the exact same power-up set.
            self.assertEqual(snap_a["pickups"], snap_b["pickups"])

            # Correct kind distribution: the fixed pools are 5 shield + 7
            # fuel_cell, and every random cube may itself be any kind.
            kinds = {}
            for p in snap_a["pickups"]:
                kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
            self.assertGreaterEqual(kinds["shield"], 5)
            self.assertGreaterEqual(kinds["fuel_cell"], 7)
            self.assertEqual(sum(kinds.values()), 80)

            await a.close()
            await b.close()
        self.run_coro(scenario())

    def test_collection_is_validated_and_broadcast_once(self):
        async def scenario():
            a = await self.connect("A")
            b = await self.connect("B")
            snap = await self.snapshot(a)
            target = next(p for p in snap["pickups"] if p["active"])
            pid = target["id"]

            # Collector moves next to the power-up then claims it.
            await self.send_state(a, "A", {"x": target["x"], "y": target["y"]})
            await a.send(json.dumps({"type": "pickup_request", "id": "A", "pickupId": pid}))

            ev_a = await self.next_of_type(a, "pickup_picked")
            ev_b = await self.next_of_type(b, "pickup_picked")

            # Both clients received the identical authoritative pickup event.
            self.assertEqual(ev_a["pickupId"] if "pickupId" in ev_a else ev_a["pickup"]["id"], pid)
            self.assertEqual(ev_a, ev_b)
            self.assertEqual(ev_a["by"], "A")
            self.assertEqual(ev_a["kind"], target["kind"])
            self.assertFalse(ev_a["pickup"]["active"], "picked pickup must be inactive")

            # A second claim by another client must be rejected.
            await b.send(json.dumps({"type": "pickup_request", "id": "B", "pickupId": pid}))
            got = await self.collect_for(b, 0.6, mtype="pickup_picked")
            self.assertTrue(all(m["by"] == "A" for m in got),
                            "pickup must not be collectible twice")

            # Snapshot still shows the pickup inactive (no ghost re-appearance).
            snap2 = await self.snapshot(a)
            entry = next(p for p in snap2["pickups"] if p["id"] == pid)
            self.assertFalse(entry["active"])
            self.assertGreater(entry["respawnTimer"], 0)

            await a.close()
            await b.close()
        self.run_coro(scenario())

    def test_collection_rejected_when_player_too_far(self):
        async def scenario():
            a = await self.connect("A")
            snap = await self.snapshot(a)
            target = next(p for p in snap["pickups"] if p["active"])

            await self.send_state(a, "A", far_position(target))
            await a.send(json.dumps({
                "type": "pickup_request", "id": "A", "pickupId": target["id"],
            }))
            got = await self.collect_for(a, 0.6, mtype="pickup_picked")
            self.assertEqual([m for m in got if m["by"] == "A"], [],
                             "server must reject pickup requests from far away")

            snap2 = await self.snapshot(a)
            entry = next(p for p in snap2["pickups"] if p["id"] == target["id"])
            self.assertTrue(entry["active"], "uncollected pickup stays active")
            await a.close()
        self.run_coro(scenario())

    def test_collection_uses_claimed_position_when_state_is_stale(self):
        # A remote client's relayed last_state can lag behind its real
        # position by a state-send interval plus network latency. The client
        # therefore sends its current x/y with the pickup_request; the server
        # must validate against that claim, not the stale last_state.
        async def scenario():
            a = await self.connect("A")
            snap = await self.snapshot(a)
            target = next(p for p in snap["pickups"] if p["active"])
            pid = target["id"]

            # last_state says the player is far away...
            await self.send_state(a, "A", far_position(target))
            # ...but the player claims to be right on the pickup.
            await a.send(json.dumps({
                "type": "pickup_request", "id": "A", "pickupId": pid,
                "x": target["x"], "y": target["y"],
            }))
            ev = await self.next_of_type(a, "pickup_picked")
            self.assertEqual(ev["by"], "A")
            self.assertEqual(ev["pickup"]["id"], pid)
            self.assertFalse(ev["pickup"]["active"])
            await a.close()
        self.run_coro(scenario())

    def test_late_joiner_receives_current_state(self):
        async def scenario():
            a = await self.connect("A")
            b = await self.connect("B")
            snap = await self.snapshot(a)
            target = next(p for p in snap["pickups"] if p["active"])
            pid = target["id"]

            await self.send_state(a, "A", {"x": target["x"], "y": target["y"]})
            await a.send(json.dumps({"type": "pickup_request", "id": "A", "pickupId": pid}))
            await self.next_of_type(a, "pickup_picked")

            # C joins mid-game: must immediately see the current authoritative
            # state (including the pickup A already collected).
            c = await self.connect("C")
            snap_c = await self.snapshot(c)
            snap_a = await self.snapshot(a)

            # Live respawn/fuse timers tick down between the two snapshots,
            # so compare everything except the countdowns.
            self.assertEqual(
                [norm_pickup(p) for p in snap_a["pickups"]],
                [norm_pickup(p) for p in snap_c["pickups"]],
            )
            entry = next(p for p in snap_c["pickups"] if p["id"] == pid)
            self.assertFalse(entry["active"], "late joiner must see pickup as collected")

            await a.close()
            await b.close()
            await c.close()
        self.run_coro(scenario())

    def test_damage_is_validated_and_broadcast(self):
        async def scenario():
            a = await self.connect("A")
            b = await self.connect("B")
            snap = await self.snapshot(a)

            target = next(p for p in snap["pickups"]
                          if p["active"] and p["kind"] != "fuel_cell")
            pid = target["id"]

            await a.send(json.dumps({
                "type": "pickup_hit", "id": "A", "pickupId": pid,
                "dmg": PICKUP_DEFS[target["kind"]]["hp"], "firedBy": "player",
            }))

            ev_a = await self.next_of_type(a, "pickup_damaged")
            ev_b = await self.next_of_type(b, "pickup_damaged")
            self.assertEqual(ev_a, ev_b)
            self.assertTrue(ev_a["destroyed"])
            self.assertEqual(ev_a["pickup"]["id"], pid)
            self.assertFalse(ev_a["pickup"]["active"])

            snap2 = await self.snapshot(a)
            entry = next(p for p in snap2["pickups"] if p["id"] == pid)
            self.assertFalse(entry["active"])

            await a.close()
            await b.close()
        self.run_coro(scenario())

    def test_fuel_cell_primes_then_explodes_everywhere(self):
        async def scenario():
            a = await self.connect("A")
            b = await self.connect("B")
            snap = await self.snapshot(a)

            target = next(p for p in snap["pickups"]
                          if p["active"] and p["kind"] == "fuel_cell")
            pid = target["id"]
            hp = PICKUP_DEFS["fuel_cell"]["hp"]

            await a.send(json.dumps({
                "type": "pickup_hit", "id": "A", "pickupId": pid,
                "dmg": hp, "firedBy": "player",
            }))

            primed_a = await self.next_of_type(a, "pickup_damaged")
            primed_b = await self.next_of_type(b, "pickup_damaged")
            self.assertEqual(primed_a, primed_b)
            self.assertTrue(primed_a["destroyed"])
            self.assertTrue(primed_a["primed"], "destroyed fuel cell must prime its fuse")
            self.assertTrue(primed_a["pickup"]["fuseActive"])
            self.assertFalse(primed_a["pickup"]["active"])

            # Both clients must see the explosion after the fuse timer.
            expl_a = await self.next_of_type(a, "pickup_exploded", timeout=6.0)
            expl_b = await self.next_of_type(b, "pickup_exploded", timeout=6.0)
            self.assertEqual(expl_a, expl_b)
            self.assertEqual(expl_a["pickup"]["id"], pid)
            self.assertEqual(expl_a["x"], target["x"])
            self.assertEqual(expl_a["y"], target["y"])
            self.assertEqual(expl_a["blastRadius"], PICKUP_DEFS["fuel_cell"]["blastRadius"])
            self.assertFalse(expl_a["pickup"]["fuseActive"])
            self.assertFalse(expl_a["pickup"]["active"])

            await a.close()
            await b.close()
        self.run_coro(scenario())

    def test_periodic_snapshots_provide_resync(self):
        async def scenario():
            a = await self.connect("A")
            s1 = await self.snapshot(a)
            s2 = await self.snapshot(a)
            s3 = await self.snapshot(a)
            self.assertLess(s1["seq"], s2["seq"])
            self.assertLess(s2["seq"], s3["seq"])
            self.assertEqual(s1["pickups"], s2["pickups"])
            await a.close()
        self.run_coro(scenario())


if __name__ == "__main__":
    unittest.main()
