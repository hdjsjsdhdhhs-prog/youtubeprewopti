<#
.SYNOPSIS
  One-time / idempotent local dev setup (native Windows, no Docker).

.DESCRIPTION
  1. Creates backend\.venv (Python 3.12) and installs the backend with dev extras.
  2. Runs setup-db.ps1 (roles, databases, private config) unless -SkipDb.
  3. Applies Alembic migrations to the dev database.
  4. Installs frontend dependencies if frontend\package.json exists.
#>
param(
    [string]$ConfigFile = "$env:USERPROFILE\.ytlead-secrets\backend.conf",
    [switch]$SkipDb
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$venvPy = Join-Path $backend ".venv\Scripts\python.exe"

function Assert-Ok([string]$step) { if ($LASTEXITCODE -ne 0) { throw "$step failed (exit $LASTEXITCODE)" } }

# 1. Backend venv + dependencies
if (-not (Test-Path $venvPy)) {
    Write-Host "Creating backend\.venv ..."
    & py -3.12 -m venv (Join-Path $backend ".venv"); Assert-Ok "venv"
}
& $venvPy -m pip install --disable-pip-version-check -q -e "$backend[dev]"; Assert-Ok "pip install"

# 2. Database roles/databases + private config
if (-not $SkipDb) {
    & (Join-Path $PSScriptRoot "setup-db.ps1") -ConfigFile $ConfigFile
}

# 3. Migrations
$env:YTL_ENV_FILE = $ConfigFile
Push-Location $backend
try { & $venvPy -m alembic upgrade head; Assert-Ok "alembic upgrade" } finally { Pop-Location }

# 4. Frontend
$frontend = Join-Path $root "frontend"
if (Test-Path (Join-Path $frontend "package.json")) {
    Push-Location $frontend
    try { & npm install --no-fund --no-audit; Assert-Ok "npm install" } finally { Pop-Location }
} else {
    Write-Host "frontend\package.json not found - skipping npm install."
}

Write-Host "OK: setup complete. Start dev services with scripts\dev.ps1"
