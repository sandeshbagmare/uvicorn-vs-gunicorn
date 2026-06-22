#!/usr/bin/env bash
# Run the demo app under Gunicorn with Uvicorn workers (Linux/macOS/WSL ONLY).
# Gunicorn does not run on Windows -- use WSL or the docker/ setup there.
#
#   ./benchmarks/run_gunicorn.sh            # workers = (2*cores)+1, the classic formula
#   WORKERS=4 ./benchmarks/run_gunicorn.sh  # explicit worker count
#
# Production-flavoured flags are included and commented so this doubles as a reference.
set -euo pipefail
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
CORES="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
WORKERS="${WORKERS:-$(( 2 * CORES + 1 ))}"
cd "$(dirname "$0")/.."

echo "Starting Gunicorn master + ${WORKERS} UvicornWorker(s) on http://${HOST}:${PORT}"
exec python -m gunicorn app.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers "$WORKERS" \
    --bind "${HOST}:${PORT}" \
    --timeout 30 \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --max-requests 10000 \
    --max-requests-jitter 1000 \
    --log-level info
    # --timeout 30           : a worker silent for 30s is killed and respawned (hung-request protection)
    # --max-requests 10000   : recycle each worker after N requests (mitigates slow memory leaks)
    # --max-requests-jitter  : randomise recycling so workers don't all restart at once
    # --graceful-timeout 30  : on reload/restart, give in-flight requests up to 30s to finish
