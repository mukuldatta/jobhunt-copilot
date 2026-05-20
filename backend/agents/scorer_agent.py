import os
import json
import re
from llm_provider import LLMProvider
from db.mongodb import get_resume
from utils.job_parser import truncate_description
from dotenv import load_dotenv

load_dotenv()


class ScorerAgent:
    def __init__(self):
        self.llm = LLMProvider(provider=os.getenv("LLM_PROVIDER", "groq"))
        self._resume_cache = None

    async def _get_resume_text(self) -> str:
        if self._resume_cache:
            return self._resume_cache
        resume = await get_resume()
        if resume:
            self._resume_cache = resume.get("parsed_text", "")
        return self._resume_cache or ""

    async def score(self, job: dict) -> dict:
        resume_text = await self._get_resume_text()
        job_description = truncate_description(job.get("description", ""), max_chars=3000)
        title = job.get("title", "")
        company = job.get("company", "")

        location = job.get("location", "")

        prompt = f"""You are a resume-job matcher. Score how well this resume matches the job posting.

RESUME:
{resume_text[:2000]}

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
            response = self.llm.complete(prompt)
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
        except Exception as e:
            print(f"Scorer error for {job.get('job_id')}: {e}")
            return {
                "match_score": 0,
                "score_breakdown": {"skills_score": 0, "experience_score": 0, "domain_score": 0, "location_score": 0},
                "gap_analysis": [],
            }

    def _parse_json(self, text: str) -> dict:
        text = text.strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
        return json.loads(text)
