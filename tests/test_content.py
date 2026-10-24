from dataclasses import replace
import json
import pytest
from daily_news.ai import SynthesisError, synthesize, validate_result
from daily_news.editorial import build_stories, same_event
from daily_news.feeds import CollectionError, canonical_url, collect, parse_feed, plain_text
from daily_news.pipeline import EmptyDigest, create_digest
from daily_news.render import html_content, markdown_content, write_artifacts
from conftest import FakeHttp, rss


def test_rss_uses_real_timestamp_and_removes_tracking(cfg, now):
    items, skipped = parse_feed(rss(), cfg.sources[0], cfg, now)
    assert skipped == 0 and len(items) == 1
    assert items[0].url == "https://news.example/a"
    assert items[0].published_at == "2026-10-25T00:00:00+00:00"
    assert items[0].excerpt == "A measured research result."


def test_atom_and_timezone(cfg, now):
    body = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>Feed</title><entry>
    <title>Atom story</title><link href="https://news.example/atom"/>
    <updated>2026-10-25T08:00:00+08:00</updated><summary>Details</summary></entry></feed>"""
    items, _ = parse_feed(body, cfg.sources[0], cfg, now)
    assert items[0].published_at == "2026-10-25T00:00:00+00:00"


@pytest.mark.parametrize("date", ["Mon, 19 Oct 2026 00:00:00 GMT", "Mon, 26 Oct 2026 00:00:00 GMT", "invalid"])
def test_old_future_and_unknown_dates_excluded(cfg, now, date):
    assert parse_feed(rss(date=date), cfg.sources[0], cfg, now)[0] == []


def test_unknown_date_explicit_opt_in(cfg, now):
    cfg.data["briefing"]["allow_undated"] = True
    digest = create_digest(cfg, FakeHttp(rss(date="invalid")), now, "edition")
    assert digest.stories[0].articles[0].published_at is None
    assert digest.warnings


def test_keywords_actually_filter(cfg, now):
    cfg.data["briefing"]["include_keywords"] = ["电池"]
    assert not parse_feed(rss(title="AI news"), cfg.sources[0], cfg, now)[0]
    assert parse_feed(rss(title="电池研究获得进展"), cfg.sources[0], cfg, now)[0]
    cfg.data["briefing"]["exclude_keywords"] = ["研究"]
    assert not parse_feed(rss(title="电池研究获得进展"), cfg.sources[0], cfg, now)[0]


def test_empty_results_never_generate_fake_news(cfg, now):
    with pytest.raises(EmptyDigest):
        create_digest(cfg, FakeHttp(rss(date="invalid")), now, "edition")


def test_html_error_page_not_counted_as_feed(cfg, now):
    with pytest.raises(CollectionError):
        collect(cfg, FakeHttp(b"<html><title>Login required</title></html>"), now)


def test_source_failure_retains_partial_coverage(cfg, now):
    cfg.data["sources"].append({**cfg.sources[0], "id": "bad", "name": "Bad", "url": "https://bad.example/rss"})
    client = FakeHttp()
    original = client.get
    from daily_news.network import NetworkError

    def get(url, headers=None):
        if "bad.example" in url:
            raise NetworkError("HTTP 503")
        return original(url, headers)
    client.get = get
    digest = create_digest(cfg, client, now, "edition")
    assert len(digest.stories) == 1
    assert digest.source_reports[1]["status"] == "error"
    assert "Bad" in digest.warnings[0]


def test_tracking_parameters_removed_but_article_identifiers_preserved():
    assert canonical_url("https://EXAMPLE.com/a?id=1&utm_source=x#top") == "https://example.com/a?id=1"
    assert canonical_url("https://example.com/a?id=1") != canonical_url("https://example.com/a?id=2")
    assert canonical_url("javascript:alert(1)") is None
    assert canonical_url("https://user:password@example.com") is None


def test_plain_text_drops_script_and_hidden_content():
    assert plain_text("<p>Hello</p><script>steal()</script><style>body{}</style><b>World</b>") == "Hello World"


def test_same_event_merged_and_distinct_versions_preserved(cfg, now, article):
    second = replace(article, id="two", url="https://second.example/story", source_id="two")
    stories = build_stories([article, second], cfg, now)
    assert len(stories) == 1 and len(stories[0].articles) == 2
    assert not same_event(replace(article, title="Company releases model 3.0"),
                          replace(second, title="Company releases model 4.0"))


def test_seen_articles_and_source_diversity(cfg, now, article):
    cfg.data["briefing"]["max_per_source"] = 1
    other = replace(article, id="two", title="Astronomy discovery", url="https://news.example/b")
    third = replace(other, id="three", title="Sports event", source_id="other", url="https://other.example/c")
    assert len(build_stories([article, other, third], cfg, now)) == 2
    assert not build_stories([article], cfg, now, seen={article.id})


def valid_ai(digest):
    return {"stories": [{"id": digest.stories[0].id,
                         "bullets": [{"text": "研究取得进展。", "sources": [digest.stories[0].articles[0].id]}]}]}


def test_ai_cannot_cite_an_unprovided_source(digest):
    value = valid_ai(digest)
    value["stories"][0]["bullets"][0]["sources"] = ["invented"]
    with pytest.raises(SynthesisError):
        validate_result(value, digest.stories)


def test_ai_must_cover_each_event_once(digest):
    value = valid_ai(digest)
    value["stories"] *= 2
    with pytest.raises(SynthesisError):
        validate_result(value, digest.stories)


def test_valid_ai_has_grounded_source_ids(cfg, digest, monkeypatch):
    cfg.data["ai"].update(enabled=True, model="test-model")
    monkeypatch.setenv("DAILY_NEWS_AI_KEY", "secret-key")
    response = {"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(valid_ai(digest))}}],
        "usage": {"total_tokens": 123, "untrusted_field": "secret"}}
    client = FakeHttp(post_response=(200, {}, json.dumps(response).encode()))
    usage = synthesize(digest.stories, cfg, client)
    assert digest.stories[0].mode == "ai_synthesis"
    assert usage == {"total_tokens": 123}
    assert len(client.post_calls) == 1


def test_ai_fallback_is_labelled_and_can_be_required(cfg, now, monkeypatch):
    cfg.data["ai"].update(enabled=True, model="test-model")
    monkeypatch.setenv("DAILY_NEWS_AI_KEY", "secret-key")
    client = FakeHttp(post_response=(503, {}, b"secret remote error"))
    digest = create_digest(cfg, client, now, "edition")
    assert digest.stories[0].mode == "source_excerpt"
    assert digest.warnings and "secret" not in digest.warnings[0]
    cfg.data["ai"]["require_success"] = True
    with pytest.raises(SynthesisError):
        create_digest(cfg, client, now, "edition")


def test_ai_truncated_response_not_accepted(cfg, digest, monkeypatch):
    cfg.data["ai"].update(enabled=True, model="test")
    monkeypatch.setenv("DAILY_NEWS_AI_KEY", "secret-key")
    client = FakeHttp(post_response=(200, {}, b'{"choices":[{"finish_reason":"length"}]}'))
    with pytest.raises(SynthesisError):
        synthesize(digest.stories, cfg, client)


def test_render_escapes_untrusted_text_and_produces_all_formats(digest, tmp_path):
    digest.title = '<img src=x onerror="alert(1)">'
    digest.stories[0].bullets[0]["text"] = "<script>alert(1)</script>"
    rendered = html_content(digest)
    assert "<script>" not in rendered and "<img src=x" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "[1](<https://news.example/a>)" in markdown_content(digest)
    files = write_artifacts(digest, tmp_path)
    assert set(files) == {"txt", "md", "html", "json"}
    from daily_news.models import Digest
    recovered = Digest.from_dict(json.loads((tmp_path / (digest.edition + ".json")).read_text(encoding="utf-8")))
    assert recovered.to_dict() == digest.to_dict()

def test_video_and_text_of_same_named_event_merge(article):
    first = replace(article, title="Watch as fire engulfs Hitler's mountaintop Eagle's Nest retreat")
    second = replace(article, id="other", url="https://news.example/video",
                     title="Man arrested after fire at Hitler's mountaintop Eagle's Nest retreat")
    assert same_event(first, second)


def test_different_locations_do_not_merge_on_generic_words(article):
    first = replace(article, title="Man arrested after fire at New York research center")
    second = replace(article, id="other", url="https://news.example/other",
                     title="Man arrested after fire at London university campus")
    assert not same_event(first, second)


def test_truncated_source_excerpt_is_marked(cfg, now, article):
    long_article = replace(article, excerpt="材料中的完整句子。" * 100)
    story = build_stories([long_article], cfg, now)[0]
    assert story.bullets[0]["text"].endswith("…")
    assert len(story.bullets[0]["text"]) <= 261

