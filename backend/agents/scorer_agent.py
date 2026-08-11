import os
import json
import re
import asyncio
from llm_provider import LLMProvider, RateLimited
from db.mongodb import get_resume
from utils.job_parser import truncate_description
from dotenv import load_dotenv

load_dotenv()


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

    async def score(self, job: dict) -> dict:
        resume_text = await self._get_resume_text()
        # Keep the prompt lean — the free Groq tier has a daily token budget and
        # a long resume+JD per job exhausts it after ~100 jobs.
        job_description = truncate_description(job.get("description", ""), max_chars=1500)
        title = job.get("title", "")
        company = job.get("company", "")

        location = job.get("location", "")

        prompt = f"""You are a resume-job matcher. Score how well this resume matches the job posting.

RESUME:
{resume_text[:1200]}

JOB TITLE: {title}
COMPANY: {company}
LOCATION: {location}
JOB DESCRIPTION:
{job_description}

Score the match across these 4 dimensions (must sum to 100):
1. Skills match (max 40): How many required skills does the resume have?
2. Experience match (max 30): Does experience level and years match?
3. Domain match (max 20): Is the industry/domain relevant?
4. Location match (max 10): Use this priority:
   - Hyderabad, Bangalore, Bengaluru, or Pune (India) = 10
   - Anywhere in India or hybrid India = 8
   - US Remote or fully remote = 5
   - US onsite only = 3
   - Other = 2

Also identify the top 3 skill gaps (things the job requires that the resume lacks).

Respond in this exact JSON format:
{{
  "skills_score": <0-40>,
  "experience_score": <0-30>,
  "domain_score": <0-20>,
  "location_score": <0-10>,
  "gap_analysis": ["gap1", "gap2", "gap3"]
}}

Only return the JSON, no explanation."""

        try:
            # llm.complete() is synchronous; run it off the event loop so the API
            # stays responsive while a long scoring batch runs in the background.
            response = await asyncio.to_thread(self.llm.complete, prompt)
            data = self._parse_json(response)
            skills = min(data.get("skills_score", 0), 40)
            experience = min(data.get("experience_score", 0), 30)
            domain = min(data.get("domain_score", 0), 20)
            location = min(data.get("location_score", 0), 10)
            total = skills + experience + domain + location

            return {
                "match_score": total,
                "score_breakdown": {
                    "skills_score": skills,
                    "experience_score": experience,
                    "domain_score": domain,
                    "location_score": location,
                },
                "gap_analysis": data.get("gap_analysis", [])[:5],
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
