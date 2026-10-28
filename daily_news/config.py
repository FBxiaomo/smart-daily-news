"""Validated, portable configuration and non-destructive initialization."""
from dataclasses import dataclass
from pathlib import Path
import os
import re
import tomllib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .models import fingerprint


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    path: Path
    data: dict

    def section(self, name: str) -> dict:
        return self.data[name]

    @property
    def sources(self) -> list[dict]:
        custom = [s for s in self.data["sources"] if s.get("enabled", True)]
        options = self.data["topics"]
        if not options["enabled"]:
            return custom
        from .topics import build_sources
        combined = build_sources(options["selected"]) + custom
        by_id = {}
        for source in combined:
            by_id[source["id"]] = source
        return list(by_id.values())

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.data["briefing"]["timezone"])

    @property
    def state_dir(self) -> Path:
        return self.path.parent / self.data["briefing"]["state_dir"]

    @property
    def output_dir(self) -> Path:
        return self.path.parent / self.data["briefing"]["output_dir"]

    @property
    def content_fingerprint(self) -> str:
        value = {k: self.data[k] for k in ("briefing", "sources", "ai")}
        # Default translation preserves the fingerprint of older saved snapshots.
        value["ai"] = dict(value["ai"])
        if value["ai"].get("translate_titles") is True:
            value["ai"].pop("translate_titles")
        # Preserve previously persisted editions when the new options remain at their defaults.
        defaults = {"layout": "editorial", "theme": "light", "density": "standard"}
        if (self.data["topics"]["enabled"] or self.data["presentation"] != defaults
                or self.data["push"]["content"] != "full"):
            value["topics"] = self.data["topics"]
            value["presentation"] = self.data["presentation"]
            value["push"] = self.data["push"]
        return fingerprint(value)

    @property
    def channels(self) -> list[str]:
        return [name for name in ("email", "webhook", "wecom", "feishu", "serverchan")
                if self.data[name]["enabled"]]


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f".env 第 {number} 行必须为 KEY=VALUE")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ConfigError(f".env 第 {number} 行变量名无效")
        if value[:1] in ('"', "'") and value[-1:] == value[:1]:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _merge(default: dict, supplied: dict, context: str = "") -> dict:
    result = dict(default)
    for key, value in supplied.items():
        if key not in default:
            raise ConfigError(f"未知配置项: {context}{key}")
        if isinstance(default[key], dict):
            if not isinstance(value, dict):
                raise ConfigError(f"{context}{key} 必须为配置表")
            result[key] = _merge(default[key], value, f"{context}{key}.")
        else:
            if type(value) is not type(default[key]):
                raise ConfigError(f"{context}{key} 的类型应为 {type(default[key]).__name__}")
            result[key] = value
    return result


def secret(section: dict, field: str, required: bool = True) -> str:
    key = section[field]
    value = os.environ.get(key, "").strip()
    if required and not value:
        raise ConfigError(f"缺少环境变量: {key}")
    return value


def recipients(value: str) -> list[str]:
    items = [s.strip() for s in value.split(",") if s.strip()]
    if not items or any(not re.fullmatch(r"[^\s<>@,]+@[^\s<>@,]+\.[^\s<>@,]+", s) for s in items):
        raise ConfigError("邮箱地址无效；请填写邮箱，多个收件人用逗号分隔")
    return list(dict.fromkeys(items))


def channel_identity(cfg: Config, name: str) -> str:
    section = cfg.section(name)
    if name == "email":
        return fingerprint({
            "host": section["host"], "port": section["port"],
            "from": secret(section, "from_env"),
            "to": sorted(recipients(secret(section, "to_env"))),
        })
    if name == "serverchan":
        return fingerprint({"sendkey": secret(section, "sendkey_env")})
    return fingerprint({"url": secret(section, "url_env")})


def validate_credentials(cfg: Config, *, sending: bool = False) -> None:
    from .network import validate_url
    if cfg.section("ai")["enabled"] and cfg.section("ai")["require_success"]:
        secret(cfg.section("ai"), "api_key_env")
    if not sending:
        return
    if not cfg.channels:
        raise ConfigError("尚未启用投递渠道；可先使用 preview，或配置 email/webhook")
    if "email" in cfg.channels:
        section = cfg.section("email")
        secret(section, "username_env")
        secret(section, "password_env")
        recipients(secret(section, "from_env"))
        if len(recipients(secret(section, "from_env"))) != 1:
            raise ConfigError("发件地址必须只有一个")
        recipients(secret(section, "to_env"))
    for name in ("webhook", "wecom", "feishu"):
        if name in cfg.channels:
            validate_url(secret(cfg.section(name), "url_env"), cfg.section("network"), resolve=False)
    if "wecom" in cfg.channels or "feishu" in cfg.channels:
        from .delivery import DeliveryError
        from .notifications import _robot_url
        for name in ("wecom", "feishu"):
            if name in cfg.channels:
                try:
                    _robot_url(secret(cfg.section(name), "url_env"), name)
                except DeliveryError as exc:
                    raise ConfigError(str(exc)) from None
    if "serverchan" in cfg.channels:
        key = secret(cfg.section("serverchan"), "sendkey_env")
        if not re.fullmatch(r"SCT[A-Za-z0-9_-]{8,200}", key):
            raise ConfigError("Server酱需要 Turbo 版 SCT 开头的 SendKey")
    if "feishu" in cfg.channels and any(c in cfg.section("feishu")["keyword"] for c in "<>\r\n"):
        raise ConfigError("飞书机器人关键词不能包含换行或尖括号")


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    if not path.is_file():
        raise ConfigError(f"找不到配置文件 {path}；请运行 python main.py init")
    defaults = tomllib.loads(Path(__file__).with_name("config.example.toml").read_text(encoding="utf-8"))
    try:
        supplied = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"TOML 配置语法错误: {exc}") from None
    data = _merge(defaults, supplied)
    b, schedule, network, ai = [data[k] for k in ("briefing", "schedule", "network", "ai")]
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", b["id"]):
        raise ConfigError("briefing.id 只能包含字母、数字、下划线或连字符")
    if not b["title"].strip() or "\n" in b["title"] or "\r" in b["title"]:
        raise ConfigError("briefing.title 必须为非空单行文本")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", b["send_at"]):
        raise ConfigError("briefing.send_at 必须为 HH:MM（24 小时制）")
    times = schedule["times"]
    if (len(times) > 12 or any(not isinstance(t, str) or not re.fullmatch(
            r"(?:[01]\d|2[0-3]):[0-5]\d", t) for t in times) or len(set(times)) != len(times)):
        raise ConfigError("schedule.times 应为最多 12 个不重复的 HH:MM 时间")
    days = schedule["weekdays"]
    if (not days or any(type(day) is not int or not 1 <= day <= 7 for day in days)
            or len(set(days)) != len(days)):
        raise ConfigError("schedule.weekdays 应为 1 到 7 的不重复整数，1 代表周一")
    try:
        ZoneInfo(b["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError("未知时区；Windows 用户请确认已安装 tzdata") from None
    bounds = [
        (b, "lookback_hours", 1, 168), (b, "max_stories", 1, 30),
        (b, "max_per_source", 1, 30), (b, "min_sources_ok", 1, 100),
        (b, "dedupe_days", 0, 30), (schedule, "catch_up_hours", 1, 23),
        (schedule, "retry_minutes", 1, 1440), (schedule, "max_attempts", 1, 10),
        (network, "timeout_seconds", 1, 120), (network, "get_retries", 0, 4),
        (network, "fetch_parallelism", 1, 8),
        (network, "max_feed_bytes", 1024, 10000000),
        (ai, "timeout_seconds", 1, 300), (ai, "max_output_tokens", 256, 16000),
        (ai, "max_input_chars", 2000, 100000),
    ]
    for section, key, low, high in bounds:
        if not low <= section[key] <= high:
            raise ConfigError(f"{key} 应位于 {low} 到 {high}")
    for key in ("include_keywords", "exclude_keywords"):
        if any(not isinstance(s, str) or not s.strip() for s in b[key]):
            raise ConfigError(f"{key} 必须是非空字符串数组")
    from .presentation import resolve_presentation
    try:
        resolve_presentation(data["presentation"])
    except ValueError as exc:
        raise ConfigError(str(exc)) from None
    topic_settings = data["topics"]
    if topic_settings["enabled"]:
        from .topics import build_sources
        if not topic_settings["selected"]:
            raise ConfigError("请选择至少一个新闻类型")
        try:
            build_sources(topic_settings["selected"])
        except (TypeError, ValueError) as exc:
            raise ConfigError(str(exc)) from None
    ids = set()
    from .network import validate_url
    for source in data["sources"]:
        if not isinstance(source, dict):
            raise ConfigError("sources 必须为配置表数组")
        allowed = {"id", "name", "url", "category", "weight", "enabled"}
        if set(source) - allowed:
            raise ConfigError(f"新闻源含未知字段: {sorted(set(source) - allowed)}")
        for key in ("id", "name", "url"):
            if not isinstance(source.get(key), str) or not source[key].strip():
                raise ConfigError(f"每个新闻源都需要非空的 {key}")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", source["id"]) or source["id"] in ids:
            raise ConfigError("新闻源 id 必须唯一，且只包含字母、数字、下划线或连字符")
        ids.add(source["id"])
        source.setdefault("category", "综合")
        source.setdefault("weight", 1.0)
        source.setdefault("enabled", True)
        if not isinstance(source["category"], str) or type(source["enabled"]) is not bool:
            raise ConfigError("新闻源 category/enabled 类型无效")
        if type(source["weight"]) not in (int, float) or not 0.1 <= source["weight"] <= 5:
            raise ConfigError("新闻源 weight 必须位于 0.1 到 5")
        validate_url(source["url"], network, resolve=False)
    if not 1 <= len(data["sources"]) <= 100:
        raise ConfigError("请配置 1 到 100 个新闻源")
    enabled = Config(path, data).sources
    if len(enabled) > 100:
        raise ConfigError("启用的新闻源不能超过 100 个")
    if b["min_sources_ok"] > len(enabled):
        raise ConfigError("min_sources_ok 不能超过启用的新闻源数")
    if data["email"]["security"] not in ("ssl", "starttls"):
        raise ConfigError("email.security 只能为 ssl 或 starttls")
    if not 1 <= data["email"]["port"] <= 65535:
        raise ConfigError("email.port 无效")
    if ai["provider"] not in ("deepseek", "qwen", "compatible"):
        raise ConfigError("ai.provider 必须为 deepseek、qwen 或 compatible")
    if data["push"]["content"] not in ("full", "summary", "notice"):
        raise ConfigError("push.content 必须为 full、summary 或 notice")
    if ai["enabled"]:
        if not ai["model"].strip():
            raise ConfigError("启用 AI 时需要填写 ai.model")
        validate_url(ai["base_url"], network, resolve=False)
    for name in ("wecom", "feishu", "serverchan"):
        if not 1 <= data[name]["timeout_seconds"] <= 120:
            raise ConfigError(f"{name}.timeout_seconds 必须为 1 至 120 秒")
    for name in ("ai", "email", "webhook", "wecom", "feishu", "serverchan"):
        for key, value in data[name].items():
            if key.endswith("_env") and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
                raise ConfigError(f"{name}.{key} 必须为环境变量名")
    load_env(path.parent / ".env")
    return Config(path, data)


def initialize(path: Path) -> list[Path]:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    written = []
    for target, resource in [(path, "config.example.toml"), (path.parent / ".env", "env.example")]:
        if target.exists():
            continue
        content = Path(__file__).with_name(resource).read_text(encoding="utf-8")
        if resource == "config.example.toml":
            # New installations begin with all available subjects; old configurations
            # continue to inherit the disabled default and keep their existing sources.
            import tomlkit
            from .topics import TOPICS
            document = tomlkit.parse(content)
            document["topics"]["enabled"] = True
            document["topics"]["selected"] = [topic["id"] for topic in TOPICS]
            content = tomlkit.dumps(document)
        with target.open("x", encoding="utf-8", newline="\n") as f:
            f.write(content)
        if target.name == ".env":
            target.chmod(0o600)
        written.append(target)
    return written
