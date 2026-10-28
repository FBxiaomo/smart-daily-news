"""Local-only setup studio. It never starts delivery without an explicit action."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import json
import os
import re
import secrets
import tempfile
import threading
import tomllib
import webbrowser

import tomlkit

from . import archive
from .ai import SynthesisError, synthesize
from .config import ConfigError, initialize, load_config, validate_credentials
from .feeds import CollectionError
from .models import Article, Digest, Story, utc_now
from .network import HttpClient, NetworkError
from .pipeline import preview, run
from .presentation import DENSITIES, LAYOUTS, THEMES, resolve_presentation
from .render import html_content
from .scheduler import serve as schedule
from .state import State, StateError

# Restrict what a browser session can change; keep advanced configuration and comments intact.
EDITABLE = {
    "presentation": ("layout", "theme", "density"),
    "topics": ("enabled", "selected", "balance"),
    "briefing": ("title", "timezone", "lookback_hours", "max_stories", "include_keywords"),
    "schedule": ("times", "weekdays"),
    "ai": ("enabled", "provider", "base_url", "model", "require_success", "translate_titles"),
    "email": ("enabled", "host", "port", "security"),
    "webhook": ("enabled",),
    "wecom": ("enabled",),
    "feishu": ("enabled", "keyword"),
    "serverchan": ("enabled",),
    "push": ("content",),
}
SECRET_FIELDS = {
    "ai_key": ("ai", "api_key_env"),
    "smtp_user": ("email", "username_env"),
    "smtp_password": ("email", "password_env"),
    "email_from": ("email", "from_env"),
    "email_to": ("email", "to_env"),
    "webhook_url": ("webhook", "url_env"),
    "webhook_token": ("webhook", "bearer_token_env"),
    "webhook_secret": ("webhook", "signing_secret_env"),
    "wecom_url": ("wecom", "url_env"),
    "feishu_url": ("feishu", "url_env"),
    "feishu_secret": ("feishu", "secret_env"),
    "serverchan_key": ("serverchan", "sendkey_env"),
}
MAX_REQUEST_BYTES = 100_000


class StudioError(ValueError):
    pass


class RevisionConflict(StudioError):
    pass


def _read_env(path):
    values = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] in ('"', "'") and value[-1:] == value[:1]:
            value = value[1:-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            values.setdefault(key, value)
    return values


def _update_env(old_text, updates):
    lines, handled = [], set()
    for line in old_text.splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        key = match.group(1) if match else None
        if key in updates:
            if key not in handled:
                lines.append(f'{key}="{updates[key]}"')
                handled.add(key)
        else:
            lines.append(line)
    for key, value in updates.items():
        if key not in handled:
            lines.append(f'{key}="{value}"')
    return "\n".join(lines).rstrip() + "\n"


def _atomic_write(path, content, *, private=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=".daily-settings-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if private and os.name != "nt":
            os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _revision(path):
    env = path.parent / ".env"
    stamp = env.stat().st_mtime_ns if env.exists() else 0
    return sha256(path.read_bytes() + str(stamp).encode()).hexdigest()


def _options():
    from .topics import TOPICS
    return {
        "layouts": [{"id": k, "name": v} for k, v in LAYOUTS.items()],
        "themes": [{"id": k, "name": v} for k, v in THEMES.items()],
        "densities": [{"id": k, "name": v} for k, v in DENSITIES.items()],
        "topics": [{key: topic[key] for key in ("id", "name", "group", "description")} for topic in TOPICS],
    }


def sample_digest(settings):
    options = resolve_presentation(settings.get("presentation"))
    title = settings.get("title") or "我的每日简报"
    if not isinstance(title, str) or len(title) > 150 or any(c in title for c in "\r\n"):
        raise StudioError("标题应为不超过 150 个字符的单行文字")
    now = datetime.now(timezone.utc)
    stories = []
    samples = [
        ("科技", "示例新闻：人工智能研究迎来新进展", "科技观察"),
        ("汽车", "示例新闻：新能源汽车产业发布新动态", "汽车观察"),
        ("生活", "示例新闻：城市公共服务推出新举措", "生活观察"),
    ]
    for i, (category, headline, name) in enumerate(samples, 1):
        texts = [
            "这是一段用于展示的新闻摘要。真实日报将根据你选择的主题和来源采集新闻，并保留原文地址。",
            "不同来源可能提供不同角度的补充信息。标准和深度阅读会显示更多要点，帮助你了解背景。",
            "这段文字用于演示更完整的阅读效果。你可以先比较版式，再用真实新闻生成预览，确认是否适合日常阅读。",
        ]
        articles = [
            Article(f"{i}-{j}", headline, f"https://example.com/demo-{i}-{j}", f"sample-{j}",
                    f"{name} · 示例来源 {j}", category, now.isoformat(), text)
            for j, text in enumerate(texts, 1)
        ]
        bullets = [{"text": text, "sources": [article.id]} for text, article in zip(texts, articles)]
        stories.append(Story(f"demo-{i}", headline, category, articles, bullets))
    return Digest("preview-sample", title, now.isoformat(), "Asia/Shanghai", stories,
                  [{"id": "sample", "name": "演示数据", "status": "ok", "accepted": 3}],
                  warnings=["这是版式示例，内容不是真实新闻。"], presentation=options)


class Studio:
    def __init__(self, path):
        self.path = Path(path).resolve()
        if not self.path.exists():
            initialize(self.path)
        self.token = secrets.token_urlsafe(32)
        self.initial_env = dict(os.environ)
        self.lock = threading.RLock()
        self.thread = None
        self.stop_event = None
        self.last_result = {}
        self.error = ""

    def _config(self):
        # Refresh .env values written in the studio while honoring process-level overrides.
        document = tomllib.loads(self.path.read_text(encoding="utf-8-sig"))
        env = _read_env(self.path.parent / ".env")
        default_path = Path(__file__).with_name("config.example.toml")
        defaults = tomllib.loads(default_path.read_text(encoding="utf-8"))
        for section, field in SECRET_FIELDS.values():
            key = document.get(section, {}).get(field, defaults[section][field])
            if key in self.initial_env:
                os.environ[key] = self.initial_env[key]
            elif key in env:
                os.environ[key] = env[key]
            else:
                os.environ.pop(key, None)
        return load_config(self.path)

    def scheduler_status(self):
        thread = self.thread
        active = thread is not None and thread.is_alive()
        state = "stopping" if active and self.stop_event and self.stop_event.is_set() else (
            "running" if active else "stopped")
        return {"status": state, "message": self.error, "last_result": self.last_result}

    def settings(self):
        cfg = self._config()
        result = {section: {key: cfg.data[section][key] for key in keys}
                  for section, keys in EDITABLE.items()}
        if not result["schedule"]["times"]:
            result["schedule"]["times"] = [cfg.data["briefing"]["send_at"]]
        result["sources"] = cfg.data["sources"]
        result["secrets_set"] = {
            name: bool(os.environ.get(cfg.data[section][field], "").strip())
            for name, (section, field) in SECRET_FIELDS.items()
        }
        result["secret_overrides"] = {
            name: cfg.data[section][field] in self.initial_env
            for name, (section, field) in SECRET_FIELDS.items()
        }
        result["revision"] = _revision(self.path)
        return result

    def meta(self):
        with self.lock:
            return {"options": _options(), "settings": self.settings(),
                    "scheduler": self.scheduler_status()}

    def save(self, payload):
        if not isinstance(payload, dict):
            raise StudioError("设置必须是 JSON 对象")
        allowed = set(EDITABLE) | {"sources", "secrets", "revision"}
        if set(payload) - allowed:
            raise StudioError("设置中存在未知字段")
        with self.lock:
            if self.scheduler_status()["status"] != "stopped":
                raise StudioError("请先暂停定时推送，等待当前任务结束，再保存设置")
            if not secrets.compare_digest(str(payload.get("revision", "")), _revision(self.path)):
                raise RevisionConflict("配置已被其他程序修改，请刷新页面后重新设置")
            cfg = self._config()
            document = tomlkit.parse(self.path.read_text(encoding="utf-8-sig"))
            for section, keys in EDITABLE.items():
                data = payload.get(section, {})
                if not isinstance(data, dict) or set(data) - set(keys):
                    raise StudioError(f"{section} 中包含无效设置")
                if section not in document:
                    document[section] = tomlkit.table()
                for key, value in data.items():
                    document[section][key] = value
            if "sources" in payload:
                sources = payload["sources"]
                if not isinstance(sources, list) or len(sources) > 100:
                    raise StudioError("新闻源必须为最多 100 项的数组")
                if sources != cfg.data["sources"]:
                    source_list = tomlkit.aot()
                    for item in sources:
                        allowed_source = {"id", "name", "url", "category", "weight", "enabled"}
                        if not isinstance(item, dict) or set(item) - allowed_source:
                            raise StudioError("新闻源字段无效")
                        table = tomlkit.table()
                        for key, value in item.items():
                            table.add(key, value)
                        source_list.append(table)
                    document["sources"] = source_list
            # Check the candidate with the same validator in an isolated directory.
            candidate = tomlkit.dumps(document)
            with tempfile.TemporaryDirectory(prefix="daily-studio-") as folder:
                temp_path = Path(folder) / "config.toml"
                temp_path.write_text(candidate, encoding="utf-8")
                validated = load_config(temp_path)
            values = payload.get("secrets", {})
            if not isinstance(values, dict) or set(values) - set(SECRET_FIELDS):
                raise StudioError("凭据字段无效")
            updates = {}
            for name, value in values.items():
                if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
                    raise StudioError("凭据不能包含控制字符或超过长度限制")
                if value:
                    section, field = SECRET_FIELDS[name]
                    updates[validated.data[section][field]] = value
            env_path = self.path.parent / ".env"
            prior_env = env_path.read_text(encoding="utf-8-sig") if env_path.exists() else None
            if updates:
                _atomic_write(env_path, _update_env(prior_env or "", updates), private=True)
            try:
                _atomic_write(self.path, candidate)
            except OSError:
                if updates:
                    if prior_env is None:
                        env_path.unlink(missing_ok=True)
                    else:
                        _atomic_write(env_path, prior_env, private=True)
                raise
            return self.settings()

    @contextmanager
    def state(self):
        cfg = self._config()
        state = State(cfg.state_dir)
        try:
            yield cfg, state
        finally:
            state.close()

    def live_preview(self, use_ai=False):
        cfg = self._config()
        if use_ai and not cfg.section("ai")["enabled"]:
            raise StudioError("请先在设置中启用 AI 并配置密钥")
        if not use_ai:
            cfg = replace(cfg, data={**cfg.data, "ai": {**cfg.section("ai"), "enabled": False}})
        digest, _ = preview(cfg)
        return {"html": html_content(digest), "history_id": digest.history_id,
                "warnings": digest.warnings, "stories": len(digest.stories),
                "ai_used": any(story.mode == "ai_synthesis" for story in digest.stories),
                "ai_usage": digest.ai_usage}

    def history(self, query=""):
        with self.state() as (_, state):
            return {"items": archive.list_history(state, query=query),
                    "favorites": archive.list_favorites(state, query=query)}

    def history_item(self, history_id):
        with self.state() as (_, state):
            digest, kind = archive.get_history(state, history_id)
            return {"digest": digest.to_dict(), "kind": kind, "html": html_content(digest)}

    def favorite(self, history_id, article_id):
        with self.state() as (_, state):
            return {"id": archive.favorite(state, history_id, article_id, utc_now())}

    def remove_favorite(self, favorite_id):
        with self.state() as (_, state):
            return {"removed": archive.remove_favorite(state, favorite_id)}

    def delete_history(self, history_id):
        with self.state() as (_, state):
            return {"deleted": archive.delete_history(state, history_id)}

    def weekly_preview(self, use_ai=False):
        if type(use_ai) is not bool:
            raise StudioError("AI 选项必须为布尔值")
        now = utc_now()
        edition = "weekly-" + now.date().isoformat() + "-" + secrets.token_hex(4)
        with self.state() as (cfg, state):
            digest, metrics = archive.weekly_digest(state, cfg, now, edition)
            if use_ai:
                if not cfg.section("ai")["enabled"]:
                    raise StudioError("请先在设置中启用 AI 并配置密钥")
                validate_credentials(cfg)
                client = HttpClient(cfg.section("network"))
                try:
                    digest.ai_usage = synthesize(digest.stories, cfg, client)
                except SynthesisError as exc:
                    if cfg.section("ai")["require_success"]:
                        raise
                    digest.warnings.append("AI 不可用：" + str(exc) + "；本期使用原文摘要")
                finally:
                    client.close()
            history_id = archive.save_preview(state, digest, kind="weekly")
        return {"html": html_content(digest), "history_id": history_id, "metrics": metrics,
                "ai_usage": digest.ai_usage, "warnings": digest.warnings,
                "ai_used": any(story.mode == "ai_synthesis" for story in digest.stories)}

    def weekly_send(self, history_id):
        cfg = self._config()
        with self.state() as (_, state):
            draft, kind = archive.get_history(state, history_id)
            if kind != "weekly" or not draft.edition.startswith("weekly-"):
                raise StudioError("请先生成周报预览")
        if resolve_presentation(draft.presentation) != resolve_presentation(cfg.section("presentation")):
            raise StudioError("日报样式已变化，请重新预览周报")
        def from_draft(_cfg, _client, _now, edition, state):
            try:
                digest, draft_kind = archive.get_history(state, history_id)
            except archive.ArchiveError as exc:
                raise ConfigError(str(exc)) from None
            if draft_kind != "weekly" or digest.edition != edition:
                raise ConfigError("找不到对应的周报预览")
            return digest
        return run(cfg, edition=draft.edition, generator=from_draft)

    def start(self):
        with self.lock:
            if self.scheduler_status()["status"] != "stopped":
                raise StudioError("定时任务已经启动或正在停止")
            cfg = self._config()
            validate_credentials(cfg, sending=True)
            self.stop_event = threading.Event()
            self.error = ""
            self.last_result = {}
            def tracked_runner(config, **kwargs):
                try:
                    result = run(config, **kwargs)
                    self.last_result = {key: result[key] for key in ("edition", "status", "channels", "warnings")
                                        if key in result}
                    return result
                except Exception as exc:
                    self.last_result = {"status": "error", "type": type(exc).__name__}
                    raise
            def worker():
                try:
                    schedule(cfg, stop_event=self.stop_event, runner=tracked_runner)
                except (ConfigError, StateError) as exc:
                    self.error = str(exc)
                except Exception:
                    self.error = "定时任务停止，请查看运行日志"
            self.thread = threading.Thread(target=worker, name="daily-news-scheduler", daemon=True)
            self.thread.start()
        return self.scheduler_status()

    def stop(self):
        with self.lock:
            if self.stop_event:
                self.stop_event.set()
        return self.scheduler_status()


class StudioServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, app):
        super().__init__(address, StudioHandler)
        self.app = app


class StudioHandler(BaseHTTPRequestHandler):
    server_version = "DailyNewsStudio"
    sys_version = ""

    def log_message(self, *args):
        # URLs may contain source or user data; do not log request targets.
        pass

    def _authorize(self, *, api=False):
        expected = f"127.0.0.1:{self.server.server_port}"
        origin = self.headers.get("Origin")
        if (self.client_address[0] != "127.0.0.1" or self.headers.get("Host") != expected
                or origin and origin != "http://" + expected):
            return False
        if api:
            return secrets.compare_digest(self.headers.get("X-DailyNews-Token", ""), self.server.app.token)
        return True

    def _send(self, status, data, content_type):
        body = data if isinstance(data, bytes) else data.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data:; connect-src 'self'; frame-src 'self'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, data):
        self._send(status, json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8")

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise StudioError("请求长度无效") from None
        if not 1 <= length <= MAX_REQUEST_BYTES:
            raise StudioError("请求为空或过大")
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            raise StudioError("请求必须使用 JSON")
        try:
            value = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            raise StudioError("请求 JSON 无效") from None
        if not isinstance(value, dict):
            raise StudioError("请求必须是 JSON 对象")
        return value

    def _handle_error(self, exc):
        if isinstance(exc, RevisionConflict):
            self._json(409, {"error": str(exc)})
        elif isinstance(exc, (StudioError, ConfigError, archive.ArchiveError, StateError,
                              SynthesisError, CollectionError)):
            self._json(400, {"error": str(exc)})
        elif isinstance(exc, NetworkError):
            self._json(502, {"error": "新闻源暂时不可用，请检查网络或来源设置"})
        else:
            self._json(500, {"error": "操作未完成，请检查设置或本地日志"})

    def do_GET(self):
        parsed = urlsplit(self.path)
        api = parsed.path.startswith("/api/")
        if not self._authorize(api=api):
            self._json(403, {"error": "访问未授权"})
            return
        app = self.server.app
        try:
            if parsed.path == "/":
                template = Path(__file__).with_name("studio.html").read_text(encoding="utf-8")
                self._send(200, template.replace("__STUDIO_TOKEN__", app.token), "text/html; charset=utf-8")
            elif parsed.path in ("/studio.css", "/studio.js"):
                path = Path(__file__).with_name(parsed.path[1:])
                mime = "text/css" if path.suffix == ".css" else "application/javascript"
                self._send(200, path.read_bytes(), mime + "; charset=utf-8")
            elif parsed.path == "/api/meta":
                self._json(200, app.meta())
            elif parsed.path == "/api/schedule/status":
                self._json(200, {"scheduler": app.scheduler_status()})
            elif parsed.path == "/api/history":
                query = parse_qs(parsed.query).get("q", [""])[0]
                self._json(200, app.history(query))
            elif parsed.path == "/api/history/item":
                history_id = parse_qs(parsed.query).get("id", [""])[0]
                self._json(200, app.history_item(history_id))
            else:
                self._json(404, {"error": "页面不存在"})
        except Exception as exc:
            self._handle_error(exc)

    def do_POST(self):
        if not self._authorize(api=True):
            self._json(403, {"error": "访问未授权"})
            return
        try:
            body = self._body()
            app = self.server.app
            path = urlsplit(self.path).path
            if path == "/api/settings":
                result = {"settings": app.save(body)}
            elif path == "/api/preview/sample":
                result = {"html": html_content(sample_digest(body))}
            elif path == "/api/preview/live":
                result = app.live_preview()
            elif path == "/api/preview/ai":
                result = app.live_preview(use_ai=True)
            elif path == "/api/schedule/start":
                result = {"scheduler": app.start()}
            elif path == "/api/schedule/stop":
                result = {"scheduler": app.stop()}
            elif path == "/api/history/favorite":
                result = app.favorite(body.get("history_id", ""), body.get("article_id", ""))
            elif path == "/api/history/delete":
                result = app.delete_history(body.get("history_id", ""))
            elif path == "/api/favorites/remove":
                result = app.remove_favorite(body.get("favorite_id", ""))
            elif path == "/api/weekly/preview":
                result = app.weekly_preview(body.get("ai", False))
            elif path == "/api/weekly/send":
                result = app.weekly_send(body.get("history_id", ""))
            else:
                self._json(404, {"error": "接口不存在"})
                return
            self._json(200, result)
        except Exception as exc:
            self._handle_error(exc)


def serve_studio(config_path, *, port=0, open_browser=True):
    app = Studio(config_path)
    server = StudioServer(("127.0.0.1", port), app)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"日报设置页面：{url}", flush=True)
    print("设置服务运行期间才会执行页面启动的定时任务；按 Ctrl+C 停止。", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
        server.server_close()
    return 0
