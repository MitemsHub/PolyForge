#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f ".env" ]]; then
  echo "Missing .env. Create it from .env.example:" >&2
  echo "  cp .env.example .env" >&2
  exit 1
fi

PROFILE_ARGS=()
if [[ "${1:-}" == "--with-infra" ]]; then
  PROFILE_ARGS+=(--profile infra)
fi

docker compose "${PROFILE_ARGS[@]}" up -d --build
docker compose ps
