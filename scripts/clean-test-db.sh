#!/usr/bin/env bash
# Clean all data in the test database (truncate tables) so the next test run starts from a clean state.
# Requires TEST_DATABASE_URL or connection params (PGHOST, PGPORT, PGUSER, PGPASSWORD) and database name.
#
# Usage:
#   export TEST_DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/agent_backend_test'
#   ./scripts/clean-test-db.sh
#
# Or with psql-style env:
#   PGHOST=localhost PGPORT=5432 PGUSER=postgres PGPASSWORD=postgres ./scripts/clean-test-db.sh
#   (uses database agent_backend_test by default; set PGDATABASE to override)

set -e

# Parse database name from TEST_DATABASE_URL if set (e.g. postgresql+asyncpg://user:pass@host:5432/dbname)
if [ -n "$TEST_DATABASE_URL" ]; then
  if echo "$TEST_DATABASE_URL" | grep -q '@.*/'; then
    PGDATABASE="${PGDATABASE:-$(echo "$TEST_DATABASE_URL" | sed -n 's|.*/\([^/?]*\).*|\1|p')}"
  fi
fi

PGDATABASE="${PGDATABASE:-agent_backend_test}"

if ! command -v psql >/dev/null 2>&1; then
  echo "psql not found. Install PostgreSQL client to run this script." >&2
  exit 1
fi

echo "Cleaning test database: $PGDATABASE"

# Core tables (must exist): truncate with CASCADE; fail on error.
psql -d "$PGDATABASE" -v ON_ERROR_STOP=1 <<'EOSQL'
TRUNCATE TABLE messages, session_summaries CASCADE;
TRUNCATE TABLE sessions CASCADE;
TRUNCATE TABLE role_abilities, prompt_versions CASCADE;
TRUNCATE TABLE employee_roles CASCADE;
TRUNCATE TABLE audit_logs CASCADE;
TRUNCATE TABLE code_reviews CASCADE;
TRUNCATE TABLE custom_abilities CASCADE;
EOSQL

# Optional table (created by archive task; may not exist in minimal test DB)
psql -d "$PGDATABASE" -v ON_ERROR_STOP=0 -c "TRUNCATE TABLE messages_archive CASCADE;" 2>/dev/null || true

echo "Test database cleaned."
