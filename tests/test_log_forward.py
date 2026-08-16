import json
import socket
import subprocess
import sys
import time
import unittest

import log_interface as li


def recv_lines(sock, deadline=4.0, wanted=None):
    """Collect newline-delimited JSON lines until `wanted` lines arrive or deadline."""
    lines = []
    buf = b""
    end = time.time() + deadline
    while time.time() < end and (wanted is None or len(lines) < wanted):
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if line:
                lines.append(json.loads(line))
    return lines


class ParseForwardAddressTest(unittest.TestCase):

    def test_host_and_port(self):
        self.assertEqual(li.parse_forward_address("192.168.1.5:9100"), ("192.168.1.5", 9100))
        self.assertEqual(li.parse_forward_address("logs.example.com:9000"), ("logs.example.com", 9000))

    def test_bare_port_defaults_to_localhost(self):
        self.assertEqual(li.parse_forward_address(":9000"), ("127.0.0.1", 9000))

    def test_invalid_address_raises(self):
        for bad in ("9000", "host", "host:port", ""):
            with self.assertRaises(ValueError):
                li.parse_forward_address(bad)


class ForwarderTest(unittest.TestCase):

    def setUp(self):
        li.set_log_level(li.DEBUG)
        li.stop_log_server()
        li.stop_log_forward()
        with li._broadcast_lock:
            li._broadcast_queue.clear()
        with li._forward_lock:
            li._forwarder_queue.clear()
        self.server = None

    def tearDown(self):
        if getattr(self, "server", None) is not None:
            self.server.stop()
            self.server = None
        li.stop_log_forward()
        li.stop_log_server()

    def test_forwarder_pushes_entries_to_central_server(self):
        self.server = li.LogServer("127.0.0.1", 0)
        self.server.start()

        term = socket.create_connection(("127.0.0.1", self.server.port), timeout=3)
        term.settimeout(0.2)

        fwd = li.LogForwarder("127.0.0.1", self.server.port)
        fwd.start()
        time.sleep(0.5)
        li.get_logger("ForwarderSrc").info("forwarded-hello")
        lines = recv_lines(term, wanted=1)

        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["message"], "forwarded-hello")
        self.assertEqual(lines[0]["source"], "ForwarderSrc")
        fwd.stop()
        term.close()

    def test_forwarded_entries_are_not_echoed_back_to_their_source(self):
        self.server = li.LogServer("127.0.0.1", 0)
        self.server.start()

        client_a = socket.create_connection(("127.0.0.1", self.server.port), timeout=3)
        client_a.settimeout(0.2)
        client_b = socket.create_connection(("127.0.0.1", self.server.port), timeout=3)
        client_b.settimeout(0.2)
        time.sleep(0.2)

        client_a.sendall(b'{"level": "INFO", "time": "t", "source": "A", "message": "from-a"}\n')

        got_a = recv_lines(client_a, deadline=1.0)
        self.assertEqual(len(got_a), 0, "sender must not see its own entry echoed back")

        got_b = recv_lines(client_b, wanted=1)
        self.assertEqual(got_b[0]["message"], "from-a")
        client_a.close()
        client_b.close()

    def test_forwarder_reconnects_when_central_server_starts_later(self):
        fwd = li.LogForwarder("127.0.0.1", 9999)
        fwd.start()
        time.sleep(0.5)
        self.assertIsNone(fwd._sock)

        self.server = li.LogServer("127.0.0.1", 0)
        self.server.start()

        term = socket.create_connection(("127.0.0.1", self.server.port), timeout=3)
        term.settimeout(0.2)

        fwd.host = "127.0.0.1"
        fwd.port = self.server.port
        time.sleep(2.5)
        self.assertIsNotNone(fwd._sock)

        li.get_logger("ReconnectSrc").info("reconnect-ok")
        lines = recv_lines(term, wanted=1)
        self.assertEqual(lines[0]["message"], "reconnect-ok")
        fwd.stop()
        term.close()

    def test_singleton_start_stop_forward_is_idempotent(self):
        fwd = li.start_log_forward("127.0.0.1", 9999)
        self.assertIsNotNone(fwd)
        again = li.start_log_forward("127.0.0.1", 9999)
        self.assertIs(fwd, again)
        li.stop_log_forward()
        self.assertIsNone(li._forwarder)
        li.stop_log_forward()  # second stop is a no-op


class AggregatorTest(unittest.TestCase):

    def setUp(self):
        li.set_log_level(li.DEBUG)
        li.stop_log_server()
        li.stop_log_forward()
        with li._broadcast_lock:
            li._broadcast_queue.clear()
        with li._forward_lock:
            li._forwarder_queue.clear()
        self.server = None

    def tearDown(self):
        if getattr(self, "server", None) is not None:
            self.server.stop()
            self.server = None
        li.stop_log_forward()
        li.stop_log_server()

    def test_two_terminals_both_receive_a_local_entry(self):
        self.server = li.LogServer("127.0.0.1", 0)
        self.server.start()

        term1 = socket.create_connection(("127.0.0.1", self.server.port), timeout=3)
        term1.settimeout(0.2)
        term2 = socket.create_connection(("127.0.0.1", self.server.port), timeout=3)
        term2.settimeout(0.2)
        time.sleep(0.2)

        li.get_logger("BroadcastSrc").info("to-everyone")
        lines1 = recv_lines(term1, wanted=1)
        lines2 = recv_lines(term2, wanted=1)

        self.assertEqual(lines1[0]["message"], "to-everyone")
        self.assertEqual(lines2[0]["message"], "to-everyone")
        term1.close()
        term2.close()

    def test_forwarder_and_terminal_aggregate_on_one_port(self):
        self.server = li.LogServer("127.0.0.1", 0)
        self.server.start()

        term = socket.create_connection(("127.0.0.1", self.server.port), timeout=3)
        term.settimeout(0.2)
        fwd = li.LogForwarder("127.0.0.1", self.server.port)
        fwd.start()
        time.sleep(0.5)

        li.get_logger("AggregateLocal").info("local-msg")
        li.get_logger("AggregateRemote").info("remote-msg")
        lines = recv_lines(term, wanted=3)

        messages = set(e["message"] for e in lines)
        self.assertIn("local-msg", messages)
        self.assertIn("remote-msg", messages)
        fwd.stop()
        term.close()


class CrossProcessForwarderTest(unittest.TestCase):
    """True end-to-end: a subprocess forwards its own logs to this process."""

    def setUp(self):
        li.set_log_level(li.DEBUG)
        li.stop_log_server()
        li.stop_log_forward()
        with li._broadcast_lock:
            li._broadcast_queue.clear()
        with li._forward_lock:
            li._forwarder_queue.clear()
        self.server = None

    def tearDown(self):
        if getattr(self, "server", None) is not None:
            self.server.stop()
            self.server = None
        li.stop_log_forward()
        li.stop_log_server()

    def test_subprocess_forwards_logs_to_central_server(self):
        self.server = li.LogServer("127.0.0.1", 0)
        self.server.start()

        term = socket.create_connection(("127.0.0.1", self.server.port), timeout=3)
        term.settimeout(0.2)

        script = (
            "import sys,time\n"
            "sys.path.insert(0,'/home/mmolo39/Documents/Cdings/xpilot-webnet')\n"
            "import log_interface as li\n"
            "li.start_log_forward('127.0.0.1', %d)\n"
            "time.sleep(0.5)\n"
            "li.get_logger('SubProc').info('from-subprocess')\n"
            "time.sleep(0.5)\n"
            "li.stop_log_forward()\n"
        ) % self.server.port
        proc = subprocess.Popen([sys.executable, "-c", script])
        proc.wait(timeout=15)

        lines = recv_lines(term, wanted=1)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["message"], "from-subprocess")
        self.assertEqual(lines[0]["source"], "SubProc")
        term.close()


if __name__ == "__main__":
    unittest.main()
