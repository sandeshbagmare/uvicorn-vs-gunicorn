#!/usr/bin/env bash
# Run the demo app under Uvicorn (Linux/macOS/WSL).
#   ./benchmarks/run_uvicorn.sh           # 1 worker
#   WORKERS=4 ./benchmarks/run_uvicorn.sh # 4 workers
set -euo pipefail
WORKERS="${WORKERS:-1}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
cd "$(dirname "$0")/.."   # project root so app.main:app imports
echo "Starting Uvicorn with ${WORKERS} worker(s) on http://${HOST}:${PORT}"
exec python -m uvicorn app.main:app --host "$HOST" --port "$PORT" --workers "$WORKERS"
