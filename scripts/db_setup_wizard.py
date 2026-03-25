#!/usr/bin/env python3

from __future__ import annotations

import argparse
import html
import os
import socket
import threading
import time
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>数据库初始化</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7efe6;
      --card: rgba(255, 251, 246, 0.96);
      --line: rgba(104, 74, 52, 0.12);
      --text: #33241a;
      --muted: #7a6659;
      --accent: #be6a42;
      --accent-soft: rgba(190, 106, 66, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: "PingFang SC", "Noto Sans SC", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(255, 214, 170, 0.55), transparent 28%),
        radial-gradient(circle at top right, rgba(204, 226, 211, 0.4), transparent 24%),
        linear-gradient(180deg, #fbf6ef, #f4ecdf);
      color: var(--text);
      display: grid;
      place-items: center;
      padding: 24px;
    }}
    .card {{
      width: min(100%, 720px);
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 28px;
      box-shadow: 0 28px 60px rgba(80, 53, 33, 0.12);
      padding: 28px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 30px;
    }}
    p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.7;
    }}
    .choices {{
      display: grid;
      gap: 12px;
      margin: 22px 0 18px;
    }}
    .choice {{
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 16px 18px;
      background: rgba(255, 255, 255, 0.55);
    }}
    .choice input {{ margin-right: 8px; }}
    .choice strong {{ font-size: 18px; }}
    .choice span {{
      display: block;
      margin-top: 6px;
      color: var(--muted);
      font-size: 14px;
    }}
    .fields {{
      display: grid;
      gap: 14px;
      margin-top: 18px;
    }}
    .field-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }}
    label {{
      display: grid;
      gap: 6px;
      font-size: 14px;
      font-weight: 700;
    }}
    input {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 12px 14px;
      font: inherit;
      color: var(--text);
      background: rgba(255, 255, 255, 0.9);
    }}
    input:focus {{
      outline: none;
      border-color: rgba(190, 106, 66, 0.7);
      box-shadow: 0 0 0 4px rgba(190, 106, 66, 0.12);
    }}
    .section {{
      display: none;
    }}
    .section.is-active {{
      display: grid;
      gap: 14px;
    }}
    .error {{
      margin-top: 14px;
      color: #a43b20;
      background: rgba(223, 79, 46, 0.08);
      border: 1px solid rgba(223, 79, 46, 0.16);
      border-radius: 16px;
      padding: 12px 14px;
    }}
    .actions {{
      display: flex;
      justify-content: flex-end;
      margin-top: 22px;
    }}
    button {{
      border: 0;
      border-radius: 999px;
      padding: 12px 20px;
      background: var(--accent);
      color: #fff;
      font: inherit;
      font-weight: 800;
      cursor: pointer;
      box-shadow: 0 14px 30px rgba(190, 106, 66, 0.24);
    }}
    .tips {{
      margin-top: 18px;
      padding-top: 18px;
      border-top: 1px solid rgba(104, 74, 52, 0.08);
      color: var(--muted);
      font-size: 13px;
      line-height: 1.7;
    }}
    @media (max-width: 640px) {{
      body {{ padding: 14px; }}
      .card {{ padding: 20px; border-radius: 22px; }}
      h1 {{ font-size: 24px; }}
      .field-grid {{ grid-template-columns: 1fr; }}
      .actions button {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <form class="card" method="post" action="/save">
    <h1>首次启动先选数据库</h1>
    <p>这个项目会在第一次启动时记住你的数据库选择。后续再启动时，就不会重复弹出这个页面了。</p>

    {error_block}

    <div class="choices">
      <label class="choice">
        <div>
          <input type="radio" name="backend" value="sqlite" {sqlite_checked}>
          <strong>SQLite</strong>
        </div>
        <span>适合本地开发，零额外依赖，默认使用项目根目录下的 <code>db.sqlite3</code>。</span>
      </label>
      <label class="choice">
        <div>
          <input type="radio" name="backend" value="postgres" {postgres_checked}>
          <strong>PostgreSQL</strong>
        </div>
        <span>适合后续切换到正式环境或多人协作，首次会记录连接参数。</span>
      </label>
    </div>

    <div class="fields">
      <div class="section {sqlite_active}" data-section="sqlite">
        <label>
          SQLite 文件路径
          <input type="text" name="sqlite_path" value="{sqlite_path}" placeholder="{default_sqlite_path}">
        </label>
      </div>

      <div class="section {postgres_active}" data-section="postgres">
        <div class="field-grid">
          <label>
            数据库名
            <input type="text" name="db_name" value="{db_name}" placeholder="websocket_chat">
          </label>
          <label>
            用户名
            <input type="text" name="db_user" value="{db_user}" placeholder="postgres">
          </label>
          <label>
            密码
            <input type="password" name="db_password" value="{db_password}" placeholder="可留空">
          </label>
          <label>
            主机
            <input type="text" name="db_host" value="{db_host}" placeholder="127.0.0.1">
          </label>
          <label>
            端口
            <input type="text" name="db_port" value="{db_port}" placeholder="5432">
          </label>
          <label>
            SSL mode
            <input type="text" name="db_sslmode" value="{db_sslmode}" placeholder="disable">
          </label>
        </div>
      </div>
    </div>

    <div class="actions">
      <button type="submit">保存并继续启动</button>
    </div>

    <div class="tips">
      保存后配置会写入本地 <code>.runtime-db.env</code>，不会进入 Git。
      如果后面想重新选择数据库，可以执行 <code>./scripts/service.sh db-reset</code>。
    </div>
  </form>

  <script>
    const radios = document.querySelectorAll('input[name="backend"]');
    const sections = document.querySelectorAll('[data-section]');
    function syncSections() {{
      const active = document.querySelector('input[name="backend"]:checked')?.value || 'sqlite';
      sections.forEach((section) => {{
        section.classList.toggle('is-active', section.dataset.section === active);
      }});
    }}
    radios.forEach((radio) => radio.addEventListener('change', syncSections));
    syncSections();
  </script>
</body>
</html>
"""

SUCCESS_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>数据库配置已保存</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      font-family: "PingFang SC", "Noto Sans SC", sans-serif;
      background: linear-gradient(180deg, #fbf6ef, #f4ecdf);
      color: #33241a;
    }}
    .card {{
      width: min(100%, 520px);
      padding: 28px;
      border-radius: 24px;
      background: rgba(255, 251, 246, 0.96);
      border: 1px solid rgba(104, 74, 52, 0.12);
      box-shadow: 0 28px 60px rgba(80, 53, 33, 0.12);
      text-align: center;
    }}
    h1 {{ margin: 0 0 10px; }}
    p {{ margin: 0; line-height: 1.7; color: #7a6659; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>数据库配置已保存</h1>
    <p>现在可以回到终端，项目会继续启动。这个页面可以直接关闭。</p>
  </div>
</body>
</html>
"""


def shell_quote(value: str) -> str:
    return shlex_quote(value)


def shlex_quote(value: str) -> str:
    if value == "":
        return "''"
    return "'" + value.replace("'", "'\"'\"'") + "'"


def write_config(path: Path, values: dict[str, str]) -> None:
    content = "\n".join(
        [
            f"DB_BACKEND={shell_quote(values.get('DB_BACKEND', ''))}",
            f"DB_NAME={shell_quote(values.get('DB_NAME', ''))}",
            f"DB_USER={shell_quote(values.get('DB_USER', ''))}",
            f"DB_PASSWORD={shell_quote(values.get('DB_PASSWORD', ''))}",
            f"DB_HOST={shell_quote(values.get('DB_HOST', ''))}",
            f"DB_PORT={shell_quote(values.get('DB_PORT', ''))}",
            f"DB_SSLMODE={shell_quote(values.get('DB_SSLMODE', ''))}",
            f"SQLITE_PATH={shell_quote(values.get('SQLITE_PATH', ''))}",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_context(defaults: dict[str, str], error: str = "") -> dict[str, str]:
    backend = defaults.get("backend", "sqlite")
    return {
        "error_block": f'<div class="error">{html.escape(error)}</div>' if error else "",
        "sqlite_checked": "checked" if backend == "sqlite" else "",
        "postgres_checked": "checked" if backend == "postgres" else "",
        "sqlite_active": "is-active" if backend == "sqlite" else "",
        "postgres_active": "is-active" if backend == "postgres" else "",
        "default_sqlite_path": html.escape(defaults["default_sqlite_path"]),
        "sqlite_path": html.escape(defaults.get("sqlite_path", defaults["default_sqlite_path"])),
        "db_name": html.escape(defaults.get("db_name", "websocket_chat")),
        "db_user": html.escape(defaults.get("db_user", "postgres")),
        "db_password": html.escape(defaults.get("db_password", "")),
        "db_host": html.escape(defaults.get("db_host", "127.0.0.1")),
        "db_port": html.escape(defaults.get("db_port", "5432")),
        "db_sslmode": html.escape(defaults.get("db_sslmode", "disable")),
    }


def find_free_port(bind_host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((bind_host, 0))
        return int(sock.getsockname()[1])


def detect_local_ip() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return str(probe.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-file", required=True)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--sqlite-path", default="")
    parser.add_argument("--db-name", default="websocket_chat")
    parser.add_argument("--db-user", default="postgres")
    parser.add_argument("--db-password", default="")
    parser.add_argument("--db-host", default="127.0.0.1")
    parser.add_argument("--db-port", default="5432")
    parser.add_argument("--db-sslmode", default="disable")
    parser.add_argument("--wizard-host", default="0.0.0.0")
    args = parser.parse_args()

    config_path = Path(args.config_file)
    project_dir = Path(args.project_dir)
    defaults = {
        "backend": "sqlite",
        "default_sqlite_path": args.sqlite_path or str(project_dir / "db.sqlite3"),
        "sqlite_path": args.sqlite_path or str(project_dir / "db.sqlite3"),
        "db_name": args.db_name,
        "db_user": args.db_user,
        "db_password": args.db_password,
        "db_host": args.db_host,
        "db_port": args.db_port,
        "db_sslmode": args.db_sslmode,
    }

    state = {
        "saved": False,
        "error": "",
        "defaults": defaults,
        "server": None,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._send_html(HTML_TEMPLATE.format(**make_context(state["defaults"], state["error"])))

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            payload = urllib.parse.parse_qs(body)
            backend = (payload.get("backend", ["sqlite"])[0] or "sqlite").strip().lower()
            next_defaults = {
                "backend": backend,
                "default_sqlite_path": defaults["default_sqlite_path"],
                "sqlite_path": (payload.get("sqlite_path", [defaults["default_sqlite_path"]])[0] or defaults["default_sqlite_path"]).strip(),
                "db_name": (payload.get("db_name", [defaults["db_name"]])[0] or defaults["db_name"]).strip(),
                "db_user": (payload.get("db_user", [defaults["db_user"]])[0] or defaults["db_user"]).strip(),
                "db_password": payload.get("db_password", [defaults["db_password"]])[0],
                "db_host": (payload.get("db_host", [defaults["db_host"]])[0] or defaults["db_host"]).strip(),
                "db_port": (payload.get("db_port", [defaults["db_port"]])[0] or defaults["db_port"]).strip(),
                "db_sslmode": (payload.get("db_sslmode", [defaults["db_sslmode"]])[0] or defaults["db_sslmode"]).strip(),
            }
            state["defaults"] = next_defaults

            if backend == "postgres":
                if not next_defaults["db_name"] or not next_defaults["db_user"] or not next_defaults["db_host"] or not next_defaults["db_port"]:
                    state["error"] = "PostgreSQL 需要填写数据库名、用户名、主机和端口。"
                    self._send_html(HTML_TEMPLATE.format(**make_context(state["defaults"], state["error"])), status=HTTPStatus.BAD_REQUEST)
                    return
                values = {
                    "DB_BACKEND": "postgres",
                    "DB_NAME": next_defaults["db_name"],
                    "DB_USER": next_defaults["db_user"],
                    "DB_PASSWORD": next_defaults["db_password"],
                    "DB_HOST": next_defaults["db_host"],
                    "DB_PORT": next_defaults["db_port"],
                    "DB_SSLMODE": next_defaults["db_sslmode"] or "disable",
                    "SQLITE_PATH": "",
                }
            else:
                values = {
                    "DB_BACKEND": "sqlite",
                    "DB_NAME": "",
                    "DB_USER": "",
                    "DB_PASSWORD": "",
                    "DB_HOST": "",
                    "DB_PORT": "",
                    "DB_SSLMODE": "",
                    "SQLITE_PATH": next_defaults["sqlite_path"] or defaults["default_sqlite_path"],
                }

            write_config(config_path, values)
            state["saved"] = True
            state["error"] = ""
            self._send_html(SUCCESS_HTML)
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

        def _send_html(self, payload: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = payload.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    bind_host = args.wizard_host or "0.0.0.0"
    port = find_free_port(bind_host)
    server = ThreadingHTTPServer((bind_host, port), Handler)
    state["server"] = server
    local_ip = detect_local_ip()
    browser_url = f"http://127.0.0.1:{port}/"
    access_url = f"http://{local_ip}:{port}/" if bind_host == "0.0.0.0" else f"http://{bind_host}:{port}/"

    print("")
    print("首次启动需要先选择数据库。")
    print(f"请在浏览器打开：{access_url}")
    if access_url != browser_url:
        print(f"本机也可以访问：{browser_url}")
    print("保存后终端会继续启动项目。")
    print("")

    threading.Thread(target=lambda: (time.sleep(0.3), webbrowser.open(browser_url)), daemon=True).start()
    server.serve_forever()

    if not state["saved"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
