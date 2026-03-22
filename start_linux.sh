#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "$#" -eq 0 ]; then
  exec "$PROJECT_DIR/scripts/service.sh" install
fi

exec "$PROJECT_DIR/scripts/service.sh" "$@"
