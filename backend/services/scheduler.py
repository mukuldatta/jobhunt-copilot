from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import logging

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def setup_scheduler():
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
        logger.info("Scheduler: scoring unscored jobs...")
        agent = ScorerAgent()
        unscored = await get_unscored_jobs()
        for job in unscored:
            try:
                result = await agent.score(job)
                await update_job(job["job_id"], {
                    "match_score": result["match_score"],
                    "score_breakdown": result["score_breakdown"],
                    "gap_analysis": result["gap_analysis"],
                })
            except Exception as e:
                logger.error(f"Scoring failed for {job.get('job_id')}: {e}")
        logger.info(f"Scheduler: scored {len(unscored)} jobs")

    async def send_alerts():
        logger.info("Scheduler: checking for high-match jobs to alert...")
        high_match = await get_high_match_jobs(threshold=70)
        for job in high_match:
            send_email_alert(job)
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

    scheduler.start()
    logger.info("Scheduler started — running every 30 minutes")
