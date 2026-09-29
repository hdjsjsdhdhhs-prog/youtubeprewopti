#!/usr/bin/env bash
# First-start initialisation of the postgres container (runs only on an empty data volume).
# Mirrors scripts/setup-db.ps1: schema owner + DML-only app role, database, extensions, grants.
set -euo pipefail

: "${YTL_DB_OWNER_PASSWORD:?YTL_DB_OWNER_PASSWORD is required}"
: "${YTL_DB_APP_PASSWORD:?YTL_DB_APP_PASSWORD is required}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v owner_pw="$YTL_DB_OWNER_PASSWORD" -v app_pw="$YTL_DB_APP_PASSWORD" <<'SQL'
CREATE ROLE ytlead_owner WITH LOGIN PASSWORD :'owner_pw';
CREATE ROLE ytlead_app WITH LOGIN PASSWORD :'app_pw';
CREATE DATABASE ytlead OWNER ytlead_owner ENCODING 'UTF8' TEMPLATE template0;

\connect ytlead
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO ytlead_owner;
GRANT USAGE ON SCHEMA public TO ytlead_app;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- Objects created later by the owner (migrations) are automatically usable by the app role.
ALTER DEFAULT PRIVILEGES FOR ROLE ytlead_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ytlead_app;
ALTER DEFAULT PRIVILEGES FOR ROLE ytlead_owner IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO ytlead_app;
ALTER DEFAULT PRIVILEGES FOR ROLE ytlead_owner IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO ytlead_app;
SQL

echo "ytlead: roles ytlead_owner/ytlead_app and database ytlead created"
