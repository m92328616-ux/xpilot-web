"""
HTTP(S) web server for XPilot web versions.
Serves HTML files and a /status JSON endpoint.

Plain HTTP:
    python web-server.py --port 8000

HTTPS (self-signed cert auto-generated on first run if --cert/--key
are not given -- fine for LAN play with friends; browsers will show a
"not secure" warning to click through once per visitor):
    python web-server.py --port 8443 --https

HTTPS with your own certificate (e.g. from Let's Encrypt, for a real
public domain):
    python web-server.py --port 443 --https --cert fullchain.pem --key privkey.pem

The /status endpoint reports whether this process is running over TLS,
which is the simplest way to confirm "HTTPS status" for the site:
    curl -k https://localhost:8443/status
    -> {"status": "ok", "version": "web", "https": true, "port": 8443}
"""
import http.server
import socketserver
import argparse
import os
import ssl
import json
import subprocess
import sys

from log_interface import (
    get_logger,
    parse_forward_address,
    set_log_level,
    start_log_forward,
    start_log_server,
    stop_log_forward,
    stop_log_server,
)

log = get_logger("WebServer")

SERVE_DIR = os.path.dirname(__file__)
HTTPS_ENABLED = False
BOUND_PORT = None


class ReusableTCPServer(socketserver.TCPServer):
    """TCPServer that sets SO_REUSEADDR so the port frees up on restart."""

    allow_reuse_address = True


def ensure_self_signed_cert(cert_path, key_path):
    """Generate a self-signed cert/key pair via OpenSSL if missing.

    This is sufficient for friends-on-LAN play and for proving the
    server can speak TLS. For a real public site (friends connecting
    from anywhere over the internet, no browser warning), replace
    these with a certificate from a CA such as Let's Encrypt.
    """
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return
    log.info(f"No cert found at {cert_path} -- generating a self-signed one...")
    try:
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", key_path, "-out", cert_path,
                "-days", "365", "-nodes",
                "-subj", "/CN=localhost",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info(f"Generated self-signed cert: {cert_path}, key: {key_path}")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        log.error(f"Could not generate a self-signed certificate automatically: {e}")
        log.error("Install OpenSSL, or pass --cert/--key pointing to an existing cert.")
        sys.exit(1)


class GameHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        """Serve files from the current directory."""
        path = path.split('?', 1)[0]  # Remove query string
        path = path.split('#', 1)[0]  # Remove fragment
        if path == '/':
            path = '/xpilot-web.html'
        path = super().translate_path(path)
        return path

    def do_GET(self):
        """Handle GET requests."""
        if self.path == '/':
            self.path = '/xpilot-web.html'

        if self.path in ['/xpilot-web.html', '/xpilot-pyodide.html']:
            file_path = os.path.join(SERVE_DIR, self.path.lstrip('/'))
            if os.path.exists(file_path):
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                with open(file_path, 'rb') as f:
                    self.wfile.write(f.read())
                return

        if self.path == '/status':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            payload = {
                "status": "ok",
                "version": "web",
                "https": HTTPS_ENABLED,
                "port": BOUND_PORT,
            }
            self.wfile.write(json.dumps(payload).encode('utf8'))
            return

        super().do_GET()

    def log_message(self, format, *args):
        """Quiet logging."""
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='XPilot Web Server')
    parser.add_argument('--port', type=int, default=8000, help='Port to listen on')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--https', action='store_true', help='Serve over HTTPS (TLS)')
    parser.add_argument('--cert', default=None, help='Path to TLS certificate (PEM). Auto-generated if omitted.')
    parser.add_argument('--key', default=None, help='Path to TLS private key (PEM). Auto-generated if omitted.')
    parser.add_argument('--log-port', type=int, default=9000, help='TCP port for external log terminal (default: 9000)')
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

    os.chdir(SERVE_DIR)

    HTTPS_ENABLED = args.https
    BOUND_PORT = args.port

    set_log_level(args.log_level)
    if args.log_forward:
        fwd_host, fwd_port = parse_forward_address(args.log_forward)
        start_log_forward(fwd_host, fwd_port)
    else:
        start_log_server(port=args.log_port)
    log.info("Web server starting")

    Handler = GameHTTPHandler
    httpd = ReusableTCPServer((args.host, args.port), Handler)

    scheme = 'http'
    if args.https:
        cert_path = args.cert or os.path.join(SERVE_DIR, 'cert.pem')
        key_path = args.key or os.path.join(SERVE_DIR, 'key.pem')
        ensure_self_signed_cert(cert_path, key_path)

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = 'https'

    log.info(f"XPilot Web Server listening on {args.host}:{args.port} ({scheme})")
    log.info(f"  -> {scheme}://localhost:{args.port}/xpilot-web.html (JavaScript version)")
    log.info(f"  -> {scheme}://localhost:{args.port}/xpilot-pyodide.html (Python/Pyodide version)")
    log.info(f"  -> {scheme}://localhost:{args.port}/status (server status, JSON)")
    if args.https:
        log.info('  Note: self-signed certs trigger a browser warning ("not private") --')
        log.info('  click "Advanced -> Proceed" once per browser/visitor, or supply a real')
        log.info("  cert via --cert/--key for a public domain (e.g. Let's Encrypt).")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        stop_log_server()
        stop_log_forward()