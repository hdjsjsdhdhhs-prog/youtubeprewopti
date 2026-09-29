# Agent helper: mirror the agent working copy (idea\_ws) into the project root.
# Excludes generated/runtime directories. Never deletes files in the target.
$src = "C:\youtubesistemprew\idea\_ws"
$dst = "C:\youtubesistemprew"
robocopy $src $dst /E /NFL /NDL /NJH /NJS /NP /XD node_modules .venv .next storage __pycache__ .pytest_cache | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Error "SYNC FAILED ($LASTEXITCODE)"; exit 1 }
exit 0
