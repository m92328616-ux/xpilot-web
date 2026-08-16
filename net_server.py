"""
Simple UDP relay server for the XPilot minimal game.
Run on a machine reachable by clients.

Usage:
    python net_server.py --host 0.0.0.0 --port 50000

The server starts an HTTP status endpoint on port 8000 by default.
Log output goes through the centralized logging interface
(log_interface.py) and can be streamed to an external terminal via
``--log-port`` (default 9000).
"""
import socket
import argparse
import threading
import http.server
import socketserver
import json

from log_interface import (
    get_logger,
    parse_forward_address,
    set_log_level,
    start_log_forward,
    start_log_server,
    stop_log_forward,
    stop_log_server,
)

log = get_logger("NetServer")


def run(host, port, http_port=8000):
    if http_port is None:
        http_port = 8000
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    log.info(f"Relay server listening on {host}:{port}")
    
    clients = set()
    clients_lock = threading.Lock()

    def start_http(http_port):
        """Start HTTP status endpoint in background thread."""
        class StatusHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != '/status':
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                # snapshot clients
                with clients_lock:
                    cl = list(clients)
                payload = {'clients': len(cl), 'list': [{'ip': c[0], 'port': c[1]} for c in cl]}
                self.wfile.write(json.dumps(payload).encode('utf8'))

            def log_message(self, format, *args):
                pass  # suppress default logging

        try:
            httpd = socketserver.TCPServer(('0.0.0.0', http_port), StatusHandler)
            httpd.allow_reuse_address = True
        except Exception as e:
            log.error(f'HTTP status server failed to start on port {http_port}: {e}')
            return
        log.info(f'HTTP status endpoint listening on 0.0.0.0:{http_port} (GET /status)')
        httpd.serve_forever()

    # Start HTTP server in background if requested
    if http_port:
        http_t = threading.Thread(target=start_http, args=(http_port,), daemon=True)
        http_t.start()

    try:
        while True:
            try:
                data, addr = sock.recvfrom(8192)
            except Exception:
                continue
            # register client
            with clients_lock:
                if addr not in clients:
                    clients.add(addr)
                    log.info(f"Client joined: {addr}")
            # broadcast to others
            dead = []
            with clients_lock:
                for c in list(clients):
                    if c == addr:
                        continue
                    try:
                        sock.sendto(data, c)
                    except Exception:
                        dead.append(c)
            if dead:
                with clients_lock:
                    for d in dead:
                        try:
                            clients.remove(d)
                        except KeyError:
                            pass
    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        sock.close()
        stop_log_server()
        stop_log_forward()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='0.0.0.0', help='UDP bind host')
    parser.add_argument('--port', default=50000, type=int, help='UDP bind port')
    parser.add_argument('--http-port', default=8000, type=int, help='HTTP port for GET /status (default: 8000)')
    parser.add_argument('--log-port', default=9000, type=int, help='TCP port for external log terminal (default: 9000)')
    parser.add_argument(
        '--log-level', default='debug',
        choices=('debug', 'info', 'warning', 'error', 'critical'),
        help='Minimum log level to emit (default: debug)',
    )
    parser.add_argument(
        '--log-forward', default=None, metavar='HOST:PORT',
        help='Instead of hosting a log listener, push all log entries to a '
             'central log server at HOST:PORT (e.g. 127.0.0.1:9000)',
    )
    args = parser.parse_args()
    set_log_level(args.log_level)
    if args.log_forward:
        fwd_host, fwd_port = parse_forward_address(args.log_forward)
        start_log_forward(fwd_host, fwd_port)
    else:
        start_log_server(port=args.log_port)
    run(args.host, args.port, args.http_port)
