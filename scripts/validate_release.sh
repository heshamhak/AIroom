#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[1/3] Running Backend Tests (pytest)..."
"$ROOT_DIR/backend/.venv/bin/pytest" -v "$ROOT_DIR/backend/tests/"

echo "[2/3] Running Flutter Analyze & Tests..."
cd "$ROOT_DIR/app"
flutter analyze
flutter test

echo "[3/3] Checking no secrets/DB files are exposed..."
if [ -f "$ROOT_DIR/backend/data/api_token" ]; then
  perms=$(stat -c "%a" "$ROOT_DIR/backend/data/api_token")
  echo "api_token perms: $perms"
fi

echo "Release validation PASSED."
