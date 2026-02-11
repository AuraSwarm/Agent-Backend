#!/usr/bin/env bash
# One-time setup for system PostgreSQL so the app can use postgres/postgres and agent_backend DB.
# Run on the host where Postgres runs (e.g. where you run ./run node).
# Requires: sudo and a postgres OS user (typical on Linux).
#
# Usage: ./scripts/setup-system-postgres.sh

set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v sudo >/dev/null 2>&1; then
  echo "sudo not found. To fix password authentication for user postgres:" >&2
  echo "  1. Run as the postgres OS user (e.g. su - postgres) and execute:" >&2
  echo "     psql -d postgres -c \"ALTER USER postgres PASSWORD 'postgres';\"" >&2
  echo "     psql -d postgres -c \"CREATE DATABASE agent_backend;\"" >&2
  echo "  2. Or set DATABASE_URL to your connection string before starting the app:" >&2
  echo "     export DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@127.0.0.1:5432/agent_backend'" >&2
  exit 1
fi

echo "Setting postgres user password to 'postgres' and creating database agent_backend..."
if sudo -u postgres psql -d postgres -c "ALTER USER postgres PASSWORD 'postgres';" 2>/dev/null; then
  echo "Password set."
else
  echo "Could not set password (postgres user or peer auth may differ). Try setting DATABASE_URL." >&2
  exit 1
fi

if sudo -u postgres psql -d postgres -tA -c "SELECT 1 FROM pg_database WHERE datname='agent_backend';" 2>/dev/null | grep -q 1; then
  echo "Database agent_backend already exists."
else
  sudo -u postgres psql -d postgres -c "CREATE DATABASE agent_backend;" 2>/dev/null || { echo "Could not create database." >&2; exit 1; }
  echo "Database agent_backend created."
fi

echo "Done. You can run ./run node (no need to set DATABASE_URL)."
