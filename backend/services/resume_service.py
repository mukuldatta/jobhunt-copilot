"""
One tailored resume per job, generated once and reused everywhere.

Three call sites used to tailor independently — the Review preview, the PDF
download, and the apply run — so the document you approved was not the document
that got downloaded, and neither was the one submitted. Each was a fresh
stochastic generation from the same prompt. That is a correctness problem before
it is a cost one, though it is also the single biggest consumer of a quota that
already rate-limits the primary model.

Generation and validation live here so both the API routes and ApplyAgent share
one path, and neither has to import the other (ApplyAgent pulls in Playwright).
"""

from agents.tailor_agent import TailorAgent
from utils.resume_validator import validate_tailored_resume
from db.mongodb import get_resume, get_job, update_job
from llm_provider import RateLimited


def _resume_version(resume: dict) -> str:
    """Cache key component — a new resume upload must invalidate every tailoring."""
    if not resume:
        return "none"
    stamp = resume.get("uploaded_at")
    return str(stamp) if stamp else str(len(resume.get("parsed_text", "")))


def _offending_terms(issues: list) -> list:
    marker = "Introduces skills not in the original resume:"
    terms = []
    for issue in issues or []:
        if marker in issue:
            terms += [t.strip() for t in issue.split(marker, 1)[1].split(",") if t.strip()]
    return terms


async def build_tailored_resume(job: dict, *, user_name: str = None, user_email: str = None,
                                force: bool = False) -> dict:
    """
    Return the tailored resume for this job.

    {"text", "cached", "ok", "issues", "rate_limited"} — text is None when the
    caller should not proceed. A cached entry is returned as-is: it was validated
    before it was stored, so re-validating would only risk a different verdict on
    identical input.
    """
    resume = await get_resume() or {}
    version = _resume_version(resume)
    job_id = job.get("job_id", "")

    if not force and job_id:
        stored = await get_job(job_id) or {}
        cached = stored.get("tailored_resume_text")
        if cached and stored.get("tailored_resume_version") == version:
            return {"text": cached, "cached": True, "ok": True,
                    "issues": [], "rate_limited": False}

    async def _once(avoid=None):
        try:
            return await TailorAgent().tailor(job, avoid=avoid), False
        except RateLimited:
            return None, True
        except Exception as e:
            print(f"resume_service: tailoring failed: {e}")
            return None, False

    text, limited = await _once()
    if limited:
        return {"text": None, "cached": False, "ok": False,
                "issues": ["LLM quota exhausted"], "rate_limited": True}
    if not text:
        return {"text": None, "cached": False, "ok": False,
                "issues": ["Tailoring produced nothing"], "rate_limited": False}

    if not resume:
        return {"text": text, "cached": False, "ok": True, "issues": [], "rate_limited": False}

    v = validate_tailored_resume(text, resume, user_name=user_name, user_email=user_email)
    if not v["ok"]:
        # Stochastic output, so a rejection is usually a bad roll — re-roll once,
        # telling it exactly which terms were rejected.
        retry, limited = await _once(avoid=_offending_terms(v["issues"]))
        if limited:
            return {"text": None, "cached": False, "ok": False,
                    "issues": ["LLM quota exhausted"], "rate_limited": True}
        if retry:
            v2 = validate_tailored_resume(retry, resume, user_name=user_name,
                                          user_email=user_email)
            if v2["ok"]:
                text, v = retry, v2

    if not v["ok"]:
        return {"text": None, "cached": False, "ok": False,
                "issues": v["issues"], "rate_limited": False}

    if job_id:
        await update_job(job_id, {"tailored_resume_text": v["text"],
                                  "tailored_resume_version": version})
    return {"text": v["text"], "cached": False, "ok": True,
            "issues": v["issues"], "rate_limited": False}
