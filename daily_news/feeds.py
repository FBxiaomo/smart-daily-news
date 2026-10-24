"""RSS/Atom parsing, source provenance, freshness, and topic filtering."""
import calendar
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import re
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
        if b["include_keywords"] and not any(k.casefold() in text for k in b["include_keywords"]):
            skipped += 1
            continue
        if any(k.casefold() in text for k in b["exclude_keywords"]):
            skipped += 1
            continue
        articles.append(Article(
            id=fingerprint(url)[:24], title=title, url=url, source_id=source["id"],
            source_name=source["name"], category=source["category"],
            published_at=published.isoformat() if published else None,
            excerpt=excerpt, weight=source["weight"],
        ))
    return articles, skipped


def collect(cfg: Config, client, now: datetime, cache=None) -> tuple[list[Article], list[dict]]:
    articles, reports = [], []
    for source in cfg.sources:
        report = {"id": source["id"], "name": source["name"], "status": "error", "accepted": 0}
        try:
            previous = cache.get_feed(source["url"]) if cache else None
            headers = {}
            if previous:
                if previous["etag"]:
                    headers["If-None-Match"] = previous["etag"]
                if previous["modified"]:
                    headers["If-Modified-Since"] = previous["modified"]
            body, response_headers, status = client.get(source["url"], headers=headers)
            if status == 304:
                if not previous:
                    raise CollectionError("源返回 304，但本地没有缓存")
                body = previous["body"]
            items, skipped = parse_feed(body, source, cfg, now)
            if cache and status != 304:
                cache.save_feed(source["url"], body, response_headers.get("ETag", response_headers.get("etag", "")),
                                response_headers.get("Last-Modified", response_headers.get("last-modified", "")))
            articles.extend(items)
            report.update(status="cached" if status == 304 else "ok", accepted=len(items), skipped=skipped)
        except (NetworkError, ConfigError, CollectionError) as exc:
            report["error"] = str(exc)
        reports.append(report)
    successful = sum(r["status"] != "error" for r in reports)
    if successful < cfg.section("briefing")["min_sources_ok"]:
        errors = "; ".join(f"{r['name']}: {r.get('error', r['status'])}" for r in reports)
        raise CollectionError(f"可用新闻源不足（{successful}）；{errors}")
    return articles, reports
