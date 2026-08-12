import os
import json
import re
import asyncio
from llm_provider import LLMProvider, RateLimited
from db.mongodb import get_resume
from utils.job_parser import truncate_description
from utils.score_rules import skills_match, location_points, SKILLS_MAX
from dotenv import load_dotenv

load_dotenv()


def _dedupe(gaps: list) -> list:
    """
    Drop repeats and near-repeats. The model is shown the computed gaps and
    routinely echoes them back expanded ("scala" -> "Scala", "gcp" -> "Google
    Cloud Platform (GCP)"), which reads as four gaps where there are two.
    """
    out, seen = [], []
    for g in gaps:
        g = (g or "").strip()
        if not g:
            continue
        key = g.lower()
        if any(key == s or key in s or s in key for s in seen):
            continue
        seen.append(key)
        out.append(g)
    return out


class ScorerAgent:
    def __init__(self):
        self.llm = LLMProvider(provider=os.getenv("LLM_PROVIDER", "groq"))
        self._resume_cache = None
        self.last_retry_after = None   # seconds hinted by the provider, if any

    async def _get_resume_text(self) -> str:
        if self._resume_cache:
            return self._resume_cache
        resume = await get_resume()
        if resume:
            self._resume_cache = resume.get("parsed_text", "")
        return self._resume_cache or ""

    # Below this a "description" is a title echo or a location string, not
    # something you can match a resume against.
    MIN_DESCRIPTION_CHARS = 200

    async def score(self, job: dict) -> dict:
        # Scoring a job with no description is not a hard match, it is a
        # fabrication: the model has nothing to deduct from, so it returns the
        # top of the range and the job outranks everything real. Those scores
        # then drive auto-apply selection. Leave it unscored instead — same
        # contract as an error below, so it stays out of the apply pool and gets
        # picked up again once the description arrives.
        description = (job.get("description") or "").strip()
        if len(description) < self.MIN_DESCRIPTION_CHARS:
            self.last_retry_after = None
            print(f"Scorer: skipping {job.get('job_id')} — description is "
                  f"{len(description)} chars, nothing to score against.")
            return None

        resume_text = await self._get_resume_text()
        # Keep the prompt lean — the free Groq tier has a daily token budget and
        # a long resume+JD per job exhausts it after ~100 jobs.
        job_description = truncate_description(job.get("description", ""), max_chars=1500)
        title = job.get("title", "")
        company = job.get("company", "")

        location = job.get("location", "")

        # Facts first — computed, not asked for.
        resume = await get_resume() or {}
        skills = skills_match(job_description, resume.get("skills", []))
        location_score = location_points(location, job_description)

        if skills["points"] is None:
            print(f"Scorer: skipping {job.get('job_id')} — JD names no recognisable "
                  f"technology, nothing to match on.")
            return None

        prompt = f"""You are a resume-job matcher. Judge TWO dimensions only.

RESUME:
{resume_text[:1200]}

JOB TITLE: {title}
COMPANY: {company}
JOB DESCRIPTION:
{job_description}

1. experience_score (max 30): do the years and the seniority level line up?
   Deduct for a role clearly more senior or more junior than the resume shows.
2. domain_score (max 20): is the industry and problem space relevant to this
   candidate's background? A different domain scores low even when the job
   title looks similar.

Also list up to 3 requirements this resume does not evidence, beyond the
technologies already accounted for: {', '.join(skills['missing'][:5]) or 'none found'}.

Be strict. A maximum means nothing is missing on that dimension. Most genuine
matches land in the middle of each range.

Return only JSON. The numbers are an EXAMPLE of shape, not values to copy:
{{"experience_score": 21, "domain_score": 12, "gap_analysis": ["gap1", "gap2"]}}"""

        try:
            # llm.complete() is synchronous; run it off the event loop so the API
            # stays responsive while a long scoring batch runs in the background.
            response = await asyncio.to_thread(self.llm.complete, prompt)
            data = self._parse_json(response)
            experience = max(0, min(int(data.get("experience_score", 0)), 30))
            domain = max(0, min(int(data.get("domain_score", 0)), 20))

            # A model that maxes every dimension it was given is reciting the
            # template, not judging. Refuse it rather than bank a 100.
            if experience == 30 and domain == 20 and skills["points"] == SKILLS_MAX:
                print(f"Scorer: rejecting degenerate all-maximum score for {job.get('job_id')}")
                return None

            total = skills["points"] + experience + domain + location_score
            gaps = [g for g in (data.get("gap_analysis") or []) if isinstance(g, str)]

            return {
                "match_score": total,
                "score_breakdown": {
                    "skills_score": skills["points"],
                    "experience_score": experience,
                    "domain_score": domain,
                    "location_score": location_score,
                },
                # Derived from the same pass as the score, so a named gap can
                # never sit next to a perfect skills mark again. Deduped because
                # the model tends to restate the ones it was already shown.
                "gap_analysis": _dedupe(skills["missing"] + gaps)[:5],
                "skills_matched": skills["matched"],
            }
        except RateLimited as e:
            # Providers are throttled — surface the hint so the caller can back
            # off for the right amount of time.
            m = re.search(r"retry_after=(\d+)", str(e))
            self.last_retry_after = float(m.group(1)) if m else None
            print(f"Scorer rate limited for {job.get('job_id')}: {e}")
            return None
        except Exception as e:
            # Do NOT return a 0 score here: a saved 0 is indistinguishable from a
            # genuine poor match, so the job would look scored and never be
            # retried. Signal failure and let the caller leave it unscored.
            self.last_retry_after = None
            print(f"Scorer error for {job.get('job_id')}: {e}")
            return None

    def _parse_json(self, text: str) -> dict:
        text = text.strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
        return json.loads(text)
