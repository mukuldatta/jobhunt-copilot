import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

# The apply agent narrates itself with print(). Under a process manager stdout
# is a pipe, not a TTY, so those lines sit in a block buffer and a run that
# fails looks like a run that produced nothing at all.
#
# The encoding is here for the same reason: on Windows stdout defaults to the
# ANSI codepage, so an em dash in a status line came out as "needs_review ?" and
# an emoji could raise UnicodeEncodeError mid-run. errors="replace" means the
# worst case is one mangled character rather than a lost log line.
sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")

# The scheduler, orchestrator and scoring service narrate their runs through
# logging rather than print(). Without a root handler configured, the root
# logger sits at WARNING and every one of those lines was discarded — the
# agent worked in total silence, which is the one thing an unattended agent
# must not do.
import logging

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

from fastapi import FastAPI, UploadFile, File, HTTPException, Query, BackgroundTasks, Depends
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.background import BackgroundTask
from contextlib import asynccontextmanager
import asyncio
import re
import tempfile
from dotenv import load_dotenv

from db.mongodb import (
    get_jobs, count_jobs, get_job, update_job,
    get_applications, record_application, update_application,
    get_resume, save_resume, get_stats,
)
from models.schemas import (
    ApplicationStatusUpdate, JobStatusUpdate, ScrapeRequest, AutoApplyRunRequest,
    ApplyProfile, AnswerUpsert, AgentRules,
)
from utils.resume_parser import parse_resume_pdf
from utils.pdf_generator import generate_resume_pdf
from agents.scraper_agent import ScraperAgent
from agents.cover_letter_agent import CoverLetterAgent
from agents.outreach_agent import OutreachAgent
from agents.apply_agent import ApplyAgent
from services.scheduler import setup_scheduler, scheduler

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from db.mongodb import ensure_indexes
        await ensure_indexes()
    except Exception:
        logging.getLogger(__name__).exception(
            "Index setup skipped — the app still runs, queries are just slower."
        )
    await setup_scheduler()
    yield
    scheduler.shutdown()


app = FastAPI(
    title="JobHunt Copilot",
    description="AI-powered job hunting assistant for Mukul",
    version="1.0.0",
    lifespan=lifespan,
)

# This API has no auth in front of it, and it serves your parsed resume, your
# answers to screening questions, and a POST that drives a real browser session.
# "*" was harmless while it only ever listened on localhost; it stops being
# harmless the first time this is deployed. Dev needs nothing set — the Vite
# proxy makes those calls same-origin anyway. Set CORS_ORIGINS (comma separated)
# when the frontend is served from somewhere else.
CORS_ORIGINS = [
    o.strip() for o in os.environ.get(
        "CORS_ORIGINS",
        os.environ.get("FRONTEND_URL", "http://localhost:3000,http://localhost:5173"),
    ).split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "jobhunt-copilot"}


# --- Resume ---

@app.get("/resume")
async def get_resume_route():
    resume = await get_resume()
    if not resume:
        raise HTTPException(status_code=404, detail="No resume uploaded yet")
    return resume


@app.post("/resume/upload")
async def upload_resume(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    # pdfplumber is synchronous CPU work; off the event loop it would stall
    # every other request, including the sidebar's 5-second poll.
    try:
        parsed = await asyncio.to_thread(parse_resume_pdf, tmp_path)
    finally:
        # Not conditional on success: a parse failure used to leak the temp PDF.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    await save_resume(parsed)
    return {"message": "Resume uploaded and parsed", "skills_found": len(parsed["skills"])}


# --- Jobs ---

@app.get("/jobs")
async def list_jobs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    min_score: int = Query(None, ge=0, le=100),
    status: str = Query(None),
    source: str = Query(None),
    sponsorship: str = Query(None),
    sort_by: str = Query("date_desc"),
    search: str = Query(None),
    region: str = Query(None),
):
    jobs = await get_jobs(
        skip=skip, limit=limit, min_score=min_score, status=status,
        source=source, sponsorship=sponsorship, sort_by=sort_by, search=search,
        region=region,
    )
    # count is this page; total is the whole filtered set, which is what the
    # header in Review is actually reporting.
    total = await count_jobs(
        min_score=min_score, status=status, source=source,
        sponsorship=sponsorship, search=search, region=region,
    )
    return {"jobs": jobs, "count": len(jobs), "total": total}


async def job_or_404(job_id: str) -> dict:
    """The job named in the path, or a 404. Eight routes spelled this out
    verbatim; FastAPI resolves it once, before any of them runs."""
    job = await get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/jobs/{job_id}")
async def get_job_route(job: dict = Depends(job_or_404)):
    return job


@app.post("/jobs/{job_id}/tailor")
async def tailor_resume(force: bool = Query(False), job: dict = Depends(job_or_404)):
    """
    The tailored resume for this job — the same one the PDF download and the
    apply run use. Previously each of the three tailored independently, so the
    document you previewed was not the one that got submitted. Pass force=true
    to deliberately re-roll.
    """
    from services.resume_service import build_tailored_resume
    built = await build_tailored_resume(
        job, force=force,
        user_name=f"{os.environ.get('USER_FIRST_NAME', '')} "
                  f"{os.environ.get('USER_LAST_NAME', '')}".strip() or None,
        user_email=os.environ.get("MY_EMAIL"))

    if built["rate_limited"]:
        raise HTTPException(status_code=429,
                            detail="LLM quota exhausted — try again shortly.")
    if not built["ok"]:
        raise HTTPException(status_code=422,
                            detail=f"Tailored resume rejected: {'; '.join(built['issues'])}")
    return {"tailored_resume": built["text"], "cached": built["cached"],
            "warnings": built["issues"]}


@app.get("/jobs/{job_id}/tailor-pdf")
async def download_tailored_pdf(job: dict = Depends(job_or_404)):
    # Same artefact as the preview and the apply run — see /jobs/{id}/tailor.
    from services.resume_service import build_tailored_resume
    built = await build_tailored_resume(
        job,
        user_name=f"{os.environ.get('USER_FIRST_NAME', '')} "
                  f"{os.environ.get('USER_LAST_NAME', '')}".strip() or None,
        user_email=os.environ.get("MY_EMAIL"))
    # Same distinction the preview route draws: quota exhaustion is a "come
    # back shortly", not a rejected document. This route used to report both
    # as 422, so a rate limit looked like a failed integrity check.
    if built["rate_limited"]:
        raise HTTPException(status_code=429,
                            detail="LLM quota exhausted — try again shortly.")
    if not built["ok"]:
        raise HTTPException(status_code=422,
                            detail=f"Tailored resume unavailable: {'; '.join(built['issues'])}")
    tailored_text = built["text"]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()
    # fpdf2 is synchronous CPU work — same reasoning as the upload route.
    await asyncio.to_thread(generate_resume_pdf, tailored_text, tmp.name, built["links"])
    # The company name reaches a Content-Disposition header, so keep it to
    # characters that cannot break out of the filename.
    safe_company = re.sub(r"[^A-Za-z0-9]+", "_", job.get("company") or "job").strip("_")
    filename = f"resume_{safe_company or 'job'}.pdf"
    # delete=False is required (the file has to outlive this handler so it can be
    # streamed), so the cleanup has to be scheduled — otherwise every download
    # leaves a PDF behind in the temp directory forever.
    return FileResponse(tmp.name, media_type="application/pdf", filename=filename,
                        background=BackgroundTask(os.unlink, tmp.name))


@app.post("/jobs/{job_id}/auto-apply")
async def auto_apply(background_tasks: BackgroundTasks, job: dict = Depends(job_or_404)):
    async def _run_apply():
        # ApplyAgent.apply() now owns dedup, job-status transitions, and
        # recording the application — so we just kick it off and log.
        from services import agent_state
        agent_state.start("applying")
        try:
            agent = ApplyAgent()
            result = await agent.apply(job)
            agent_state.log(f"    → {result.get('status')}: {result.get('message', '')}")
        finally:
            agent_state.finish()

    background_tasks.add_task(_run_apply)
    return {"message": "Auto-apply started in background. A browser window may open — watch the logs for result."}


@app.post("/jobs/{job_id}/cover-letter")
async def generate_cover_letter(job: dict = Depends(job_or_404)):
    agent = CoverLetterAgent()
    letter = await agent.generate(job)
    return {"cover_letter": letter}


@app.post("/jobs/{job_id}/outreach")
async def generate_outreach(job: dict = Depends(job_or_404)):
    agent = OutreachAgent()
    message = await agent.generate(job)
    return {"outreach_message": message}


@app.patch("/jobs/{job_id}/status")
async def set_job_status(body: JobStatusUpdate, job: dict = Depends(job_or_404)):
    """Move a job through new / reviewed / skipped without applying to it."""
    await update_job(job["job_id"], {"status": body.status})
    return {"message": f"Job marked {body.status}", "status": body.status}


@app.post("/jobs/{job_id}/apply")
async def mark_applied(job: dict = Depends(job_or_404)):
    from datetime import datetime
    await update_job(job["job_id"], {"status": "applied"})
    # Idempotent upsert, like the auto-apply path: a plain insert meant a second
    # click on "mark applied" created a second application row, which then
    # double-counted in the pipeline and in /stats.
    app_id = await record_application(job["job_id"],
                                      {"status": "applied", "applied_at": datetime.utcnow()})
    return {"message": "Marked as applied", "application_id": app_id}


# --- Applications ---

@app.get("/applications")
async def list_applications(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    apps = await get_applications(skip=skip, limit=limit)
    return {"applications": apps, "count": len(apps)}


@app.patch("/applications/{application_id}/status")
async def update_application_status(application_id: str, body: ApplicationStatusUpdate):
    updates = {"status": body.status}
    if body.notes:
        updates["notes"] = body.notes
    updated = await update_application(application_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Application not found")
    return {"message": "Status updated"}


# --- Stats ---

@app.get("/stats")
async def get_dashboard_stats():
    return await get_stats()


# --- Agent state + rules ---

@app.get("/agent/state")
async def agent_state_route():
    """
    What the sidebar's agent strip renders: whether a run is in flight, when the
    next scheduled one fires, today's applications against the daily cap, and
    any application paused waiting for a human.
    """
    from services.agent_state import snapshot
    return await snapshot()


@app.get("/agent/log")
async def agent_log_route(since: int = 0):
    """
    What the agent is doing right now, line by line.

    Separate from /agent/state because the two are polled at different rates by
    different screens: the sidebar wants a small snapshot every 5s on every
    page, while the log is only worth fetching while someone is watching Today.
    Pass the `seq` from the last response as `since` to receive only what is
    new — the buffer holds a few hundred lines and re-sending them every two
    seconds would be the most expensive thing on the page.
    """
    from services.agent_state import tail
    return tail(since)


@app.get("/platforms")
async def platforms_route():
    """
    Which boards the agent will submit to, and why the others are excluded. The
    UI needs this to offer the right action per job — duplicating the policy
    client-side is how the two drift apart.
    """
    from platforms import APPLY_DISABLED
    from agents.apply_agent import SUPPORTED_PLATFORMS
    return {"apply_capable": SUPPORTED_PLATFORMS, "apply_disabled": APPLY_DISABLED}


@app.get("/settings")
async def get_settings_route():
    from services.settings_service import get_agent_rules
    return await get_agent_rules()


@app.put("/settings")
async def save_settings_route(body: AgentRules):
    from services.settings_service import save_agent_rules
    from services.scheduler import reschedule_auto_apply
    # exclude_unset so a partial save never resets a rule the form didn't send.
    rules = await save_agent_rules(body.model_dump(exclude_unset=True))
    await reschedule_auto_apply()
    return rules


# --- Manual triggers ---

@app.post("/scrape/trigger")
async def trigger_scrape(background_tasks: BackgroundTasks, body: ScrapeRequest = ScrapeRequest()):
    from services import agent_state

    agent = ScraperAgent()
    agent.max_jobs_per_source = min(body.max_jobs, 100)

    async def _run():
        agent_state.start("scraping")
        try:
            await agent.scrape_all()
        finally:
            agent_state.finish()

    background_tasks.add_task(_run)
    return {"message": "Scrape started in background. Refresh jobs in a few minutes."}


# --- Apply profile (questionnaire) ---

@app.get("/profile")
async def get_profile():
    from db.mongodb import get_apply_profile
    profile = await get_apply_profile()
    if not profile:
        # Seed sensible defaults from env so the form starts pre-filled.
        profile = ApplyProfile(
            full_name=f"{os.environ.get('USER_FIRST_NAME', '')} {os.environ.get('USER_LAST_NAME', '')}".strip() or None,
            email=os.environ.get("MY_EMAIL"),
            phone=os.environ.get("MY_PHONE"),
        ).model_dump()
    return profile


@app.put("/profile")
async def update_profile(body: ApplyProfile):
    from db.mongodb import save_apply_profile
    await save_apply_profile(body.model_dump())
    return {"message": "Profile saved"}


@app.get("/profile/questions")
async def list_pending_questions():
    from db.mongodb import get_pending_questions
    return {"questions": await get_pending_questions()}


@app.post("/profile/questions")
async def answer_pending_question(body: AnswerUpsert):
    from db.mongodb import upsert_learned_answer
    await upsert_learned_answer(body.question, body.answer)
    return {"message": "Answer saved and will be reused"}


@app.delete("/profile/questions")
async def dismiss_pending_question(question: str = Query(...)):
    from db.mongodb import delete_pending_question
    await delete_pending_question(question)
    return {"message": "Question dismissed"}


@app.get("/auth/status")
async def auth_status():
    from db.mongodb import get_auth_states
    from agents.apply_agent import SUPPORTED_PLATFORMS, platform_label
    states = await get_auth_states()
    # `logged_in` is the record of a sign-in, not a live check — the cookie can
    # have died any time since. logged_in_at is returned so the UI can say when
    # it was last established rather than asserting it is true now; POST
    # /auth/{platform}/check is the only thing here that actually knows.
    return {"platforms": [
        {"platform": p, "label": platform_label(p),
         "logged_in_at": states.get(p), "logged_in": states.get(p) is not None}
        for p in SUPPORTED_PLATFORMS
    ]}


@app.post("/auth/{platform}/check")
async def auth_check(platform: str):
    from agents.apply_agent import ApplyAgent, SUPPORTED_PLATFORMS
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unsupported platform '{platform}'")
    # Live probe — runs inline (opens a browser briefly) and returns the truth.
    return await ApplyAgent().check_login(platform)


@app.post("/auth/{platform}/login")
async def auth_login(platform: str, background_tasks: BackgroundTasks):
    from agents.apply_agent import ApplyAgent, SUPPORTED_PLATFORMS
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unsupported platform '{platform}'")

    async def _run():
        result = await ApplyAgent().login_interactive(platform)
        print(f"AuthLogin [{platform}]: {result.get('status')} — {result.get('message', '')}")

    background_tasks.add_task(_run)
    return {"message": f"Opening a browser to sign in to {platform.title()}. "
                       f"Complete the login in that window — the session will be saved."}


@app.post("/auto-apply/run")
async def auto_apply_run(background_tasks: BackgroundTasks, body: AutoApplyRunRequest = AutoApplyRunRequest()):
    from services.orchestrator import run_auto_apply_cycle

    # dry_run is fast and read-only — run inline so the preview comes right back.
    if body.dry_run:
        return await run_auto_apply_cycle(max_apply=body.max_apply, dry_run=True)

    async def _run():
        result = await run_auto_apply_cycle(max_apply=body.max_apply, force=body.force)
        print(f"AutoApplyCycle: {result.get('status')} — results={result.get('results', result.get('message',''))}")

    background_tasks.add_task(_run)
    return {"message": "Auto-apply cycle started in background. A browser window may open; watch the logs."}


@app.post("/score/trigger")
async def trigger_scoring(background_tasks: BackgroundTasks,
                          limit: int = Query(None, ge=1, le=1000)):
    """
    Score everything that needs it, newest-unscored first.

    limit overrides SCORE_PER_RUN for this run only. Bumping SCORER_VERSION
    queues every previously-scored job for re-scoring, and the default per-run
    cap is sized for a 30-minute cycle rather than for catching up in one go.
    """
    from services.scoring_service import run_scoring
    from services import agent_state

    async def _run():
        agent_state.start("scoring")
        try:
            await run_scoring(limit=limit)
        finally:
            agent_state.finish()

    background_tasks.add_task(_run)
    return {"message": "Scoring started in background. Refresh jobs in a few minutes."}
