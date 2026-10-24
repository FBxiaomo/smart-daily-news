"""Generate once, persist before delivery, and retry only known-safe failures."""
from datetime import datetime, timedelta
import logging
import re
from .ai import SynthesisError, synthesize
from .config import Config, ConfigError, channel_identity, validate_credentials
from .delivery import DeliveryError, deliver
from .editorial import build_stories
from .feeds import CollectionError, collect
from .models import Digest, utc_now
from .network import HttpClient
from .render import write_artifacts
from .state import State, StateError, run_lock

logger = logging.getLogger("daily_news")


class EmptyDigest(CollectionError):
    pass


def edition_id(cfg: Config, now: datetime) -> str:
    return cfg.section("briefing")["id"] + "-" + now.astimezone(cfg.zone).date().isoformat()


def create_digest(cfg, client, now, edition, state=None, *, use_seen=False):
    articles, reports = collect(cfg, client, now, cache=state)
    seen = state.seen_articles(now, cfg.section("briefing")["dedupe_days"]) if state and use_seen else set()
    stories = build_stories(articles, cfg, now, seen)
    if not stories:
        raise EmptyDigest("没有符合时间、关键词或去重条件的新内容；本次未生成或发送空日报")
    warnings = []
    failed = [r["name"] for r in reports if r["status"] == "error"]
    if failed:
        warnings.append("部分来源采集失败：" + "、".join(failed))
    if any(a.published_at is None for s in stories for a in s.articles):
        warnings.append("包含发布时间未知的文章，已在来源列表标注")
    usage = {}
    try:
        usage = synthesize(stories, cfg, client)
    except SynthesisError as exc:
        if cfg.section("ai")["require_success"]:
            raise
        warnings.append(str(exc) + "；本期使用来源摘要")
    return Digest(
        edition=edition, title=cfg.section("briefing")["title"], created_at=now.isoformat(),
        timezone=cfg.section("briefing")["timezone"], stories=stories,
        source_reports=reports, warnings=warnings, ai_usage=usage,
    )


def preview(cfg: Config, *, now=None, client=None):
    validate_credentials(cfg)
    now = now or utc_now()
    owned = client is None
    client = client or HttpClient(cfg.section("network"))
    try:
        with run_lock(cfg.state_dir):
            state = State(cfg.state_dir)
            try:
                digest = create_digest(cfg, client, now, edition_id(cfg, now), state)
                files = write_artifacts(digest, cfg.output_dir, preview=True)
                return digest, files
            finally:
                state.close()
    finally:
        if owned:
            client.close()


def run(cfg: Config, *, now=None, edition=None, client=None, sender=deliver, manual_retry=False):
    validate_credentials(cfg, sending=True)
    now = now or utc_now()
    edition = edition or edition_id(cfg, now)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", edition):
        raise ConfigError("期号只能包含字母、数字、下划线和连字符，最多 100 字符")
    options = cfg.section("schedule")
    targets = {name: channel_identity(cfg, name) for name in cfg.channels}
    owned = client is None
    client = client or HttpClient(cfg.section("network"))
    try:
        with run_lock(cfg.state_dir):
            state = State(cfg.state_dir)
            try:
                saved = state.get_digest(edition)
                if saved:
                    digest, old_fingerprint = saved
                    if old_fingerprint != cfg.content_fingerprint:
                        raise StateError("本期已有固定快照，但内容配置已变化；请恢复配置，或使用新 --edition")
                    rows = state.deliveries(edition)
                    if {r["channel"]: r["target"] for r in rows} != targets:
                        raise StateError("本期投递目标已变化；请恢复原目标，或使用新 --edition")
                else:
                    if not state.start_generation(edition, now, options["max_attempts"], manual_retry):
                        return {"edition": edition, "status": "deferred",
                                "reason": "尚未到重试时间或已达自动重试上限；修复后可使用 retry"}
                    try:
                        digest = create_digest(cfg, client, now, edition, state, use_seen=True)
                        state.save_digest(digest, cfg.content_fingerprint, targets, now)
                    except (CollectionError, SynthesisError, ConfigError, OSError) as exc:
                        state.generation_failed(edition, now, options["retry_minutes"], str(exc))
                        raise
                files = write_artifacts(digest, cfg.output_dir)
                state.recover_interrupted(edition, now)
                if manual_retry:
                    state.requeue_failed(edition, now)
                for row in state.deliveries(edition):
                    if row["status"] in ("sent", "uncertain"):
                        continue
                    if row["status"] == "failed" and not row["retryable"]:
                        continue
                    if row["attempts"] >= options["max_attempts"]:
                        continue
                    if row["next_at"] and now.isoformat() < row["next_at"]:
                        continue
                    name = row["channel"]
                    state.begin_send(edition, name, now)
                    logger.info("send_started edition=%s channel=%s", edition, name)
                    try:
                        sender(name, digest, cfg, client)
                    except DeliveryError as exc:
                        status = "uncertain" if exc.uncertain else "failed"
                        state.outcome(edition, name, status, now, exc.retryable, str(exc),
                                      now + timedelta(minutes=options["retry_minutes"]) if exc.retryable else None)
                        logger.warning("send_%s edition=%s channel=%s reason=%s", status, edition, name, exc)
                    except Exception:
                        # If an adapter crashes after submission, automatic resend is unsafe.
                        state.outcome(edition, name, "uncertain", now, False, "投递模块异常，需核对接收结果")
                        raise
                    else:
                        state.outcome(edition, name, "sent", now)
                        logger.info("send_accepted edition=%s channel=%s", edition, name)
                deliveries = state.deliveries(edition)
                complete = all(row["status"] == "sent" for row in deliveries)
                return {"edition": edition, "status": "complete" if complete else "incomplete",
                        "files": files,
                        "channels": {r["channel"]: r["status"] for r in deliveries},
                        "warnings": digest.warnings, "ai_usage": digest.ai_usage}
            finally:
                state.close()
    finally:
        if owned:
            client.close()
