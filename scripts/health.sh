#!/usr/bin/env bash
# Report container state plus liveness and readiness through the web origin.
set -uo pipefail
cd "$(dirname "$0")/.."
port=$(grep -E '^TS_WEB_PORT=' .env 2>/dev/null | cut -d= -f2); port=${port:-8080}
bind=$(grep -E '^TS_BIND_ADDRESS=' .env 2>/dev/null | cut -d= -f2); bind=${bind:-127.0.0.1}
docker compose ps
echo "live:  $(curl -sS "http://${bind}:${port}/health/live" || echo unreachable)"
echo "ready: $(curl -sS "http://${bind}:${port}/health/ready" || echo unreachable)"
curl -fsS "http://${bind}:${port}/health/ready" >/dev/null
