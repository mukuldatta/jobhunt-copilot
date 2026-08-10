"""
Autonomous apply orchestrator.

Ties the pipeline together: pick high-match, unapplied jobs -> apply to each via
ApplyAgent (which tailors + validates the resume, runs a headed session, and
pauses for manual CAPTCHA) -> stop when a guardrail says so.

Guardrails (all irreversibility protection — you can't un-apply):
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

from db.mongodb import get_apply_candidates, count_applications_today, get_login_failures

load_dotenv()
logger = logging.getLogger(__name__)


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "y")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


async def run_auto_apply_cycle(max_apply: int = None, dry_run: bool = False,
                               force: bool = False) -> dict:
    min_score = _int("AUTO_APPLY_MIN_SCORE", 70)
    daily_cap = _int("AUTO_APPLY_DAILY_CAP", 20)
    per_run = max_apply if max_apply is not None else _int("AUTO_APPLY_PER_RUN", 5)
    region = os.environ.get("AUTO_APPLY_REGION", "india")

    if not _truthy("AUTO_APPLY_ENABLED") and not force and not dry_run:
        return {"status": "disabled",
                "message": "AUTO_APPLY_ENABLED is not set. Set it, or call with dry_run/force."}

    applied_today = await count_applications_today()
    budget = max(0, daily_cap - applied_today)
    if budget <= 0 and not dry_run:
        return {"status": "cap_reached", "applied_today": applied_today, "daily_cap": daily_cap,
                "message": f"Daily cap of {daily_cap} already reached."}

    take = per_run if dry_run else min(per_run, budget)
    candidates = await get_apply_candidates(min_score=min_score, region=region, limit=take)
    if not candidates:
        return {"status": "no_candidates",
                "message": f"No 'new' jobs with score >= {min_score} in region '{region}'."}

    if dry_run:
        preview = [{"title": j.get("title"), "company": j.get("company"),
                    "score": j.get("match_score"), "source": j.get("source")}
                   for j in candidates]
        return {"status": "dry_run", "would_apply": len(preview), "jobs": preview,
                "applied_today": applied_today, "daily_cap": daily_cap, "budget": budget}

    # Import here so a dry_run never needs Playwright/Chrome.
    from agents.apply_agent import ApplyAgent
    agent = ApplyAgent()

    results = {}
    log = []
    consecutive_login_fail = 0
    delay_min = _int("AUTO_APPLY_DELAY_MIN_SEC", 20)
    delay_max = _int("AUTO_APPLY_DELAY_MAX_SEC", 40)

    for job in candidates:
        source = job.get("source", "")
        if source and await get_login_failures(source) >= 5:
            results["skipped_platform"] = results.get("skipped_platform", 0) + 1
            log.append({"job": job.get("title"), "result": "skipped_platform",
                        "msg": f"{source} login disabled after repeated failures"})
            continue

        result = await agent.apply(job)
        st = result.get("status", "error")
        results[st] = results.get(st, 0) + 1
        log.append({"job": f"{job.get('title')} @ {job.get('company')}",
                    "result": st, "msg": result.get("message", "")})
        logger.info(f"AutoApply: [{st}] {job.get('title')} @ {job.get('company')} — {result.get('message','')}")

        if st == "login_failed":
            consecutive_login_fail += 1
            if consecutive_login_fail >= 3:
                log.append({"result": "halted", "msg": "3 consecutive login failures — stopping cycle"})
                break
        else:
            consecutive_login_fail = 0

        await asyncio.sleep(random.uniform(delay_min, delay_max))

    summary = {
        "status": "done",
        "results": results,
        "applied_today_after": await count_applications_today(),
        "daily_cap": daily_cap,
        "log": log,
    }
    logger.info(f"AutoApply cycle complete: {results}")
    return summary
