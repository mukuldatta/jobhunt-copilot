"""
Autonomous apply orchestrator.

Ties the pipeline together: pick high-match, unapplied jobs -> apply to each via
ApplyAgent (which tailors + validates the resume, runs a headed session, and
pauses for manual CAPTCHA) -> stop when a guardrail says so.

Guardrails (all irreversibility protection — you can't un-apply). Each reads
from Setup > Agent rules if saved there, and from the env var named below
otherwise:
  - AUTO_APPLY_ENABLED must be truthy (master kill switch). dry_run/force bypass.
  - AUTO_APPLY_DAILY_CAP caps applications per calendar day.
  - AUTO_APPLY_MIN_SCORE gates which jobs qualify.
  - AUTO_APPLY_PER_RUN caps applications per cycle.
  - Platforms whose login has failed >= 5 times are skipped.
  - 3 consecutive login failures halt the cycle.
  - dry_run previews what WOULD be applied to, submitting nothing.
"""

import os
import random
import asyncio
import logging
from dotenv import load_dotenv

from db.mongodb import (
    get_apply_candidates, count_applications_today, record_run,
    release_stale_apply_claims,
)
from platforms import APPLY_DISABLED
from services import agent_state
from services.settings_service import get_agent_rules

load_dotenv()
logger = logging.getLogger(__name__)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


async def run_auto_apply_cycle(max_apply: int = None, dry_run: bool = False,
                               force: bool = False) -> dict:
    rules = await get_agent_rules()
    min_score = rules["min_score"]
    daily_cap = rules["daily_cap"]
    per_run = max_apply if max_apply is not None else rules["per_run"]
    region = rules["region"]

    # These three end the cycle before agent_state.start, so without a line here
    # pressing Run agent produces no visible effect at all — the reason it did
    # nothing is exactly what the log exists to answer.
    if not rules["auto_apply_enabled"] and not force and not dry_run:
        agent_state.log("run refused: auto-apply is off in Setup > Agent rules")
        return {"status": "disabled",
                "message": "Auto-apply is off. Turn it on in Setup > Agent rules "
                           "(or set AUTO_APPLY_ENABLED), or call with dry_run/force."}

    # Before choosing candidates, take back anything a previous run claimed and
    # never finished — otherwise a backend restart mid-apply quietly removes a
    # job from the pipeline permanently.
    try:
        freed = await release_stale_apply_claims()
        if freed:
            agent_state.log(f"returned {freed} abandoned claim(s) to the queue")
    except Exception as e:
        logger.warning(f"Could not release stale apply claims: {e}")

    applied_today = await count_applications_today()
    budget = max(0, daily_cap - applied_today)
    if budget <= 0 and not dry_run:
        agent_state.log(f"run refused: daily cap of {daily_cap} already reached")
        return {"status": "cap_reached", "applied_today": applied_today, "daily_cap": daily_cap,
                "message": f"Daily cap of {daily_cap} already reached."}

    take = per_run if dry_run else min(per_run, budget)
    candidates = await get_apply_candidates(min_score=min_score, region=region, limit=take,
                                            exclude_sources=list(APPLY_DISABLED))
    if not candidates:
        agent_state.log(f"run refused: nothing scoring {min_score}%+ in '{region}' "
                        f"is waiting on an apply-capable board")
        return {"status": "no_candidates",
                "message": f"No 'new' jobs with score >= {min_score} in region '{region}' "
                           f"on an apply-capable board."}

    if dry_run:
        preview = [{"title": j.get("title"), "company": j.get("company"),
                    "score": j.get("match_score"), "source": j.get("source")}
                   for j in candidates]
        # A dry run's whole purpose is to be looked at, and until now its answer
        # existed only in the response body of whoever called it.
        agent_state.log(f"dry run: {len(preview)} posting(s) would be applied to "
                        f"(cap {daily_cap}, {applied_today} used today)")
        for p in preview[:10]:
            agent_state.log(f"    would apply: {p['title']} @ {p['company']} "
                            f"· {p['source']} · {p['score']}%")
        if len(preview) > 10:
            agent_state.log(f"    …and {len(preview) - 10} more")
        return {"status": "dry_run", "would_apply": len(preview), "jobs": preview,
                "applied_today": applied_today, "daily_cap": daily_cap, "budget": budget}

    # Import here so a dry_run never needs Playwright/Chrome.
    from agents.apply_agent import ApplyAgent
    agent = ApplyAgent()

    agent_state.start("applying")
    try:
        summary = await _apply_all(agent, candidates, daily_cap)
    finally:
        agent_state.finish()

    # A background cycle has no caller to return to, so the outcome has to
    # outlive the process's stdout or nobody ever sees it.
    try:
        await record_run(summary)
    except Exception as e:
        logger.warning(f"Could not record run summary: {e}")
    return summary


async def _apply_all(agent, candidates: list, daily_cap: int) -> dict:
    results = {}
    log = []
    login_needed = set()   # platforms not signed in — skip their remaining jobs
    stuck = 0              # consecutive needs_review (unattended CAPTCHA, etc.)
    delay_min = _int("AUTO_APPLY_DELAY_MIN_SEC", 20)
    delay_max = _int("AUTO_APPLY_DELAY_MAX_SEC", 40)

    total = len(candidates)
    agent_state.log(f"{total} candidate(s) to work through")

    for i, job in enumerate(candidates, 1):
        source = job.get("source", "")
        if source in login_needed:
            results["login_required"] = results.get("login_required", 0) + 1
            log.append({"job": job.get("title"), "result": "login_required",
                        "msg": f"{source} not signed in — use the Login button"})
            agent_state.log(f"[{i}/{total}] skipped {job.get('title')} — {source} not signed in")
            continue

        # The narration is per-job rather than per-run because that is the unit
        # a watcher is waiting on: one posting can hold the browser for minutes,
        # and "3 of 5" is the difference between patience and pulling the plug.
        agent_state.log(f"[{i}/{total}] {job.get('title')} @ {job.get('company')} "
                        f"· {source} · {job.get('match_score', '—')}%")
        result = await agent.apply(job)
        st = result.get("status", "error")
        results[st] = results.get(st, 0) + 1
        log.append({"job": f"{job.get('title')} @ {job.get('company')}",
                    "result": st, "msg": result.get("message", "")})
        agent_state.log(f"    → {st}: {result.get('message', '')}")
        logger.info(f"AutoApply: [{st}] {job.get('title')} @ {job.get('company')} — {result.get('message','')}")

        if st == "deferred":
            # The quota wall is global, not per-job — every remaining candidate
            # would hit it too, and each attempt still costs a browser launch.
            # Stop; these jobs are still 'new' and come back on the next cycle.
            log.append({"result": "deferred",
                        "msg": f"LLM quota exhausted — {result.get('message', '')} "
                               f"Remaining jobs left for the next run."})
            logger.warning("AutoApply halted: LLM quota exhausted, jobs deferred")
            agent_state.log("halted: LLM quota exhausted — the rest wait for the next run")
            break

        if st == "login_required":
            # No live session for this platform; stop trying it this cycle.
            login_needed.add(source)
            continue

        # Repeated needs_review usually means an unattended bot check: each one
        # burns a full APPLY_HUMAN_TIMEOUT wait and fires an alert, so stop the
        # cycle rather than pausing on every remaining job.
        if st == "needs_review":
            stuck += 1
            if stuck >= 2:
                log.append({"result": "halted",
                            "msg": "2 consecutive applications needed manual review "
                                   "(likely an unattended CAPTCHA) — stopping cycle"})
                logger.warning("AutoApply halted: 2 consecutive needs_review")
                agent_state.log("halted: two applications in a row needed review "
                                "(usually an unattended CAPTCHA)")
                break
        else:
            stuck = 0

        # Say so, rather than going quiet for up to 40 seconds. An idle gap in a
        # live log reads as a hang, and this one is deliberate pacing.
        pause = random.uniform(delay_min, delay_max)
        if i < total:
            agent_state.log(f"    waiting {pause:.0f}s before the next posting")
        await asyncio.sleep(pause)

    summary = {
        "status": "done",
        "results": results,
        "applied_today_after": await count_applications_today(),
        "daily_cap": daily_cap,
        "log": log,
    }
    logger.info(f"AutoApply cycle complete: {results}")
    return summary
