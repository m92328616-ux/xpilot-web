# xpilot-webnet server image.
#
# This one image runs BOTH server processes -- ws_server.py (the multiplayer
# relay) and web-server.py (the static file server) -- selected at deploy
# time via the container's command/args. See k8s/ for how each Deployment
# overrides the default CMD below.
#
# Deliberately does NOT include pygame/SDL: xpilot.py (the desktop client)
# is not part of this image, only the two network-facing servers are.
# pvp_system.py is also desktop-client-only (imported by xpilot.py, not by
# either server) and is likewise omitted.

FROM python:3.12-slim AS base

WORKDIR /app

COPY requirements-server.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements-server.txt

# Python: what the two servers actually import
COPY log_interface.py .
COPY pickup_sync.py .
COPY ws_server.py .
COPY web-server.py .

# Static assets served to the browser
COPY xpilot-web.html .
COPY xpilot-pyodide.html .
COPY pickup-system.js .
COPY fuel-system.js .
COPY pvp-system.js .
COPY game-log.js .

# Non-root
RUN useradd --system --create-home --uid 10001 xpilot \
    && chown -R xpilot:xpilot /app
USER xpilot

# 8000: web-server.py HTTP (static site + /status)
# 8765: ws_server.py WebSocket relay
# 8766: ws_server.py HTTP /status (health checks)
EXPOSE 8000 8765 8766

# Default: run the static web server. The ws-server Deployment in k8s/
# overrides `args` to run ws_server.py instead.
ENTRYPOINT ["python"]
CMD ["web-server.py", "--host", "0.0.0.0", "--port", "8000"]
