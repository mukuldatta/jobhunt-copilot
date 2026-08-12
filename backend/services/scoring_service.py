"""
Shared scoring runner used by the manual trigger and the scheduler.

Free LLM tiers have both per-minute and per-day limits, so this paces requests
and backs off on rate limits instead of bursting into the wall. Jobs that fail
are left unscored (never saved as 0) so the next cycle retries them.
"""

import os
import asyncio
import logging

from agents.scorer_agent import ScorerAgent
from db.mongodb import get_unscored_jobs, update_job

logger = logging.getLogger(__name__)


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


async def run_scoring(limit: int = None) -> dict:
    """Score unscored jobs. Returns a summary dict."""
    delay = _f("SCORE_DELAY_SEC", 4)          # ~15 req/min — under free-tier RPM
    max_backoffs = int(_f("SCORE_MAX_BACKOFFS", 3))
    per_run = int(limit or _f("SCORE_PER_RUN", 60))

    agent = ScorerAgent()
    unscored = await get_unscored_jobs()
    queue = unscored[:per_run]
    scored = failed = 0
    backoffs = 0

    for job in queue:
        result = None
        try:
            result = await agent.score(job)
        except Exception as e:
            logger.error(f"Scoring error for {job.get('job_id')}: {e}")

        if result is None:
            # Rate limited (or a transient failure): wait and retry this job once.
            wait = agent.last_retry_after or (20 * (2 ** backoffs))
            backoffs += 1
            if backoffs > max_backoffs:
                logger.warning(
                    f"Scoring paused after {max_backoffs} backoffs — {scored} scored, "
                    f"{len(unscored) - scored} still unscored (retried next cycle)."
                )
                break
            logger.info(f"Rate limited; backing off {wait:.0f}s (attempt {backoffs})")
            await asyncio.sleep(min(wait, 120))
            try:
                result = await agent.score(job)
            except Exception:
                result = None
            if result is None:
                failed += 1
                continue

        backoffs = 0
        await update_job(job["job_id"], {
            "match_score": result["match_score"],
            "score_breakdown": result["score_breakdown"],
            "gap_analysis": result["gap_analysis"],
            # Which of the resume's skills the posting actually named — turns
            # the skills bar in Review from an assertion into evidence.
            "skills_matched": result.get("skills_matched", []),
        })
        scored += 1
        await asyncio.sleep(delay)

    remaining = max(len(unscored) - scored, 0)
    summary = {"scored": scored, "failed": failed, "remaining": remaining,
               "total_unscored_at_start": len(unscored)}
    logger.info(f"Scoring run complete: {summary}")
    print(f"Scoring run complete: {summary}")
    return summary
