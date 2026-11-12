param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Create the project .venv and install requirements-dev.txt first; see README.md.'
}

# Keep this launcher local to the PC. HTTPS hosting uses the host configuration.
$env:AUTH_COOKIE_SECURE = 'false'
$env:DB_PATH = Join-Path $PSScriptRoot 'data/local-arbitrage.sqlite'
if (-not $env:AUTH_SETUP_TOKEN) {
    $env:AUTH_SETUP_TOKEN = (& $pythonPath -c 'import secrets; print(secrets.token_urlsafe(32))').Trim()
    if ($LASTEXITCODE -ne 0 -or -not $env:AUTH_SETUP_TOKEN) {
        throw 'Could not generate the first-run setup token.'
    }
}

Write-Host "Local dashboard: http://127.0.0.1:$Port"
Write-Host 'First-run setup token (needed only before accounts exist):'
Write-Host $env:AUTH_SETUP_TOKEN
Write-Host 'Enter your own account passwords in the dashboard. Press Ctrl+C to stop.'
Push-Location $PSScriptRoot
try {
    & $pythonPath -m uvicorn app.main:app --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'The local server exited unsuccessfully.' }
}
finally {
    Pop-Location
}
