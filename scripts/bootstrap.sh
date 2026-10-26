#!/usr/bin/env bash
# Prepare .env (random DB password), check the port, build and start the stack, wait for health.
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash on Windows: keep container paths such as /tmp unchanged
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  pw=$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 40 || true)
  sed -i.bak "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${pw}/" .env && rm -f .env.bak
  echo "Created .env with a random database password."
fi
port=$(grep -E '^TS_WEB_PORT=' .env | cut -d= -f2); port=${port:-8080}
bind=$(grep -E '^TS_BIND_ADDRESS=' .env | cut -d= -f2); bind=${bind:-127.0.0.1}
if ! docker compose ps --status running --services 2>/dev/null | grep -q '^web$'; then
  if (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null; then
    echo "Port ${port} is already in use. Set TS_WEB_PORT in .env and re-run." >&2
    exit 1
  fi
fi

docker compose up --build -d
echo "Waiting for the API to become ready..."
for _ in $(seq 1 90); do
  if curl -fsS "http://${bind}:${port}/health/ready" >/dev/null 2>&1; then
    echo "Ready: http://${bind}:${port}"
    echo "Open it in a browser and create the owner account (first visit only), or explore the demo."
    exit 0
  fi
  sleep 2
done
echo "The stack did not become ready in time. Check: docker compose logs migrate api" >&2
exit 1
