#!/usr/bin/env python3
from __future__ import annotations

import csv
import hmac
import io
import os
import re
import secrets
import sqlite3
import tarfile
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import quote

from flask import (
    Flask,
    Response,
    abort,
    flash,
    redirect,
    render_template_string,
    request,
    send_file,
    session,
    url_for,
)
from markupsafe import Markup


DATABASE = Path(
    os.environ.get(
        "MODEL_DOWNLOADER_DATABASE",
        "/var/lib/model-downloader/library.sqlite3",
    )
)
DOWNLOADS_ROOT = Path(
    os.environ.get(
        "MODEL_DOWNLOADER_DOWNLOADS",
        "/srv/model-downloader/videos",
    )
)
COMMENTS_ROOT = Path(
    os.environ.get(
        "MODEL_DOWNLOADER_COMMENTS",
        "/srv/model-downloader/comments",
    )
)
LIBRARY_ROOT = Path(
    os.environ.get(
        "MODEL_DOWNLOADER_LIBRARY_ROOT",
        "/srv/model-downloader",
    )
)
EXPORTS_ROOT = LIBRARY_ROOT / "exports"
UPDATES_ROOT = LIBRARY_ROOT / "updates"
UPDATE_REQUEST = UPDATES_ROOT / "install.request"
UPDATE_LOG = Path("/var/lib/model-downloader/logs/update.log")
CURRENT_VERSION = Path("/opt/model-downloader/current/VERSION")
ADMIN_PASSWORD = os.environ.get("MODEL_DOWNLOADER_WEB_PASSWORD", "")
ADMIN_SECRET = os.environ.get("MODEL_DOWNLOADER_WEB_SECRET", "")
WEB_USERNAME = os.environ.get("MODEL_DOWNLOADER_WEB_USERNAME", "admin")
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
LOGIN_ATTEMPTS: dict[str, list[float]] = {}


app = Flask(__name__)
app.secret_key = ADMIN_SECRET or secrets.token_hex(32)
app.config.update(
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get(
        "MODEL_DOWNLOADER_WEB_HTTPS",
        "0",
    ).strip() == "1",
    PERMANENT_SESSION_LIFETIME=30 * 24 * 60 * 60,
)


BASE_TEMPLATE = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{{ title }} · 模型下载器</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f7fb;
      --card: #ffffff;
      --ink: #15213a;
      --muted: #6d7890;
      --line: #e1e7f0;
      --blue: #246bfe;
      --blue-dark: #1455d9;
      --green: #12865a;
      --orange: #c96b16;
      --red: #c33b3b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: "Microsoft YaHei", system-ui, sans-serif;
    }
    a { color: var(--blue); text-decoration: none; }
    .top {
      position: sticky;
      top: 0;
      z-index: 10;
      background: rgba(255,255,255,.96);
      border-bottom: 1px solid var(--line);
      backdrop-filter: blur(8px);
    }
    .top-inner, .wrap { max-width: 1180px; margin: auto; }
    .top-inner {
      min-height: 66px;
      padding: 10px 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
    }
    .brand { font-size: 21px; font-weight: 800; color: var(--ink); }
    nav { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
    nav a { padding: 9px 12px; border-radius: 9px; color: #33405a; }
    nav a:hover { background: #eef3ff; color: var(--blue); }
    .wrap { padding: 24px 18px 60px; }
    .hero {
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 18px;
      margin-bottom: 18px;
    }
    h1 { margin: 0 0 7px; font-size: 28px; }
    h2 { margin: 0 0 14px; font-size: 19px; }
    .muted { color: var(--muted); }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      box-shadow: 0 5px 18px rgba(27,44,81,.04);
      padding: 18px;
      margin-bottom: 18px;
    }
    .stat strong { display: block; font-size: 28px; margin-top: 6px; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    button, .button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 38px;
      padding: 8px 14px;
      border: 0;
      border-radius: 9px;
      background: var(--blue);
      color: white;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }
    button:hover, .button:hover { background: var(--blue-dark); color: white; }
    .button.soft { background: #edf3ff; color: var(--blue); }
    .button.green { background: var(--green); }
    .button.orange { background: var(--orange); }
    .button.gray, button.gray { background: #626f86; }
    .button.danger, button.danger { background: var(--red); }
    .danger-zone { border-color: #f2b9b9; background: #fffafa; }
    input[type=text], input[type=password], input[type=file], input[type=date] {
      width: 100%;
      min-height: 42px;
      padding: 9px 11px;
      border: 1px solid #cfd8e6;
      border-radius: 9px;
      font: inherit;
      background: white;
    }
    label { display: block; font-weight: 700; margin: 0 0 7px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 12px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { color: #59657a; font-size: 13px; }
    td { font-size: 14px; }
    .title-cell { max-width: 420px; min-width: 240px; }
    .nowrap { white-space: nowrap; }
    .table-scroll { overflow-x: auto; }
    .pill {
      display: inline-block;
      padding: 4px 8px;
      border-radius: 999px;
      background: #e9f8f1;
      color: var(--green);
      font-size: 12px;
      font-weight: 700;
    }
    .pill.fail { background: #fdecec; color: var(--red); }
    .pill.orange { background: #fff0df; color: var(--orange); }
    .pill.blue { background: #e9f3ff; color: var(--blue-dark); }
    .reply-context {
      margin-top: 8px;
      padding: 9px 11px;
      border-left: 3px solid #88aefc;
      border-radius: 7px;
      background: #f3f7ff;
      color: #43516b;
      font-size: 13px;
    }
    .reply-context strong { color: #2459ad; }
    .flash {
      padding: 12px 14px;
      margin-bottom: 14px;
      border-radius: 10px;
      background: #e9f3ff;
      color: #174d9e;
      border: 1px solid #cfe1ff;
    }
    .flash.error { background: #fff0f0; color: #a42b2b; border-color: #ffd1d1; }
    .empty { padding: 28px; text-align: center; color: var(--muted); }
    .pagination { display: flex; gap: 8px; justify-content: flex-end; margin-top: 15px; }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      background: #101828;
      color: #d9e2f2;
      padding: 14px;
      border-radius: 10px;
      max-height: 300px;
      overflow: auto;
    }
    .login {
      width: min(420px, calc(100% - 32px));
      margin: 10vh auto;
    }
    .login .card { padding: 28px; }
    .login button { width: 100%; margin-top: 14px; }
    .form-row { margin-bottom: 14px; }
    .note {
      padding: 12px 14px;
      border-left: 4px solid var(--blue);
      background: #f0f5ff;
      border-radius: 6px;
      color: #40506a;
    }
    @media (max-width: 850px) {
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .top-inner { align-items: flex-start; flex-direction: column; }
      .hero { align-items: flex-start; flex-direction: column; }
    }
    @media (max-width: 520px) {
      .grid { grid-template-columns: 1fr; }
      nav a { padding: 7px 8px; }
      .wrap { padding-left: 12px; padding-right: 12px; }
    }
  </style>
</head>
<body>
{% if logged_in %}
<header class="top">
  <div class="top-inner">
    <a class="brand" href="{{ url_for('dashboard') }}">模型下载器管理中心</a>
    <nav>
      <a href="{{ url_for('dashboard') }}">视频库</a>
      <a href="{{ url_for('cleanup_page') }}">手动清理</a>
      <a href="{{ url_for('update_page') }}">系统更新</a>
      <a href="{{ url_for('backup_page') }}">数据备份</a>
      <a href="{{ url_for('logout') }}">退出</a>
    </nav>
  </div>
</header>
{% endif %}
<main class="{{ 'wrap' if logged_in else '' }}">
  {% for category, message in get_flashed_messages(with_categories=true) %}
    <div class="flash {{ 'error' if category == 'error' else '' }}">{{ message }}</div>
  {% endfor %}
  {{ body }}
</main>
</body>
</html>
"""


def render_page(title: str, body_template: str, **context: object) -> str:
    body = render_template_string(body_template, **context)
    return render_template_string(
        BASE_TEMPLATE,
        title=title,
        logged_in=bool(session.get("authenticated")),
        body=Markup(body),
    )


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(24)
        session["csrf_token"] = token
    return str(token)


app.jinja_env.globals["csrf_token"] = csrf_token


def login_required(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login", next=request.path))
        return function(*args, **kwargs)

    return wrapped


@app.before_request
def protect_post_requests():
    if request.method != "POST":
        return None
    supplied = request.form.get("csrf_token", "")
    expected = str(session.get("csrf_token", ""))
    if not expected or not hmac.compare_digest(supplied, expected):
        abort(400, "页面已过期，请刷新后重试。")
    return None


def safe_video_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    try:
        root = DOWNLOADS_ROOT.resolve()
        candidate = Path(raw_path).resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    if not candidate.is_file():
        return None
    return candidate


def safe_comment_path(video_id: str, published_at: str) -> Path | None:
    day = str(published_at or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return None
    try:
        root = COMMENTS_ROOT.resolve()
        candidate = (COMMENTS_ROOT / day / f"{video_id}.json").resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def comment_tracking_label(
    published_at: str,
    collected_at: str | None,
) -> str:
    try:
        published = datetime.fromisoformat(str(published_at))
        deadline = published + timedelta(hours=24)
        now = datetime.now(published.tzinfo) if published.tzinfo else datetime.now()
    except (TypeError, ValueError):
        return "评论时间未知"
    if now < deadline:
        if collected_at:
            last = str(collected_at)[5:16].replace("T", " ")
            return f"每小时更新中（最近 {last}）"
        return "每小时更新中（等待首次采集）"
    return "24 小时采集已结束"


def delete_video_and_comments(video_id: str) -> tuple[int, int]:
    """Delete one work and keep a tombstone so it is not downloaded again."""
    row = find_video(video_id)
    paths: list[Path] = []
    raw_video_path = str(row["file_path"] or "")
    video_path = safe_video_path(raw_video_path)
    if raw_video_path and Path(raw_video_path).exists() and video_path is None:
        raise ValueError("视频路径不在模型下载器目录内，已拒绝删除。")
    if video_path:
        paths.append(video_path)
    comment_path = safe_comment_path(video_id, row["published_at"])
    if comment_path:
        paths.append(comment_path)

    staged: list[tuple[Path, Path]] = []
    token = secrets.token_hex(5)
    try:
        for original in paths:
            temporary = original.with_name(
                f".{original.name}.delete-{token}"
            )
            original.replace(temporary)
            staged.append((original, temporary))
        with connect() as connection:
            connection.execute(
                """
                INSERT INTO deleted_videos(
                    video_id, title, published_at, deleted_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    title=excluded.title,
                    published_at=excluded.published_at,
                    deleted_at=excluded.deleted_at
                """,
                (
                    video_id,
                    str(row["title"] or ""),
                    str(row["published_at"] or ""),
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                ),
            )
            connection.execute(
                "DELETE FROM videos WHERE video_id = ?",
                (video_id,),
            )
    except Exception:
        for original, temporary in reversed(staged):
            if temporary.exists() and not original.exists():
                temporary.replace(original)
        raise

    freed = 0
    for _original, temporary in staged:
        try:
            freed += temporary.stat().st_size
            temporary.unlink()
        except OSError:
            pass
    for parent in {original.parent for original, _temporary in staged}:
        try:
            parent.rmdir()
        except OSError:
            pass
    return freed, int(row["comment_count"] or 0)


def safe_download_name(published_at: str, video_id: str) -> str:
    """Use one database-friendly filename for every browser download."""
    try:
        published = datetime.fromisoformat(str(published_at))
        timestamp = published.strftime("%Y%m%d_%H%M")
    except (TypeError, ValueError):
        digits = re.sub(r"\D", "", str(published_at or ""))[:12]
        timestamp = digits if len(digits) == 12 else "unknown_time"
    safe_id = re.sub(r"[^0-9A-Za-z_-]", "_", str(video_id))
    return f"{timestamp}_{safe_id}.mp4"


def file_size_label(size: int | None) -> str:
    value = float(size or 0)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{int(size or 0)} B"


app.jinja_env.globals["file_size_label"] = file_size_label


def current_version() -> str:
    try:
        return CURRENT_VERSION.read_text(encoding="utf-8").strip()
    except OSError:
        return "未知"


@app.errorhandler(413)
def upload_too_large(_error):
    flash("安装包超过 200 MB，未上传。", "error")
    return redirect(url_for("update_page"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("authenticated"):
        return redirect(url_for("dashboard"))
    message = ""
    if request.method == "POST":
        ip_address = request.remote_addr or "unknown"
        now = time.time()
        recent = [
            timestamp
            for timestamp in LOGIN_ATTEMPTS.get(ip_address, [])
            if now - timestamp < 300
        ]
        if len(recent) >= 8:
            message = "尝试次数过多，请 5 分钟后再试。"
        elif not ADMIN_PASSWORD:
            message = "管理密码尚未配置，请联系管理员。"
        else:
            recent.append(now)
            LOGIN_ATTEMPTS[ip_address] = recent
            username_ok = hmac.compare_digest(
                request.form.get("username", ""),
                WEB_USERNAME,
            )
            password_ok = hmac.compare_digest(
                request.form.get("password", ""),
                ADMIN_PASSWORD,
            )
            if username_ok and password_ok:
                LOGIN_ATTEMPTS.pop(ip_address, None)
                session.clear()
                session["authenticated"] = True
                session["csrf_token"] = secrets.token_urlsafe(24)
                session.permanent = True
                return redirect(url_for("dashboard"))
            message = "用户名或密码不正确。"
    return render_page(
        "登录",
        """
        <div class="login">
          <div class="card">
            <h1>模型下载器</h1>
            <p class="muted">登录后可直接查看视频、评论、更新程序和备份数据。</p>
            {% if message %}<div class="flash error">{{ message }}</div>{% endif %}
            <form method="post">
              <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
              <div class="form-row">
                <label for="username">用户名</label>
                <input id="username" name="username" value="admin" autocomplete="username">
              </div>
              <div class="form-row">
                <label for="password">管理密码</label>
                <input id="password" type="password" name="password" autocomplete="current-password" autofocus>
              </div>
              <button type="submit">登录管理中心</button>
            </form>
          </div>
        </div>
        """,
        message=message,
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    page = max(1, request.args.get("page", default=1, type=int))
    per_page = 20
    search = request.args.get("q", "").strip()
    offset = (page - 1) * per_page
    summary = {
        "videos": 0,
        "downloaded": 0,
        "comments": 0,
        "today": 0,
    }
    rows: list[dict[str, object]] = []
    total = 0
    last_scan = None
    if DATABASE.exists():
        with connect() as connection:
            summary["videos"] = connection.execute(
                "SELECT COUNT(*) FROM videos"
            ).fetchone()[0]
            summary["downloaded"] = connection.execute(
                "SELECT COUNT(*) FROM videos WHERE download_status='downloaded'"
            ).fetchone()[0]
            summary["comments"] = connection.execute(
                "SELECT COUNT(*) FROM comments"
            ).fetchone()[0]
            summary["today"] = connection.execute(
                "SELECT COUNT(*) FROM videos WHERE substr(published_at,1,10)=?",
                (datetime.now().date().isoformat(),),
            ).fetchone()[0]
            where = ""
            parameters: list[object] = []
            if search:
                where = "WHERE title LIKE ? OR video_id LIKE ?"
                pattern = f"%{search}%"
                parameters.extend([pattern, pattern])
            total = connection.execute(
                f"SELECT COUNT(*) FROM videos {where}",
                parameters,
            ).fetchone()[0]
            database_rows = connection.execute(
                f"""
                SELECT video_id, creator, title, source_url, published_at,
                       downloaded_at, file_path, file_size, duration_seconds,
                       download_status, comments_collected_at, comment_count,
                       comment_refresh_requested
                FROM videos
                {where}
                ORDER BY published_at DESC
                LIMIT ? OFFSET ?
                """,
                [*parameters, per_page, offset],
            ).fetchall()
            rows = []
            for database_row in database_rows:
                row = dict(database_row)
                row["comment_tracking"] = comment_tracking_label(
                    str(row["published_at"]),
                    str(row["comments_collected_at"] or "") or None,
                )
                row["download_name"] = safe_download_name(
                    str(row["published_at"]),
                    str(row["video_id"]),
                )
                rows.append(row)
            last_scan = connection.execute(
                """
                SELECT started_at, finished_at, success, visible_today,
                       downloaded, message
                FROM scan_runs
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
    page_count = max(1, (total + per_page - 1) // per_page)
    return render_page(
        "视频库",
        """
        <div class="hero">
          <div>
            <h1>视频与评论库</h1>
            <div class="muted">不用找服务器目录，直接在这里播放、下载和导出。</div>
          </div>
          <form method="get" class="actions">
            <input style="width:260px" type="text" name="q" value="{{ search }}" placeholder="搜索标题或作品 ID">
            <button type="submit">搜索</button>
          </form>
        </div>
        <section class="grid">
          <div class="card stat"><span class="muted">作品记录</span><strong>{{ summary.videos }}</strong></div>
          <div class="card stat"><span class="muted">已下载视频</span><strong>{{ summary.downloaded }}</strong></div>
          <div class="card stat"><span class="muted">评论数据</span><strong>{{ summary.comments }}</strong></div>
          <div class="card stat"><span class="muted">今天作品</span><strong>{{ summary.today }}</strong></div>
        </section>
        {% if last_scan %}
        <section class="card">
          <h2>最近一次自动检查</h2>
          {% if not last_scan.finished_at %}
            <span class="pill blue">正在检查</span>
          {% elif last_scan.success %}
            <span class="pill">成功</span>
          {% else %}
            <span class="pill fail">失败</span>
          {% endif %}
          <span class="muted"> {{ last_scan.started_at }} · 今日发现 {{ last_scan.visible_today }} 个 · 新下载 {{ last_scan.downloaded }} 个</span>
          {% if last_scan.message %}<div style="margin-top:8px">{{ last_scan.message }}</div>{% endif %}
        </section>
        {% endif %}
        <section class="card">
          <div class="table-scroll">
            <table>
              <thead>
                <tr><th>发布时间</th><th>作品</th><th>大小/时长</th><th>评论</th><th>操作</th></tr>
              </thead>
              <tbody>
              {% for row in rows %}
                <tr>
                  <td class="nowrap">{{ row.published_at[:16].replace('T',' ') }}</td>
                  <td class="title-cell">
                    <strong>{{ row.title or ('抖音作品_' + row.video_id) }}</strong>
                    <div class="muted">{{ row.video_id }}</div>
                    <div class="muted" style="font-size:12px">下载名：{{ row.download_name }}</div>
                  </td>
                  <td class="nowrap">
                    {{ file_size_label(row.file_size) }}
                    {% if row.duration_seconds %}<div class="muted">{{ '%.1f'|format(row.duration_seconds) }} 秒</div>{% endif %}
                  </td>
                  <td>
                    <span class="nowrap">{{ row.comment_count }} 条</span>
                    <div class="muted">{{ row.comment_tracking }}</div>
                  </td>
                  <td>
                    <div class="actions nowrap">
                      {% if row.file_path and row.download_status in ('downloaded', 'repair_requested', 'repair_failed') %}
                        <a class="button green" target="_blank" href="{{ url_for('play_video', video_id=row.video_id) }}">播放</a>
                        <a class="button" href="{{ url_for('download_video', video_id=row.video_id) }}">下载视频</a>
                      {% else %}
                        <span class="pill fail">视频未就绪</span>
                      {% endif %}
                      <a class="button soft" href="{{ url_for('comments_page', video_id=row.video_id) }}">查看评论</a>
                      <form method="post" action="{{ url_for('request_comment_refresh', video_id=row.video_id) }}">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <button class="gray" type="submit">立即补抓评论</button>
                      </form>
                      <a class="button orange" href="{{ url_for('export_comments_csv', video_id=row.video_id) }}">导出评论</a>
                      <form method="post" action="{{ url_for('request_video_repair', video_id=row.video_id) }}">
                        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                        <button class="gray" type="submit">重新下载/修复声音</button>
                      </form>
                      <a class="button danger" href="{{ url_for('delete_video_page', video_id=row.video_id) }}">删除</a>
                    </div>
                  </td>
                </tr>
              {% else %}
                <tr><td colspan="5" class="empty">目前没有符合条件的作品。</td></tr>
              {% endfor %}
              </tbody>
            </table>
          </div>
          {% if page_count > 1 %}
          <div class="pagination">
            {% if page > 1 %}<a class="button soft" href="?page={{ page-1 }}&q={{ search|urlencode }}">上一页</a>{% endif %}
            <span class="muted">第 {{ page }} / {{ page_count }} 页</span>
            {% if page < page_count %}<a class="button soft" href="?page={{ page+1 }}&q={{ search|urlencode }}">下一页</a>{% endif %}
          </div>
          {% endif %}
        </section>
        """,
        summary=summary,
        rows=rows,
        last_scan=last_scan,
        page=page,
        page_count=page_count,
        search=search,
    )


def find_video(video_id: str) -> sqlite3.Row:
    if not DATABASE.exists():
        abort(404)
    with connect() as connection:
        row = connection.execute(
            """
            SELECT video_id, title, file_path, file_size, source_url,
                   published_at, comment_count, download_status,
                   comments_collected_at, comment_refresh_requested
            FROM videos WHERE video_id=?
            """,
            (video_id,),
        ).fetchone()
    if row is None:
        abort(404)
    return row


@app.route("/videos/<video_id>/play")
@login_required
def play_video(video_id: str):
    row = find_video(video_id)
    path = safe_video_path(row["file_path"])
    if path is None:
        abort(404, "视频文件不存在。")
    return send_file(
        path,
        mimetype="video/mp4",
        conditional=True,
        etag=True,
        max_age=3600,
    )


@app.route("/videos/<video_id>/download")
@login_required
def download_video(video_id: str):
    row = find_video(video_id)
    path = safe_video_path(row["file_path"])
    if path is None:
        abort(404, "视频文件不存在。")
    return send_file(
        path,
        mimetype="video/mp4",
        as_attachment=True,
        download_name=safe_download_name(row["published_at"], video_id),
        conditional=True,
        etag=True,
    )


@app.route("/videos/<video_id>/repair", methods=["POST"])
@login_required
def request_video_repair(video_id: str):
    video = find_video(video_id)
    with connect() as connection:
        connection.execute(
            """
            UPDATE videos
            SET download_status='repair_requested', updated_at=?
            WHERE video_id=?
            """,
            (
                datetime.now().astimezone().isoformat(timespec="seconds"),
                video_id,
            ),
        )
    flash(
        f"已安排重新下载“{video['title'] or video_id}”。"
        "后台会自动重新解析、合并声音并验证 MP4；通常几分钟内完成。"
    )
    return redirect(url_for("dashboard"))


@app.route("/videos/<video_id>/comments/refresh", methods=["POST"])
@login_required
def request_comment_refresh(video_id: str):
    video = find_video(video_id)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with connect() as connection:
        connection.execute(
            """
            UPDATE videos
            SET comment_refresh_requested=1,
                comments_collected_at=NULL,
                updated_at=?
            WHERE video_id=?
            """,
            (timestamp, video_id),
        )
    flash(
        f"已安排补抓“{video['title'] or video_id}”的全部公开评论和展开回复；"
        "将在下一个自动检查周期执行。"
    )
    return redirect(url_for("comments_page", video_id=video_id))


@app.route("/videos/<video_id>/delete", methods=["GET", "POST"])
@login_required
def delete_video_page(video_id: str):
    video = find_video(video_id)
    if request.method == "POST":
        try:
            freed, comments = delete_video_and_comments(video_id)
        except (OSError, sqlite3.Error, ValueError) as exc:
            flash(f"删除失败：{exc}", "error")
            return redirect(url_for("delete_video_page", video_id=video_id))
        flash(
            f"已永久删除 1 条作品、{comments} 条评论，"
            f"释放约 {file_size_label(freed)}。该作品不会被自动重新下载。"
        )
        return redirect(url_for("dashboard"))
    return render_page(
        "确认删除",
        """
        <div class="hero">
          <div>
            <h1>确认删除这条作品</h1>
            <div class="muted">删除动作不可撤销，请先确认视频和评论已经下载到电脑。</div>
          </div>
        </div>
        <section class="card danger-zone">
          <h2>{{ video.title or ('抖音作品_' + video.video_id) }}</h2>
          <p>作品 ID：{{ video.video_id }}</p>
          <p>发布时间：{{ video.published_at[:16].replace('T', ' ') }}</p>
          <p>将同步删除：视频文件、{{ video.comment_count }} 条评论、评论 JSON 和数据库记录。</p>
          <p><strong>删除后会留下“已手动删除”标记，监控程序不会再次下载它。</strong></p>
          <div class="actions">
            <form method="post">
              <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
              <button class="danger" type="submit">确认永久删除</button>
            </form>
            <a class="button gray" href="{{ url_for('dashboard') }}">取消</a>
          </div>
        </section>
        """,
        video=video,
    )


@app.route("/cleanup")
@login_required
def cleanup_page():
    today = datetime.now().date().isoformat()
    before = request.args.get("before", today).strip()
    try:
        selected = datetime.fromisoformat(before).date().isoformat()
    except ValueError:
        selected = today
    if selected > today:
        selected = today
    rows: list[sqlite3.Row] = []
    if DATABASE.exists():
        with connect() as connection:
            rows = connection.execute(
                """
                SELECT video_id, title, published_at, file_size, comment_count
                FROM videos
                WHERE substr(published_at,1,10) < ?
                ORDER BY published_at
                """,
                (selected,),
            ).fetchall()
    total_size = sum(int(row["file_size"] or 0) for row in rows)
    total_comments = sum(int(row["comment_count"] or 0) for row in rows)
    return render_page(
        "手动清理",
        """
        <div class="hero">
          <div>
            <h1>手动清理旧作品</h1>
            <div class="muted">系统不会自动删除；只有您在这里确认后才会清理。</div>
          </div>
        </div>
        <section class="card">
          <h2>先选择日期查看范围</h2>
          <form method="get" class="actions">
            <div><label for="before">删除此日期以前的作品</label><input id="before" type="date" name="before" value="{{ selected }}" max="{{ today }}"></div>
            <button type="submit">查看将要删除的内容</button>
          </form>
          <div class="note" style="margin-top:14px">例如选择 2026-08-01，只会列出 8 月 1 日以前的作品，不包含 8 月 1 日当天。</div>
        </section>
        <section class="card danger-zone">
          <h2>本次共 {{ rows|length }} 条作品</h2>
          <p>视频约 {{ file_size_label(total_size) }}，评论 {{ total_comments }} 条。视频和对应评论会同步删除。</p>
          <div class="table-scroll">
            <table>
              <thead><tr><th>发布时间</th><th>作品</th><th>视频大小</th><th>评论</th></tr></thead>
              <tbody>
              {% for row in rows %}
                <tr><td class="nowrap">{{ row.published_at[:16].replace('T',' ') }}</td><td>{{ row.title or row.video_id }}<div class="muted">{{ row.video_id }}</div></td><td>{{ file_size_label(row.file_size) }}</td><td>{{ row.comment_count }} 条</td></tr>
              {% else %}
                <tr><td colspan="4" class="empty">这个日期范围内没有可删除的作品。</td></tr>
              {% endfor %}
              </tbody>
            </table>
          </div>
          {% if rows %}
          <form method="post" action="{{ url_for('cleanup_delete') }}" style="margin-top:16px">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <input type="hidden" name="before" value="{{ selected }}">
            <label><input type="checkbox" name="confirmed" value="yes" required> 我确认这些视频和评论已经拿到电脑，可以永久删除</label>
            <button class="danger" type="submit">永久删除以上 {{ rows|length }} 条</button>
          </form>
          {% endif %}
        </section>
        """,
        rows=rows,
        selected=selected,
        today=today,
        total_size=total_size,
        total_comments=total_comments,
    )


@app.route("/cleanup/delete", methods=["POST"])
@login_required
def cleanup_delete():
    if request.form.get("confirmed") != "yes":
        flash("请先勾选确认框。", "error")
        return redirect(url_for("cleanup_page"))
    today = datetime.now().date().isoformat()
    before = request.form.get("before", "").strip()
    try:
        selected = datetime.fromisoformat(before).date().isoformat()
    except ValueError:
        flash("日期无效，未执行删除。", "error")
        return redirect(url_for("cleanup_page"))
    if selected > today:
        flash("批量清理不能包含今天的作品。", "error")
        return redirect(url_for("cleanup_page", before=today))
    with connect() as connection:
        video_ids = [
            str(row[0])
            for row in connection.execute(
                "SELECT video_id FROM videos WHERE substr(published_at,1,10) < ?",
                (selected,),
            ).fetchall()
        ]
    deleted = 0
    comments = 0
    freed = 0
    failures: list[str] = []
    for video_id in video_ids:
        try:
            item_freed, item_comments = delete_video_and_comments(video_id)
            deleted += 1
            comments += item_comments
            freed += item_freed
        except (OSError, sqlite3.Error, ValueError) as exc:
            failures.append(f"{video_id}: {exc}")
    if failures:
        flash(
            f"已删除 {deleted} 条，但有 {len(failures)} 条失败："
            + "；".join(failures[:3]),
            "error",
        )
    else:
        flash(
            f"已手动删除 {deleted} 条作品和 {comments} 条评论，"
            f"释放约 {file_size_label(freed)}。"
        )
    return redirect(url_for("cleanup_page", before=selected))


@app.route("/videos/<video_id>/comments")
@login_required
def comments_page(video_id: str):
    video = find_video(video_id)
    page = max(1, request.args.get("page", default=1, type=int))
    view = request.args.get("view", "all").strip().lower()
    if view not in {"all", "creator", "author_liked"}:
        view = "all"
    per_page = 100
    offset = (page - 1) * per_page
    conditions = ["current.video_id=?"]
    parameters: list[object] = [video_id]
    if view == "creator":
        conditions.append("current.is_creator=1")
    elif view == "author_liked":
        conditions.append("current.is_author_digged=1")
    where_clause = " AND ".join(conditions)
    with connect() as connection:
        total = connection.execute(
            f"SELECT COUNT(*) FROM comments AS current WHERE {where_clause}",
            parameters,
        ).fetchone()[0]
        rows = connection.execute(
            f"""
            SELECT current.comment_id, current.parent_comment_id,
                   current.reply_to_comment_id,
                   current.reply_to_user_name, current.author_name,
                   current.author_uid, current.text, current.created_at,
                   current.digg_count, current.reply_count,
                   current.ip_label, current.is_creator,
                   current.is_author_digged, current.label_text,
                   current.first_seen_at, current.last_seen_at,
                   COALESCE(
                       NULLIF(target.author_name, ''),
                       NULLIF(current.reply_to_user_name, ''),
                       NULLIF(parent.author_name, ''), ''
                   ) AS replied_author_name,
                   COALESCE(
                       NULLIF(target.text, ''),
                       NULLIF(parent.text, ''), ''
                   ) AS replied_comment_text,
                   COALESCE(target.comment_id, parent.comment_id, '')
                       AS replied_comment_id
            FROM comments AS current
            LEFT JOIN comments AS parent
              ON parent.video_id=current.video_id
             AND parent.comment_id=current.parent_comment_id
            LEFT JOIN comments AS target
              ON target.video_id=current.video_id
             AND target.comment_id=current.reply_to_comment_id
            WHERE {where_clause}
            ORDER BY current.created_at DESC, current.comment_id DESC
            LIMIT ? OFFSET ?
            """,
            (*parameters, per_page, offset),
        ).fetchall()
        interaction_counts = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN is_creator=1 THEN 1 ELSE 0 END) AS creator,
                   SUM(CASE WHEN is_author_digged=1 THEN 1 ELSE 0 END) AS author_liked
            FROM comments WHERE video_id=?
            """,
            (video_id,),
        ).fetchone()
        latest_run = connection.execute(
            """
            SELECT * FROM comment_collection_runs
            WHERE video_id=? ORDER BY captured_at DESC, id DESC LIMIT 1
            """,
            (video_id,),
        ).fetchone()
        latest_metrics = connection.execute(
            """
            SELECT * FROM video_metric_snapshots
            WHERE video_id=? ORDER BY captured_at DESC, id DESC LIMIT 1
            """,
            (video_id,),
        ).fetchone()
    page_count = max(1, (total + per_page - 1) // per_page)
    return render_page(
        "查看评论",
        """
        <div class="hero">
          <div>
            <h1>评论数据</h1>
            <div class="muted">{{ video.title }} · 作品 ID {{ video.video_id }} · 共 {{ total }} 条</div>
            <div class="muted">{{ tracking_label }}</div>
          </div>
          <div class="actions">
            <a class="button soft" href="{{ url_for('dashboard') }}">返回视频库</a>
            <form method="post" action="{{ url_for('request_comment_refresh', video_id=video.video_id) }}">
              <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
              <button class="gray" type="submit">立即补抓全部回复</button>
            </form>
            <a class="button orange" href="{{ url_for('export_comments_csv', video_id=video.video_id) }}">下载评论明细</a>
            <a class="button soft" href="{{ url_for('export_comment_history_csv', video_id=video.video_id) }}">下载每小时变化</a>
            <a class="button soft" href="{{ url_for('export_video_metrics_csv', video_id=video.video_id) }}">下载作品数据</a>
          </div>
        </div>
        <section class="card">
          <h2>本次采集说明</h2>
          {% if latest_run %}
            <div class="actions">
              {% if latest_run.complete %}
                <span class="pill">评论和已发现回复已完整翻页</span>
              {% else %}
                <span class="pill orange">仍有评论或回复未完全展开，下一小时继续补采</span>
              {% endif %}
              <span class="muted">采集 {{ latest_run.rows_seen }} 条；主评论 {{ latest_run.top_level_seen }} 条；主列表 {{ latest_run.main_pages }} 页；回复 {{ latest_run.reply_pages }} 页；主动展开 {{ latest_run.expand_clicks }} 次</span>
            </div>
            <p class="muted">
              最近采集：{{ latest_run.captured_at[:16].replace('T',' ') }}。
              {% if latest_run.expected_total %}页面报告评论约 {{ latest_run.expected_total }} 条。{% endif %}
              {% if latest_run.incomplete_replies %}还有 {{ latest_run.incomplete_replies }} 组回复未完全展开。{% endif %}
            </p>
            {% if latest_run.rows_seen == 0 %}
              <p><span class="pill orange">本轮没有取得评论数据，无法判断博主点赞</span> 作品已经删除或评论接口本轮未返回内容时，无法补抓点赞标记；这不代表历史评论没有被博主点赞。</p>
            {% elif latest_run.author_like_supported %}
              <p><span class="pill blue">已取得博主点赞标记</span> 当前识别 {{ interaction_counts.author_liked or 0 }} 条博主点赞评论。</p>
            {% else %}
              <p><span class="pill orange">抖音本次接口未提供“博主点赞”标记</span> 这不等于博主没有点赞；系统会在后续每小时采集时继续尝试。</p>
            {% endif %}
          {% else %}
            <p class="muted">暂时没有采集报告，下一次评论任务完成后会显示。</p>
          {% endif %}
          <div class="actions">
            <span>模型先生本人评论/回复：<strong>{{ interaction_counts.creator or 0 }}</strong> 条（本轮二级回复 {{ latest_run.creator_reply_rows if latest_run else 0 }} 条）</span>
            {% if latest_metrics %}
              <span>作品数据：播放 {{ latest_metrics.play_count if latest_metrics.play_count is not none else '未提供' }} · 点赞 {{ latest_metrics.digg_count if latest_metrics.digg_count is not none else '未提供' }} · 评论 {{ latest_metrics.comment_count if latest_metrics.comment_count is not none else '未提供' }} · 收藏 {{ latest_metrics.collect_count if latest_metrics.collect_count is not none else '未提供' }} · 分享 {{ latest_metrics.share_count if latest_metrics.share_count is not none else '未提供' }}</span>
            {% endif %}
          </div>
        </section>
        <section class="card">
          <div class="actions" style="margin-bottom:12px">
            <a class="button {{ 'gray' if view != 'all' else '' }}" href="{{ url_for('comments_page', video_id=video.video_id, view='all') }}">全部评论</a>
            <a class="button {{ 'gray' if view != 'creator' else '' }}" href="{{ url_for('comments_page', video_id=video.video_id, view='creator') }}">只看模型先生</a>
            <a class="button {{ 'gray' if view != 'author_liked' else '' }}" href="{{ url_for('comments_page', video_id=video.video_id, view='author_liked') }}">只看博主点赞</a>
          </div>
          <div class="table-scroll">
            <table>
              <thead><tr><th>用户与身份</th><th>评论内容/回复关系</th><th>时间</th><th>点赞</th><th>回复</th><th>IP 属地</th></tr></thead>
              <tbody>
              {% for row in rows %}
                <tr>
                  <td class="nowrap">
                    {{ row.author_name or '未知用户' }}<br>
                    {% if row.is_creator %}<span class="pill">模型先生本人</span>{% endif %}
                    {% if row.is_author_digged == 1 %}<span class="pill blue">博主点赞</span>{% endif %}
                    {% if row.label_text %}<span class="pill orange">{{ row.label_text }}</span>{% endif %}
                  </td>
                  <td class="title-cell">
                    {{ row.text }}
                    {% if row.replied_comment_text or row.replied_author_name %}
                      <div class="reply-context">
                        <strong>回复 {{ row.replied_author_name or '该用户' }}：</strong>
                        <div>{{ row.replied_comment_text or '原评论正文暂未采集到，下一小时继续补采。' }}</div>
                      </div>
                    {% elif row.parent_comment_id %}
                      <div class="reply-context">原评论暂未采集到，下一小时继续补采。</div>
                    {% endif %}
                  </td>
                  <td class="nowrap">{{ (row.created_at or '')[:16].replace('T',' ') }}</td>
                  <td>{{ row.digg_count }}</td>
                  <td>{{ row.reply_count }}</td>
                  <td class="nowrap">{{ row.ip_label }}</td>
                </tr>
              {% else %}
                <tr><td colspan="6" class="empty">这个作品暂时没有采集到公开评论。</td></tr>
              {% endfor %}
              </tbody>
            </table>
          </div>
          {% if page_count > 1 %}
          <div class="pagination">
            {% if page > 1 %}<a class="button soft" href="{{ url_for('comments_page', video_id=video.video_id, page=page-1, view=view) }}">上一页</a>{% endif %}
            <span class="muted">第 {{ page }} / {{ page_count }} 页</span>
            {% if page < page_count %}<a class="button soft" href="{{ url_for('comments_page', video_id=video.video_id, page=page+1, view=view) }}">下一页</a>{% endif %}
          </div>
          {% endif %}
        </section>
        """,
        video=video,
        total=total,
        rows=rows,
        page=page,
        page_count=page_count,
        view=view,
        interaction_counts=interaction_counts,
        latest_run=latest_run,
        latest_metrics=latest_metrics,
        tracking_label=comment_tracking_label(
            str(video["published_at"]),
            str(video["comments_collected_at"] or "") or None,
        ),
    )


@app.route("/videos/<video_id>/comments.csv")
@login_required
def export_comments_csv(video_id: str):
    video = find_video(video_id)
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT current.comment_id, current.parent_comment_id,
                   current.reply_to_comment_id,
                   current.reply_to_user_name, current.author_name,
                   current.author_uid, current.is_creator,
                   current.is_author_digged, current.label_text,
                   current.text, current.created_at, current.digg_count,
                   current.reply_count, current.ip_label,
                   current.first_seen_at, current.last_seen_at,
                   current.collected_at,
                   COALESCE(
                       NULLIF(target.author_name, ''),
                       NULLIF(current.reply_to_user_name, ''),
                       NULLIF(parent.author_name, ''), ''
                   ) AS replied_author_name,
                   COALESCE(
                       NULLIF(target.text, ''),
                       NULLIF(parent.text, ''), ''
                   ) AS replied_comment_text
            FROM comments AS current
            LEFT JOIN comments AS parent
              ON parent.video_id=current.video_id
             AND parent.comment_id=current.parent_comment_id
            LEFT JOIN comments AS target
              ON target.video_id=current.video_id
             AND target.comment_id=current.reply_to_comment_id
            WHERE current.video_id=?
            ORDER BY current.created_at, current.comment_id
            """,
            (video_id,),
        ).fetchall()
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow(
        [
            "评论ID",
            "父评论ID",
            "回复目标评论ID",
            "接口返回的回复对象",
            "实际被回复用户",
            "被回复的原评论",
            "用户昵称",
            "用户标识",
            "是否模型先生本人",
            "是否博主点赞",
            "公开标签",
            "评论内容",
            "发布时间",
            "点赞数",
            "回复数",
            "IP属地",
            "首次发现时间",
            "最后发现时间",
            "采集时间",
        ]
    )
    for row in rows:
        writer.writerow([
            row["comment_id"], row["parent_comment_id"],
            row["reply_to_comment_id"], row["reply_to_user_name"],
            row["replied_author_name"], row["replied_comment_text"],
            row["author_name"], row["author_uid"],
            "是" if row["is_creator"] else "否",
            (
                "平台未提供"
                if row["is_author_digged"] is None
                else ("是" if row["is_author_digged"] else "否")
            ),
            row["label_text"], row["text"], row["created_at"],
            row["digg_count"], row["reply_count"], row["ip_label"],
            row["first_seen_at"], row["last_seen_at"],
            row["collected_at"],
        ])
    payload = io.BytesIO(("\ufeff" + text.getvalue()).encode("utf-8"))
    filename = f"{video_id}-评论.csv"
    return send_file(
        payload,
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/videos/<video_id>/comment-history.csv")
@login_required
def export_comment_history_csv(video_id: str):
    find_video(video_id)
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT snapshots.captured_at, comments.comment_id,
                   comments.author_name, comments.text,
                   comments.is_creator, snapshots.digg_count,
                   snapshots.reply_count, snapshots.is_author_digged,
                   COALESCE(
                       NULLIF(target.author_name, ''),
                       NULLIF(comments.reply_to_user_name, ''),
                       NULLIF(parent.author_name, ''), ''
                   ) AS replied_author_name,
                   COALESCE(
                       NULLIF(target.text, ''),
                       NULLIF(parent.text, ''), ''
                   ) AS replied_comment_text
            FROM comment_snapshots AS snapshots
            JOIN comments ON comments.comment_id=snapshots.comment_id
            LEFT JOIN comments AS parent
              ON parent.video_id=comments.video_id
             AND parent.comment_id=comments.parent_comment_id
            LEFT JOIN comments AS target
              ON target.video_id=comments.video_id
             AND target.comment_id=comments.reply_to_comment_id
            WHERE snapshots.video_id=?
            ORDER BY snapshots.captured_at, comments.created_at,
                     comments.comment_id
            """,
            (video_id,),
        ).fetchall()
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow([
        "采集时间", "评论ID", "用户昵称", "评论内容",
        "实际被回复用户", "被回复的原评论",
        "是否模型先生本人", "当时点赞数", "当时回复数",
        "当时是否博主点赞",
    ])
    for row in rows:
        writer.writerow([
            row["captured_at"], row["comment_id"], row["author_name"],
            row["text"], row["replied_author_name"],
            row["replied_comment_text"],
            "是" if row["is_creator"] else "否",
            row["digg_count"], row["reply_count"],
            (
                "平台未提供"
                if row["is_author_digged"] is None
                else ("是" if row["is_author_digged"] else "否")
            ),
        ])
    payload = io.BytesIO(("\ufeff" + text.getvalue()).encode("utf-8"))
    return send_file(
        payload,
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=f"{video_id}-评论每小时变化.csv",
    )


@app.route("/videos/<video_id>/metrics.csv")
@login_required
def export_video_metrics_csv(video_id: str):
    find_video(video_id)
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT captured_at, play_count, digg_count, comment_count,
                   collect_count, share_count
            FROM video_metric_snapshots
            WHERE video_id=? ORDER BY captured_at
            """,
            (video_id,),
        ).fetchall()
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow(["采集时间", "播放数", "点赞数", "评论数", "收藏数", "分享数"])
    writer.writerows([list(row) for row in rows])
    payload = io.BytesIO(("\ufeff" + text.getvalue()).encode("utf-8"))
    return send_file(
        payload,
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=f"{video_id}-作品数据趋势.csv",
    )


@app.route("/backup")
@login_required
def backup_page():
    EXPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    backups = sorted(
        (
            path
            for path in EXPORTS_ROOT.glob("模型下载器数据备份-*.tar.gz")
            if path.is_file()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:10]
    return render_page(
        "数据备份",
        """
        <div class="hero">
          <div>
            <h1>数据备份</h1>
            <div class="muted">把评论、作品索引和历史导出文件打成一个压缩包。</div>
          </div>
        </div>
        <section class="card">
          <h2>一键生成备份</h2>
          <p>备份包含评论数据和数据库，不重复打包体积很大的 MP4。视频可在“视频库”里逐个下载。</p>
          <form method="post" action="{{ url_for('create_backup') }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <button type="submit">生成新的数据备份</button>
          </form>
        </section>
        <section class="card">
          <h2>已有备份</h2>
          <div class="table-scroll">
            <table>
              <thead><tr><th>文件</th><th>大小</th><th>操作</th></tr></thead>
              <tbody>
              {% for path in backups %}
                <tr>
                  <td>{{ path.name }}</td>
                  <td>{{ file_size_label(path.stat().st_size) }}</td>
                  <td><a class="button" href="{{ url_for('download_backup', filename=path.name) }}">下载到电脑</a></td>
                </tr>
              {% else %}
                <tr><td colspan="3" class="empty">还没有生成过备份。</td></tr>
              {% endfor %}
              </tbody>
            </table>
          </div>
        </section>
        """,
        backups=backups,
    )


@app.route("/backup/create", methods=["POST"])
@login_required
def create_backup():
    EXPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    final_path = EXPORTS_ROOT / f"模型下载器数据备份-{stamp}.tar.gz"
    temporary = final_path.with_suffix(final_path.suffix + ".part")
    database_copy = EXPORTS_ROOT / f".database-{stamp}.sqlite3"
    try:
        if DATABASE.exists():
            source = sqlite3.connect(DATABASE)
            destination = sqlite3.connect(database_copy)
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
        with tarfile.open(temporary, "w:gz") as archive:
            if database_copy.exists():
                archive.add(database_copy, arcname="library.sqlite3")
            if COMMENTS_ROOT.exists():
                archive.add(COMMENTS_ROOT, arcname="comments")
            readme = (
                "本备份由模型下载器管理中心生成。\n"
                "library.sqlite3 是视频与评论索引；comments 目录是评论 JSON 快照。\n"
                "MP4 视频没有重复打包，请在管理中心视频库中下载。\n"
            ).encode("utf-8")
            info = tarfile.TarInfo("使用说明.txt")
            info.size = len(readme)
            info.mtime = int(time.time())
            archive.addfile(info, io.BytesIO(readme))
        temporary.replace(final_path)
    finally:
        database_copy.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
    flash("数据备份已生成，现在可以下载到电脑。")
    return redirect(url_for("backup_page"))


@app.route("/backup/download/<path:filename>")
@login_required
def download_backup(filename: str):
    if Path(filename).name != filename:
        abort(404)
    path = (EXPORTS_ROOT / filename).resolve()
    try:
        path.relative_to(EXPORTS_ROOT.resolve())
    except ValueError:
        abort(404)
    if (
        not path.is_file()
        or not filename.startswith("模型下载器数据备份-")
        or not filename.endswith(".tar.gz")
    ):
        abort(404)
    return send_file(path, as_attachment=True, download_name=filename)


@app.route("/update")
@login_required
def update_page():
    try:
        log_tail = UPDATE_LOG.read_text(
            encoding="utf-8",
            errors="replace",
        )[-5000:]
    except OSError:
        log_tail = "暂无更新记录。"
    pending = UPDATE_REQUEST.exists()
    return render_page(
        "系统更新",
        """
        <div class="hero">
          <div>
            <h1>系统更新</h1>
            <div class="muted">当前版本：{{ version }}</div>
          </div>
        </div>
        <section class="card">
          <h2>选择新安装包</h2>
          <p>选择我以后交给您的 <strong>.tar.gz</strong> 文件，再点一次按钮即可。视频、评论、数据库和抖音登录状态都会保留。</p>
          {% if pending %}
            <div class="flash">服务器正在准备安装，请约 20 秒后刷新本页面。</div>
          {% endif %}
          <form method="post" action="{{ url_for('upload_update') }}" enctype="multipart/form-data">
            <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
            <div class="form-row">
              <label for="package">新版本安装包（最大 200 MB）</label>
              <input id="package" type="file" name="package" accept=".gz,.tgz,application/gzip" required>
            </div>
            <button type="submit">上传并开始更新</button>
          </form>
          <div class="note" style="margin-top:14px">安装期间页面可能短暂断开，通常 20～60 秒后刷新即可恢复。</div>
        </section>
        <section class="card">
          <h2>最近更新记录</h2>
          <pre>{{ log_tail }}</pre>
        </section>
        """,
        version=current_version(),
        pending=pending,
        log_tail=log_tail,
    )


@app.route("/update/upload", methods=["POST"])
@login_required
def upload_update():
    uploaded = request.files.get("package")
    if uploaded is None or not uploaded.filename:
        flash("请选择安装包。", "error")
        return redirect(url_for("update_page"))
    original_name = Path(uploaded.filename).name
    if not (
        original_name.lower().endswith(".tar.gz")
        or original_name.lower().endswith(".tgz")
    ):
        flash("只接受模型下载器的 .tar.gz 安装包。", "error")
        return redirect(url_for("update_page"))
    UPDATES_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    random_part = secrets.token_hex(4)
    filename = f"web-upload-{stamp}-{random_part}.tar.gz"
    temporary = UPDATES_ROOT / f".{filename}.part"
    target = UPDATES_ROOT / filename
    try:
        uploaded.save(temporary)
        if temporary.stat().st_size <= 0:
            raise ValueError("安装包为空。")
        temporary.replace(target)
        request_temp = UPDATE_REQUEST.with_suffix(".request.tmp")
        request_temp.write_text(filename + "\n", encoding="ascii")
        request_temp.replace(UPDATE_REQUEST)
    except (OSError, ValueError) as exc:
        temporary.unlink(missing_ok=True)
        flash(f"安装包保存失败：{exc}", "error")
        return redirect(url_for("update_page"))
    flash("安装包已上传，服务器正在后台更新。约 20～60 秒后刷新本页面查看新版本。")
    return redirect(url_for("update_page"))


@app.route("/health")
def health():
    return Response("ok\n", mimetype="text/plain")


if __name__ == "__main__":
    from waitress import serve

    serve(
        app,
        host=os.environ.get("MODEL_DOWNLOADER_WEB_HOST", "0.0.0.0"),
        port=int(os.environ.get("MODEL_DOWNLOADER_WEB_PORT", "8787")),
        threads=6,
        channel_timeout=300,
    )
