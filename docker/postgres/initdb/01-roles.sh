#!/bin/sh
# Runs once, when the PostgreSQL data directory is initialized (docker-entrypoint-initdb.d).
# Creates the application roles:
#   catalog_owner  - owns the catalog database/schema; used by migrations and the CLI
#   catalog_reader - SELECT only, read-only transactions; used by the REST API
set -eu

: "${CATALOG_OWNER_PASSWORD:?CATALOG_OWNER_PASSWORD must be set}"
: "${CATALOG_READER_PASSWORD:?CATALOG_READER_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v owner_pw="$CATALOG_OWNER_PASSWORD" \
    -v reader_pw="$CATALOG_READER_PASSWORD" \
    -v dbname="$POSTGRES_DB" <<'SQL'
CREATE ROLE catalog_owner LOGIN PASSWORD :'owner_pw';
CREATE ROLE catalog_reader LOGIN PASSWORD :'reader_pw';
ALTER ROLE catalog_reader SET default_transaction_read_only = on;
ALTER DATABASE :"dbname" OWNER TO catalog_owner;
SQL
