"""
In-process record of what the agent is doing right now.

The redesigned shell shows a live status line ("Agent running · applying") and
drives three animations off it, so the frontend needs an answer to "is a run in
flight?" that is cheaper and more truthful than inferring it from /stats. A run
lives inside one FastAPI process as a BackgroundTask, so a module-level record
is enough — nothing here survives a restart, which is correct: a run doesn't
either.

Also tracks the one blocking condition the UI surfaces on Today: an application
paused waiting for a human to clear a CAPTCHA / 2FA in the open browser window.
"""

from datetime import datetime, timedelta

# "idle" | "running" | "paused" — paused means a run is up but blocked on a human.
_state = {
    "state": "idle",
    "phase": None,          # short verb shown after the state: "scraping", "applying", "scoring"
    "started_at": None,
    "human_required": None,  # {platform, reason, job_title, since} while blocked
}


def _iso(dt):
    return dt.isoformat() if isinstance(dt, datetime) else dt


def start(phase: str = "applying"):
    _state.update(state="running", phase=phase, started_at=datetime.utcnow())


def set_phase(phase: str):
    if _state["state"] == "running":
        _state["phase"] = phase


def finish():
    _state.update(state="idle", phase=None, started_at=None, human_required=None)


def begin_human_wait(platform: str, reason: str, job_title: str = ""):
    _state["human_required"] = {
        "platform": platform,
        "reason": reason,
        "job_title": job_title,
        "since": datetime.utcnow(),
    }
    if _state["state"] == "running":
        _state["state"] = "paused"


def end_human_wait():
    _state["human_required"] = None
    if _state["state"] == "paused":
        _state["state"] = "running"


def _next_scheduled_run():
    """Earliest next_run_time across the scheduler's jobs, as a datetime."""
    try:
        from services.scheduler import scheduler
        if not scheduler.running:
            return None
        times = [j.next_run_time for j in scheduler.get_jobs() if j.next_run_time]
        return min(times) if times else None
    except Exception:
        return None


async def snapshot() -> dict:
    """Everything the sidebar's agent strip renders, in one call."""
    from db.mongodb import count_applications_today
    from services.settings_service import get_agent_rules

    rules = await get_agent_rules()
    try:
        applied_today = await count_applications_today()
    except Exception:
        applied_today = 0

    human = _state["human_required"]
    if human:
        human = {**human, "since": _iso(human["since"]),
                 "waiting_seconds": int((datetime.utcnow() - _state["human_required"]["since"]).total_seconds())}

    next_run = _next_scheduled_run()
    return {
        "state": _state["state"],
        "phase": _state["phase"],
        "started_at": _iso(_state["started_at"]),
        "next_run_at": _iso(next_run),
        "applied_today": applied_today,
        "daily_cap": rules["daily_cap"],
        "human_required": human,
    }
