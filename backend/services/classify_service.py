"""
Deciding whether a posting can be submitted to, without submitting to it.

The apply queue only offers jobs known to take an in-platform application, so a
job nobody has classified is a job the agent will never look at. That answer
used to be produced only as a side effect of trying to apply — inside a capped
batch, at the cost of a browser launch, and often after a tailored resume had
already been written for a posting that turned out to hand off to the
employer's careers site.

This settles it separately and cheaply:

  - LinkedIn costs no browser at all. The guest jobPosting endpoint marks an
    off-site posting with an "offsite-apply" glyph, which is the same signal the
    scraper already reads for freshly scraped jobs.
  - Naukri needs the page, so it reuses the scrape profile's headed browser and
    reads the same "Apply on company site" markers the apply agent trusts.

Only apply_type_hint is written. apply_type stays the property of an actual
apply run, so a wrong guess here can be corrected by one, and neither value is
ever inferred from the other.
"""

import asyncio
import logging
import os
import random
import re

import httpx

from db.mongodb import get_db, update_job
from platforms import NAUKRI_SCRAPE_PROFILE

logger = logging.getLogger(__name__)

# Same headers the scraper uses; the guest endpoint refuses an empty UA.
from agents.scraper_agent import HEADERS, PROFILE_ROOT

_LINKEDIN_ID = re.compile(r"/jobs/view/(?:.*-)?(\d+)")

NAUKRI_EXTERNAL = ('#company-site-button', 'button:has-text("Apply on company site")',
                   'a:has-text("Apply on company site")')
NAUKRI_APPLY = 'button#apply-button, a#apply-button, button.apply-button'


async def unclassified(limit: int = 100, min_score: int = 0) -> list:
    """
    Jobs with no answer either way, best-scoring first.

    Ordered by score because classification is what admits a job to the apply
    queue, and the queue spends its budget in score order — classifying a 60%
    posting before a 95% one delays the job that matters.
    """
    db = get_db()
    cursor = db.jobs.find(
        {"status": "new",
         "match_score": {"$gte": min_score},
         "apply_type": {"$in": [None]},
         "apply_type_hint": {"$in": [None]}},
        {"_id": 0, "job_id": 1, "url": 1, "source": 1, "title": 1, "match_score": 1},
    ).sort("match_score", -1).limit(limit)
    return [j async for j in cursor]


async def _classify_linkedin(jobs: list) -> int:
    """No browser: the guest endpoint says whether the apply is off-site."""
    done = 0
    fail_streak = 0
    async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
        for job in jobs:
            if fail_streak >= 3:
                logger.warning("Classify: LinkedIn stopping early (3 consecutive failures)")
                break
            m = _LINKEDIN_ID.search(job.get("url", ""))
            if not m:
                continue
            try:
                resp = await client.get(
                    f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}")
                if resp.status_code != 200:
                    fail_streak += 1
                    continue
                fail_streak = 0
                hint = "external" if "offsite-apply" in resp.text else "in_platform"
                await update_job(job["job_id"], {"apply_type_hint": hint})
                done += 1
                await asyncio.sleep(random.uniform(1.0, 2.0))
            except Exception as e:
                fail_streak += 1
                logger.debug(f"Classify: {job.get('title', '')[:30]}: {type(e).__name__}: {e}")
    return done


async def _classify_naukri(jobs: list) -> int:
    """Needs the page. Read-only: it never clicks Apply."""
    if not jobs:
        return 0
    from playwright.async_api import async_playwright
    from agents.apply_agent import UA
    from agents.scraper_agent import is_closed_error

    done = 0
    async with async_playwright() as pw:
        user_dir = os.path.join(PROFILE_ROOT, NAUKRI_SCRAPE_PROFILE)
        try:
            ctx = await pw.chromium.launch_persistent_context(
                user_dir, channel="chrome", headless=False,
                viewport={"width": 1360, "height": 900}, locale="en-IN", user_agent=UA,
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"])
        except Exception as e:
            logger.warning(f"Classify: no browser for Naukri ({type(e).__name__}); skipping")
            return 0
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            for job in jobs:
                try:
                    await page.goto(job["url"], wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(random.uniform(1.5, 2.5))
                    offsite = None
                    for sel in NAUKRI_EXTERNAL:
                        offsite = await page.query_selector(sel)
                        if offsite:
                            break
                    inplace = None if offsite else await page.query_selector(NAUKRI_APPLY)
                    if not offsite and not inplace:
                        continue                    # nothing readable; leave it unclassified
                    await update_job(job["job_id"],
                                     {"apply_type_hint": "external" if offsite else "in_platform"})
                    done += 1
                    await asyncio.sleep(random.uniform(1.0, 2.0))
                except Exception as e:
                    if is_closed_error(e):
                        logger.warning("Classify: browser gone — stopping")
                        break
                    logger.debug(f"Classify: {job.get('title', '')[:30]}: {type(e).__name__}")
        finally:
            try:
                await ctx.close()
            except Exception:
                pass
    return done


async def run_classification(limit: int = 60, min_score: int = 0) -> dict:
    """Classify a batch of unknown postings. Returns what it settled."""
    jobs = await unclassified(limit=limit, min_score=min_score)
    if not jobs:
        return {"classified": 0, "remaining": 0, "message": "nothing left to classify"}

    linkedin = [j for j in jobs if j.get("source") == "linkedin"]
    naukri = [j for j in jobs if j.get("source") == "naukri"]

    done_li = await _classify_linkedin(linkedin)
    done_nk = await _classify_naukri(naukri)

    remaining = len(await unclassified(limit=1000, min_score=min_score))
    summary = {"classified": done_li + done_nk, "linkedin": done_li, "naukri": done_nk,
               "remaining": remaining}
    logger.info(f"Classification: {summary}")
    return summary
