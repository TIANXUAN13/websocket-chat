#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$PROJECT_DIR"

VENV_PATH="${VENV_PATH:-.venv}"
PYTHON_BIN="$PROJECT_DIR/$VENV_PATH/bin/python"
DAPHNE_BIN="$PROJECT_DIR/$VENV_PATH/bin/daphne"
BIND_HOST="${BIND_HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
APP_MODULE="${APP_MODULE:-websocket_project.asgi:application}"
MIGRATE_ON_START="${MIGRATE_ON_START:-1}"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Python executable not found: $PYTHON_BIN"
  echo "Please create the virtual environment and install dependencies first."
  exit 1
fi

if [ ! -x "$DAPHNE_BIN" ]; then
  echo "Daphne executable not found: $DAPHNE_BIN"
  echo "Please install dependencies into $VENV_PATH first."
  exit 1
fi

if [ "$MIGRATE_ON_START" = "1" ]; then
  echo "Applying migrations before service start..."
  "$PYTHON_BIN" manage.py migrate --noinput
fi

echo "Starting Daphne on ${BIND_HOST}:${PORT} with ${APP_MODULE}"
exec "$DAPHNE_BIN" -b "$BIND_HOST" -p "$PORT" "$APP_MODULE"
