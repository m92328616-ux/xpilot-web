# xpilot-webnet roadmap

## 1. Multiplayer -- done
`ws_server.py` is a host-authoritative relay: host election by lowest
client ID, 12 Hz enemy-state broadcast, client-side hit reporting with
generation counters to reject stale hits, automatic host failover on
disconnect, plus power-up sync (`pickup_sync.py`) and PvP (`pvp_system.py`
/ `pvp-system.js`).

The client no longer hardcodes `wss://<host>:8765` -- `web-server.py`
injects `XPILOT_WS_URL` into the served HTML in place of a
`__XPILOT_WS_URL__` placeholder. Unset (local/LAN dev) -> falls back to
the old same-host:8765 guess. Set (production) -> connects to a fixed
URL, e.g. `wss://xpilot.spdns.eu/ws`.

**A real bug was found and fixed post-deploy**: the placeholder string
appeared twice in each HTML file (once in a `<meta>` tag, once in the JS
comparison checking whether substitution had happened). The server's
blind text-replace clobbered both, permanently breaking the fallback
logic. Fixed by checking the injected value's URL shape instead of
comparing against the placeholder text.

Confirmed working end to end over `wss://xpilot.spdns.eu/ws` with a real,
browser-trusted Let's Encrypt certificate.

## 2. Deployable to your infra -- done, in production, over HTTPS
- `Dockerfile`: one multi-arch (amd64+arm64) image, two entrypoints
  (`ws_server.py` / `web-server.py`), non-root.
- `k8s/`: Deployments + Services + a Traefik Ingress, spread across all 3
  nodes (a cluster-networking fault -- missing UDP 8472 for flannel's
  VXLAN overlay -- was found and fixed along the way).
- Live at **https://xpilot.spdns.eu**, real Let's Encrypt production
  certificate via `cert-manager`, auto-renewing.
- `RUNBOOK.md` has the complete ordered command list; `DEPLOYMENT.md` has
  the narrative reasoning; `ARCHITECTURE.md` has the solution architecture
  with diagrams.

## 3. CI/CD -- workflow ready, runner deployment in progress
`.github/workflows/deploy.yml`: test -> build & push to Docker Hub
(multi-arch) -> deploy, on every push to `main`. Deploys via a self-hosted
GitHub Actions runner living inside the k3s cluster (`k8s/ci-runner/`),
scoped via RBAC to the `xpilot` namespace only.

## 4. UX improvements -- ongoing
Recent additions: PvP system, power-up sync improvements, difficulty
selector with persistence, HUD difficulty label, AI/combat tuning passes,
chat/log overlay improvements. Still open:
- Reconnect/backoff on the client if the WebSocket drops mid-game.
- A lobby/room concept (currently one global room per deployment).
- Rate limiting / abuse guardrails on the relay.
