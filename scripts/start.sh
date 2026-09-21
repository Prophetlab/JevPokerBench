#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data
if [[ -f data/server.pid ]] && kill -0 "$(cat data/server.pid)" 2>/dev/null; then
  echo 'Existing server PID is active; stop it deliberately before restarting.'
  exit 1
fi
nohup .venv/bin/uvicorn pokerbench.api:app --timeout-graceful-shutdown 5 --host 127.0.0.1 --port "${POKERBENCH_PORT:-8097}" > data/server.log 2>&1 < /dev/null &
echo $! > data/server.pid
echo "PokerBench started on loopback port ${POKERBENCH_PORT:-8097}."
