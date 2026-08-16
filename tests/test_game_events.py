"""
Tests for the in-game chat/log system (issue #32).

The relay server owns the in-game log. It assigns every logged event a
monotonically increasing "seq", keeps a bounded replay history that late
joiners receive, and re-broadcasts each event to *all* clients so every
player sees the same messages at the same time.

Gameplay events that appear in the log:

* join     — a player connected ("X joined the game")
* leave    — a player disconnected
* death    — the host broadcast an authoritative player_hp with dead=True
             ("X was destroyed by Y"), de-duplicated per respawn cycle
* chat     — a player sent a chat message
* pickup   — a player collected a power-up

Run with:  python -m unittest discover -s tests -p '*.py' -v
"""

import asyncio
import json
import unittest

import websockets

import ws_server
from pickup_sync import PowerUpManager


class GameEventUnitTest(unittest.TestCase):
    """Unit tests for the pure server-side log machinery."""

    def setUp(self):
        ws_server.reset_game_events()

    def test_make_game_event_assigns_monotonic_seq(self):
        e1 = ws_server._make_game_event("join", id="A", name="A")
        e2 = ws_server._make_game_event("death", id="B", name="B", killer="A")
        self.assertEqual(e1["seq"], 1)
        self.assertEqual(e2["seq"], 2)
        self.assertLess(e1["seq"], e2["seq"])
        self.assertEqual(e1["type"], "game_event")
        self.assertEqual(e1["event"], "join")
        self.assertEqual(e1["id"], "A")
        self.assertTrue(e1["time"] > 0)
        self.assertEqual(e2["event"], "death")
        self.assertEqual(e2["killer"], "A")

    def test_history_is_bounded(self):
        ws_server.GAME_EVENT_MAX_HISTORY = 5
        try:
            for i in range(20):
                ws_server._make_game_event("chat", id="A", name="A", text=str(i))
            self.assertEqual(len(ws_server.EVENT_HISTORY), 5)
            self.assertEqual(ws_server.EVENT_HISTORY[0]["seq"], 16)
            self.assertEqual(ws_server.EVENT_HISTORY[-1]["seq"], 20)
        finally:
            ws_server.GAME_EVENT_MAX_HISTORY = 200

    def test_reset_game_events(self):
        ws_server._make_game_event("join", id="A", name="A")
        ws_server.DEAD_PLAYERS["B"] = True
        ws_server.reset_game_events()
        self.assertEqual(ws_server.EVENT_HISTORY, [])
        self.assertEqual(ws_server.EVENT_SEQ, 0)
        self.assertEqual(ws_server.DEAD_PLAYERS, {})


class GameEventRelayTest(unittest.TestCase):
    """Integration tests: events are authored and broadcast over the relay."""

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
        ws_server.POWERUP_MANAGER = None
        ws_server.reset_game_events()

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

    def tearDown(self):
        self.server.close()
        self.loop.run_until_complete(self.server.wait_closed())

    # -- helpers -------------------------------------------------------------
    def run_coro(self, coro):
        return self.loop.run_until_complete(coro)

    async def connect(self, cid):
        ws = await websockets.connect(f"ws://127.0.0.1:{self.port}")
        await ws.send(json.dumps({"type": "join", "id": cid, "name": cid}))
        while True:
            raw = await asyncio.wait_for(ws.recv(), 5.0)
            msg = json.loads(raw)
            if msg.get("type") == "roster":
                break
        return ws

    async def collect(self, ws, seconds, mtype=None):
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

    # -- join / history ------------------------------------------------------
    def test_join_publishes_event_to_everyone(self):
        async def scenario():
            a = await self.connect("A")
            got_a = await self.collect(a, 0.5, mtype="game_event")
            # A's own join event is sent to A as well.
            joins = [m for m in got_a if m.get("event") == "join"]
            self.assertEqual(len(joins), 1)
            self.assertEqual(joins[0]["id"], "A")
            self.assertEqual(joins[0]["name"], "A")

            b = await self.connect("B")
            got_b = await self.collect(b, 0.5, mtype="game_event")
            joins_b = [m for m in got_b if m.get("event") == "join"]
            self.assertEqual(len(joins_b), 1)
            self.assertEqual(joins_b[0]["id"], "B")

            # A must also have seen B's join event.
            await b.send(json.dumps({"type": "leave", "id": "B"}))
            await a.close()
        self.run_coro(scenario())

    def test_late_joiner_receives_log_history(self):
        async def scenario():
            a = await self.connect("A")
            b = await self.connect("B")
            # A records a death (via a host-authoritative player_hp broadcast).
            await a.send(json.dumps({
                "type": "player_hp", "id": "B", "hp": 0,
                "dead": True, "killer": "A",
            }))
            await asyncio.sleep(0.3)

            c = await self.connect("C")
            got = await self.collect(c, 0.6, mtype="log_history")
            self.assertEqual(len(got), 1)
            events = got[0]["events"]
            self.assertTrue(any(
                e.get("event") == "join" and e.get("id") == "A" for e in events
            ))
            deaths = [e for e in events if e.get("event") == "death"]
            self.assertEqual(len(deaths), 1)
            self.assertEqual(deaths[0]["id"], "B")
            self.assertEqual(deaths[0]["killer"], "A")
            self.assertEqual(deaths[0]["killerName"], "A")
            # Seq numbers in history are unique and ascending.
            seqs = [e["seq"] for e in events]
            self.assertEqual(seqs, sorted(set(seqs)))
            await a.close()
            await b.close()
            await c.close()
        self.run_coro(scenario())

    # -- death ---------------------------------------------------------------
    def test_death_event_is_broadcast_with_killer_names(self):
        async def scenario():
            a = await self.connect("A")
            b = await self.connect("B")
            await a.send(json.dumps({
                "type": "player_hp", "id": "B", "hp": 0,
                "dead": True, "killer": "A",
            }))
            got_a = await self.collect(a, 0.6, mtype="game_event")
            got_b = await self.collect(b, 0.6, mtype="game_event")
            for got in (got_a, got_b):
                deaths = [m for m in got if m.get("event") == "death"]
                self.assertEqual(len(deaths), 1)
                d = deaths[0]
                self.assertEqual(d["id"], "B")
                self.assertEqual(d["name"], "B")
                self.assertEqual(d["killer"], "A")
                self.assertEqual(d["killerName"], "A")
                self.assertTrue(d["seq"] > 0)
            await a.close()
            await b.close()
        self.run_coro(scenario())

    def test_death_is_deduplicated_and_reset_on_respawn(self):
        async def scenario():
            a = await self.connect("A")
            b = await self.connect("B")
            hp = {
                "type": "player_hp", "id": "B", "hp": 0,
                "dead": True, "killer": "A",
            }
            # Two identical broadcasts (a host retransmit) must log once.
            await a.send(json.dumps(hp))
            await a.send(json.dumps(hp))
            await asyncio.sleep(0.3)
            deaths = await self.collect(b, 0.4, mtype="game_event")
            deaths = [m for m in deaths if m.get("event") == "death"]
            self.assertEqual(len(deaths), 1)

            # Respawn clears the flag, so a later death logs again.
            await a.send(json.dumps({
                "type": "player_hp", "id": "B", "hp": 3, "dead": False,
                "killer": "A",
            }))
            await asyncio.sleep(0.2)
            await a.send(json.dumps(hp))
            await asyncio.sleep(0.2)
            deaths = await self.collect(b, 0.4, mtype="game_event")
            deaths = [m for m in deaths if m.get("event") == "death"]
            self.assertEqual(len(deaths), 1)
            await a.close()
            await b.close()
        self.run_coro(scenario())

    # -- chat ----------------------------------------------------------------
    def test_chat_is_broadcast_as_game_event(self):
        async def scenario():
            a = await self.connect("A")
            b = await self.connect("B")
            await a.send(json.dumps({"type": "chat", "id": "A", "text": "hello"}))
            got_a = await self.collect(a, 0.6, mtype="game_event")
            got_b = await self.collect(b, 0.6, mtype="game_event")
            for got in (got_a, got_b):
                chats = [m for m in got if m.get("event") == "chat"]
                self.assertEqual(len(chats), 1)
                self.assertEqual(chats[0]["id"], "A")
                self.assertEqual(chats[0]["name"], "A")
                self.assertEqual(chats[0]["text"], "hello")
            # Whitespace-only chat is dropped.
            await a.send(json.dumps({"type": "chat", "id": "A", "text": "   "}))
            got = await self.collect(b, 0.4, mtype="game_event")
            self.assertFalse(any(m.get("event") == "chat" for m in got))
            await a.close()
            await b.close()
        self.run_coro(scenario())

    # -- leave ---------------------------------------------------------------
    def test_leave_publishes_event(self):
        async def scenario():
            a = await self.connect("A")
            b = await self.connect("B")
            await b.send(json.dumps({"type": "leave", "id": "B"}))
            got = await self.collect(a, 0.6, mtype="game_event")
            leaves = [m for m in got if m.get("event") == "leave"]
            self.assertEqual(len(leaves), 1)
            self.assertEqual(leaves[0]["id"], "B")
            self.assertEqual(leaves[0]["name"], "B")
            await a.close()
        self.run_coro(scenario())

    # -- pickup --------------------------------------------------------------
    def test_pickup_event_is_published(self):
        async def scenario():
            ws_server.POWERUP_MANAGER = PowerUpManager()
            a = await self.connect("A")
            pickup = next(iter(ws_server.POWERUP_MANAGER.pickups.values()))
            # Position A exactly on the pickup so collection is valid.
            await a.send(json.dumps({
                "type": "state", "id": "A", "x": pickup["x"], "y": pickup["y"],
                "angle": 0.0, "vx": 0.0, "vy": 0.0, "dead": False,
            }))
            await asyncio.sleep(0.2)
            await a.send(json.dumps({
                "type": "pickup_request", "id": "A", "pickupId": pickup["id"],
            }))
            got = await self.collect(a, 0.6, mtype="game_event")
            pickups = [m for m in got if m.get("event") == "pickup"]
            self.assertEqual(len(pickups), 1)
            self.assertEqual(pickups[0]["id"], "A")
            self.assertEqual(pickups[0]["name"], "A")
            self.assertEqual(pickups[0]["item"], pickup["kind"])
            await a.close()
        self.run_coro(scenario())


if __name__ == "__main__":
    unittest.main()
