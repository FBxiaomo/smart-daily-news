from datetime import timedelta
import threading

import pytest

from daily_news.feeds import CollectionError, collect
from daily_news.network import HttpClient, NetworkError
from conftest import FakeHttp, rss


class ThreadCheckedCache:
    def __init__(self, entries=None):
        self.owner = threading.get_ident()
        self.entries = entries or {}
        self.reads = []
        self.writes = []

    def get_feed(self, url):
        assert threading.get_ident() == self.owner, "SQLite read left the caller thread"
        self.reads.append(url)
        return self.entries.get(url)

    def save_feed(self, url, body, etag, modified):
        assert threading.get_ident() == self.owner, "SQLite write left the caller thread"
        self.writes.append((url, body, etag, modified))
        self.entries[url] = {"body": body, "etag": etag, "modified": modified}


class ParallelBackend:
    def __init__(self, outcomes=None):
        self.lock = threading.Lock()
        self.barrier = threading.Barrier(2, timeout=5)
        self.outcomes = outcomes or {}
        self.next_id = 0
        self.active = 0
        self.max_active = 0
        self.calls = []
        self.closed = []
        self.clones = []
        self.first_two = 0

    def fork(self):
        with self.lock:
            self.next_id += 1
            child = WorkerClient(self, self.next_id)
            self.clones.append(child)
            return child


class WorkerClient:
    def __init__(self, backend, ident):
        self.backend = backend
        self.ident = ident

    def get(self, url, headers=None):
        with self.backend.lock:
            self.backend.active += 1
            self.backend.max_active = max(self.backend.max_active, self.backend.active)
            self.backend.calls.append((url, headers, self.ident, threading.get_ident()))
            self.backend.first_two += 1
            wait = self.backend.first_two <= 2
        try:
            if wait:
                self.backend.barrier.wait()
            result = self.backend.outcomes.get(url, (rss(), {"ETag": "new-tag"}, 200))
            if isinstance(result, Exception):
                raise result
            return result
        finally:
            with self.backend.lock:
                self.backend.active -= 1

    def close(self):
        with self.backend.lock:
            self.backend.closed.append(self.ident)


def sources(cfg, count):
    template = cfg.data["sources"][0]
    cfg.data["network"]["fetch_parallelism"] = 2
    cfg.data["sources"] = [
        {**template, "id": f"source-{i}", "name": f"Source {i}", "url": f"https://news{i}.example/feed"}
        for i in range(count)
    ]
    return cfg.data["sources"]


def test_production_fork_uses_distinct_sessions_and_custom_session_is_serial(cfg):
    first = HttpClient(cfg.section("network"))
    a = first.fork()
    b = first.fork()
    try:
        assert first.session is not a.session
        assert first.session is not b.session
        assert a.session is not b.session
    finally:
        first.close()
        a.close()
        b.close()
    injected = HttpClient(cfg.section("network"), session=FakeSession())
    assert injected.fork() is None
    injected.close()


class FakeSession:
    def __init__(self):
        self.headers = {}

    def close(self):
        pass


def test_concurrent_fetch_keeps_source_order_and_sqlite_on_main_thread(cfg, now):
    expected = sources(cfg, 4)
    client = ParallelBackend()
    cache = ThreadCheckedCache()
    articles, reports = collect(cfg, client, now, cache)
    assert client.max_active == 2
    assert [r["id"] for r in reports] == [s["id"] for s in expected]
    assert [a.source_id for a in articles] == [s["id"] for s in expected]
    assert cache.reads == [s["url"] for s in expected]
    assert [write[0] for write in cache.writes] == [s["url"] for s in expected]
    # Each worker uses only its own session, and every created client is closed once.
    owners = {}
    for _, _, clone, thread in client.calls:
        owners.setdefault(clone, set()).add(thread)
    assert all(len(threads) == 1 for threads in owners.values())
    assert sorted(client.closed) == sorted(c.ident for c in client.clones)
    assert len(client.closed) == len(set(client.closed))


def test_304_cache_headers_and_mixed_failures_preserve_order(cfg, now):
    selected = sources(cfg, 3)
    cached = rss(title="Cached event")
    cache = ThreadCheckedCache({
        selected[0]["url"]: {"body": cached, "etag": '"old"', "modified": "Yesterday"},
    })
    client = ParallelBackend({
        selected[0]["url"]: (b"", {}, 304),
        selected[1]["url"]: NetworkError("新闻源返回 HTTP 503"),
        selected[2]["url"]: (rss(title="Fresh event"), {"ETag": '"new"'}, 200),
    })
    articles, reports = collect(cfg, client, now, cache)
    headers = {url: values for url, values, _, _ in client.calls}
    assert headers[selected[0]["url"]] == {"If-None-Match": '"old"', "If-Modified-Since": "Yesterday"}
    assert [r["status"] for r in reports] == ["cached", "error", "ok"]
    assert reports[1]["error"] == "新闻源返回 HTTP 503"
    assert [a.title for a in articles] == ["Cached event", "Fresh event"]
    assert [write[0] for write in cache.writes] == [selected[2]["url"]]


def test_304_without_cached_body_is_a_reported_error(cfg, now):
    selected = sources(cfg, 2)
    client = ParallelBackend({selected[0]["url"]: (b"", {}, 304)})
    articles, reports = collect(cfg, client, now, ThreadCheckedCache())
    assert reports[0]["status"] == "error"
    assert reports[0]["error"] == "源返回 304，但本地没有缓存"
    assert len(articles) == 1


def test_failure_never_exposes_private_source_url_or_token(cfg, now):
    selected = sources(cfg, 2)
    selected[0]["url"] = "https://news.example/feed?key=private-token"
    client = ParallelBackend({
        selected[0]["url"]: NetworkError("Failed to access https://news.example/feed?key=private-token"),
    })
    _, reports = collect(cfg, client, now)
    assert reports[0]["status"] == "error"
    assert "private-token" not in str(reports)
    assert reports[0]["error"] == "网络读取失败"


def test_insufficient_sources_produces_sanitized_collection_error(cfg, now):
    selected = sources(cfg, 2)
    cfg.data["briefing"]["min_sources_ok"] = 2
    client = ParallelBackend({selected[0]["url"]: NetworkError("https://secret.example/feed?token=private")})
    with pytest.raises(CollectionError) as error:
        collect(cfg, client, now)
    assert "private" not in str(error.value)


def test_clients_without_fork_fall_back_to_original_serial_behavior(cfg, now):
    selected = sources(cfg, 3)
    original_thread = threading.get_ident()
    client = FakeHttp()
    calls = []
    get = client.get

    def serial_get(url, headers=None):
        calls.append((url, threading.get_ident()))
        return get(url, headers)

    client.get = serial_get
    _, reports = collect(cfg, client, now)
    assert [r["id"] for r in reports] == [s["id"] for s in selected]
    assert [u for u, _ in calls] == [s["url"] for s in selected]
    assert all(thread == original_thread for _, thread in calls)


def test_fork_returning_none_falls_back_to_serial(cfg, now):
    sources(cfg, 2)
    client = FakeHttp()
    client.fork = lambda: None
    _, reports = collect(cfg, client, now)
    assert client.get_calls == 2
    assert all(r["status"] == "ok" for r in reports)


def test_duplicate_urls_remain_serial_for_consistent_conditional_cache(cfg, now):
    selected = sources(cfg, 2)
    selected[1]["url"] = selected[0]["url"]
    cache = ThreadCheckedCache()
    caller = threading.get_ident()

    class CheckingClient:
        def __init__(self):
            self.calls = []

        def fork(self):
            pytest.fail("duplicate URLs should use serial requests")

        def get(self, url, headers=None):
            self.calls.append((headers, threading.get_ident()))
            if len(self.calls) == 1:
                return rss(), {"ETag": "v1"}, 200
            return b"", {}, 304

    client = CheckingClient()
    _, reports = collect(cfg, client, now, cache)
    assert [r["status"] for r in reports] == ["ok", "cached"]
    assert client.calls[1][0]["If-None-Match"] == "v1"
    assert all(t == caller for _, t in client.calls)


def test_thread_clients_closed_when_fetch_fails(cfg, now):
    selected = sources(cfg, 2)
    client = ParallelBackend({selected[0]["url"]: NetworkError("HTTP 503")})
    collect(cfg, client, now + timedelta(minutes=1))
    assert sorted(client.closed) == sorted(c.ident for c in client.clones)
