import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from daily_news.pipeline import run
from conftest import rss


@pytest.mark.parametrize("provider", ["deepseek", "qwen"])
def test_provider_specific_non_thinking_body(cfg, digest, monkeypatch, provider):
    from daily_news.ai import synthesize
    from conftest import FakeHttp
    cfg.data["ai"].update(enabled=True, model="integration", provider=provider)
    monkeypatch.setenv("DAILY_NEWS_AI_KEY", "local-key")
    result = {"stories": [{"id": digest.stories[0].id, "translated_title": "电池研究取得进展",
        "source_titles": {a.id: "电池研究取得进展" for a in digest.stories[0].articles}, "bullets": [
        {"text": "研究进展。", "sources": [digest.stories[0].articles[0].id]}]}]}
    response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]}
    client = FakeHttp(post_response=(200, {}, json.dumps(response).encode()))
    synthesize(digest.stories, cfg, client)
    request = json.loads(client.post_calls[0][1]["body"])
    if provider == "qwen":
        assert request["enable_thinking"] is False and "thinking" not in request
    else:
        assert request["thinking"] == {"type": "disabled"} and "enable_thinking" not in request


@pytest.mark.skipif(os.environ.get("RUN_LOCAL_HTTP_TEST") != "1", reason="explicit local socket test")
def test_real_http_pipeline_rss_ai_webhook_and_no_duplicate(cfg, now, monkeypatch):
    counts, delivered = {"rss": 0, "ai": 0}, []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            counts["rss"] += 1
            body = rss()
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            value = json.loads(body)
            if self.path == "/v1/chat/completions":
                counts["ai"] += 1
                material = json.loads(value["messages"][1]["content"])
                result = {"stories": [
                    {"id": story["id"], "translated_title": "研究结果已发布",
                     "source_titles": {article["id"]: "研究结果已发布" for article in story["articles"]},
                     "bullets": [{"text": "研究结果已发布。",
                                                   "sources": [story["articles"][0]["id"]]}]}
                    for story in material["stories"]
                ]}
                response = {"choices": [{"finish_reason": "stop",
                                          "message": {"content": json.dumps(result)}}]}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())
            else:
                delivered.append((self.headers["Idempotency-Key"], value))
                self.send_response(202)
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    cfg.data["network"].update(allow_http=True, allow_private_hosts=True)
    cfg.data["sources"][0]["url"] = root + "/rss"
    cfg.data["ai"].update(enabled=True, provider="qwen", model="fixture", base_url=root + "/v1")
    cfg.data["webhook"]["enabled"] = True
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("DAILY_NEWS_AI_KEY", "local-fixture")
    monkeypatch.setenv("DAILY_NEWS_WEBHOOK_URL", root + "/webhook")
    try:
        first = run(cfg, now=now)
        second = run(cfg, now=now)
        assert first["status"] == second["status"] == "complete"
        assert counts == {"rss": 1, "ai": 1}
        assert len(delivered) == 1
        assert delivered[0][1]["digest"]["stories"][0]["mode"] == "ai_synthesis"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
