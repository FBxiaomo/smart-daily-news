import json

import pytest

from daily_news.ai import SynthesisError, synthesize, validate_result
from daily_news.models import Digest
from daily_news.config import ConfigError
from daily_news.pipeline import preview, run
from daily_news.notifications import compact_text
from daily_news.render import html_content, markdown_content, text_content
from conftest import FakeHttp


def response_for(digest):
    story = digest.stories[0]
    return {"stories": [{"id": story.id, "translated_title": "研究团队发布最新发现",
                         "source_titles": {a.id: "研究团队发布最新发现" for a in story.articles},
                         "bullets": [{"text": "研究团队发布了相关研究。", "sources": [story.articles[0].id]}]}]}


def test_translation_is_displayed_across_formats_and_original_is_retained(cfg, digest, monkeypatch):
    cfg.data["ai"].update(enabled=True, model="mock-model", translate_titles=True)
    monkeypatch.setenv("DAILY_NEWS_AI_KEY", "local-test-key")
    result = response_for(digest)
    response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]}
    synthesize(digest.stories, cfg, FakeHttp(post_response=(200, {}, json.dumps(response).encode())))
    assert "研究团队发布最新发现" in html_content(digest)
    assert "研究团队发布最新发现" in markdown_content(digest)
    assert "研究团队发布最新发现" in text_content(digest)
    assert "研究团队发布最新发现" in compact_text(digest, max_bytes=4096)
    assert digest.stories[0].title == "AI model improves battery research"
    assert digest.stories[0].articles[0].title == "AI model improves battery research"
    restored = Digest.from_dict(digest.to_dict())
    assert restored.stories[0].translated_title == "研究团队发布最新发现"


@pytest.mark.parametrize("corruption", ["missing", "unknown", "empty"])
def test_invalid_translated_source_titles_are_rejected(digest, corruption):
    result = response_for(digest)
    translations = result["stories"][0]["source_titles"]
    if corruption == "missing":
        translations.clear()
    elif corruption == "unknown":
        translations["invented"] = "不存在的来源"
    else:
        translations[digest.stories[0].articles[0].id] = ""
    with pytest.raises(SynthesisError, match="翻译"):
        validate_result(result, digest.stories, require_translation=True)


def test_failed_translation_does_not_partially_change_stories(cfg, digest, monkeypatch):
    cfg.data["ai"].update(enabled=True, model="mock", translate_titles=True)
    monkeypatch.setenv("DAILY_NEWS_AI_KEY", "test")
    result = response_for(digest)
    result["stories"][0]["source_titles"] = {}
    response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]}
    before = digest.to_dict()
    with pytest.raises(SynthesisError):
        synthesize(digest.stories, cfg, FakeHttp(post_response=(200, {}, json.dumps(response).encode())))
    assert digest.to_dict() == before


def test_translation_can_be_disabled_for_compatible_providers(cfg, digest, monkeypatch):
    cfg.data["ai"].update(enabled=True, model="mock", translate_titles=False)
    monkeypatch.setenv("DAILY_NEWS_AI_KEY", "test")
    result = {"stories": [{"id": digest.stories[0].id, "bullets": [
        {"text": "研究进展", "sources": [digest.stories[0].articles[0].id]}]}]}
    response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]}
    synthesize(digest.stories, cfg, FakeHttp(post_response=(200, {}, json.dumps(response).encode())))
    assert digest.stories[0].translated_title == ""


@pytest.mark.parametrize("missing_key", [True, False])
def test_ai_failure_delivers_original_with_warning_and_reuses_snapshot(sending_cfg, now, monkeypatch, missing_key):
    cfg = sending_cfg
    cfg.data["ai"].update(enabled=True, model="mock", require_success=False)
    if missing_key:
        monkeypatch.delenv("DAILY_NEWS_AI_KEY", raising=False)
    else:
        monkeypatch.setenv("DAILY_NEWS_AI_KEY", "local-test")
    client = FakeHttp(post_response=(503, {}, b"untrusted-error"))
    delivered = []
    def sender(channel, content, *_):
        delivered.append((channel, content))
    first = run(cfg, now=now, client=client, sender=sender)
    second = run(cfg, now=now, client=client, sender=sender)
    assert first["status"] == second["status"] == "complete"
    assert len(delivered) == 2  # One delivery per configured channel.
    assert len(client.post_calls) == (0 if missing_key else 1)
    for _, content in delivered:
        assert content.stories[0].mode == "source_excerpt"
        assert content.stories[0].title == "AI model improves battery research"
        assert any("AI 不可用" in warning for warning in content.warnings)
        assert "AI 不可用" in compact_text(content, max_bytes=4096)
        assert "AI 不可用" in html_content(content)
    assert "untrusted-error" not in str(first["warnings"])


def test_ai_failure_required_policy_blocks_generation_before_delivery(cfg, now, monkeypatch):
    cfg.data["ai"].update(enabled=True, model="mock", require_success=True)
    monkeypatch.delenv("DAILY_NEWS_AI_KEY", raising=False)
    client = FakeHttp()
    with pytest.raises(ConfigError):
        preview(cfg, now=now, client=client)
    assert client.get_calls == 0 and not client.post_calls
