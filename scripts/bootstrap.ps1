# Prepare .env (random DB password), check the port, build and start the stack, wait for health.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    $chars = [char[]]"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    $bytes = New-Object byte[] 40
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $pw = -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
    (Get-Content .env) -replace '^POSTGRES_PASSWORD=.*', "POSTGRES_PASSWORD=$pw" | Set-Content .env -Encoding utf8
    Write-Host "Created .env with a random database password."
}

$envMap = @{}
Get-Content .env | Where-Object { $_ -match '^[A-Z_]+=' } | ForEach-Object { $k, $v = $_ -split '=', 2; $envMap[$k] = $v }
$port = if ($envMap.TS_WEB_PORT) { $envMap.TS_WEB_PORT } else { "8088" }
$bind = if ($envMap.TS_BIND_ADDRESS) { $envMap.TS_BIND_ADDRESS } else { "127.0.0.1" }

$running = docker compose ps --status running --services 2>$null
if (-not ($running -contains "web")) {
    $inUse = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($inUse) { throw "Port $port is already in use. Set TS_WEB_PORT in .env and re-run." }
}

docker compose up --build -d
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }
Write-Host "Waiting for the API to become ready..."
for ($i = 0; $i -lt 90; $i++) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "http://${bind}:${port}/health/ready" -TimeoutSec 3
        if ($r.StatusCode -eq 200) {
            Write-Host "Ready: http://${bind}:${port}"
            Write-Host "Open it in a browser and create the owner account (first visit only), or explore the demo."
            exit 0
        }
    } catch { Start-Sleep -Seconds 2 }
}
throw "The stack did not become ready in time. Check: docker compose logs migrate api"
