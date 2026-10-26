# Report container state plus liveness and readiness through the web origin.
Set-Location (Join-Path $PSScriptRoot "..")
$envMap = @{}
if (Test-Path .env) { Get-Content .env | Where-Object { $_ -match '^[A-Z_]+=' } | ForEach-Object { $k, $v = $_ -split '=', 2; $envMap[$k] = $v } }
$port = if ($envMap.TS_WEB_PORT) { $envMap.TS_WEB_PORT } else { "8080" }
$bind = if ($envMap.TS_BIND_ADDRESS) { $envMap.TS_BIND_ADDRESS } else { "127.0.0.1" }
docker compose ps
$ok = $true
foreach ($p in "live", "ready") {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "http://${bind}:${port}/health/$p" -TimeoutSec 5
        Write-Host "${p}: $($r.Content)"
    } catch {
        Write-Host "${p}: unreachable or not ready ($($_.Exception.Message))"
        $ok = $false
    }
}
if (-not $ok) { exit 1 }
