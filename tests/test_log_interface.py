"""
Tests for log_interface.py (issue #30 — centralized in-game logging).

Covers the acceptance criteria of the issue:

* A centralized logging interface is implemented (get_logger / Logger).
* Log messages include timestamps, severity, and source information.
* Logging operations are thread-safe.
* Log levels can be configured and filtered (per logger and globally).
* The interface is ready for external log consumers (TCP streaming).
* Buffered broadcasting never blocks the caller and never leaks memory
  when no log server is running.

Run with:  python -m unittest discover -s tests -p '*.py' -v
"""

import contextlib
import io
import json
import re
import socket
import threading
import time
import unittest

import log_interface as li

LINE_RE = re.compile(
    r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\] "
    r"\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\] \[([^\]]+)\] (.*)$"
)

TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$")


def capture_stderr(fn):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        fn()
    return buf.getvalue()


class LoggerFormatTest(unittest.TestCase):

    def setUp(self):
        li.set_log_level(li.DEBUG)

    def test_log_lines_include_timestamp_severity_and_source(self):
        log = li.get_logger("FormatSrc")
        out = capture_stderr(lambda: log.info("format-message"))
        lines = [l for l in out.splitlines() if "format-message" in l]
        self.assertEqual(len(lines), 1)
        m = LINE_RE.match(lines[0])
        self.assertIsNotNone(m, f"unexpected format: {lines[0]!r}")
        level, source, message = m.groups() if m else ("", "", "")
        self.assertEqual(level, "INFO")
        self.assertEqual(source, "FormatSrc")
        self.assertEqual(message, "format-message")

    def test_all_level_methods_exist_and_are_formatted(self):
        log = li.get_logger("LevelSrc")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            log.debug("msg-DEBUG")
            log.info("msg-INFO")
            log.warning("msg-WARNING")
            log.error("msg-ERROR")
            log.critical("msg-CRITICAL")
        for name in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            self.assertIn(f"[{name}] [LevelSrc] msg-{name}", buf.getvalue())


class LevelFilterTest(unittest.TestCase):

    def setUp(self):
        li.set_log_level(li.DEBUG)

    def test_per_logger_level_filters_lower_messages(self):
        log = li.get_logger("FilterSrc", level=li.WARNING)
        out = capture_stderr(lambda: (
            log.debug("f-debug"),
            log.info("f-info"),
            log.warning("f-warning"),
            log.error("f-error"),
        ))
        self.assertNotIn("f-debug", out)
        self.assertNotIn("f-info", out)
        self.assertIn("f-warning", out)
        self.assertIn("f-error", out)

    def test_global_level_filters_every_logger(self):
        li.set_log_level(li.INFO)
        log = li.get_logger("GlobalSrc")
        out = capture_stderr(lambda: (
            log.debug("g-debug"),
            log.info("g-info"),
            log.warning("g-warning"),
        ))
        self.assertNotIn("g-debug", out)
        self.assertIn("g-info", out)
        self.assertIn("g-warning", out)

    def test_global_level_can_be_set_by_name(self):
        li.set_log_level("warning")
        self.assertEqual(li.get_log_level(), li.WARNING)
        li.set_log_level("ERROR")
        self.assertEqual(li.get_log_level(), li.ERROR)
        li.set_log_level("debug")
        self.assertEqual(li.get_log_level(), li.DEBUG)

    def test_unknown_level_name_raises(self):
        with self.assertRaises(ValueError):
            li.set_log_level("bogus")
        with self.assertRaises(ValueError):
            li.set_log_level(1.5)


class ThreadSafetyTest(unittest.TestCase):

    def setUp(self):
        li.set_log_level(li.DEBUG)

    def test_concurrent_logging_from_many_threads_is_safe(self):
        log = li.get_logger("ThreadSrc")
        errors = []

        def worker(n):
            try:
                for i in range(150):
                    log.info(f"thread-{n}-{i}")
            except Exception as e:  # pragma: no cover - only on failure
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


class LogServerTest(unittest.TestCase):

    def setUp(self):
        li.set_log_level(li.DEBUG)
        self.server = None

    def tearDown(self):
        if self.server is not None:
            self.server.stop()
        li.stop_log_server()
        with li._broadcast_lock:
            li._broadcast_queue.clear()

    def _read_json_lines(self, sock, want_message, timeout=3.0):
        buf = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if entry.get("message") == want_message:
                    return entry
        self.fail(f"message {want_message!r} not received")

    def test_streams_json_entries_to_a_connected_terminal(self):
        self.server = li.LogServer("127.0.0.1", 0)
        self.server.start()
        cli = socket.create_connection(("127.0.0.1", self.server.port), timeout=3)
        cli.settimeout(0.5)
        time.sleep(0.1)

        log = li.get_logger("StreamSrc")
        log.info("stream-hello")

        entry = self._read_json_lines(cli, "stream-hello")
        self.assertEqual(entry["level"], "INFO")
        self.assertEqual(entry["source"], "StreamSrc")
        self.assertEqual(entry["message"], "stream-hello")
        self.assertRegex(entry["time"], TIME_RE)
        cli.close()

    def test_started_server_uses_the_bound_port(self):
        self.server = li.LogServer("127.0.0.1", 0)
        self.server.start()
        self.assertGreater(self.server.port, 0)
        self.assertNotEqual(self.server.port, 0)

    def test_server_can_be_stopped_and_restarted(self):
        srv = li.LogServer("127.0.0.1", 0)
        srv.start()
        port1 = srv.port
        srv.stop()
        srv2 = li.LogServer("127.0.0.1", 0)
        srv2.start()
        self.assertGreater(srv2.port, 0)
        srv2.stop()
        self.assertNotEqual(port1, 0)

    def test_port_in_use_raises_oserror(self):
        self.server = li.LogServer("127.0.0.1", 0)
        self.server.start()
        clash = li.LogServer("127.0.0.1", self.server.port)
        with self.assertRaises(OSError):
            clash.start()

    def test_singleton_start_stop_is_idempotent(self):
        srv = li.start_log_server(port=0)
        self.assertIsNotNone(srv)
        again = li.start_log_server(port=0)
        self.assertIs(srv, again)
        li.stop_log_server()
        self.assertIsNone(li._log_server)
        li.stop_log_server()  # second stop is a no-op


class NoServerMemoryLeakTest(unittest.TestCase):

    def setUp(self):
        li.set_log_level(li.DEBUG)
        li.stop_log_server()
        with li._broadcast_lock:
            li._broadcast_queue.clear()

    def test_messages_are_dropped_when_no_server_is_running(self):
        log = li.get_logger("LeakSrc")
        for _ in range(200):
            log.info("spam")
        with li._broadcast_lock:
            self.assertEqual(len(li._broadcast_queue), 0)


if __name__ == "__main__":
    unittest.main()
