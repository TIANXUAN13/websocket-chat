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
./start_mac.sh
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

`start_mac.sh` 只用于本地测试，它会自动：

- 创建或复用虚拟环境
- 安装/更新 `requirements.txt` 依赖
- 自动执行数据库迁移
- 启动 Django 开发服务器

`start.sh` 仍然保留，等价于执行 `./start_mac.sh`。

## Linux 持续运行部署

Linux 统一使用 `start_linux.sh`。

首次执行下面这条即可完成完整服务部署并持续运行：

```bash
chmod +x start_linux.sh scripts/service.sh
./start_linux.sh
```

如果你希望服务以指定用户运行，例如 `www-data`：

```bash
APP_USER=www-data APP_GROUP=www-data ./start_linux.sh
```

常用命令：

```bash
./start_linux.sh start
./start_linux.sh stop
./start_linux.sh restart
./start_linux.sh status
./start_linux.sh enable
./start_linux.sh disable
./start_linux.sh logs
./start_linux.sh config
./start_linux.sh uninstall
./start_linux.sh serve
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
GEOCODE_PROVIDER=auto
GEOCODE_TIMEOUT=8
AMAP_WEB_API_KEY=你的高德Web服务Key
```

地理位置反解说明：

- 现在支持通过环境变量切换反向地理编码提供方。
- 默认 `GEOCODE_PROVIDER=auto`，中国大陆坐标且配置了 `AMAP_WEB_API_KEY` 时，会优先走高德，再回退到其他公共接口。
- 国内服务器如果访问国外接口不稳定，建议配置 `AMAP_WEB_API_KEY`。

`start_linux.sh` 默认会执行完整部署流程，脚本会自动：

- 创建虚拟环境
- 在依赖变化时自动安装 `requirements.txt` 依赖
- 启动前自动执行数据库迁移
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
