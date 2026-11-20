#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Starting AIroom Backend on http://127.0.0.1:8765 ..."
cd "$ROOT_DIR/backend"
"$ROOT_DIR/backend/.venv/bin/uvicorn" main:app --host 127.0.0.1 --port 8765 &
BACKEND_PID=$!

cleanup() {
  kill "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT

sleep 1
echo "Launching AIroom Flutter App (Linux Desktop)..."
cd "$ROOT_DIR/app"
flutter run -d linux
