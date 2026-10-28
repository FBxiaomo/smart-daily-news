from datetime import datetime, timezone
import pytest
from daily_news.config import ConfigError, load_config
from daily_news.pipeline import edition_id
from daily_news.scheduler import due_slot, serve


def test_weekdays_apply_in_configured_local_timezone(cfg):
    cfg.data["schedule"]["weekdays"] = [1, 2, 3, 4, 5]
    # Sunday 23:30 UTC is already Monday 07:30 in Shanghai.
    assert due_slot(cfg, datetime(2026, 10, 25, 23, 30, tzinfo=timezone.utc)) is None
    assert due_slot(cfg, datetime(2026, 10, 26, 0, 1, tzinfo=timezone.utc)) == "08:00"
    # Saturday 08:00 local is not a selected workday.
    assert due_slot(cfg, datetime(2026, 10, 23, 0, 5, tzinfo=timezone.utc)) == "08:00"
    assert due_slot(cfg, datetime(2026, 10, 24, 0, 5, tzinfo=timezone.utc)) is None


def test_multiple_times_have_distinct_editions_and_latest_catchup(cfg):
    cfg.data["schedule"]["times"] = ["09:00", "08:00", "18:30"]
    local_0930 = datetime(2026, 10, 26, 1, 30, tzinfo=timezone.utc)
    assert due_slot(cfg, local_0930) == "09:00"
    assert edition_id(cfg, local_0930, "08:00") == "daily-2026-10-26-0800"
    assert edition_id(cfg, local_0930, "09:00") == "daily-2026-10-26-0900"
    assert edition_id(cfg, local_0930) == "daily-2026-10-26-manual"


def test_scheduler_passes_slot_edition_to_runner(sending_cfg):
    sending_cfg.data["schedule"]["times"] = ["08:00", "18:00"]
    sent = []
    now = datetime(2026, 10, 26, 10, 1, tzinfo=timezone.utc)
    result = serve(sending_cfg, once=True, clock=lambda: now,
                   runner=lambda cfg, **kwargs: sent.append(kwargs) or {"status": "complete"})
    assert result["status"] == "complete"
    assert sent[0]["edition"] == "daily-2026-10-26-1800"


@pytest.mark.parametrize("entry", [
    'times = ["08:00", "08:00"]',
    'times = ["25:00"]',
    'weekdays = []',
    'weekdays = [0, 8]',
    'weekdays = [true]',
])
def test_invalid_schedule_config_is_rejected(tmp_path, entry):
    path = tmp_path / "config.toml"
    path.write_text("[schedule]\\n" + entry, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)

def test_scheduler_is_singleton_across_processes(sending_cfg, now):
    from daily_news.state import StateError, run_lock
    with run_lock(sending_cfg.state_dir, "scheduler.lock"):
        with pytest.raises(StateError):
            serve(sending_cfg, once=True, clock=lambda: now, runner=lambda *a, **k: {})


def test_stop_event_stops_without_executing_jobs(sending_cfg, now):
    import threading
    event = threading.Event()
    event.set()
    result = serve(sending_cfg, clock=lambda: now, stop_event=event,
                   runner=lambda *a, **k: pytest.fail("stopped scheduler must not run"))
    assert result == {"status": "stopped"}


def test_cleaned_completed_edition_cannot_be_regenerated(sending_cfg, now):
    from daily_news import archive
    from daily_news.pipeline import run
    from daily_news.state import State
    from conftest import FakeHttp

    first = run(sending_cfg, now=now, client=FakeHttp(), sender=lambda *a: None)
    state = State(sending_cfg.state_dir)
    try:
        assert archive.delete_history(state, "edition:" + first["edition"])
    finally:
        state.close()
    result = run(sending_cfg, now=now, edition=first["edition"], client=FakeHttp(),
                 sender=lambda *a: pytest.fail("must not resend"), manual_retry=True)
    assert result["status"] == "deferred"

