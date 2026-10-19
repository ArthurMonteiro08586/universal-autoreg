#!/usr/bin/env bash
# UNIVERSAL AUTOREG v2 - Linux/macOS launcher
set -e
cd "$(dirname "$0")"
mkdir -p data logs
export PYTHONIOENCODING=utf-8
PY="${UA_PYTHON:-python3}"
if [ ! -f config.json ] && [ -f config.example.json ]; then
  echo "[ua] config.json не найден — скопировал example, заполни секреты"
  cp config.example.json config.json
fi
exec "$PY" dashboard.py "$@"
