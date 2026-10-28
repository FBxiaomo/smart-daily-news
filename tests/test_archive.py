from dataclasses import replace
from datetime import timedelta, timezone
import json

import pytest

from daily_news.archive import (
    ArchiveError, delete_history, favorite, get_history, list_favorites,
    list_history, remove_favorite, save_preview, weekly_digest,
)
from daily_news.models import Story
from daily_news.state import State


@pytest.fixture
def state(tmp_path):
    instance = State(tmp_path / "archive")
    try:
        yield instance
    finally:
        instance.close()


def edition_row(state, digest, statuses):
    state.db.execute("INSERT INTO editions VALUES (?,?,?,?)",
                     (digest.edition, "fingerprint", json.dumps(digest.to_dict(), ensure_ascii=False),
                      digest.created_at))
    for index, status in enumerate(statuses):
        state.db.execute("INSERT INTO deliveries(edition,channel,target,status,updated_at) VALUES(?,?,?,?,?)",
                         (digest.edition, f"channel{index}", "fingerprint", status, digest.created_at))


def test_old_database_gets_archive_tables_without_changing_schema_version(state, digest):
    assert state.db.execute("PRAGMA user_version").fetchone()[0] == 1
    history_id = save_preview(state, digest)
    assert history_id.startswith("preview:")
    assert get_history(state, history_id) == (digest, "preview")
    assert state.db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_history_contains_previews_and_editions_and_searches_actual_content(state, digest):
    preview_id = save_preview(state, digest)
    edition_row(state, digest, ["sent"])
    assert {row["id"] for row in list_history(state)} == {preview_id, "edition:" + digest.edition}
    assert len(list_history(state, "battery")) == 2
    assert list_history(state, "not present anywhere") == []
    # Search values, not internal JSON field names.
    assert list_history(state, "source_reports") == []
    assert get_history(state, "edition:" + digest.edition) == (digest, "edition")
    assert {row["status"] for row in list_history(state)} == {"preview", "sent"}


def test_history_search_escapes_sql_wildcards(state, digest):
    digest.title = "AI report 100% with a_b"
    save_preview(state, digest)
    assert len(list_history(state, "100%")) == 1
    assert len(list_history(state, "a_b")) == 1
    assert list_history(state, "100_") == []
    assert list_history(state, "a%b") == []


def test_history_limit_and_invalid_input_are_checked(state, digest):
    save_preview(state, digest)
    with pytest.raises(ArchiveError):
        list_history(state, limit=0)
    with pytest.raises(ArchiveError):
        list_history(state, limit=501)
    with pytest.raises(ArchiveError):
        list_history(state, query=None)
    with pytest.raises(ArchiveError):
        get_history(state, "bad-id")
    with pytest.raises(ArchiveError):
        save_preview(state, digest, kind="other")


def test_favorites_are_independent_and_deduplicate_by_canonical_url(state, digest, now):
    preview_id = save_preview(state, digest)
    first_id = favorite(state, preview_id, digest.stories[0].articles[0].id, now)
    second_id = favorite(state, preview_id, digest.stories[0].articles[0].id, now)
    assert first_id == second_id
    assert len(list_favorites(state)) == 1
    assert len(list_favorites(state, query="battery")) == 1
    assert delete_history(state, preview_id)
    saved = list_favorites(state)[0]
    assert saved["article"]["url"] == digest.stories[0].articles[0].url
    assert saved["story_title"] == digest.stories[0].title
    assert remove_favorite(state, first_id)
    assert not remove_favorite(state, first_id)
    assert list_favorites(state) == []


def test_cannot_favorite_missing_article_or_naive_time(state, digest, now):
    preview_id = save_preview(state, digest)
    with pytest.raises(ArchiveError, match="不在"):
        favorite(state, preview_id, "not-in-digest", now)
    with pytest.raises(ArchiveError, match="时区"):
        favorite(state, preview_id, digest.stories[0].articles[0].id, now.replace(tzinfo=None))


@pytest.mark.parametrize("status", ["pending", "failed", "uncertain", "sending"])
def test_outstanding_delivery_snapshot_cannot_be_deleted(state, digest, status):
    edition_row(state, digest, ["sent", status])
    record = list_history(state)[0]
    assert not record["can_delete"]
    with pytest.raises(ArchiveError, match="不能清理"):
        delete_history(state, record["id"])
    assert get_history(state, record["id"])[0] == digest


def test_unknown_delivery_status_cannot_be_deleted(state, digest):
    edition_row(state, digest, [])
    with pytest.raises(ArchiveError):
        delete_history(state, "edition:" + digest.edition)


def test_sent_edition_can_be_cleared_but_completion_tombstone_remains(state, digest, now):
    edition_row(state, digest, ["sent", "sent"])
    history_id = "edition:" + digest.edition
    bookmark = favorite(state, history_id, digest.stories[0].articles[0].id, now)
    assert delete_history(state, history_id)
    assert not delete_history(state, history_id)
    assert list_history(state) == []
    assert state.db.execute("SELECT status FROM jobs WHERE edition=?", (digest.edition,)).fetchone()[0] == "complete"
    assert len(list_favorites(state)) == 1
    assert list_favorites(state)[0]["id"] == bookmark


def make_digest(base, edition, created, articles):
    story = Story("story-" + edition, articles[0].title, articles[0].category, articles,
                  [{"text": articles[0].excerpt, "sources": [articles[0].id]}])
    return replace(base, edition=edition, created_at=created.isoformat(), stories=[story])


def test_weekly_deduplicates_url_and_ranks_cross_source_coverage(state, cfg, digest, article, now):
    a = replace(article, title="AI battery research breakthrough", url="https://one.example/item?id=1",
                source_id="one", id="a1")
    b = replace(a, url="https://two.example/item", source_id="two", source_name="Source Two", id="b1")
    c = replace(a, title="A different economic story", url="https://three.example/item",
                source_id="three", id="c1")
    day1 = make_digest(digest, "daily-one", now - timedelta(days=1), [a, b])
    day2 = make_digest(digest, "daily-two", now - timedelta(days=2), [a, c])
    save_preview(state, day1)
    edition_row(state, day2, ["sent"])
    weekly, metrics = weekly_digest(state, cfg, now, "weekly-2026-10-25")
    assert metrics["snapshots"] == 2
    assert metrics["articles"] == 3 and metrics["events"] == 2
    assert metrics["sources"] == 3 and metrics["multi_source_events"] == 1
    assert weekly.stories[0].title == a.title
    assert {item.source_id for item in weekly.stories[0].articles} == {"one", "two"}
    assert metrics["event_metrics"][0]["sources"] == 2
    assert "不代表全网热度" in weekly.warnings[0]


def test_weekly_window_uses_local_snapshots_last_168_hours_and_excludes_future(state, cfg, digest, article, now):
    at_boundary = make_digest(digest, "at-boundary", now - timedelta(days=7), [article])
    before = make_digest(digest, "before", now - timedelta(days=7, seconds=1),
                         [replace(article, url="https://two.example/old", id="old")])
    future = make_digest(digest, "future", now + timedelta(seconds=1),
                         [replace(article, url="https://two.example/future", id="future")])
    save_preview(state, at_boundary)
    save_preview(state, before)
    save_preview(state, future)
    local_now = now.astimezone(timezone(timedelta(hours=8)))
    result, metrics = weekly_digest(state, cfg, local_now, "weekly-window")
    assert metrics["snapshots"] == metrics["articles"] == 1
    assert result.stories[0].articles[0].url == article.url


def test_weekly_records_are_not_used_even_after_weekly_preview_is_deleted(state, cfg, digest, article, now):
    daily = make_digest(digest, "daily-now", now, [article])
    save_preview(state, daily)
    weekly = make_digest(digest, "weekly-previous", now, [
        replace(article, title="Old weekly-only topic", url="https://weekly.example/item", id="weekly-only")
    ])
    preview_id = save_preview(state, weekly, "weekly")
    edition_row(state, weekly, ["sent"])
    assert delete_history(state, preview_id)
    with pytest.raises(ArchiveError):
        get_history(state, preview_id)
    result, metrics = weekly_digest(state, cfg, now, "weekly-current")
    assert metrics["snapshots"] == 1 and metrics["articles"] == 1
    assert [story.title for story in result.stories] == [article.title]


def test_weekly_requires_real_data_and_valid_edition(state, cfg, now):
    with pytest.raises(ArchiveError, match="没有可用"):
        weekly_digest(state, cfg, now, "weekly-empty")
    with pytest.raises(ArchiveError, match="weekly-"):
        weekly_digest(state, cfg, now, "daily-invalid")
    with pytest.raises(ArchiveError, match="时区"):
        weekly_digest(state, cfg, now.replace(tzinfo=None), "weekly-naive")


def test_weekly_saves_roundtrip_preview_and_copies_presentation(state, cfg, digest, now):
    save_preview(state, digest)
    cfg.data["presentation"] = {"layout": "cards", "theme": "dark", "density": "brief"}
    result, metrics = weekly_digest(state, cfg, now, "weekly-today")
    assert result.presentation == cfg.data["presentation"]
    preview_id = save_preview(state, result, "weekly")
    loaded, kind = get_history(state, preview_id)
    assert kind == "weekly" and loaded.to_dict() == result.to_dict()
    assert metrics["events"] == 1


def test_weekly_metrics_disclose_truncated_source_display(state, cfg, digest, article, now):
    articles = [replace(article, id=f"id-{i}", source_id=f"source-{i}",
                        url=f"https://source{i}.example/article") for i in range(13)]
    save_preview(state, make_digest(digest, "daily-many", now, articles))
    result, metrics = weekly_digest(state, cfg, now, "weekly-many")
    assert metrics["event_metrics"][0] == {
        "story_id": result.stories[0].id, "sources": 13, "articles": 13, "displayed_articles": 12
    }
    assert len(result.stories[0].articles) == 12
    assert any("12 篇" in warning for warning in result.warnings)

def test_actual_sent_weekly_without_saved_preview_is_not_counted(state, cfg, digest, article, now):
    save_preview(state, digest)
    previous, _ = weekly_digest(state, cfg, now, "weekly-previous")
    edition_row(state, previous, ["sent"])
    _, metrics = weekly_digest(state, cfg, now, "weekly-next")
    assert metrics["snapshots"] == 1 and metrics["articles"] == 1


def test_normal_daily_profile_with_weekly_prefix_is_not_excluded(state, cfg, digest, now):
    normal = replace(digest, edition="weekly-daily-profile")
    edition_row(state, normal, ["sent"])
    _, metrics = weekly_digest(state, cfg, now, "weekly-next")
    assert metrics["snapshots"] == 1 and metrics["articles"] == 1

