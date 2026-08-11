import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, UploadFile, File, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import tempfile
from dotenv import load_dotenv

from db.mongodb import (
    get_jobs, get_job, update_job,
    get_applications, get_application, insert_application, update_application,
    get_resume, save_resume, get_stats,
)
from models.schemas import (
    ApplicationStatusUpdate, ScrapeRequest, AutoApplyRunRequest,
    ApplyProfile, AnswerUpsert,
)
from utils.resume_parser import parse_resume_pdf
from utils.pdf_generator import generate_resume_pdf
from agents.scraper_agent import ScraperAgent
from agents.scorer_agent import ScorerAgent
from agents.tailor_agent import TailorAgent
from agents.cover_letter_agent import CoverLetterAgent
from agents.outreach_agent import OutreachAgent
from agents.apply_agent import ApplyAgent
from services.scheduler import setup_scheduler, scheduler

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_scheduler()
    yield
    scheduler.shutdown()


app = FastAPI(
    title="JobHunt Copilot",
    description="AI-powered job hunting assistant for Mukul",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

    parsed = parse_resume_pdf(tmp_path)
    os.unlink(tmp_path)
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
    return {"jobs": jobs, "count": len(jobs)}


@app.get("/jobs/{job_id}")
async def get_job_route(job_id: str):
    job = await get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/jobs/{job_id}/tailor")
async def tailor_resume(job_id: str):
    job = await get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    agent = TailorAgent()
    tailored = await agent.tailor(job)
    return {"tailored_resume": tailored}


@app.get("/jobs/{job_id}/tailor-pdf")
async def download_tailored_pdf(job_id: str):
    job = await get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    agent = TailorAgent()
    tailored_text = await agent.tailor(job)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()
    generate_resume_pdf(tailored_text, tmp.name)
    filename = f"resume_{job.get('company', 'job').replace(' ', '_')}.pdf"
    return FileResponse(tmp.name, media_type="application/pdf", filename=filename)


@app.post("/jobs/{job_id}/auto-apply")
async def auto_apply(job_id: str, background_tasks: BackgroundTasks):
    job = await get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def _run_apply():
        # ApplyAgent.apply() now owns dedup, job-status transitions, and
        # recording the application — so we just kick it off and log.
        agent = ApplyAgent()
        result = await agent.apply(job)
        print(f"AutoApply [{job_id}]: {result.get('status')} — {result.get('message', '')}")

    background_tasks.add_task(_run_apply)
    return {"message": "Auto-apply started in background. A browser window may open — watch the logs for result."}


@app.post("/jobs/{job_id}/cover-letter")
async def generate_cover_letter(job_id: str):
    job = await get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    agent = CoverLetterAgent()
    letter = await agent.generate(job)
    return {"cover_letter": letter}


@app.post("/jobs/{job_id}/outreach")
async def generate_outreach(job_id: str):
    job = await get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    agent = OutreachAgent()
    message = await agent.generate(job)
    return {"outreach_message": message}


@app.post("/jobs/{job_id}/apply")
async def mark_applied(job_id: str):
    job = await get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    from datetime import datetime
    await update_job(job_id, {"status": "applied"})
    app_id = await insert_application({"job_id": job_id, "status": "applied", "applied_at": datetime.utcnow()})
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


# --- Manual triggers ---

@app.post("/scrape/trigger")
async def trigger_scrape(background_tasks: BackgroundTasks, body: ScrapeRequest = ScrapeRequest()):
    agent = ScraperAgent()
    agent.max_jobs_per_source = min(body.max_jobs, 100)
    background_tasks.add_task(agent.scrape_all)
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
    from agents.apply_agent import SUPPORTED_PLATFORMS
    states = await get_auth_states()
    return {"platforms": [
        {"platform": p, "logged_in_at": states.get(p), "logged_in": states.get(p) is not None}
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
async def trigger_scoring(background_tasks: BackgroundTasks):
    from db.mongodb import get_unscored_jobs, update_job as _update_job

    async def _run_scoring():
        agent = ScorerAgent()
        unscored = await get_unscored_jobs()
        scored = failures = 0
        for job in unscored:
            try:
                result = await agent.score(job)
                if result is None:
                    # Leave unscored so it is retried later.
                    failures += 1
                    if failures >= 3:
                        print(f"Scoring stopped after {failures} consecutive failures "
                              f"(likely LLM rate limit). {scored} scored, "
                              f"{len(unscored) - scored} left for the next run.")
                        return
                    continue
                failures = 0
                await _update_job(job["job_id"], {
                    "match_score": result["match_score"],
                    "score_breakdown": result["score_breakdown"],
                    "gap_analysis": result["gap_analysis"],
                })
                scored += 1
            except Exception as e:
                print(f"Score error: {e}")
        print(f"Background scoring complete: {scored} jobs scored")

    background_tasks.add_task(_run_scoring)
    return {"message": "Scoring started in background. Refresh jobs in a few minutes."}
