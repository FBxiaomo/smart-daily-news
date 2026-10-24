"""Operator CLI. Preview is the default; all external delivery requires --send."""
import argparse
from dataclasses import replace
from importlib import metadata
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
from .config import ConfigError, initialize, load_config, validate_credentials
from .feeds import CollectionError, collect
from .models import utc_now
from .network import HttpClient, NetworkError
from .pipeline import preview, run
from .scheduler import serve
from .state import State, StateError, run_lock


def configure_logging(directory):
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("daily_news")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s")
    import time
    formatter.converter = time.gmtime
    for handler in (logging.StreamHandler(sys.stderr),
                    RotatingFileHandler(directory / "run.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")):
        handler.setFormatter(formatter)
        logger.addHandler(handler)


def parser():
    root = argparse.ArgumentParser(description="Smart Daily News — 生成、预览和可靠投递来源可追溯的日报")
    root.add_argument("--config", type=Path, default=Path("config.toml"), help="配置路径（默认 config.toml）")
    commands = root.add_subparsers(dest="command")
    commands.add_parser("init", help="生成配置和 .env；保留已有文件")
    doctor = commands.add_parser("doctor", help="检查配置和依赖；不发送消息，不调用付费 AI")
    doctor.add_argument("--online", action="store_true", help="实际检查新闻源连通性")
    show = commands.add_parser("preview", help="生成 HTML/Markdown/文本/JSON 预览，不发送")
    show.add_argument("--no-ai", action="store_true", help="本次仅使用来源摘要")
    for name, description in (("run", "生成或恢复本期并投递"), ("retry", "修复后手动重试已知失败")):
        sub = commands.add_parser(name, help=description)
        sub.add_argument("--send", action="store_true", help="允许向配置的目标发送")
        sub.add_argument("--edition", help="指定期号；默认使用本地时区当天")
    daemon = commands.add_parser("schedule", help="持续运行每日调度器")
    daemon.add_argument("--send", action="store_true", help="允许定时向配置目标发送")
    daemon.add_argument("--once", action="store_true", help="仅执行一次到期检查")
    commands.add_parser("status", help="查看生成和投递状态（不输出凭据）")
    resolve = commands.add_parser("resolve", help="人工核对不确定投递的结果")
    resolve.add_argument("--edition", required=True)
    resolve.add_argument("--channel", choices=["email", "webhook"], required=True)
    resolve.add_argument("--action", choices=["delivered", "retry"], required=True)
    resolve.add_argument("--note", required=True, help="核对依据，将写入操作记录")
    backup = commands.add_parser("backup", help="创建 SQLite 一致性备份")
    backup.add_argument("--output", type=Path, required=True)
    return root


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def doctor(cfg, online=False):
    validate_credentials(cfg, sending=bool(cfg.channels))
    result = {
        "status": "ok", "timezone": cfg.section("briefing")["timezone"],
        "sources": len(cfg.sources), "channels": cfg.channels,
        "ai_enabled": cfg.section("ai")["enabled"],
        "dependencies": {name: metadata.version(name) for name in ("requests", "feedparser", "tzdata")},
    }
    if online:
        client = HttpClient(cfg.section("network"))
        try:
            _, result["source_reports"] = collect(cfg, client, utc_now())
        finally:
            client.close()
    return result


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    command = args.command or "preview"
    try:
        if command == "init":
            written = initialize(args.config)
            print_json({"created": [str(p) for p in written],
                        "next": "编辑 config.toml 和 .env 后运行 doctor / preview；已有文件保持不变"})
            return 0
        cfg = load_config(args.config)
        if command == "doctor":
            print_json(doctor(cfg, args.online))
            return 0
        configure_logging(cfg.state_dir)
        if command == "preview":
            if getattr(args, "no_ai", False):
                cfg = replace(cfg, data={**cfg.data, "ai": {**cfg.section("ai"), "enabled": False}})
            digest, files = preview(cfg)
            print_json({"status": "preview", "edition": digest.edition, "stories": len(digest.stories),
                        "files": files, "warnings": digest.warnings, "ai_usage": digest.ai_usage})
        elif command in ("run", "retry", "schedule"):
            if not args.send:
                raise ConfigError(f"{command} 会向配置目标发送，请显式添加 --send；只查看内容请用 preview")
            result = serve(cfg, once=args.once) if command == "schedule" else run(
                cfg, edition=args.edition, manual_retry=command == "retry")
            print_json(result)
            return 0 if result["status"] in ("complete", "not_due") else 1
        else:
            with run_lock(cfg.state_dir):
                state = State(cfg.state_dir)
                try:
                    if command == "status":
                        print_json(state.status())
                    elif command == "resolve":
                        state.resolve(args.edition, args.channel, args.action, utc_now(), args.note)
                        print_json({"status": "resolved", "action": args.action,
                                    "note": "若选择 retry，下一次 run/retry 才会实际发送"})
                    elif command == "backup":
                        state.backup(args.output.resolve())
                        print_json({"backup": str(args.output.resolve())})
                finally:
                    state.close()
        return 0
    except KeyboardInterrupt:
        print("已停止。运行记录已保留。", file=sys.stderr)
        return 130
    except (ConfigError, StateError, CollectionError, NetworkError, OSError, ValueError) as exc:
        print_json({"status": "error", "message": str(exc)})
        return 1
    except Exception as exc:
        # Provider bodies/exception repr can contain tokens or private source text.
        print_json({"status": "error", "message": "操作未完成", "error_type": type(exc).__name__})
        return 1
