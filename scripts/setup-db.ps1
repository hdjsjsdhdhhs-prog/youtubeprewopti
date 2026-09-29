<#
.SYNOPSIS
  Creates PostgreSQL roles/databases for local development and writes the backend
  dev config (DB URLs, master key) to a private file outside the repository.

.DESCRIPTION
  Idempotent: existing roles keep their passwords unless -RotatePasswords is given.
  Roles:  ytlead_owner (schema owner, runs migrations), ytlead_app (DML only).
  DBs:    ytlead (dev), ytlead_test (tests).
  Secrets are never printed.
#>
param(
    [string]$SuperuserPasswordFile = "$env:USERPROFILE\.ytlead-secrets\postgres_superuser.txt",
    [string]$ConfigFile = "$env:USERPROFILE\.ytlead-secrets\backend.conf",
    [string]$PgHost = "127.0.0.1",
    [int]$PgPort = 5432,
    [switch]$RotatePasswords
)
$ErrorActionPreference = "Stop"

function New-Secret([int]$bytes = 24) {
    $b = New-Object byte[] $bytes
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return $b
}
function New-Password { ([Convert]::ToBase64String((New-Secret 24)) -replace '[+/=]', 'x') }

function Invoke-Psql([string]$db, [string]$sql) {
    $out = & psql -h $PgHost -p $PgPort -U postgres -d $db -v ON_ERROR_STOP=1 -tAc $sql 2>&1
    if ($LASTEXITCODE -ne 0) { throw "psql failed: $out" }
    return $out
}

if (-not (Get-Command psql -ErrorAction SilentlyContinue)) { throw "psql not found in PATH" }
$env:PGPASSWORD = (Get-Content -Raw $SuperuserPasswordFile).Trim()

# Load existing config (key=value) if present
$cfg = [ordered]@{}
if (Test-Path $ConfigFile) {
    foreach ($line in Get-Content $ConfigFile) {
        if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') { $cfg[$Matches[1]] = $Matches[2] }
    }
}

$ownerPw = if ($RotatePasswords -or -not $cfg.Contains('YTL_DB_OWNER_PASSWORD')) { New-Password } else { $cfg['YTL_DB_OWNER_PASSWORD'] }
$appPw   = if ($RotatePasswords -or -not $cfg.Contains('YTL_DB_APP_PASSWORD'))   { New-Password } else { $cfg['YTL_DB_APP_PASSWORD'] }

foreach ($r in @(@('ytlead_owner', $ownerPw), @('ytlead_app', $appPw))) {
    $name = $r[0]; $pw = $r[1]
    $exists = Invoke-Psql 'postgres' "SELECT 1 FROM pg_roles WHERE rolname = '$name'"
    if ($exists -eq '1') { Invoke-Psql 'postgres' "ALTER ROLE $name WITH LOGIN PASSWORD '$pw'" | Out-Null }
    else { Invoke-Psql 'postgres' "CREATE ROLE $name WITH LOGIN PASSWORD '$pw'" | Out-Null }
}

# Server-level requirement (ADR-0003): unlocalized messages
Invoke-Psql 'postgres' "ALTER SYSTEM SET lc_messages TO 'C'" | Out-Null
Invoke-Psql 'postgres' "SELECT pg_reload_conf()" | Out-Null

foreach ($db in @('ytlead', 'ytlead_test')) {
    $exists = Invoke-Psql 'postgres' "SELECT 1 FROM pg_database WHERE datname = '$db'"
    if ($exists -ne '1') {
        Invoke-Psql 'postgres' "CREATE DATABASE $db OWNER ytlead_owner ENCODING 'UTF8' TEMPLATE template0" | Out-Null
    }
    Invoke-Psql $db "REVOKE ALL ON SCHEMA public FROM PUBLIC" | Out-Null
    Invoke-Psql $db "ALTER SCHEMA public OWNER TO ytlead_owner" | Out-Null
    Invoke-Psql $db "GRANT USAGE ON SCHEMA public TO ytlead_app" | Out-Null
    Invoke-Psql $db "CREATE EXTENSION IF NOT EXISTS citext" | Out-Null
    Invoke-Psql $db "CREATE EXTENSION IF NOT EXISTS pg_trgm" | Out-Null
    # Objects created later by the owner (migrations) are automatically usable by the app role
    Invoke-Psql $db "ALTER DEFAULT PRIVILEGES FOR ROLE ytlead_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ytlead_app" | Out-Null
    Invoke-Psql $db "ALTER DEFAULT PRIVILEGES FOR ROLE ytlead_owner IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO ytlead_app" | Out-Null
    Invoke-Psql $db "ALTER DEFAULT PRIVILEGES FOR ROLE ytlead_owner IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO ytlead_app" | Out-Null
    Invoke-Psql $db "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ytlead_app" | Out-Null
    Invoke-Psql $db "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO ytlead_app" | Out-Null
}

$cfg['YTL_DB_OWNER_PASSWORD'] = $ownerPw
$cfg['YTL_DB_APP_PASSWORD'] = $appPw
$cfg['YTL_DATABASE_URL'] = "postgresql+psycopg://ytlead_app:$appPw@${PgHost}:$PgPort/ytlead"
$cfg['YTL_MIGRATION_DATABASE_URL'] = "postgresql+psycopg://ytlead_owner:$ownerPw@${PgHost}:$PgPort/ytlead"
$cfg['YTL_TEST_DATABASE_URL'] = "postgresql+psycopg://ytlead_owner:$ownerPw@${PgHost}:$PgPort/ytlead_test"
if (-not $cfg.Contains('YTL_MASTER_KEY')) { $cfg['YTL_MASTER_KEY'] = [Convert]::ToBase64String((New-Secret 32)) }

$dir = Split-Path $ConfigFile
New-Item -ItemType Directory -Force $dir | Out-Null
($cfg.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) | Set-Content -Encoding UTF8 $ConfigFile
icacls $dir /inheritance:r /grant:r 'Administrators:(OI)(CI)F' 'SYSTEM:(OI)(CI)F' | Out-Null

Remove-Item Env:PGPASSWORD
Write-Host "OK: roles ytlead_owner/ytlead_app, databases ytlead/ytlead_test ready."
Write-Host "Config written to $ConfigFile (secrets not shown)."
