"""Time-zone aware daily scheduling with a bounded same-day catch-up window."""
from datetime import timedelta
import logging
import time
from .config import validate_credentials
from .models import utc_now
from .pipeline import edition_id, run
from .state import run_lock

logger = logging.getLogger("daily_news")


def due_slot(cfg, now):
    local = now.astimezone(cfg.zone)
    options = cfg.section("schedule")
    if local.isoweekday() not in options.get("weekdays", list(range(1, 8))):
        return None
    slots = options.get("times") or [cfg.section("briefing")["send_at"]]
    due = []
    for slot in slots:
        hour, minute = map(int, slot.split(":"))
        target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= local < target + timedelta(hours=options["catch_up_hours"]):
            due.append(slot)
    # Recover only the latest eligible slot after downtime, avoiding a burst of old digests.
    return max(due) if due else None


def is_due(cfg, now):
    return due_slot(cfg, now) is not None


def serve(cfg, *, once=False, clock=utc_now, sleeper=time.sleep, runner=run, stop_event=None):
    validate_credentials(cfg, sending=True)
    last_status = None
    # One active scheduler per state directory, even across application processes.
    with run_lock(cfg.state_dir, "scheduler.lock"):
        while not stop_event or not stop_event.is_set():
            now = clock()
            result = {"status": "not_due"}
            slot = due_slot(cfg, now)
            if slot:
                current_edition = edition_id(cfg, now, slot)
                if (last_status and last_status.get("status") == "complete"
                        and last_status.get("edition") == current_edition):
                    if once:
                        return last_status
                    if stop_event:
                        stop_event.wait(30)
                    else:
                        sleeper(30)
                    continue
                try:
                    result = runner(cfg, now=now, edition=current_edition)
                except Exception as exc:
                    # Daemon survives transient failures; state controls retry deadlines and counts.
                    result = {"status": "error", "error_type": type(exc).__name__}
                    if result != last_status:
                        logger.error("schedule_failed type=%s", type(exc).__name__)
            if result != last_status:
                logger.info("schedule_status %s", result.get("status"))
                last_status = result
            if once:
                return result
            if stop_event:
                stop_event.wait(30)
            else:
                sleeper(30)
    return {"status": "stopped"}
