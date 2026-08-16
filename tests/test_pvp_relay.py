"""
Integration tests for PvP message relay in the WebSocket server.

PvP (player versus player) combat (issue #10) relies on the relay server
forwarding three message types between clients:

* player_hit      — a non-host reports damage it dealt to another player;
                    the host applies the authoritative damage.
* player_hp       — the host broadcasts the authoritative HP/elimination
                    state to every client.
* player_respawn  — a player that restarts asks the host to reset its HP.

The relay is not the authority for PvP state (the host-elected client is),
so these tests only assert that the messages are relayed to the other
connected clients.

Run with:  python -m unittest discover -s tests -p '*.py' -v
"""

import asyncio
import json
import unittest

import websockets

import ws_server

WORLD_W = 3200
WORLD_H = 2400


class PvpRelayTest(unittest.TestCase):

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
        # Wait for the server to register us: it replies to the joiner with a
        # roster *after* processing the join, so once we see it we know later
        # messages will be relayed to this socket.
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

    # -- tests ---------------------------------------------------------------
    def test_player_hit_is_relayed_to_other_clients(self):
        async def scenario():
            a = await self.connect("A")
            b = await self.connect("B")
            # A reports shooting B; B (and any other client) must receive it.
            await a.send(json.dumps({
                "type": "player_hit", "id": "A", "target": "B", "dmg": 1,
            }))
            got = await self.collect(b, 0.6, mtype="player_hit")
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0]["id"], "A")
            self.assertEqual(got[0]["target"], "B")
            self.assertEqual(got[0]["dmg"], 1)
            await a.close()
            await b.close()
        self.run_coro(scenario())

    def test_player_hp_is_broadcast_to_other_clients(self):
        async def scenario():
            a = await self.connect("A")
            b = await self.connect("B")
            # Host A broadcasts authoritative HP for target B.
            await a.send(json.dumps({
                "type": "player_hp", "id": "B", "hp": 2,
                "dead": False, "killer": "A",
            }))
            got = await self.collect(b, 0.6, mtype="player_hp")
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0]["id"], "B")
            self.assertEqual(got[0]["hp"], 2)
            self.assertEqual(got[0]["dead"], False)
            self.assertEqual(got[0]["killer"], "A")
            await a.close()
            await b.close()
        self.run_coro(scenario())

    def test_player_hp_elimination_is_relayed(self):
        async def scenario():
            a = await self.connect("A")
            b = await self.connect("B")
            await a.send(json.dumps({
                "type": "player_hp", "id": "B", "hp": 0,
                "dead": True, "killer": "A",
            }))
            got = await self.collect(b, 0.6, mtype="player_hp")
            self.assertEqual(len(got), 1)
            self.assertTrue(got[0]["dead"])
            self.assertEqual(got[0]["hp"], 0)
            await a.close()
            await b.close()
        self.run_coro(scenario())

    def test_player_respawn_is_relayed(self):
        async def scenario():
            a = await self.connect("A")
            b = await self.connect("B")
            await a.send(json.dumps({"type": "player_respawn", "id": "A"}))
            got = await self.collect(b, 0.6, mtype="player_respawn")
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0]["id"], "A")
            await a.close()
            await b.close()
        self.run_coro(scenario())

    def test_player_state_still_relayed_with_pvp_fields(self):
        async def scenario():
            a = await self.connect("A")
            b = await self.connect("B")
            await a.send(json.dumps({
                "type": "state", "id": "A", "x": 100, "y": 200,
                "angle": 1.0, "vx": 0, "vy": 0, "dead": False,
                "hp": 2, "maxHp": 3,
            }))
            got = await self.collect(b, 0.6, mtype="state")
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0]["id"], "A")
            self.assertEqual(got[0]["hp"], 2)
            self.assertEqual(got[0]["maxHp"], 3)
            await a.close()
            await b.close()
        self.run_coro(scenario())


if __name__ == "__main__":
    unittest.main()
