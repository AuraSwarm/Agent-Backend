#!/usr/bin/env bash
# Create the test database and (optionally) test buckets so test runs do not pollute
# dev/prod data. Run once before using TEST_DATABASE_URL or real-DB tests.
#
# Usage:
#   ./scripts/create-test-db.sh
#
# Uses PGHOST, PGPORT, PGUSER, PGPASSWORD if set; otherwise defaults (e.g. postgres@localhost:5432).
# Creates database: agent_backend_test
#
# For OSS/MinIO test isolation, create a separate bucket (e.g. aura-mem-test, archives-test)
# and set TEST_OSS_BUCKET / TEST_MINIO_BUCKET when running tests.

set -e

TEST_DB_NAME="${TEST_DB_NAME:-agent_backend_test}"

if command -v psql >/dev/null 2>&1; then
  # Prefer psql with connection params from env (PGHOST, PGPORT, PGUSER, PGPASSWORD)
  if sudo -u postgres psql -d postgres -tA -c "SELECT 1 FROM pg_database WHERE datname='$TEST_DB_NAME';" 2>/dev/null | grep -q 1; then
    echo "Database $TEST_DB_NAME already exists."
  else
    echo "Creating database $TEST_DB_NAME..."
    sudo -u postgres psql -d postgres -c "CREATE DATABASE $TEST_DB_NAME;" 2>/dev/null || {
      echo "Trying without sudo (e.g. peer auth)..." >&2
      psql -d postgres -c "CREATE DATABASE $TEST_DB_NAME;" || {
        echo "Failed to create $TEST_DB_NAME. Set PGHOST, PGPORT, PGUSER, PGPASSWORD and ensure you can CREATE DATABASE." >&2
        exit 1
      }
    }
    echo "Database $TEST_DB_NAME created."
  fi
else
  echo "psql not found. Create the test database manually, e.g.:" >&2
  echo "  createdb $TEST_DB_NAME" >&2
  echo "  # or: psql -d postgres -c \"CREATE DATABASE $TEST_DB_NAME;\"" >&2
  echo "" >&2
  echo "Then set TEST_DATABASE_URL when running tests:" >&2
  echo "  export TEST_DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/$TEST_DB_NAME'" >&2
  exit 1
fi

echo ""
echo "Run tests with test DB (avoid polluting dev/prod):"
echo "  export TEST_DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/$TEST_DB_NAME'"
echo "  pytest tests/ -v"
echo ""
echo "For OSS/MinIO isolation, create test buckets (e.g. aura-mem-test, archives-test) and set:"
echo "  export TEST_OSS_BUCKET=aura-mem-test"
echo "  export TEST_MINIO_BUCKET=archives-test"
echo "  pytest tests/ -v"
