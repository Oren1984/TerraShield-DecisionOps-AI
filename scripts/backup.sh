#!/usr/bin/env bash
# Consistent logical backup (pg_dump custom format) including the vector extension and embeddings.
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash on Windows: keep container paths such as /tmp unchanged
cd "$(dirname "$0")/.."
mkdir -p backups
out="backups/terrashield-$(date -u +%Y%m%dT%H%M%SZ).dump"
# Dump inside the container and copy the file out (no binary data over a TTY/stdout).
docker compose exec -T db pg_dump -U terrashield -d terrashield -Fc -f /tmp/terrashield.dump
docker cp "$(docker compose ps -q db):/tmp/terrashield.dump" "$out" >/dev/null
docker compose exec -T db rm -f /tmp/terrashield.dump
size=$(wc -c <"$out")
[ "$size" -gt 1000 ] || { echo "Backup looks empty: $out" >&2; exit 1; }
echo "Backup written: $out (${size} bytes)"
