#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


AUTO_EXCLUDES = [
    "contenttypes",
    "auth.permission",
]


def build_env(base: dict[str, str], **overrides: str) -> dict[str, str]:
    env = dict(base)
    env.update({key: value for key, value in overrides.items() if value is not None})
    return env


def run(command: list[str], env: dict[str, str], cwd: Path, capture: bool = False) -> subprocess.CompletedProcess[str]:
    print("$", " ".join(command))
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        check=True,
        capture_output=capture,
    )


def build_pg_env(base_env: dict[str, str], args: argparse.Namespace) -> dict[str, str]:
    return build_env(
        base_env,
        DJANGO_SETTINGS_MODULE="websocket_project.settings",
        DB_BACKEND="postgres",
        DB_NAME=args.db_name,
        DB_USER=args.db_user,
        DB_PASSWORD=args.db_password,
        DB_HOST=args.db_host,
        DB_PORT=args.db_port,
        DB_SSLMODE=args.db_sslmode,
        SQLITE_PATH="",
    )


def build_sqlite_env(base_env: dict[str, str], sqlite_path: str) -> dict[str, str]:
    return build_env(
        base_env,
        DJANGO_SETTINGS_MODULE="websocket_project.settings",
        DB_BACKEND="sqlite",
        SQLITE_PATH=sqlite_path,
        DB_NAME="",
        DB_USER="",
        DB_PASSWORD="",
        DB_HOST="",
        DB_PORT="",
        DB_SSLMODE="",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把当前项目的 SQLite 数据完整迁移到 PostgreSQL。",
    )
    parser.add_argument("--source-sqlite", default="db.sqlite3", help="源 SQLite 文件路径，默认 db.sqlite3")
    parser.add_argument("--db-name", required=True, help="目标 PostgreSQL 数据库名")
    parser.add_argument("--db-user", required=True, help="目标 PostgreSQL 用户名")
    parser.add_argument("--db-password", default="", help="目标 PostgreSQL 密码")
    parser.add_argument("--db-host", default="127.0.0.1", help="目标 PostgreSQL 主机，默认 127.0.0.1")
    parser.add_argument("--db-port", default="5432", help="目标 PostgreSQL 端口，默认 5432")
    parser.add_argument("--db-sslmode", default="disable", help="目标 PostgreSQL SSL mode，默认 disable")
    parser.add_argument("--fixture", default="", help="可选：指定中间 dump JSON 文件路径；默认使用临时文件")
    parser.add_argument("--keep-fixture", action="store_true", help="保留中间 JSON 文件")
    parser.add_argument("--reset-target", action="store_true", help="迁移前自动清空目标 PostgreSQL 数据")
    parser.add_argument("--skip-check-empty", action="store_true", help="跳过目标库是否为空的检查")
    parser.add_argument("--include-sessions", action="store_true", help="默认不迁移会话表；加上这个参数后连 sessions 一起迁移")
    return parser.parse_args()


def ensure_sqlite_exists(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"SQLite 文件不存在: {path}")


def get_fixture_path(project_dir: Path, args: argparse.Namespace) -> tuple[Path, bool]:
    if args.fixture:
        return Path(args.fixture).resolve(), False
    fd, path = tempfile.mkstemp(prefix="sqlite_to_pg_", suffix=".json", dir=project_dir)
    os.close(fd)
    return Path(path), True


def target_has_user_data(project_dir: Path, python_bin: Path, env: dict[str, str]) -> bool:
    code = """
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'websocket_project.settings')
import django
django.setup()
from django.apps import apps

ignored = {
    'contenttypes.ContentType',
    'auth.Permission',
}

has_data = False
for model in apps.get_models():
    label = f"{model._meta.app_label}.{model.__name__}"
    if label in ignored:
        continue
    try:
        if model.objects.exists():
            has_data = True
            break
    except Exception:
        continue

print('1' if has_data else '0')
"""
    result = run([str(python_bin), "-c", code], env=env, cwd=project_dir, capture=True)
    return result.stdout.strip() == "1"


def main() -> int:
    args = parse_args()
    project_dir = Path(__file__).resolve().parent.parent
    python_bin = project_dir / ".venv" / "bin" / "python"
    manage_py = project_dir / "manage.py"

    if not python_bin.exists():
        raise SystemExit(f"未找到虚拟环境 Python: {python_bin}")
    if not manage_py.exists():
        raise SystemExit(f"未找到 manage.py: {manage_py}")

    sqlite_path = Path(args.source_sqlite).resolve()
    ensure_sqlite_exists(sqlite_path)

    fixture_path, remove_fixture_when_done = get_fixture_path(project_dir, args)
    base_env = dict(os.environ)
    sqlite_env = build_sqlite_env(base_env, str(sqlite_path))
    pg_env = build_pg_env(base_env, args)

    excludes = list(AUTO_EXCLUDES)
    if not args.include_sessions:
        excludes.append("sessions")

    dump_command = [str(python_bin), str(manage_py), "dumpdata", "--output", str(fixture_path), "--natural-foreign", "--natural-primary"]
    for item in excludes:
        dump_command.extend(["--exclude", item])

    try:
        print(f"源 SQLite: {sqlite_path}")
        print(f"目标 PostgreSQL: {args.db_user}@{args.db_host}:{args.db_port}/{args.db_name}")
        print(f"中间数据文件: {fixture_path}")

        run(dump_command, env=sqlite_env, cwd=project_dir)
        run([str(python_bin), str(manage_py), "migrate", "--noinput"], env=pg_env, cwd=project_dir)

        if not args.skip_check_empty:
            has_data = target_has_user_data(project_dir, python_bin, pg_env)
            if has_data and not args.reset_target:
                raise SystemExit(
                    "目标 PostgreSQL 已有数据。请先清空目标库，或追加 `--reset-target` 让脚本自动执行 flush。"
                )

        if args.reset_target:
            run([str(python_bin), str(manage_py), "flush", "--noinput"], env=pg_env, cwd=project_dir)

        run([str(python_bin), str(manage_py), "loaddata", str(fixture_path)], env=pg_env, cwd=project_dir)

        print("")
        print("SQLite -> PostgreSQL 数据迁移完成。")
        print("如果你准备让项目后续直接跑 PostgreSQL，可以重新启动项目并在首次数据库配置页中选择 PostgreSQL。")
        return 0
    finally:
        if remove_fixture_when_done and not args.keep_fixture and fixture_path.exists():
            fixture_path.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
