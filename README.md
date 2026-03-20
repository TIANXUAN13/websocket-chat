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
python manage.py runserver
```

或者直接一键启动：

```bash
./start.sh
```

如果你需要 WebSocket 聊天功能，优先使用 `./start.sh`。当前它会使用已启用 ASGI 的 `runserver`，这样开发环境下 WebSocket 和静态文件都能正常工作。

启动后访问：

```text
http://127.0.0.1:8000/chat/login/
```

## 可选启动方式

如果你想按 ASGI 方式运行，也可以执行：

```bash
daphne -b 127.0.0.1 -p 8000 websocket_project.asgi:application
```

## 关于当前仓库里的 venv

仓库里现在已经有一个 `venv/` 目录，但我检查过，这个环境当前几乎只有 `pip`，没有安装 Django，所以直接使用它会报错：

```text
ModuleNotFoundError: No module named 'django'
```

更稳妥的做法是重新创建一个新的虚拟环境，比如 `.venv/`，避免和旧环境混淆。

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
