# Restore a backup into the running db service, then verify pgvector and embeddings.
# Usage: scripts/restore.ps1 backups/terrashield-YYYYmmddTHHmmssZ.dump
param([Parameter(Mandatory = $true)][string]$File)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (-not (Test-Path $File)) { throw "No such file: $File" }
Write-Host "Stopping api, worker and web so nothing writes during restore..."
docker compose stop api worker web
$cid = docker compose ps -q db
docker cp $File "${cid}:/tmp/restore.dump" | Out-Null
docker compose exec -T db psql -U terrashield -d postgres -v ON_ERROR_STOP=1 `
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='terrashield' AND pid <> pg_backend_pid();" `
    -c "DROP DATABASE IF EXISTS terrashield;" -c "CREATE DATABASE terrashield OWNER terrashield;" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Recreating the database failed" }
docker compose exec -T db pg_restore -U terrashield -d terrashield --no-owner --exit-on-error /tmp/restore.dump
if ($LASTEXITCODE -ne 0) { throw "pg_restore failed" }
docker compose exec -T db rm -f /tmp/restore.dump | Out-Null
$ext = docker compose exec -T db psql -U terrashield -d terrashield -Atc "SELECT extversion FROM pg_extension WHERE extname='vector'"
$vec = docker compose exec -T db psql -U terrashield -d terrashield -Atc "SELECT count(*) FROM chunk_embeddings"
$runs = docker compose exec -T db psql -U terrashield -d terrashield -Atc "SELECT count(*) FROM runs"
if (-not $ext) { throw "pgvector extension missing after restore" }
Write-Host "Restored: pgvector $ext, $vec embeddings, $runs runs."
docker compose up -d api worker web
