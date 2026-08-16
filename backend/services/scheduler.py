from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import logging

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def setup_scheduler():
    from agents.scraper_agent import ScraperAgent
    from services.alert_service import send_email_alert, send_sms_alert
    from db.mongodb import (
        get_high_match_jobs, delete_old_jobs, update_job, release_stale_apply_claims,
    )

    async def scrape_jobs():
        logger.info("Scheduler: starting job scrape...")
        agent = ScraperAgent()
        jobs = await agent.scrape_all()
        logger.info(f"Scheduler: scraped {len(jobs)} new jobs")

    async def score_new_jobs():
        from services.scoring_service import run_scoring
        logger.info("Scheduler: scoring unscored jobs...")
        await run_scoring()

    async def send_alerts():
        from services.settings_service import get_agent_rules
        rules = await get_agent_rules()
        if not rules["alerts_enabled"]:
            return
        logger.info("Scheduler: checking for high-match jobs to alert...")
        from datetime import datetime
        high_match = await get_high_match_jobs(threshold=rules["min_score"])
        for job in high_match:
            send_email_alert(job)
            if rules["sms_alerts"]:
                send_sms_alert(job)
            # Record that you were told. Deliberately NOT a status change —
            # status decides apply eligibility and is not ours to spend.
            await update_job(job["job_id"], {"alerted_at": datetime.utcnow()})
        logger.info(f"Scheduler: sent alerts for {len(high_match)} jobs")

    async def cleanup_old_jobs():
        deleted = await delete_old_jobs(days=7)
        logger.info(f"Scheduler: cleaned up {deleted} old jobs")

    # A scrape opens a headed browser and can pause for a manual CAPTCHA, and a
    # scoring pass paces itself around free-tier rate limits — both can overrun
    # the 30-minute interval. APScheduler's default 1-second misfire grace then
    # drops the next execution silently. A 5-minute grace lets a late run still
    # happen; max_instances=1 + coalesce keep it from stacking up behind itself.
    common = {"replace_existing": True, "max_instances": 1,
              "coalesce": True, "misfire_grace_time": 300}

    # A restart is the usual way a job ends up claimed but never finished: the
    # process holding the run goes away and nothing moves it out of 'applying'.
    # Startup is therefore the one moment we can be certain no run is in flight,
    # which makes it the safest place to take those claims back.
    try:
        freed = await release_stale_apply_claims()
        if freed:
            logger.info(f"Startup: returned {freed} abandoned apply claim(s) to the queue")
    except Exception as e:
        logger.warning(f"Startup: could not release stale apply claims: {e}")

    scheduler.add_job(scrape_jobs, IntervalTrigger(minutes=30), id="scrape_jobs", **common)
    scheduler.add_job(score_new_jobs, IntervalTrigger(minutes=30), id="score_jobs", **common)
    scheduler.add_job(send_alerts, IntervalTrigger(minutes=30), id="send_alerts", **common)
    scheduler.add_job(cleanup_old_jobs, IntervalTrigger(hours=24), id="cleanup_jobs", **common)

    # Reading your replies to screening questions. Registered only when IMAP
    # credentials exist, so the default install polls nothing and the emailed
    # link into Setup > Saved answers stays the way to answer.
    from services import inbox_service
    if inbox_service.configured():
        import os as _os

        async def poll_question_replies():
            saved = await inbox_service.poll_answers()
            if saved:
                logger.info(f"Inbox: learned {saved} answer(s) from email replies")

        minutes = max(1, int(_os.environ.get("IMAP_POLL_MINUTES", "5") or 5))
        scheduler.add_job(poll_question_replies, IntervalTrigger(minutes=minutes),
                          id="poll_question_replies", **common)
        logger.info(f"Question-reply inbox polling every {minutes} minutes")
    else:
        logger.info("Question-reply inbox polling off (no IMAP_* credentials)")

    # Autonomous apply is opt-in: it opens a real browser window and submits
    # applications, so it only runs on a schedule when it has been turned on in
    # Setup > Agent rules (or via AUTO_APPLY_ENABLED).
    from services.settings_service import get_agent_rules
    rules = await get_agent_rules()
    if rules["auto_apply_enabled"]:
        from services.orchestrator import run_auto_apply_cycle
        interval = rules["interval_minutes"]
        scheduler.add_job(run_auto_apply_cycle, IntervalTrigger(minutes=interval),
                          id="auto_apply", replace_existing=True)
        logger.info(f"Auto-apply scheduled every {interval} minutes")

    scheduler.start()
    logger.info("Scheduler started — running every 30 minutes")


async def reschedule_auto_apply():
    """
    Re-read the auto-apply rules and add / update / drop its scheduled job.
    Called after Setup > Agent rules is saved, so the toggle takes effect now
    rather than at the next restart.
    """
    from services.settings_service import get_agent_rules
    from services.orchestrator import run_auto_apply_cycle

    rules = await get_agent_rules()
    if rules["auto_apply_enabled"]:
        scheduler.add_job(run_auto_apply_cycle,
                          IntervalTrigger(minutes=rules["interval_minutes"]),
                          id="auto_apply", replace_existing=True)
        logger.info(f"Auto-apply rescheduled every {rules['interval_minutes']} minutes")
    elif scheduler.get_job("auto_apply"):
        scheduler.remove_job("auto_apply")
        logger.info("Auto-apply unscheduled")
