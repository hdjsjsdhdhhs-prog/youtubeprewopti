<#
.SYNOPSIS
  Starts local dev services (native Windows): FastAPI API, the Procrastinate worker and, when
  present, the Next.js frontend.

.DESCRIPTION
  Uses the private config from setup-db.ps1 (YTL_ENV_FILE). The API runs on the selector event
  loop because psycopg async does not support the Windows Proactor loop.
  -ApiOnly: run only the API in the foreground (Ctrl+C to stop).
  -NoWorker: do not start the background worker.
  The worker has no auto-reload: restart dev.ps1 after changing task code.
  -WebHost: interface for `next dev` (default 127.0.0.1). The API trusts X-Forwarded-For from the local
  Next proxy, and Next passes a client-supplied X-Forwarded-For through unchanged — exposing Next on other
  interfaces lets anyone who can reach it spoof their IP (login rate limit). See docs/DEPLOYMENT.md.
#>
param(
    [string]$ConfigFile = "$env:USERPROFILE\.ytlead-secrets\backend.conf",
    [int]$ApiPort = 8000,
    [string]$WebHost = "127.0.0.1",
    [switch]$ApiOnly,
    [switch]$NoWorker
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$venvPy = Join-Path $backend ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPy)) { throw "backend\.venv not found - run scripts\setup.ps1 first" }
if (-not (Test-Path $ConfigFile)) { throw "Config $ConfigFile not found - run scripts\setup-db.ps1 first" }
$env:YTL_ENV_FILE = $ConfigFile
if (-not $env:YTL_LOG_JSON) { $env:YTL_LOG_JSON = "false" }

$apiArgs = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", $ApiPort,
             "--loop", "asyncio:SelectorEventLoop", "--reload", "--reload-dir", "app")

if ($ApiOnly) {
    Write-Host "API: http://127.0.0.1:$ApiPort/api/docs"
    Push-Location $backend
    try { & $venvPy @apiArgs } finally { Pop-Location }
    exit $LASTEXITCODE
}

$children = @()
$worker = $null
if (-not $NoWorker) {
    $worker = Start-Process -FilePath $venvPy -ArgumentList @("-m", "app.workers") -WorkingDirectory $backend `
        -NoNewWindow -PassThru
    $children += $worker
    Write-Host "Worker: all queues (pid $($worker.Id))"
}

$hasFrontend = Test-Path (Join-Path $frontend "package.json")
try {
    if ($hasFrontend) {
        $api = Start-Process -FilePath $venvPy -ArgumentList $apiArgs -WorkingDirectory $backend -NoNewWindow -PassThru
        $children += $api
        Write-Host "API: http://127.0.0.1:$ApiPort/api/docs (pid $($api.Id))"
        $env:YTL_API_ORIGIN = "http://127.0.0.1:$ApiPort"  # Next proxies /api/* here (next.config.ts)
        Push-Location $frontend
        try { & npm run dev -- -H $WebHost } finally { Pop-Location }
    } else {
        Write-Host "frontend\package.json not found - starting API (+ worker) only."
        Write-Host "API: http://127.0.0.1:$ApiPort/api/docs"
        Push-Location $backend
        try { & $venvPy @apiArgs } finally { Pop-Location }
    }
} finally {
    foreach ($c in $children) { if (-not $c.HasExited) { Stop-Process -Id $c.Id -Force } }
}
