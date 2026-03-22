# websocket_project

这是一个基于 Django 和 Channels 的 WebSocket 聊天项目。

## 技术栈

- Django
- Channels
- Daphne
- SQLite

## 本地启动

在项目根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

或者直接一键启动：

```bash
./start.sh
```

启动后访问：

```text
http://127.0.0.1:8000/chat/login/
```

## 可选启动方式

如果你想按 ASGI 方式运行，也可以执行：

```bash
daphne -b 0.0.0.0 -p 8000 websocket_project.asgi:application
```

## Linux systemd 部署

项目内置了 `systemd` 安装脚本，可以自动生成服务并控制启停：

```bash
chmod +x scripts/run_linux_service.sh scripts/systemd_service.sh
./scripts/systemd_service.sh install
```

如果你希望服务以指定用户运行，例如 `www-data`：

```bash
APP_USER=www-data APP_GROUP=www-data ./scripts/systemd_service.sh install
```

常用命令：

```bash
./scripts/systemd_service.sh start
./scripts/systemd_service.sh stop
./scripts/systemd_service.sh restart
./scripts/systemd_service.sh status
./scripts/systemd_service.sh enable
./scripts/systemd_service.sh disable
./scripts/systemd_service.sh logs
./scripts/systemd_service.sh config
./scripts/systemd_service.sh uninstall
```

可选环境变量：

```bash
SERVICE_NAME=websocket-chat
APP_USER=www-data
APP_GROUP=www-data
VENV_PATH=.venv
BIND_HOST=0.0.0.0
PORT=8000
APP_MODULE=websocket_project.asgi:application
MIGRATE_ON_START=1
```

脚本会自动：

- 创建虚拟环境
- 安装 `requirements.txt` 依赖
- 执行数据库迁移
- 生成 `/etc/systemd/system/websocket-chat.service`
- 生成 `/etc/default/websocket-chat`
- 执行 `systemctl enable` 并启动服务


## 当前最小依赖

`requirements.txt` 已经整理为项目当前实际需要的最小依赖：

- Django
- channels
- daphne
- requests

## 说明

- 数据库使用本地 `db.sqlite3`
- WebSocket 路由为 `/ws/chat/<room_name>/`
- 当前 Channels 使用内存通道层，适合本地开发
