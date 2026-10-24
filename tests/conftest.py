from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import copy
import pytest
from daily_news.config import load_config
from daily_news.models import Article, Digest, Story


@pytest.fixture
def now():
    return datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)


@pytest.fixture
def cfg(tmp_path):
    source = Path(__file__).parents[1] / "daily_news" / "config.example.toml"
    path = tmp_path / "config.toml"
    path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    config = load_config(path)
    data = copy.deepcopy(config.data)
    data["sources"] = [{"id": "one", "name": "Source One", "url": "https://news.example/feed",
                        "category": "科技", "weight": 1.0, "enabled": True}]
    return replace(config, data=data)


@pytest.fixture
def sending_cfg(cfg, monkeypatch):
    cfg.data["email"]["enabled"] = True
    cfg.data["webhook"]["enabled"] = True
    for key, value in {
        "DAILY_NEWS_SMTP_USER": "sender@example.com",
        "DAILY_NEWS_SMTP_PASSWORD": "do-not-print-this-password",
        "DAILY_NEWS_EMAIL_FROM": "sender@example.com",
        "DAILY_NEWS_EMAIL_TO": "receiver@example.com",
        "DAILY_NEWS_WEBHOOK_URL": "https://receiver.example/hook?token=secret",
    }.items():
        monkeypatch.setenv(key, value)
    return cfg


@pytest.fixture
def article(now):
    return Article("article-one", "AI model improves battery research", "https://news.example/a",
                   "one", "Source One", "科技", now.isoformat(), "A measured research result.", 1.0)


@pytest.fixture
def digest(article, now):
    return Digest(
        "daily-2026-10-25", "每日简报", now.isoformat(), "Asia/Shanghai",
        [Story("story-one", article.title, "科技", [article],
               [{"text": article.excerpt, "sources": [article.id]}])],
        [{"id": "one", "name": "Source One", "status": "ok", "accepted": 1}],
    )


def rss(title="AI model improves battery research", date="Sun, 25 Oct 2026 00:00:00 GMT", extra=""):
    return f"""<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title>
    <link>https://news.example</link><description>News</description><item><title>{title}</title>
    <link>https://news.example/a?utm_source=test</link><pubDate>{date}</pubDate>
    <description>&lt;p&gt;A measured research result.&lt;/p&gt;</description></item>
    {extra}</channel></rss>""".encode()


class FakeHttp:
    def __init__(self, body=None, post_response=None):
        self.body = rss() if body is None else body
        self.get_calls = 0
        self.post_calls = []
        self.post_response = post_response

    def get(self, url, headers=None):
        self.get_calls += 1
        return self.body, {"ETag": '"v1"'}, 200

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if isinstance(self.post_response, Exception):
            raise self.post_response
        return self.post_response or (204, {}, b"")

    def close(self):
        pass
