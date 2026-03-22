#!/usr/bin/env bash

set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-websocket-chat}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="/etc/default/${SERVICE_NAME}"
VENV_PATH="${VENV_PATH:-.venv}"
PYTHON_BIN="$PROJECT_DIR/$VENV_PATH/bin/python"
RUN_SCRIPT="$PROJECT_DIR/scripts/run_linux_service.sh"
APP_USER="${APP_USER:-$(id -un)}"
APP_GROUP="${APP_GROUP:-$(id -gn)}"
BIND_HOST="${BIND_HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
APP_MODULE="${APP_MODULE:-websocket_project.asgi:application}"
MIGRATE_ON_START="${MIGRATE_ON_START:-1}"

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

install_dependencies() {
  ensure_venv

  if [ ! -f "$PROJECT_DIR/requirements.txt" ]; then
    echo "requirements.txt not found."
    exit 1
  fi

  echo "Installing Python dependencies..."
  "$PYTHON_BIN" -m pip install --upgrade pip
  "$PYTHON_BIN" -m pip install -r "$PROJECT_DIR/requirements.txt"
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
ExecStart=$RUN_SCRIPT
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
  run_as_root systemctl "$action" "$SERVICE_NAME"
}

install_service() {
  ensure_systemd
  install_dependencies
  chmod +x "$RUN_SCRIPT" "$PROJECT_DIR/scripts/systemd_service.sh"
  echo "Applying migrations before installation..."
  "$PYTHON_BIN" "$PROJECT_DIR/manage.py" migrate --noinput
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
Service name:     $SERVICE_NAME
Project dir:      $PROJECT_DIR
Unit file:        $UNIT_FILE
Environment file: $ENV_FILE
Run script:       $RUN_SCRIPT
App user/group:   $APP_USER:$APP_GROUP
Bind host/port:   $BIND_HOST:$PORT
ASGI module:      $APP_MODULE
Migrate on start: $MIGRATE_ON_START
EOF
}

usage() {
  cat <<EOF
Usage: ./scripts/systemd_service.sh <command>

Commands:
  install     Create the virtualenv if needed, install deps, write the service, enable and start it
  start       Start the service
  stop        Stop the service
  restart     Restart the service
  status      Show service status
  enable      Enable the service at boot
  disable     Disable the service at boot
  logs        Tail service logs
  config      Show the generated service configuration
  uninstall   Stop, disable and remove the service

Optional environment variables:
  SERVICE_NAME      Default: websocket-chat
  APP_USER          Default: current user
  APP_GROUP         Default: current user's primary group
  VENV_PATH         Default: .venv
  BIND_HOST         Default: 0.0.0.0
  PORT              Default: 8000
  APP_MODULE        Default: websocket_project.asgi:application
  MIGRATE_ON_START  Default: 1
EOF
}

main() {
  local command="${1:-}"

  case "$command" in
    install)
      install_service
      ;;
    start|stop|restart|status|enable|disable)
      ensure_systemd
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
