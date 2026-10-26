#!/usr/bin/env bash
# Restore a backup into the running db service, then verify pgvector and embeddings.
# Usage: scripts/restore.sh backups/terrashield-YYYYmmddTHHMMSSZ.dump
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash on Windows: keep container paths such as /tmp unchanged
cd "$(dirname "$0")/.."
file=${1:?usage: restore.sh <backup.dump>}
[ -f "$file" ] || { echo "No such file: $file" >&2; exit 1; }
echo "Stopping api and worker so nothing writes during restore..."
docker compose stop api worker web
docker compose exec -T db psql -U terrashield -d postgres -v ON_ERROR_STOP=1 \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='terrashield' AND pid <> pg_backend_pid();" \
  -c "DROP DATABASE IF EXISTS terrashield;" -c "CREATE DATABASE terrashield OWNER terrashield;" >/dev/null
docker cp "$file" "$(docker compose ps -q db):/tmp/restore.dump" >/dev/null
docker compose exec -T db pg_restore -U terrashield -d terrashield --no-owner --exit-on-error /tmp/restore.dump
docker compose exec -T db rm -f /tmp/restore.dump
ext=$(docker compose exec -T db psql -U terrashield -d terrashield -Atc "SELECT extversion FROM pg_extension WHERE extname='vector'")
vec=$(docker compose exec -T db psql -U terrashield -d terrashield -Atc "SELECT count(*) FROM chunk_embeddings")
runs=$(docker compose exec -T db psql -U terrashield -d terrashield -Atc "SELECT count(*) FROM runs")
[ -n "$ext" ] || { echo "pgvector extension missing after restore" >&2; exit 1; }
echo "Restored: pgvector ${ext}, ${vec} embeddings, ${runs} runs."
docker compose up -d api worker web
