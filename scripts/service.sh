#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$PROJECT_DIR"

SERVICE_NAME="${SERVICE_NAME:-websocket-chat}"
UNIT_FILE="${UNIT_FILE:-/etc/systemd/system/${SERVICE_NAME}.service}"
ENV_FILE="${ENV_FILE:-/etc/default/${SERVICE_NAME}}"
VENV_PATH="${VENV_PATH:-.venv}"
PYTHON_BIN="$PROJECT_DIR/$VENV_PATH/bin/python"
DAPHNE_BIN="$PROJECT_DIR/$VENV_PATH/bin/daphne"
APP_USER="${APP_USER:-$(id -un)}"
APP_GROUP="${APP_GROUP:-$(id -gn)}"
BIND_HOST="${BIND_HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
APP_MODULE="${APP_MODULE:-websocket_project.asgi:application}"
MIGRATE_ON_START="${MIGRATE_ON_START:-1}"
INSTALL_DEPS_ON_START="${INSTALL_DEPS_ON_START:-1}"
PYPI_MIRROR_URL="${PYPI_MIRROR_URL:-https://mirrors.aliyun.com/pypi/simple/}"
PYPI_TRUSTED_HOST="${PYPI_TRUSTED_HOST:-mirrors.aliyun.com}"
GEOCODE_PROVIDER="${GEOCODE_PROVIDER:-auto}"
GEOCODE_TIMEOUT="${GEOCODE_TIMEOUT:-8}"
AMAP_WEB_API_KEY="${AMAP_WEB_API_KEY:-}"
REVERSE_GEOCODE_URL="${REVERSE_GEOCODE_URL:-https://nominatim.openstreetmap.org/reverse}"
BIGDATA_REVERSE_URL="${BIGDATA_REVERSE_URL:-https://api.bigdatacloud.net/data/reverse-geocode-client}"
GEOCODE_USER_AGENT="${GEOCODE_USER_AGENT:-websocket-chat/1.0 (location reverse geocoding)}"
REQUIREMENTS_STAMP="$PROJECT_DIR/$VENV_PATH/.requirements.installed"
SELF_SCRIPT="$PROJECT_DIR/scripts/service.sh"

if [ "$(id -u)" -eq 0 ]; then
  SUDO_CMD=""
else
  SUDO_CMD="sudo"
fi

run_as_root() {
  if [ -n "$SUDO_CMD" ]; then
    "$SUDO_CMD" "$@"
  else
    "$@"
  fi
}

ensure_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "$command_name is not installed."
    exit 1
  fi
}

ensure_python3() {
  ensure_command python3
  echo "python3"
}

ensure_systemd() {
  ensure_command systemctl
  ensure_command journalctl
}

ensure_venv() {
  local python3_bin
  python3_bin="$(ensure_python3)"

  if [ ! -d "$PROJECT_DIR/$VENV_PATH" ]; then
    echo "Creating virtual environment at $PROJECT_DIR/$VENV_PATH"
    "$python3_bin" -m venv "$PROJECT_DIR/$VENV_PATH"
  fi

  if [ ! -x "$PYTHON_BIN" ]; then
    echo "Python executable not found in $VENV_PATH"
    exit 1
  fi
}

requirements_changed() {
  if [ ! -f "$PROJECT_DIR/requirements.txt" ]; then
    echo "requirements.txt not found."
    exit 1
  fi

  if [ ! -f "$REQUIREMENTS_STAMP" ]; then
    return 0
  fi

  if ! cmp -s "$PROJECT_DIR/requirements.txt" "$REQUIREMENTS_STAMP"; then
    return 0
  fi

  return 1
}

install_dependencies() {
  ensure_venv

  if requirements_changed; then
    echo "Installing Python dependencies..."
    "$PYTHON_BIN" -m pip install --upgrade pip \
      -i "$PYPI_MIRROR_URL" \
      --trusted-host "$PYPI_TRUSTED_HOST"
    "$PYTHON_BIN" -m pip install -r "$PROJECT_DIR/requirements.txt" \
      -i "$PYPI_MIRROR_URL" \
      --trusted-host "$PYPI_TRUSTED_HOST"
    cp "$PROJECT_DIR/requirements.txt" "$REQUIREMENTS_STAMP"
  else
    echo "Python dependencies are up to date."
  fi
}

prepare_runtime() {
  ensure_venv

  if [ "$INSTALL_DEPS_ON_START" = "1" ]; then
    install_dependencies
  fi

  if [ "$MIGRATE_ON_START" = "1" ]; then
    echo "Applying migrations..."
    "$PYTHON_BIN" "$PROJECT_DIR/manage.py" migrate --noinput
  fi
}

run_dev_server() {
  prepare_runtime
  echo "Starting Django ASGI development server at http://${BIND_HOST}:${PORT}"
  exec "$PYTHON_BIN" "$PROJECT_DIR/manage.py" runserver "${BIND_HOST}:${PORT}" --insecure
}

run_daphne_server() {
  prepare_runtime

  if [ ! -x "$DAPHNE_BIN" ]; then
    echo "Daphne executable not found: $DAPHNE_BIN"
    echo "Please ensure dependencies are installed correctly."
    exit 1
  fi

  echo "Starting Daphne on ${BIND_HOST}:${PORT} with ${APP_MODULE}"
  exec "$DAPHNE_BIN" -b "$BIND_HOST" -p "$PORT" "$APP_MODULE"
}

write_env_file() {
  run_as_root mkdir -p "$(dirname "$ENV_FILE")"
  run_as_root tee "$ENV_FILE" >/dev/null <<EOF
PROJECT_DIR=$PROJECT_DIR
VENV_PATH=$VENV_PATH
BIND_HOST=$BIND_HOST
PORT=$PORT
APP_MODULE=$APP_MODULE
MIGRATE_ON_START=$MIGRATE_ON_START
INSTALL_DEPS_ON_START=$INSTALL_DEPS_ON_START
PYPI_MIRROR_URL=$PYPI_MIRROR_URL
PYPI_TRUSTED_HOST=$PYPI_TRUSTED_HOST
GEOCODE_PROVIDER=$GEOCODE_PROVIDER
GEOCODE_TIMEOUT=$GEOCODE_TIMEOUT
AMAP_WEB_API_KEY=$AMAP_WEB_API_KEY
REVERSE_GEOCODE_URL=$REVERSE_GEOCODE_URL
BIGDATA_REVERSE_URL=$BIGDATA_REVERSE_URL
GEOCODE_USER_AGENT=$GEOCODE_USER_AGENT
EOF
}

write_unit_file() {
  run_as_root tee "$UNIT_FILE" >/dev/null <<EOF
[Unit]
Description=WebSocket Chat Django Service
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$SELF_SCRIPT serve
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF
}

reload_systemd() {
  run_as_root systemctl daemon-reload
}

service_action() {
  local action="$1"
  ensure_systemd
  run_as_root systemctl "$action" "$SERVICE_NAME"
}

install_service() {
  ensure_systemd
  chmod +x "$SELF_SCRIPT" "$PROJECT_DIR/start.sh"
  prepare_runtime
  write_env_file
  write_unit_file
  reload_systemd
  service_action enable
  service_action restart
  echo "Installed and started ${SERVICE_NAME}.service"
}

uninstall_service() {
  ensure_systemd
  set +e
  run_as_root systemctl stop "$SERVICE_NAME" >/dev/null 2>&1
  run_as_root systemctl disable "$SERVICE_NAME" >/dev/null 2>&1
  set -e
  run_as_root rm -f "$UNIT_FILE" "$ENV_FILE"
  reload_systemd
  echo "Removed ${SERVICE_NAME}.service"
}

logs_service() {
  ensure_systemd
  run_as_root journalctl -u "$SERVICE_NAME" -n 200 -f
}

show_config() {
  cat <<EOF
Service name:          $SERVICE_NAME
Project dir:           $PROJECT_DIR
Unit file:             $UNIT_FILE
Environment file:      $ENV_FILE
Launcher script:       $SELF_SCRIPT
App user/group:        $APP_USER:$APP_GROUP
Bind host/port:        $BIND_HOST:$PORT
ASGI module:           $APP_MODULE
Migrate on start:      $MIGRATE_ON_START
Install deps on start: $INSTALL_DEPS_ON_START
Virtual env path:      $VENV_PATH
Geocode provider:      $GEOCODE_PROVIDER
Geocode timeout:       $GEOCODE_TIMEOUT
AMap key:              ${AMAP_WEB_API_KEY:+configured}
Reverse geocode URL:   $REVERSE_GEOCODE_URL
Secondary geocode URL: $BIGDATA_REVERSE_URL
EOF
}

usage() {
  cat <<EOF
Usage: ./scripts/service.sh <command>

Commands:
  dev         Prepare venv/deps/migrations and start Django dev server
  serve       Prepare venv/deps/migrations and start Daphne
  install     Install/update the systemd service and start it
  start       Start the systemd service
  stop        Stop the systemd service
  restart     Restart the systemd service
  status      Show service status
  enable      Enable the service at boot
  disable     Disable the service at boot
  logs        Tail service logs
  config      Show the resolved runtime configuration
  uninstall   Stop, disable and remove the service

Optional environment variables:
  SERVICE_NAME           Default: websocket-chat
  APP_USER               Default: current user
  APP_GROUP              Default: current user's primary group
  VENV_PATH              Default: .venv
  BIND_HOST              Default: 0.0.0.0
  PORT                   Default: 8000
  APP_MODULE             Default: websocket_project.asgi:application
  MIGRATE_ON_START       Default: 1
  INSTALL_DEPS_ON_START  Default: 1
  PYPI_MIRROR_URL        Default: https://mirrors.aliyun.com/pypi/simple/
  PYPI_TRUSTED_HOST      Default: mirrors.aliyun.com
  GEOCODE_PROVIDER       Default: auto
  GEOCODE_TIMEOUT        Default: 8
  AMAP_WEB_API_KEY       Optional: 高德 Web 服务 Key，国内服务器建议配置
  REVERSE_GEOCODE_URL    Default: https://nominatim.openstreetmap.org/reverse
  BIGDATA_REVERSE_URL    Default: https://api.bigdatacloud.net/data/reverse-geocode-client
EOF
}

main() {
  local command="${1:-dev}"

  case "$command" in
    dev)
      run_dev_server
      ;;
    serve)
      run_daphne_server
      ;;
    install)
      install_service
      ;;
    start|stop|restart|status|enable|disable)
      service_action "$command"
      ;;
    logs)
      logs_service
      ;;
    config)
      show_config
      ;;
    uninstall)
      uninstall_service
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
