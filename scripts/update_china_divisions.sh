#!/usr/bin/env bash

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

if [ -d ".venv" ]; then
  VENV_PATH=".venv"
elif [ -d "venv" ]; then
  VENV_PATH="venv"
else
  echo "No virtual environment found."
  exit 1
fi

PYTHON_BIN="$PROJECT_DIR/$VENV_PATH/bin/python"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Python executable not found in $VENV_PATH."
  exit 1
fi

"$PYTHON_BIN" manage.py update_china_divisions "$@"
