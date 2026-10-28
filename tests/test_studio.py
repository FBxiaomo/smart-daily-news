import os
import threading

import pytest
import requests

from daily_news.archive import list_history
from daily_news.feeds import parse_feed
from daily_news.pipeline import preview
from daily_news.presentation import resolve_presentation
from daily_news.state import State
from daily_news.studio import Studio, StudioError, StudioServer, _read_env, _update_env, sample_digest
from conftest import FakeHttp, rss


@pytest.fixture
def studio(cfg, monkeypatch):
    for name in list(os.environ):
        if name.startswith("DAILY_NEWS_"):
            monkeypatch.delenv(name, raising=False)
    return Studio(cfg.path)


def test_studio_saves_without_disclosing_or_overwriting_secrets(studio):
    before = studio.meta()["settings"]
    payload = {
        "revision": before["revision"],
        "presentation": {"layout": "cards", "theme": "dark", "density": "brief"},
        "schedule": {"times": ["08:00", "18:00"], "weekdays": [1, 2, 3, 4, 5]},
        "topics": {"enabled": True, "selected": ["ai", "automotive"]},
        "secrets": {"smtp_password": 'very-secret="x"', "serverchan_key": "SCT-test-key"},
    }
    result = studio.save(payload)
    assert result["presentation"]["layout"] == "cards"
    assert result["secrets_set"]["smtp_password"]
    assert "very-secret" not in repr(result)
    assert _read_env(studio.path.parent / ".env")["DAILY_NEWS_SMTP_PASSWORD"] == 'very-secret="x"'
    config = studio.path.read_text(encoding="utf-8")
    assert "# Copy with" in config
    assert "very-secret" not in config
    # Blank means keep the saved value; secrets are never prefilled.
    studio.save({"revision": result["revision"], "secrets": {"smtp_password": ""}})
    assert _read_env(studio.path.parent / ".env")["DAILY_NEWS_SMTP_PASSWORD"] == 'very-secret="x"'


def test_process_environment_precedence_is_retained(cfg, monkeypatch):
    monkeypatch.setenv("DAILY_NEWS_SMTP_PASSWORD", "external-secret")
    app = Studio(cfg.path)
    result = app.meta()["settings"]
    app.save({"revision": result["revision"], "secrets": {"smtp_password": "new-local-secret"}})
    assert os.environ["DAILY_NEWS_SMTP_PASSWORD"] == "external-secret"
    assert _read_env(cfg.path.parent / ".env")["DAILY_NEWS_SMTP_PASSWORD"] == "new-local-secret"


def test_revision_check_avoids_overwriting_external_edits(studio):
    before = studio.meta()["settings"]
    studio.path.write_text(studio.path.read_text(encoding="utf-8") + "\n# external edit\n", encoding="utf-8")
    with pytest.raises(StudioError, match="其他程序修改"):
        studio.save({"revision": before["revision"], "secrets": {"smtp_password": "new-secret"}})
    assert not (studio.path.parent / ".env").exists()
    assert "# external edit" in studio.path.read_text(encoding="utf-8")


def test_unknown_and_invalid_settings_do_not_write_config(studio):
    before = studio.meta()["settings"]
    original = studio.path.read_bytes()
    with pytest.raises(ValueError):
        studio.save({"revision": before["revision"], "presentation": {"theme": "not-a-theme"}})
    assert studio.path.read_bytes() == original
    with pytest.raises(StudioError):
        studio.save({"revision": before["revision"], "secrets": {"smtp_password": "bad\ninjection"}})


def test_custom_sources_are_preserved_and_addable(studio):
    before = studio.meta()["settings"]
    extra = {"id": "custom-one", "name": "Example", "url": "https://example.org/rss",
             "category": "综合", "weight": 1.0, "enabled": True}
    result = studio.save({"revision": before["revision"], "sources": before["sources"] + [extra]})
    assert result["sources"][-1] == extra
    assert len(result["sources"]) == len(before["sources"]) + 1


def test_env_update_retains_comments_and_replaces_duplicate_keys():
    value = _update_env("# Keep\nA=old\nB=x\nA=stale\n", {"A": "new=ok"})
    assert value == '# Keep\nA="new=ok"\nB=x\n'


def test_sample_is_labeled_and_each_combination_is_available():
    digest = sample_digest({"title": "我的日报", "presentation": {
        "layout": "columns", "theme": "accent", "density": "detailed"}})
    assert "示例" in digest.warnings[0]
    assert resolve_presentation(digest.presentation)["layout"] == "columns"


def test_topic_selection_filters_mixed_source(cfg, now):
    cfg.data["topics"].update(enabled=True, selected=["automotive"])
    source = {"id": "ithome", "name": "IT之家", "url": "https://www.ithome.com/rss/",
              "category": "科技", "weight": 1.0, "enabled": True}
    assert not parse_feed(rss(title="手机新品发布"), source, cfg, now)[0]
    assert parse_feed(rss(title="新能源汽车产业发布新车型"), source, cfg, now)[0][0].category == "汽车与出行"


def test_generated_preview_is_saved_in_history(cfg, now):
    digest, files = preview(cfg, now=now, client=FakeHttp())
    state = State(cfg.state_dir)
    try:
        rows = list_history(state)
        assert rows[0]["id"] == digest.history_id
        assert rows[0]["kind"] == "preview"
        assert os.path.exists(files["html"])
    finally:
        state.close()


def test_start_and_stop_never_send_by_themselves(studio, monkeypatch):
    before = studio.meta()["settings"]
    studio.save({"revision": before["revision"], "webhook": {"enabled": True},
                 "secrets": {"webhook_url": "https://receiver.example/hook"}})
    entered = threading.Event()
    def fake_schedule(cfg, *, stop_event, runner):
        entered.set()
        stop_event.wait(3)
    monkeypatch.setattr("daily_news.studio.schedule", fake_schedule)
    result = studio.start()
    assert result["status"] == "running" and entered.wait(1)
    with pytest.raises(StudioError, match="暂停"):
        studio.save({"revision": studio.settings()["revision"], "briefing": {"title": "changed"}})
    studio.stop()
    studio.thread.join(timeout=2)
    assert studio.scheduler_status()["status"] == "stopped"


@pytest.mark.skipif(os.environ.get("RUN_LOCAL_HTTP_TEST") != "1", reason="explicit local socket test")
def test_loopback_api_rejects_invalid_origin_and_token(studio):
    server = StudioServer(("127.0.0.1", 0), studio)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    client = requests.Session()
    client.trust_env = False
    try:
        assert client.get(base + "/api/meta", timeout=3).status_code == 403
        headers = {"X-DailyNews-Token": studio.token}
        assert client.get(base + "/api/meta", headers=headers, timeout=3).status_code == 200
        response = client.post(base + "/api/preview/sample", json={}, headers={
            **headers, "Origin": "https://evil.example"}, timeout=3)
        assert response.status_code == 403
        response = client.post(base + "/api/preview/sample", json={"presentation": {
            "layout": "cards", "theme": "dark", "density": "brief"}}, headers=headers, timeout=3)
        assert response.status_code == 200 and 'data-layout="cards"' in response.json()["html"]
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)

def test_weekly_preview_and_manual_send_reuse_exact_draft(studio, digest, monkeypatch):
    from dataclasses import replace
    from daily_news import archive
    from daily_news.models import utc_now
    from daily_news.pipeline import run as real_run

    before = studio.meta()["settings"]
    studio.save({"revision": before["revision"], "webhook": {"enabled": True},
                 "secrets": {"webhook_url": "https://receiver.example/hook"}})
    with studio.state() as (_, state):
        seed = replace(digest, created_at=utc_now().isoformat())
        archive.save_preview(state, seed)
    prepared = studio.weekly_preview()
    assert prepared["metrics"]["articles"] == 1
    shown = studio.history_item(prepared["history_id"])["digest"]
    calls = []
    def safe_run(cfg, **kwargs):
        return real_run(cfg, **kwargs, client=FakeHttp(),
                        sender=lambda channel, content, *_: calls.append((channel, content.to_dict())))
    monkeypatch.setattr("daily_news.studio.run", safe_run)
    first = studio.weekly_send(prepared["history_id"])
    second = studio.weekly_send(prepared["history_id"])
    assert first["status"] == second["status"] == "complete"
    assert len(calls) == 1
    assert calls[0][1] == shown


def test_weekly_ai_is_optional_and_never_called_when_disabled(studio, digest):
    from dataclasses import replace
    from daily_news import archive
    from daily_news.models import utc_now

    with studio.state() as (_, state):
        archive.save_preview(state, replace(digest, created_at=utc_now().isoformat()))
    assert studio.weekly_preview()["history_id"].startswith("preview:")
    with pytest.raises(StudioError, match="启用 AI"):
        studio.weekly_preview(use_ai=True)


def test_daily_preview_only_uses_ai_on_explicit_request(studio, cfg, now, monkeypatch):
    before = studio.settings()
    studio.save({"revision": before["revision"], "ai": {"enabled": True}})
    client = FakeHttp(post_response=(503, {}, b"error"))
    def local_preview(config):
        config.data["sources"] = cfg.data["sources"]
        return preview(config, now=now, client=client)
    monkeypatch.setattr("daily_news.studio.preview", local_preview)
    free = studio.live_preview()
    assert not free["ai_used"] and not free["warnings"]
    assert not client.post_calls
    fallback = studio.live_preview(use_ai=True)
    assert not fallback["ai_used"] and "AI 不可用" in fallback["html"]
    assert fallback["history_id"] != free["history_id"]


def test_weekly_ai_failure_falls_back_with_visible_warning(studio, digest, monkeypatch):
    from dataclasses import replace
    from daily_news import archive
    from daily_news.models import utc_now
    before = studio.settings()
    studio.save({"revision": before["revision"], "ai": {"enabled": True}})
    with studio.state() as (_, state):
        archive.save_preview(state, replace(digest, created_at=utc_now().isoformat()))
    monkeypatch.setattr("daily_news.studio.HttpClient", lambda *_: FakeHttp())
    result = studio.weekly_preview(use_ai=True)
    assert not result["ai_used"] and "AI 不可用" in result["html"]

