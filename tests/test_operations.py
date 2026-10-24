from datetime import datetime, timedelta, timezone
import os
import pytest
from daily_news.config import ConfigError, initialize, load_config, load_env
from daily_news.delivery import DeliveryError
from daily_news.pipeline import run
from daily_news.scheduler import is_due, serve
from daily_news.state import State, StateError, run_lock
from conftest import FakeHttp, rss


def test_init_never_overwrites_config_or_secrets(tmp_path):
    path = tmp_path / "config.toml"
    assert len(initialize(path)) == 2
    path.write_text("my configuration", encoding="utf-8")
    (tmp_path / ".env").write_text("PRIVATE=keep", encoding="utf-8")
    assert initialize(path) == []
    assert path.read_text() == "my configuration"
    assert (tmp_path / ".env").read_text() == "PRIVATE=keep"


def test_env_does_not_override_process_and_preserves_equals(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_EXISTING", "from-process")
    monkeypatch.delenv("MY_NEW", raising=False)
    env = tmp_path / "env"
    env.write_text('MY_EXISTING=from-file\nMY_NEW="a=b#c"\n', encoding="utf-8")
    load_env(env)
    assert os.environ["MY_EXISTING"] == "from-process"
    assert os.environ["MY_NEW"] == "a=b#c"


@pytest.mark.parametrize("replacement", ['send_at = "29:00"', 'timezone = "Invalid/Zone"',
                                        'max_stories = 0', 'unknown_field = true'])
def test_invalid_config_rejected(tmp_path, replacement):
    path = tmp_path / "config.toml"
    path.write_text("[briefing]\n" + replacement, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_paths_are_relative_to_config_not_cwd(cfg, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    assert cfg.state_dir == cfg.path.parent / "data"


def test_same_edition_sent_only_once(sending_cfg, now):
    calls, client = [], FakeHttp()
    def sender(channel, *args):
        calls.append(channel)
    first = run(sending_cfg, now=now, client=client, sender=sender)
    second = run(sending_cfg, now=now + timedelta(minutes=30), client=client, sender=sender)
    assert first["status"] == second["status"] == "complete"
    assert calls == ["email", "webhook"] and client.get_calls == 1


def test_only_failed_channel_retried_from_original_snapshot(sending_cfg, now):
    calls, client = [], FakeHttp()
    def sender(channel, digest, *args):
        calls.append((channel, digest.to_dict()))
        if channel == "webhook" and len(calls) == 2:
            raise DeliveryError("limited", retryable=True)
    first = run(sending_cfg, now=now, client=client, sender=sender)
    assert first["channels"] == {"email": "sent", "webhook": "failed"}
    run(sending_cfg, now=now + timedelta(minutes=1), client=client, sender=sender)
    assert len(calls) == 2
    last = run(sending_cfg, now=now + timedelta(minutes=16), client=client, sender=sender)
    assert last["status"] == "complete"
    assert [c[0] for c in calls] == ["email", "webhook", "webhook"]
    assert calls[1][1] == calls[2][1] and client.get_calls == 1


def test_uncertain_delivery_requires_explicit_resolution(sending_cfg, now):
    calls, client = [], FakeHttp()
    def sender(channel, *args):
        calls.append(channel)
        if channel == "webhook":
            raise DeliveryError("no acknowledgement", uncertain=True)
    result = run(sending_cfg, now=now, client=client, sender=sender)
    run(sending_cfg, now=now + timedelta(hours=2), client=client, sender=sender, manual_retry=True)
    assert calls == ["email", "webhook"]
    state = State(sending_cfg.state_dir)
    state.resolve(result["edition"], "webhook", "delivered", now, "接收端已确认")
    assert state.deliveries(result["edition"])[1]["status"] == "sent"
    assert state.seen_articles(now, 3)
    state.close()


def test_crashed_adapter_is_not_automatically_resent(sending_cfg, now):
    def crash(*args):
        raise RuntimeError("adapter bug")
    with pytest.raises(RuntimeError):
        run(sending_cfg, now=now, client=FakeHttp(), sender=crash)
    calls = []
    result = run(sending_cfg, now=now, client=FakeHttp(), sender=lambda c, *a: calls.append(c))
    assert calls == ["webhook"]
    assert result["channels"]["email"] == "uncertain"


def test_process_interruption_recovered_as_uncertain(sending_cfg, now):
    calls, client = [], FakeHttp()
    def interrupt(*args):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run(sending_cfg, now=now, client=client, sender=interrupt)
    result = run(sending_cfg, now=now, client=client, sender=lambda c, *a: calls.append(c))
    assert calls == ["webhook"]
    assert result["channels"]["email"] == "uncertain"


def test_changed_destination_cannot_redirect_a_saved_retry(sending_cfg, now, monkeypatch):
    run(sending_cfg, now=now, client=FakeHttp(), sender=lambda *a: None)
    monkeypatch.setenv("DAILY_NEWS_WEBHOOK_URL", "https://different.example/hook")
    with pytest.raises(StateError, match="目标"):
        run(sending_cfg, now=now, client=FakeHttp(), sender=lambda *a: pytest.fail("must not send"))


def test_generation_retries_are_bounded_and_manual_retry_recovers(sending_cfg, now):
    from daily_news.pipeline import EmptyDigest
    bad = FakeHttp(rss(date="invalid"))
    for i in range(3):
        with pytest.raises(EmptyDigest):
            run(sending_cfg, now=now + timedelta(minutes=16 * i), client=bad)
    result = run(sending_cfg, now=now + timedelta(hours=1), client=bad)
    assert result["status"] == "deferred" and bad.get_calls == 3
    result = run(sending_cfg, now=now + timedelta(hours=1), client=FakeHttp(),
                 sender=lambda *a: None, manual_retry=True)
    assert result["status"] == "complete"


def test_backup_keeps_durable_outbox(sending_cfg, now, tmp_path):
    result = run(sending_cfg, now=now, client=FakeHttp(), sender=lambda *a: None)
    state = State(sending_cfg.state_dir)
    backup = tmp_path / "backup" / "daily-news.sqlite3"
    state.backup(backup)
    with pytest.raises(StateError):
        state.backup(backup)
    state.close()
    restored = State(backup.parent)
    assert restored.get_digest(result["edition"])
    assert all(r["status"] == "sent" for r in restored.deliveries(result["edition"]))
    restored.close()


def test_concurrent_job_is_rejected(tmp_path):
    with run_lock(tmp_path):
        with pytest.raises(StateError):
            with run_lock(tmp_path):
                pytest.fail("second worker entered")


def test_timezone_and_bounded_catchup(cfg):
    assert not is_due(cfg, datetime(2026, 10, 24, 23, 59, tzinfo=timezone.utc))
    assert is_due(cfg, datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc))
    assert not is_due(cfg, datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc))


def test_dst_gap_is_caught_up_and_fold_uses_same_edition(cfg):
    from daily_news.pipeline import edition_id
    cfg.data["briefing"].update(timezone="America/New_York", send_at="02:30")
    assert is_due(cfg, datetime(2026, 3, 8, 7, 1, tzinfo=timezone.utc))
    cfg.data["briefing"]["send_at"] = "01:30"
    a = datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)
    b = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)
    assert is_due(cfg, a) and is_due(cfg, b)
    assert edition_id(cfg, a) == edition_id(cfg, b)


def test_scheduler_once_checks_due_without_sleep(sending_cfg, now):
    calls = []
    result = serve(sending_cfg, once=True, clock=lambda: now,
                   runner=lambda *a, **k: calls.append(k["now"]) or {"status": "complete"},
                   sleeper=lambda *a: pytest.fail("should not sleep"))
    assert result["status"] == "complete" and calls == [now]
