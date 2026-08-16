"""
External log terminal for XPilot.

Connects to the game's log server and displays incoming log entries
in real-time with color coding by severity level.

Usage:
    python log_terminal.py                    # connect to localhost:9000
    python log_terminal.py --port 9000        # explicit port
    python log_terminal.py --host 192.168.1.5 # remote game host
"""
import socket
import sys
import json
import argparse


COLORS = {
    "DEBUG":    "\033[37m",    # white/gray
    "INFO":     "\033[32m",    # green
    "WARNING":  "\033[33m",    # yellow
    "ERROR":    "\033[31m",    # red
    "CRITICAL": "\033[35m",    # magenta
}
RESET = "\033[0m"
BOLD = "\033[1m"


def colorize(level, text):
    color = COLORS.get(level, "")
    return f"{color}{BOLD}{text}{RESET}" if color else text


def main(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, port))
    except ConnectionRefusedError:
        print(f"Error: Cannot connect to log server at {host}:{port}")
        print("Make sure xpilot.py is running with --log-port {port}")
        sys.exit(1)

    print(f"{BOLD}Connected to log server at {host}:{port}{RESET}")
    print("-" * 72)

    buf = ""
    try:
        while True:
            data = sock.recv(4096)
            if not data:
                print("\nLog server disconnected.")
                break
            buf += data.decode("utf-8")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    print(line)
                    continue

                level = entry.get("level", "?")
                ts = entry.get("time", "")
                source = entry.get("source", "?")
                message = entry.get("message", "")

                formatted = f"[{ts}] [{level:8s}] [{source}] {message}"
                print(colorize(level, formatted))
    except KeyboardInterrupt:
        print("\nDisconnected.")
    finally:
        sock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="XPilot external log terminal")
    parser.add_argument("--host", default="127.0.0.1", help="Log server host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9000, help="Log server port (default: 9000)")
    args = parser.parse_args()
    main(args.host, args.port)
