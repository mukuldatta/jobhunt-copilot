from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import logging

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def setup_scheduler():
    from agents.scraper_agent import ScraperAgent
    from agents.scorer_agent import ScorerAgent
    from services.alert_service import send_email_alert, send_sms_alert
    from db.mongodb import get_unscored_jobs, get_high_match_jobs, delete_old_jobs, update_job

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
        high_match = await get_high_match_jobs(threshold=rules["min_score"])
        for job in high_match:
            send_email_alert(job)
            if rules["sms_alerts"]:
                send_sms_alert(job)
            await update_job(job["job_id"], {"status": "reviewed"})
        logger.info(f"Scheduler: sent alerts for {len(high_match)} jobs")

    async def cleanup_old_jobs():
        deleted = await delete_old_jobs(days=7)
        logger.info(f"Scheduler: cleaned up {deleted} old jobs")

    scheduler.add_job(scrape_jobs, IntervalTrigger(minutes=30), id="scrape_jobs", replace_existing=True)
    scheduler.add_job(score_new_jobs, IntervalTrigger(minutes=30), id="score_jobs", replace_existing=True)
    scheduler.add_job(send_alerts, IntervalTrigger(minutes=30), id="send_alerts", replace_existing=True)
    scheduler.add_job(cleanup_old_jobs, IntervalTrigger(hours=24), id="cleanup_jobs", replace_existing=True)

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
