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

And it keeps the narration: the running commentary of what the agent is doing
step by step, which until now existed only as print() on the backend's stdout.
That is the wrong place for it. A run drives a real browser and can pause for
minutes on one posting, and the person who needs to know why is looking at
Today, not at the terminal the server happens to be running in — which, when
the backend is started detached, nobody is watching at all.
"""

import sys
from collections import deque
from datetime import datetime, timedelta

# "idle" | "running" | "paused" — paused means a run is up but blocked on a human.
_state = {
    "state": "idle",
    "phase": None,          # short verb shown after the state: "scraping", "applying", "scoring"
    "started_at": None,
    "human_required": None,  # {platform, reason, job_title, since} while blocked
    "job": "",              # "Title @ Company" the run is on right now
}

# The narration. Bounded, because a long run with a chatty modal produces
# hundreds of lines and this must never be the reason the process grows.
#
# Deliberately NOT cleared when a run starts. The lines you most want are the
# ones explaining a run that has already finished — "no answer for: expected
# CTC", "the modal did not open" — and a buffer that empties on the next start
# would throw them away at the moment they became useful.
_LOG_MAX = 400
_log = deque(maxlen=_LOG_MAX)
_seq = 0


def _iso(dt):
    return dt.isoformat() if isinstance(dt, datetime) else dt


def log(msg: str, job: str = "") -> None:
    """
    Record one line of what the agent is doing, and print it.

    The single sink for narration: it still reaches stdout exactly as before, so
    nothing that was readable in a terminal stops being readable, and it is now
    also readable from the app. Call sites pass their message indented for the
    terminal; the stored copy is stripped, because the UI aligns its own rows
    and leading spaces would just render as a ragged left edge.
    """
    global _seq
    _seq += 1
    _log.append({
        "seq": _seq,
        "at": datetime.utcnow().isoformat(),
        "job": job or "",
        "msg": (msg or "").strip(),
    })
    try:
        print(msg)
    except UnicodeEncodeError:
        # A Windows console is cp1252, and these lines quote text scraped off a
        # job posting — a question with a rupee sign, a company name in
        # Devanagari, an emoji in a bullet. print() raising on that would
        # propagate out of whatever step was narrating and abort the apply,
        # which is a preposterous way to lose an application. The record above
        # is already safe; only the terminal copy needs flattening.
        enc = (sys.stdout.encoding or "ascii")
        print(msg.encode(enc, "replace").decode(enc, "replace"))


def tail(since: int = 0) -> dict:
    """
    The narration after `since`, for a poller that already has the earlier lines.

    Returns `seq` so the caller can ask for exactly what it is missing next
    time. If the buffer has rolled past what the caller last saw, it simply
    receives everything still held — a gap in a log is not worth an error path.
    """
    lines = [l for l in _log if l["seq"] > since]
    return {
        "lines": lines,
        "seq": _seq,
        "state": _state["state"],
        "phase": _state["phase"],
        "job": _state["job"],
    }


def set_job(name: str):
    """Name the posting the agent is on, so the log header can say so."""
    _state["job"] = name or ""


def start(phase: str = "applying"):
    _state.update(state="running", phase=phase, started_at=datetime.utcnow())
    log(f"—— {phase} run started ——")


def set_phase(phase: str):
    if _state["state"] == "running":
        _state["phase"] = phase


def finish():
    phase = _state["phase"]
    _state.update(state="idle", phase=None, started_at=None, human_required=None, job="")
    log(f"—— {phase or 'run'} finished ——")


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
    from db.mongodb import count_applications_today, get_last_run
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

    try:
        last_run = await get_last_run()
    except Exception:
        last_run = {}
    if last_run.get("finished_at"):
        last_run = {"finished_at": _iso(last_run["finished_at"]),
                    "status": last_run.get("status"),
                    "results": last_run.get("results", {}),
                    "log": last_run.get("log", [])[-6:]}

    next_run = _next_scheduled_run()
    return {
        "state": _state["state"],
        "phase": _state["phase"],
        "started_at": _iso(_state["started_at"]),
        "next_run_at": _iso(next_run),
        "applied_today": applied_today,
        "daily_cap": rules["daily_cap"],
        "human_required": human,
        "last_run": last_run or None,
    }
