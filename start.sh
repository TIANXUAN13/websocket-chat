#!/usr/bin/env bash

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

PYPI_MIRROR_URL="${PYPI_MIRROR_URL:-https://mirrors.aliyun.com/pypi/simple/}"
PYPI_TRUSTED_HOST="${PYPI_TRUSTED_HOST:-mirrors.aliyun.com}"

ensure_python3() {
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return
  fi

  echo "python3 is not installed. Please install Python 3 first."
  exit 1
}

if [ -d ".venv" ]; then
  VENV_PATH=".venv"
elif [ -d "venv" ]; then
  VENV_PATH="venv"
else
  VENV_PATH="venv"
  PYTHON3_BIN="$(ensure_python3)"
  echo "No virtual environment found. Creating $VENV_PATH..."
  "$PYTHON3_BIN" -m venv "$VENV_PATH"
fi

echo "Using virtual environment: $VENV_PATH"
PYTHON_BIN="$PROJECT_DIR/$VENV_PATH/bin/python"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Python executable not found in $VENV_PATH."
  echo "Try recreating the virtual environment and reinstalling dependencies."
  exit 1
fi

if [ ! -f "requirements.txt" ]; then
  echo "requirements.txt not found."
  exit 1
fi

echo "Installing dependencies from requirements.txt..."
"$PYTHON_BIN" -m pip install --upgrade pip \
  -i "$PYPI_MIRROR_URL" \
  --trusted-host "$PYPI_TRUSTED_HOST"
"$PYTHON_BIN" -m pip install -r requirements.txt \
  -i "$PYPI_MIRROR_URL" \
  --trusted-host "$PYPI_TRUSTED_HOST"

echo "Applying migrations..."
"$PYTHON_BIN" manage.py migrate

echo "Starting Django ASGI development server at http://0.0.0.0:8000"
"$PYTHON_BIN" manage.py runserver 0.0.0.0:8000 --insecure
