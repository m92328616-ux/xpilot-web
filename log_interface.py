"""
Centralized logging interface for XPilot.

Provides thread-safe logging with multiple levels and optional
real-time streaming to external terminal clients over TCP.

Usage from any module:
    from log_interface import get_logger, set_log_level
    log = get_logger("MyModule")
    log.info("Something happened")
    log.error("Something broke")

    # Optionally raise the global minimum level (accepts a name or int)
    set_log_level("warning")   # debug/info are now suppressed everywhere

To enable TCP streaming (for external terminal clients):
    from log_interface import LogServer
    server = LogServer(port=9000)
    server.start()
    # ... later ...
    server.stop()

External terminals connect to localhost:9000 and receive one JSON
log line per TCP message.

To push this process's logs to a central log server on another
process (so one port/viewer can show logs from several processes):
    from log_interface import start_log_forward
    start_log_forward("127.0.0.1", 9000)   # central server hosts port 9000
    # ... later ...
    stop_log_forward()

The central LogServer re-broadcasts forwarded entries to every
connected terminal, so a single log_terminal on port 9000 shows logs
from every process that forwards to it.
"""
import socket
import threading
import time
import json
import sys
from datetime import datetime


# Log levels
DEBUG = 10
INFO = 20
WARNING = 30
ERROR = 40
CRITICAL = 50

_LEVEL_NAMES = {
    DEBUG: "DEBUG",
    INFO: "INFO",
    WARNING: "WARNING",
    ERROR: "ERROR",
    CRITICAL: "CRITICAL",
}

LEVELS = {
    "debug": DEBUG,
    "info": INFO,
    "warning": WARNING,
    "error": ERROR,
    "critical": CRITICAL,
}

# Global minimum level applied to every logger (a stricter per-logger level
# can still be set via get_logger(source, level=...)). Guards against
# forgetting to pass level= on a logger, and allows runtime configuration
# from CLI flags such as --log-level.
_global_level = DEBUG
_level_lock = threading.Lock()


def set_log_level(level):
    """Set the global minimum log level for all loggers.

    Args:
        level: An int (DEBUG/INFO/WARNING/ERROR/CRITICAL) or a case-insensitive
               name such as "info". Thread-safe.
    """
    global _global_level
    if isinstance(level, str):
        key = level.strip().lower()
        if key not in LEVELS:
            raise ValueError(
                f"Unknown log level {level!r}. Valid values: {', '.join(sorted(LEVELS))}"
            )
        level = LEVELS[key]
    if not isinstance(level, int):
        raise ValueError(f"Log level must be an int or name, got {level!r}")
    with _level_lock:
        _global_level = level


def get_log_level():
    """Return the current global minimum log level (an int)."""
    with _level_lock:
        return _global_level


class Logger:
    """Thread-safe logger that writes to local output and streams to connected terminals."""

    def __init__(self, source, level=DEBUG):
        self.source = source
        self.level = level

    def _log(self, level, message):
        if level < self.level or level < get_log_level():
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        level_name = _LEVEL_NAMES.get(level, str(level))
        formatted = f"[{ts}] [{level_name}] [{self.source}] {message}"
        # Write to stderr so it doesn't interfere with pygame stdout usage
        print(formatted, file=sys.stderr, flush=True)
        # Broadcast to connected terminal clients
        _broadcast(level_name, ts, self.source, message)

    def debug(self, message):
        self._log(DEBUG, message)

    def info(self, message):
        self._log(INFO, message)

    def warning(self, message):
        self._log(WARNING, message)

    def error(self, message):
        self._log(ERROR, message)

    def critical(self, message):
        self._log(CRITICAL, message)


def get_logger(source, level=DEBUG):
    """Get a named logger instance.

    Args:
        source: Module/component name (e.g. "Game", "Network").
        level: Minimum log level to emit. Defaults to DEBUG.
    """
    return Logger(source, level)


# ---------------------------------------------------------------------------
# TCP log streaming – a local listener (LogServer) that external terminals
# and other processes connect to, plus an optional outbound forwarder
# (LogForwarder) that pushes every log entry to a central log server.
#
# Every log entry is queued as (payload, origin_sock). origin_sock is None
# for entries produced locally (broadcast to everyone) and the sending
# socket for entries forwarded in from another process (never echoed back
# to their source).
# ---------------------------------------------------------------------------

_clients = []           # connected socket objects (terminals + central-side forwarder conns)
_clients_lock = threading.Lock()
_broadcast_queue = []   # list of (payload, origin_sock_or_None) tuples
_broadcast_lock = threading.Condition()
_streaming = False      # True while at least one sink (server/forwarder) is active
_sinks = 0              # number of active sinks
_sender_thread = None   # the single shared sender thread

_forwarder_queue = []   # locally-produced payloads awaiting a connected forwarder
_forward_lock = threading.Condition()
_forwarding = False     # True while a LogForwarder is active


def _ensure_sender_locked():
    """Start the shared sender thread if it is not already running.

    Must be called with _broadcast_lock held.
    """
    global _sender_thread
    if _sender_thread is None or not _sender_thread.is_alive():
        _sender_thread = threading.Thread(
            target=_sender_loop, daemon=True, name="log-sender"
        )
        _sender_thread.start()


def _sink_acquire():
    """Register an active sink (a LogServer or LogForwarder). Thread-safe."""
    global _sinks, _streaming
    with _broadcast_lock:
        _sinks += 1
        if not _streaming:
            _streaming = True
            _broadcast_lock.notify()
        _ensure_sender_locked()


def _sink_release():
    """Unregister an active sink. Thread-safe."""
    global _sinks, _streaming
    with _broadcast_lock:
        _sinks = max(0, _sinks - 1)
        if _sinks == 0:
            _streaming = False
            _broadcast_lock.notify()


def _broadcast(level_name, timestamp, source, message):
    """Queue a locally-produced log entry for all connected terminals.

    When no sink (server/forwarder) is active, entries are dropped instead
    of queued forever, so logging never leaks memory even if every game
    system logs profusely. Non-blocking.
    """
    if not _streaming:
        return
    payload = json.dumps({
        "level": level_name,
        "time": timestamp,
        "source": source,
        "message": message,
    })

    # If we're forwarding to a central server, buffer the payload for the
    # forwarder thread and avoid locally enqueueing the same payload. This
    # prevents a duplicate delivery path when both a forwarder and local
    # terminal clients are connected (forwarder -> server -> terminals and
    # local broadcast -> forwarder -> server -> terminals would double-send).
    if _forwarding:
        with _forward_lock:
            _forwarder_queue.append(payload)
            _forward_lock.notify()
        return

    # Only enqueue for local broadcast when there are connected clients.
    # If no terminals are connected, drop the message (prevents memory leaks).
    with _clients_lock:
        has_clients = bool(_clients)
    if not has_clients:
        return

    with _broadcast_lock:
        _broadcast_queue.append((payload, None))
        _broadcast_lock.notify()


def _receive(payload, origin_sock):
    """Re-broadcast a log line received from a connected process/client.

    The line is sent to every client except origin_sock, so a forwarding
    process never sees its own entries echoed back.
    """
    if not _streaming:
        return
    with _broadcast_lock:
        _broadcast_queue.append((payload, origin_sock))
        _broadcast_lock.notify()


def _sender_loop():
    """Background thread that drains the broadcast queue and sends to all clients."""
    global _sender_thread, _broadcast_queue
    try:
        while True:
            with _broadcast_lock:
                while _streaming and not _broadcast_queue:
                    _broadcast_lock.wait(timeout=0.5)
                if not _streaming and not _broadcast_queue:
                    return
                batch = _broadcast_queue
                _broadcast_queue = []

            if not batch:
                continue

            dead = []
            with _clients_lock:
                targets = list(_clients)

            for sock in targets:
                for payload, origin in batch:
                    if origin is sock:
                        continue
                    try:
                        sock.sendall((payload + "\n").encode("utf-8"))
                    except Exception:
                        dead.append(sock)
                        break

            if dead:
                with _clients_lock:
                    for s in dead:
                        try:
                            _clients.remove(s)
                        except ValueError:
                            pass
                        try:
                            s.close()
                        except Exception:
                            pass
    finally:
        with _broadcast_lock:
            if _sender_thread is threading.current_thread():
                _sender_thread = None


def _client_reader(sock):
    """Read JSON lines sent by a connected client and re-broadcast them.

    This is what makes a LogServer an aggregator: forwarded entries that
    arrive over the wire are re-broadcast to every other connected client.
    """
    buf = b""
    try:
        while True:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            except Exception:
                break
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if line:
                    _receive(line.decode("utf-8", "replace"), sock)
    finally:
        with _clients_lock:
            if sock in _clients:
                _clients.remove(sock)
        try:
            sock.close()
        except Exception:
            pass


class LogServer:
    """TCP server that external terminal clients connect to for real-time logs.

    Also accepts connections from LogForwarders on other processes: any JSON
    lines they send are re-broadcast to every other connected client, so a
    single port can aggregate logs from several processes.
    """

    def __init__(self, host="127.0.0.1", port=9000):
        self.host = host
        self.requested_port = port
        self.port = port
        self._sock = None
        self._running = False
        self._accept_thread = None

    def start(self):
        """Start the log server in background threads."""
        if self._running:
            return

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.bind((self.host, self.requested_port))
        except OSError:
            self._sock.close()
            self._sock = None
            raise
        self.port = self._sock.getsockname()[1]
        self._sock.listen(5)
        self._sock.settimeout(1.0)
        self._running = True

        _sink_acquire()

        self._accept_thread = threading.Thread(
            target=self._accept_loop, daemon=True, name="log-accept"
        )
        self._accept_thread.start()
        print(f"[LogServer] Listening on {self.host}:{self.port}", file=sys.stderr)

    def _accept_loop(self):
        while self._running:
            sock = self._sock
            if sock is None:
                break
            try:
                client_sock, addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client_sock.settimeout(5.0)
            with _clients_lock:
                _clients.append(client_sock)
            print(f"[LogServer] Terminal connected from {addr}", file=sys.stderr)
            threading.Thread(
                target=_client_reader, args=(client_sock,), daemon=True, name="log-reader"
            ).start()

    def stop(self):
        """Shut down the log server and close all client connections."""
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        with _clients_lock:
            for s in _clients:
                try:
                    s.close()
                except Exception:
                    pass
            _clients.clear()
        _sink_release()


class LogForwarder:
    """Outbound TCP client that pushes every local log entry to a central server.

    If the central server is unreachable or drops the connection, the
    forwarder keeps retrying in the background. Entries produced while
    disconnected are buffered and pushed once a connection is (re)established.
    Anything the central server sends back is drained and discarded, so the
    forwarder never feeds its own entries back into the wire.
    """

    def __init__(self, host, port, reconnect_delay=1.0):
        self.host = host
        self.port = port
        self.reconnect_delay = reconnect_delay
        self._running = False
        self._sock = None
        self._sock_lock = threading.Lock()

    def start(self):
        """Start the forwarder in a background thread."""
        global _forwarding
        if self._running:
            return
        self._running = True
        _sink_acquire()
        with _forward_lock:
            _forwarding = True
            _forward_lock.notify()
        threading.Thread(target=self._loop, daemon=True, name="log-forwarder").start()

    def _connect(self):
        try:
            sock = socket.create_connection((self.host, self.port), timeout=5.0)
        except OSError:
            return None
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(0.2)
        return sock

    def _loop(self):
        while self._running:
            sock = self._connect()
            if sock is None:
                self._sleep(self.reconnect_delay)
                continue
            with self._sock_lock:
                self._sock = sock
            print(f"[LogForwarder] Connected to {self.host}:{self.port}", file=sys.stderr)
            self._push(sock)
            with self._sock_lock:
                if self._sock is sock:
                    self._sock = None
            try:
                sock.close()
            except Exception:
                pass
            print(f"[LogForwarder] Disconnected from {self.host}:{self.port}", file=sys.stderr)
            self._sleep(self.reconnect_delay)

    def _push(self, sock):
        """Push buffered local entries to the central server while draining replies."""
        while self._running:
            try:
                sock.recv(65536)
            except socket.timeout:
                pass
            except Exception:
                return
            with _forward_lock:
                while self._running and not _forwarder_queue:
                    _forward_lock.wait(timeout=0.5)
                if not _forwarder_queue:
                    return
                items = list(_forwarder_queue)
                _forwarder_queue.clear()
            for payload in items:
                try:
                    sock.sendall((payload + "\n").encode("utf-8"))
                except Exception:
                    return

    def _sleep(self, seconds):
        end = time.time() + seconds
        while self._running and time.time() < end:
            time.sleep(0.1)

    def stop(self):
        """Stop the forwarder and release its sink."""
        global _forwarding
        self._running = False
        with self._sock_lock:
            sock = self._sock
            self._sock = None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        with _forward_lock:
            _forwarding = False
            _forward_lock.notify()
        _sink_release()


# Singleton server instance (created lazily by the game)
_log_server = None
_server_lock = threading.Lock()


def start_log_server(host="127.0.0.1", port=9000):
    """Start the singleton log server. Safe to call multiple times.

    If the port is already in use (e.g. another process is already
    serving logs on that port), this silently skips starting the TCP
    server — local stderr logging still works.
    """
    global _log_server
    with _server_lock:
        if _log_server is None:
            _log_server = LogServer(host, port)
            try:
                _log_server.start()
            except OSError:
                print(
                    f"[LogServer] Port {port} already in use — "
                    f"logging to stderr only (no external terminal).",
                    file=sys.stderr,
                )
                _log_server = None
        return _log_server


def stop_log_server():
    """Stop the singleton log server."""
    global _log_server
    with _server_lock:
        if _log_server is not None:
            _log_server.stop()
            _log_server = None


# Singleton forwarder instance
_forwarder = None
_forwarder_lock = threading.Lock()


def start_log_forward(host, port):
    """Start forwarding all log entries to a central log server (singleton).

    Args:
        host: Host of the central log server (e.g. "127.0.0.1").
        port: Port the central log server is listening on.

    Safe to call multiple times. The forwarder reconnects in the background
    if the central server is not yet reachable.
    """
    global _forwarder
    with _forwarder_lock:
        if _forwarder is None:
            _forwarder = LogForwarder(host, port)
            _forwarder.start()
        return _forwarder


def stop_log_forward():
    """Stop the singleton log forwarder."""
    global _forwarder
    with _forwarder_lock:
        if _forwarder is not None:
            _forwarder.stop()
            _forwarder = None


def parse_forward_address(value):
    """Parse a 'HOST:PORT' string into a (host, port) tuple.

    A bare ':PORT' or empty host defaults to "127.0.0.1".
    """
    host, sep, port = str(value).rpartition(":")
    if not sep or not port:
        raise ValueError(f"Invalid log forward address {value!r} (expected HOST:PORT)")
    try:
        port = int(port)
    except ValueError:
        raise ValueError(f"Invalid log forward address {value!r} (expected HOST:PORT)")
    return host or "127.0.0.1", port
