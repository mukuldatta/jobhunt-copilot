import os
from llm_provider import LLMProvider
from db.mongodb import get_resume
from utils.job_parser import truncate_description
from dotenv import load_dotenv

load_dotenv()


class CoverLetterAgent:
    def __init__(self):
        self.llm = LLMProvider(provider=os.getenv("LLM_PROVIDER", "groq"))

    async def generate(self, job: dict) -> str:
        resume = await get_resume()
        if not resume:
            raise ValueError("No resume found. Upload your resume first.")

        resume_text = resume.get("parsed_text", "")
        job_description = truncate_description(job.get("description", ""), max_chars=2000)
        title = job.get("title", "")
        company = job.get("company", "")
        gaps = job.get("gap_analysis", [])

        gaps_text = "\n".join(f"- {g}" for g in gaps) if gaps else "None identified"

        prompt = f"""Write a compelling, personalized cover letter for this job application.

APPLICANT RESUME SUMMARY:
{resume_text[:1500]}

JOB: {title} at {company}
JOB DESCRIPTION:
{job_description}

SKILL GAPS TO ADDRESS (briefly and honestly):
{gaps_text}

Write a professional cover letter that:
1. Opens with genuine enthusiasm for the role and company (1 paragraph)
2. Highlights 2-3 specific achievements from the resume that directly match the job (1-2 paragraphs)
3. Briefly acknowledges any gaps and frames them as areas of active growth (1 paragraph)
4. Closes with a clear call to action (1 paragraph)

Keep it under 400 words. Be specific, not generic. Do not use clichés like "I am a passionate...".
Write in first person. Start directly with "Dear Hiring Manager," or the team name if known.
Only output the cover letter text."""

        return self.llm.complete(prompt)
