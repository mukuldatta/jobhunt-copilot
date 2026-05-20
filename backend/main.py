import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
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
    ApplicationStatusUpdate, ScrapeRequest,
)
from utils.resume_parser import parse_resume_pdf
from agents.scraper_agent import ScraperAgent
from agents.scorer_agent import ScorerAgent
from agents.tailor_agent import TailorAgent
from agents.cover_letter_agent import CoverLetterAgent
from agents.outreach_agent import OutreachAgent
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
    allow_origins=[os.environ.get("FRONTEND_URL", "http://localhost:3000"), "http://localhost:5173"],
    allow_credentials=True,
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
):
    jobs = await get_jobs(skip=skip, limit=limit, min_score=min_score, status=status)
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
async def trigger_scrape(body: ScrapeRequest = ScrapeRequest()):
    agent = ScraperAgent()
    agent.max_jobs_per_source = min(body.max_jobs, 100)
    jobs = await agent.scrape_all()
    return {"message": f"Scrape complete", "scraped": len(jobs)}


@app.get("/scrape/debug")
async def debug_scrape():
    """Test each scraping source with a single query and return raw counts."""
    import httpx as _httpx
    from bs4 import BeautifulSoup as _BS
    results = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    async with _httpx.AsyncClient(headers=headers, timeout=20, follow_redirects=True) as client:
        # Naukri
        try:
            resp = await client.get(
                "https://www.naukri.com/jobapi/v3/search?noOfResults=5&urlType=search_by_keyword&searchType=adv&keyword=Software%20Engineer&location=Hyderabad&pageNo=1",
                headers={**headers, "appid": "109", "systemid": "109", "Referer": "https://www.naukri.com/"},
            )
            data = resp.json() if resp.status_code == 200 else {}
            results["naukri"] = {"status": resp.status_code, "job_count": len(data.get("jobDetails", [])), "total": data.get("noOfJobsRecommended", 0)}
        except Exception as e:
            results["naukri"] = {"error": str(e)}

        # Indeed RSS
        try:
            resp = await client.get("https://www.indeed.com/rss?q=Software+Engineer&l=Remote&sort=date&fromage=3")
            soup = _BS(resp.text, "xml")
            results["indeed"] = {"status": resp.status_code, "item_count": len(soup.find_all("item"))}
        except Exception as e:
            results["indeed"] = {"error": str(e)}

        # Dice
        try:
            resp = await client.get(
                "https://job-search-api.svc.dhigroupinc.com/v1/dice/jobs/search?q=Software%20Engineer&countryCode2=US&page=1&pageSize=5&filters.postedDate=THREE_DAYS&filters.workplaceTypes=Remote&language=en",
                headers={**headers, "x-api-key": "1YAt0R9wBg4WfsF9VB2778F5CHLAPMVH3IFTtM6e"},
            )
            data = resp.json() if resp.status_code == 200 else {}
            results["dice"] = {"status": resp.status_code, "job_count": len(data.get("data", [])), "total": data.get("totalCount", 0)}
        except Exception as e:
            results["dice"] = {"error": str(e)}

    return results


@app.post("/score/trigger")
async def trigger_scoring():
    from db.mongodb import get_unscored_jobs, update_job as _update_job
    agent = ScorerAgent()
    unscored = await get_unscored_jobs()
    scored = 0
    for job in unscored:
        try:
            result = await agent.score(job)
            await _update_job(job["job_id"], {
                "match_score": result["match_score"],
                "score_breakdown": result["score_breakdown"],
                "gap_analysis": result["gap_analysis"],
            })
            scored += 1
        except Exception as e:
            print(f"Score error: {e}")
    return {"message": f"Scoring complete", "scored": scored}
