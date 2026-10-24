"""Time-zone aware daily scheduling with a bounded same-day catch-up window."""
from datetime import timedelta
import logging
import time
from .config import validate_credentials
from .models import utc_now
from .pipeline import edition_id, run

logger = logging.getLogger("daily_news")


def is_due(cfg, now):
    local = now.astimezone(cfg.zone)
    hour, minute = map(int, cfg.section("briefing")["send_at"].split(":"))
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # On a spring-forward gap, the first local instant after the requested wall time is eligible.
    return target <= local < target + timedelta(hours=cfg.section("schedule")["catch_up_hours"])


def serve(cfg, *, once=False, clock=utc_now, sleeper=time.sleep, runner=run):
    validate_credentials(cfg, sending=True)
    last_status = None
    while True:
        now = clock()
        result = {"status": "not_due"}
        if is_due(cfg, now):
            if (last_status and last_status.get("status") == "complete"
                    and last_status.get("edition") == edition_id(cfg, now)):
                if once:
                    return last_status
                sleeper(30)
                continue
            try:
                result = runner(cfg, now=now)
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
        sleeper(30)
