import os
from llm_provider import LLMProvider
from db.mongodb import get_resume
from utils.job_parser import truncate_description
from dotenv import load_dotenv

load_dotenv()


class TailorAgent:
    def __init__(self):
        self.llm = LLMProvider(provider=os.getenv("LLM_PROVIDER", "groq"))

    async def tailor(self, job: dict) -> str:
        resume = await get_resume()
        if not resume:
            raise ValueError("No resume found. Upload your resume first.")

        resume_text = resume.get("parsed_text", "")
        job_description = truncate_description(job.get("description", ""), max_chars=2000)
        title = job.get("title", "")
        company = job.get("company", "")

        prompt = f"""You are a professional resume writer helping tailor a resume for a specific job.

CRITICAL RULES:
- NEVER fabricate skills, experience, or education that does not exist in the original resume
- Only reframe, reorder, and emphasize existing content to better match the job
- Use keywords from the job description where they genuinely apply
- Keep the same factual information, just present it more relevantly

ORIGINAL RESUME:
{resume_text}

TARGET JOB: {title} at {company}
JOB DESCRIPTION:
{job_description}

Rewrite the resume to better match this job. Follow these steps:
1. Move the most relevant experience to the top
2. Rewrite bullet points to use keywords from the job description (only where factually accurate)
3. Highlight skills that match the job requirements
4. Keep all dates, companies, titles, and education exactly as-is

Return the full tailored resume text only. No explanations."""

        return self.llm.complete(prompt)
