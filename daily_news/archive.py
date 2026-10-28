"""Local digest archive, independent bookmarks, and source-linked seven-day review."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import re
from uuid import uuid4

from .editorial import same_event, title_tokens, use_source_excerpts
from .feeds import canonical_url
from .models import Article, Digest, Story, fingerprint


class ArchiveError(ValueError):
    """An archive operation cannot be safely completed."""


_WEEKLY_SOURCE_ID = "_system_weekly_archive"


def _ensure_tables(state):
    # Additional tables coexist with version-1 state databases; do not change user_version.
    state.db.executescript("""
    CREATE TABLE IF NOT EXISTS archive_previews (
        id TEXT PRIMARY KEY, edition TEXT NOT NULL, kind TEXT NOT NULL,
        payload TEXT, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS archive_previews_created_at ON archive_previews(created_at);
    CREATE INDEX IF NOT EXISTS archive_previews_edition ON archive_previews(edition, kind);
    CREATE TABLE IF NOT EXISTS archive_favorites (
        id TEXT PRIMARY KEY, history_id TEXT NOT NULL, story_title TEXT NOT NULL,
        article_id TEXT NOT NULL, article TEXT NOT NULL, saved_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS archive_favorites_saved_at ON archive_favorites(saved_at);
    """)


def _limit(value):
    if type(value) is not int or not 1 <= value <= 500:
        raise ArchiveError("limit 必须为 1 至 500")
    return value


def _query(value):
    if not isinstance(value, str):
        raise ArchiveError("搜索词必须为文本")
    if len(value) > 200:
        raise ArchiveError("搜索词不能超过 200 个字符")
    # LIKE metacharacters are treated as literal user text.
    value = value.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + value + "%"


def _decode(payload):
    try:
        return Digest.from_dict(json.loads(payload))
    except (TypeError, ValueError, KeyError):
        raise ArchiveError("日报历史数据损坏，无法读取") from None


def _timestamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ArchiveError("时间必须包含时区")
    return value.astimezone(timezone.utc)


def save_preview(state, digest: Digest, kind: str = "preview") -> str:
    """Store an immutable preview; a weekly preview remains identifiable after deletion."""
    if kind not in ("preview", "weekly"):
        raise ArchiveError("预览类型必须为 preview 或 weekly")
    if not isinstance(digest, Digest):
        raise ArchiveError("日报数据无效")
    if kind == "weekly" and not digest.edition.startswith("weekly-"):
        raise ArchiveError("周报期号必须以 weekly- 开头")
    try:
        created_at = _timestamp(datetime.fromisoformat(digest.created_at))
    except (TypeError, ValueError):
        raise ArchiveError("日报创建时间无效或缺少时区") from None
    _ensure_tables(state)
    history_id = "preview:" + uuid4().hex
    state.db.execute(
        "INSERT INTO archive_previews(id,edition,kind,payload,created_at) VALUES (?,?,?,?,?)",
        (history_id, digest.edition, kind, json.dumps(digest.to_dict(), ensure_ascii=False), created_at.isoformat()),
    )
    return history_id


def _edition_status(state, edition):
    statuses = [row["status"] for row in state.db.execute(
        "SELECT status FROM deliveries WHERE edition=?", (edition,)
    )]
    if not statuses:
        return "unknown"
    if all(status == "sent" for status in statuses):
        return "sent"
    if "uncertain" in statuses or "sending" in statuses:
        return "uncertain"
    if "failed" in statuses:
        return "failed"
    return "pending"


def list_history(state, query: str = "", limit: int = 100) -> list[dict]:
    """List matching previews and delivery snapshots newest-first."""
    limit, pattern = _limit(limit), _query(query)
    _ensure_tables(state)
    rows = state.db.execute("""
        SELECT * FROM (
            SELECT 'edition:' || id AS history_id, 'edition' AS kind, id AS edition, payload, created_at
            FROM editions
            UNION ALL
            SELECT id AS history_id, kind, edition, payload, created_at
            FROM archive_previews WHERE payload IS NOT NULL
        ) AS h
        WHERE ? OR EXISTS (
            SELECT 1 FROM json_tree(h.payload) AS t
            WHERE t.type='text' AND t.value LIKE ? ESCAPE '\\'
        )
        ORDER BY julianday(h.created_at) DESC, h.history_id DESC LIMIT ?
    """, (not query.strip(), pattern, limit))
    result = []
    for row in rows:
        digest = _decode(row["payload"])
        status = _edition_status(state, row["edition"]) if row["kind"] == "edition" else "preview"
        result.append({
            "id": row["history_id"], "kind": row["kind"], "edition": row["edition"],
            "title": digest.title, "created_at": row["created_at"], "status": status,
            "story_count": len(digest.stories), "can_delete": status in ("sent", "preview"),
        })
    return result


def get_history(state, history_id: str) -> tuple[Digest, str]:
    _ensure_tables(state)
    if not isinstance(history_id, str):
        raise ArchiveError("历史 ID 必须是字符串")
    if history_id.startswith("edition:"):
        row = state.db.execute("SELECT payload FROM editions WHERE id=?", (history_id[8:],)).fetchone()
        kind = "edition"
    elif history_id.startswith("preview:"):
        row = state.db.execute(
            "SELECT payload,kind FROM archive_previews WHERE id=? AND payload IS NOT NULL", (history_id,)
        ).fetchone()
        kind = row["kind"] if row else "preview"
    else:
        raise ArchiveError("历史 ID 格式无效")
    if not row:
        raise ArchiveError("日报历史不存在或已删除")
    return _decode(row["payload"]), kind


def favorite(state, history_id: str, article_id: str, now: datetime) -> str:
    """Save a self-contained article snapshot independently of its digest."""
    if not isinstance(article_id, str) or not article_id:
        raise ArchiveError("文章 ID 无效")
    now = _timestamp(now)
    digest, _ = get_history(state, history_id)
    selected = next(((story, article) for story in digest.stories for article in story.articles
                     if article.id == article_id), None)
    if selected is None:
        raise ArchiveError("这篇文章不在所选日报中")
    story, article = selected
    url = canonical_url(article.url)
    if not url:
        raise ArchiveError("文章链接无效，不能收藏")
    bookmark_id = fingerprint(url)[:24]
    state.db.execute(
        "INSERT OR IGNORE INTO archive_favorites(id,history_id,story_title,article_id,article,saved_at) "
        "VALUES (?,?,?,?,?,?)",
        (bookmark_id, history_id, story.translated_title or story.title, article.id,
         json.dumps(asdict(article), ensure_ascii=False), now.isoformat()),
    )
    return bookmark_id


def list_favorites(state, query: str = "", limit: int = 100) -> list[dict]:
    limit, pattern = _limit(limit), _query(query)
    _ensure_tables(state)
    rows = state.db.execute("""
        SELECT * FROM archive_favorites
        WHERE ? OR story_title LIKE ? ESCAPE '\\' OR EXISTS (
            SELECT 1 FROM json_tree(article) AS t
            WHERE t.type='text' AND t.value LIKE ? ESCAPE '\\'
        )
        ORDER BY julianday(saved_at) DESC, id DESC LIMIT ?
    """, (not query.strip(), pattern, pattern, limit))
    result = []
    for row in rows:
        try:
            article = asdict(Article(**json.loads(row["article"])))
        except (TypeError, ValueError):
            raise ArchiveError("收藏数据损坏，无法读取") from None
        result.append({
            "id": row["id"], "history_id": row["history_id"], "story_title": row["story_title"],
            "article_id": row["article_id"], "article": article, "saved_at": row["saved_at"],
        })
    return result


def remove_favorite(state, favorite_id: str) -> bool:
    if not isinstance(favorite_id, str) or not favorite_id:
        raise ArchiveError("收藏 ID 无效")
    _ensure_tables(state)
    return state.db.execute("DELETE FROM archive_favorites WHERE id=?", (favorite_id,)).rowcount > 0


def delete_history(state, history_id: str) -> bool:
    """Delete a preview or a fully sent edition; never delete an outstanding delivery."""
    _ensure_tables(state)
    if not isinstance(history_id, str):
        raise ArchiveError("历史 ID 必须是字符串")
    if history_id.startswith("preview:"):
        with state.transaction():
            row = state.db.execute("SELECT kind,payload FROM archive_previews WHERE id=?", (history_id,)).fetchone()
            if not row or row["payload"] is None:
                return False
            if row["kind"] == "weekly":
                # Keep only the edition marker so a sent weekly review cannot feed a later one.
                state.db.execute("UPDATE archive_previews SET payload=NULL WHERE id=?", (history_id,))
            else:
                state.db.execute("DELETE FROM archive_previews WHERE id=?", (history_id,))
        return True
    if not history_id.startswith("edition:"):
        raise ArchiveError("历史 ID 格式无效")
    edition = history_id[8:]
    with state.transaction():
        exists = state.db.execute("SELECT 1 FROM editions WHERE id=?", (edition,)).fetchone()
        if not exists:
            return False
        if _edition_status(state, edition) != "sent":
            raise ArchiveError("该期存在未完成或不确定的投递，不能清理")
        # A small completed job tombstone prevents automatic regeneration after cleanup.
        now = datetime.now(timezone.utc)
        state.db.execute("""
            INSERT INTO jobs(edition,status,attempts,updated_at,next_at,error) VALUES(?, 'complete', 1, ?, NULL, NULL)
            ON CONFLICT(edition) DO UPDATE SET status='complete',updated_at=excluded.updated_at,next_at=NULL,error=NULL
        """, (edition, now.isoformat()))
        state.db.execute("DELETE FROM deliveries WHERE edition=?", (edition,))
        state.db.execute("DELETE FROM editions WHERE id=?", (edition,))
        state.event(edition, now, "history_deleted", "completed snapshot removed; dedupe marker retained")
    return True


def weekly_digest(state, cfg, now: datetime, edition: str) -> tuple[Digest, dict]:
    """Rank events from independent articles observed in the last 168 hours of local daily digests."""
    now = _timestamp(now)
    if not isinstance(edition, str) or not re.fullmatch(r"weekly-[A-Za-z0-9_-]{1,93}", edition):
        raise ArchiveError("周报期号必须以 weekly- 开头，且仅包含字母、数字、下划线和连字符")
    _ensure_tables(state)
    start = now - timedelta(days=7)
    rows = list(state.db.execute("""
        SELECT * FROM (
            SELECT id AS edition, 'edition' AS kind, payload, created_at FROM editions
            UNION ALL
            SELECT edition,kind,payload,created_at FROM archive_previews
            WHERE payload IS NOT NULL AND kind='preview'
        ) AS h
        WHERE julianday(h.created_at) >= julianday(?) AND julianday(h.created_at) <= julianday(?)
          AND NOT EXISTS (
              SELECT 1 FROM archive_previews AS w WHERE w.kind='weekly' AND w.edition=h.edition
          )
          AND NOT EXISTS (
              SELECT 1 FROM json_each(h.payload, '$.source_reports') AS report
              WHERE json_extract(report.value, '$.id') = ?
          )
        ORDER BY julianday(h.created_at) DESC LIMIT 1001
    """, (start.isoformat(), now.isoformat(), _WEEKLY_SOURCE_ID)))
    if len(rows) > 1000:
        raise ArchiveError("近七天本地日报过多（超过 1000 期），请先清理历史")
    articles, snapshots = {}, 0
    for row in rows:
        digest = _decode(row["payload"])
        snapshots += 1
        for story in digest.stories:
            for article in story.articles:
                url = canonical_url(article.url)
                if not url:
                    continue
                # Snapshots arrive newest first: retain the freshest version of each URL.
                if url not in articles:
                    articles[url] = article
                    if len(articles) > 2000:
                        raise ArchiveError("近七天独立文章超过 2000 篇，请先清理历史")
    if not articles:
        raise ArchiveError("近七天没有可用的本地日报文章，先生成或保存几期日报再试")

    # An inverted title-token index avoids comparing wholly unrelated titles.
    groups, token_groups = [], {}
    for article in articles.values():
        tokens = title_tokens(article.title)
        candidates = set().union(*(token_groups.get(token, set()) for token in tokens)) if tokens else set()
        group_idx = next((index for index in sorted(candidates) if same_event(article, groups[index][0])), None)
        if group_idx is None:
            group_idx = len(groups)
            groups.append([article])
        else:
            groups[group_idx].append(article)
        for token in tokens:
            token_groups.setdefault(token, set()).add(group_idx)

    # Coverage counts RSS feeds, not whole-web popularity or verification quality.
    groups.sort(key=lambda group: (-len({a.source_id for a in group}), -len(group),
                                   group[0].title.casefold()))
    story_limit = cfg.section("briefing")["max_stories"]
    selected_groups = groups[:story_limit]
    stories, event_metrics = [], []
    for group in selected_groups:
        lead = group[0]
        selected = group[:12]
        story = Story(id=fingerprint(sorted(a.id for a in group))[:16], title=lead.title,
                      category=lead.category, articles=selected, translated_title=lead.translated_title)
        use_source_excerpts(story)
        stories.append(story)
        event_metrics.append({"story_id": story.id, "sources": len({a.source_id for a in group}),
                              "articles": len(group), "displayed_articles": len(selected)})
    warnings = ["基于近七天本地日报中的独立文章及不同订阅源数量整理，不代表全网热度，也不等于事实核查。"]
    if any(len(group) > 12 for group in selected_groups):
        warnings.append("单个事件最多展示 12 篇来源，完整来源数量见统计。")
    presentation = cfg.data.get("presentation", {}).copy()
    result = Digest(
        edition=edition, title=cfg.section("briefing")["title"] + " · 近七日回顾",
        created_at=now.isoformat(), timezone=cfg.section("briefing")["timezone"], stories=stories,
        source_reports=[{"id": _WEEKLY_SOURCE_ID, "name": "本地日报", "status": "ok", "accepted": snapshots}],
        warnings=warnings, presentation=presentation,
    )
    metrics = {
        "window_start": start.isoformat(), "window_end": now.isoformat(), "snapshots": snapshots,
        "articles": len(articles), "events": len(groups), "displayed_events": len(stories),
        "sources": len({a.source_id for a in articles.values()}),
        "multi_source_events": sum(len({a.source_id for a in group}) > 1 for group in groups),
        "event_metrics": event_metrics,
    }
    return result, metrics
