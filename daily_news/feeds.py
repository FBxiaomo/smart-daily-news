"""RSS/Atom parsing, source provenance, freshness, and topic filtering."""
import calendar
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import re
import threading
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
import feedparser
from .config import Config, ConfigError
from .models import Article, fingerprint
from .network import NetworkError


class CollectionError(RuntimeError):
    pass


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "iframe", "object"):
            self.hidden += 1
        elif tag in ("p", "br", "div", "li", "h1", "h2"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe", "object"):
            self.hidden = max(0, self.hidden - 1)
        else:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value: str, limit: int = 1200) -> str:
    parser = PlainText()
    parser.feed(str(value)[:50000])
    text = re.sub(r"[\x00-\x1f\x7f]", " ", "".join(parser.parts))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def canonical_url(value: str) -> str | None:
    try:
        p = urlsplit(value.strip())
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            return None
        if any(ord(c) < 33 for c in value):
            return None
        port = p.port
    except ValueError:
        return None
    host = p.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port and not (p.scheme == "https" and port == 443 or p.scheme == "http" and port == 80):
        host += f":{port}"
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid", "mc_cid", "mc_eid")]
    return urlunsplit((p.scheme.lower(), host, p.path or "/", urlencode(sorted(query)), ""))


def parse_feed(body: bytes, source: dict, cfg: Config, now: datetime) -> tuple[list[Article], int]:
    parsed = feedparser.parse(body)
    if not parsed.get("version") or (parsed.get("bozo") and not parsed.entries):
        raise CollectionError("响应不是可解析的 RSS/Atom")
    b = cfg.section("briefing")
    since = now - timedelta(hours=b["lookback_hours"])
    articles = []
    skipped = 0
    for raw_entry in parsed.entries[:1000]:
        entry = dict(raw_entry)  # Avoid feedparser's deprecated date-key aliasing.
        title = plain_text(entry.get("title", ""), 300)
        url = canonical_url(urljoin(source["url"], entry.get("link", ""))) if entry.get("link") else None
        raw_date = entry.get("published_parsed") or entry.get("updated_parsed")
        published = None
        if raw_date:
            try:
                published = datetime.fromtimestamp(calendar.timegm(raw_date), timezone.utc)
            except (ValueError, OverflowError, OSError):
                pass
        if not title or not url or (published is None and not b["allow_undated"]):
            skipped += 1
            continue
        if published and (published < since or published > now + timedelta(minutes=15)):
            skipped += 1
            continue
        # Use the feed's excerpt, never silently scrape entire copyrighted articles.
        excerpt = plain_text(entry.get("summary", ""), 1200)
        text = (title + " " + excerpt).casefold()
        category = source["category"]
        options = cfg.section("topics")
        if options["enabled"]:
            from .topics import TOPICS, match_topics
            matches = match_topics(title, excerpt, source["id"], options["selected"])
            if not matches:
                skipped += 1
                continue
            labels = {item["id"]: item["name"] for item in TOPICS}
            broad = {"domestic", "international", "society", "technology", "finance",
                     "business", "science", "lifestyle"}
            primary = next((topic for topic in matches if topic not in broad), matches[0])
            category = labels[primary]
        if b["include_keywords"] and not any(k.casefold() in text for k in b["include_keywords"]):
            skipped += 1
            continue
        if any(k.casefold() in text for k in b["exclude_keywords"]):
            skipped += 1
            continue
        articles.append(Article(
            id=fingerprint(url)[:24], title=title, url=url, source_id=source["id"],
            source_name=source["name"], category=category,
            published_at=published.isoformat() if published else None,
            excerpt=excerpt, weight=source["weight"],
        ))
    return articles, skipped


def _safe_source_error(error):
    """Keep useful status information without leaking source URLs or credentials."""
    detail = str(error)
    if isinstance(error, ConfigError):
        return "新闻源地址不符合安全配置"
    if isinstance(error, CollectionError):
        if detail in ("响应不是可解析的 RSS/Atom", "源返回 304，但本地没有缓存"):
            return detail
        return "新闻源解析失败"
    if isinstance(error, NetworkError):
        if re.fullmatch(r"(?:新闻源返回 )?HTTP [1-5][0-9]{2}", detail):
            return detail
        if detail in ("DNS 解析失败", "新闻源重定向无效或次数过多",
                      "新闻源响应超过大小上限", "新闻源重定向失败"):
            return detail
        if re.fullmatch(r"网络读取失败: [A-Za-z_]+", detail):
            return detail
    return "网络读取失败"


def _cache_request(source, cache):
    # This helper is only called on the caller thread: SQLite connections are
    # thread-bound and source credentials must never be sent to worker logs.
    previous = cache.get_feed(source["url"]) if cache else None
    headers = {}
    if previous:
        if previous["etag"]:
            headers["If-None-Match"] = previous["etag"]
        if previous["modified"]:
            headers["If-Modified-Since"] = previous["modified"]
    return source, previous, headers


def _finish_source(job, response, cfg, now, cache):
    source, previous, _ = job
    report = {"id": source["id"], "name": source["name"], "status": "error", "accepted": 0}
    try:
        if isinstance(response, (NetworkError, ConfigError)):
            raise response
        body, response_headers, status = response
        if status == 304:
            if not previous:
                raise CollectionError("源返回 304，但本地没有缓存")
            body = previous["body"]
        items, skipped = parse_feed(body, source, cfg, now)
        if cache and status != 304:
            cache.save_feed(source["url"], body, response_headers.get("ETag", response_headers.get("etag", "")),
                            response_headers.get("Last-Modified", response_headers.get("last-modified", "")))
        report.update(status="cached" if status == 304 else "ok", accepted=len(items), skipped=skipped)
        return items, report
    except (NetworkError, ConfigError, CollectionError) as exc:
        report["error"] = _safe_source_error(exc)
        return [], report


def _serial_collect(cfg, client, now, cache, sources):
    articles, reports = [], []
    for source in sources:
        job = _cache_request(source, cache)
        try:
            response = client.get(source["url"], headers=job[2])
        except (NetworkError, ConfigError) as exc:
            response = exc
        items, report = _finish_source(job, response, cfg, now, cache)
        articles.extend(items)
        reports.append(report)
    return articles, reports


def _parallel_collect(cfg, client, now, cache, sources, workers, first_client):
    # Every worker reuses its own isolated client; no session is shared between
    # threads. Only the caller reads/writes SQLite and parses/aggregates results.
    local = threading.local()
    registry = []
    registry_lock = threading.Lock()
    available = [first_client]

    def fetch(job):
        try:
            if not hasattr(local, "client"):
                with registry_lock:
                    child = available.pop() if available else client.fork()
                    if child is None or child is client or any(child is item for item in registry):
                        raise NetworkError("来源并发客户端无法隔离")
                    local.client = child
                    registry.append(child)
            source, _, headers = job
            return local.client.get(source["url"], headers=headers)
        except (NetworkError, ConfigError) as exc:
            return exc

    articles, reports = [], []
    try:
        jobs = [_cache_request(source, cache) for source in sources]
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="daily-news-feed") as executor:
            pending = deque()
            next_job = 0
            while next_job < min(workers, len(jobs)):
                pending.append(executor.submit(fetch, jobs[next_job]))
                next_job += 1
            for job in jobs:
                response = pending.popleft().result()
                items, report = _finish_source(job, response, cfg, now, cache)
                articles.extend(items)
                reports.append(report)
                if next_job < len(jobs):
                    pending.append(executor.submit(fetch, jobs[next_job]))
                    next_job += 1
    finally:
        # The pool has stopped before sessions are closed. A probe may not have
        # been acquired if preparing the cache or creating the pool failed.
        for child in [*registry, *available]:
            child.close()
    return articles, reports


def collect(cfg: Config, client, now: datetime, cache=None) -> tuple[list[Article], list[dict]]:
    sources = cfg.sources
    configured = cfg.section("network").get("fetch_parallelism", 4)
    workers = min(8, max(1, configured), len(sources)) if sources else 1
    fork = getattr(client, "fork", None)
    # Duplicate URLs keep serial order so later requests observe earlier cache writes.
    can_parallel = workers > 1 and callable(fork) and len({s["url"] for s in sources}) == len(sources)
    first_client = None
    if can_parallel:
        try:
            first_client = fork()
        except NotImplementedError:
            first_client = None
    if first_client is None or first_client is client:
        articles, reports = _serial_collect(cfg, client, now, cache, sources)
    else:
        articles, reports = _parallel_collect(cfg, client, now, cache, sources, workers, first_client)
    successful = sum(r["status"] != "error" for r in reports)
    if successful < cfg.section("briefing")["min_sources_ok"]:
        errors = "; ".join(f"{r['name']}: {r.get('error', r['status'])}" for r in reports)
        raise CollectionError(f"可用新闻源不足（{successful}）；{errors}")
    return articles, reports
