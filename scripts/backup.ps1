# Consistent logical backup (pg_dump custom format) including the vector extension and embeddings.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
New-Item -ItemType Directory -Force backups | Out-Null
$out = "backups/terrashield-$((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')).dump"
# Dump inside the container and copy the file out, so PowerShell never re-encodes binary output.
docker compose exec -T db pg_dump -U terrashield -d terrashield -Fc -f /tmp/terrashield.dump
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed" }
$cid = docker compose ps -q db
docker cp "${cid}:/tmp/terrashield.dump" $out | Out-Null
docker compose exec -T db rm -f /tmp/terrashield.dump | Out-Null
$size = (Get-Item $out).Length
if ($size -le 1000) { throw "Backup looks empty: $out" }
Write-Host "Backup written: $out ($size bytes)"
