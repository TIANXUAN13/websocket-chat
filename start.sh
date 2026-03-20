#!/usr/bin/env bash

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

if [ -d ".venv" ]; then
  VENV_PATH=".venv"
elif [ -d "venv" ]; then
  VENV_PATH="venv"
else
  echo "No virtual environment found. Create one first with:"
  echo "python3 -m venv .venv"
  exit 1
fi

echo "Using virtual environment: $VENV_PATH"
. "$VENV_PATH/bin/activate"

echo "Applying migrations..."
python manage.py migrate

echo "Starting Django ASGI development server at http://127.0.0.1:8000"
python manage.py runserver 127.0.0.1:8000 --insecure
