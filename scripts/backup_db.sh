#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TS="$(date -u +"%Y%m%dT%H%M%SZ")"
BACKUP_DIR="${BACKUP_DIR:-./data/backups}"
DUCKDB_PATH="${DUCKDB_PATH:-./data/polyforge.duckdb}"

mkdir -p "$BACKUP_DIR"

if [[ -f "$DUCKDB_PATH" ]]; then
  OUT_PATH="$BACKUP_DIR/polyforge_${TS}.duckdb"
  cp "$DUCKDB_PATH" "$OUT_PATH"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$OUT_PATH" > "${OUT_PATH}.sha256"
  fi
  echo "Backed up DuckDB: $OUT_PATH"
else
  echo "DuckDB file not found at: $DUCKDB_PATH" >&2
fi

if docker compose ps postgres >/dev/null 2>&1; then
  if docker compose ps postgres | grep -q "Up"; then
    OUT_SQL="$BACKUP_DIR/postgres_${TS}.sql"
    docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-polyforge}" "${POSTGRES_DB:-polyforge}" > "$OUT_SQL"
    if command -v sha256sum >/dev/null 2>&1; then
      sha256sum "$OUT_SQL" > "${OUT_SQL}.sha256"
    fi
    echo "Backed up Postgres: $OUT_SQL"
  fi
fi
