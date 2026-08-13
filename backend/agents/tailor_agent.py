import os
import asyncio
from llm_provider import LLMProvider
from db.mongodb import get_resume
from utils.job_parser import truncate_description
from utils.resume_validator import clean_resume_text
from dotenv import load_dotenv

load_dotenv()


class TailorAgent:
    def __init__(self):
        self.llm = LLMProvider(provider=os.getenv("LLM_PROVIDER", "groq"))

    async def tailor(self, job: dict, avoid: list = None) -> str:
        """
        Reframe the resume for one job. `avoid` carries the terms a previous
        attempt was rejected for, so a retry is corrective rather than another
        blind roll at the same prompt.
        """
        resume = await get_resume()
        if not resume:
            raise ValueError("No resume found. Upload your resume first.")

        resume_text = resume.get("parsed_text", "")
        job_description = truncate_description(job.get("description", ""), max_chars=2000)
        title = job.get("title", "")
        company = job.get("company", "")

        # The old prompt said "never fabricate" and "use keywords from the job
        # description" in the same breath, and the model resolved that tension by
        # importing whatever the JD named. An explicit inventory turns an
        # abstract prohibition into a checkable one.
        inventory = ", ".join(resume.get("skills", [])) or "(see the resume text below)"

        correction = ""
        if avoid:
            correction = (
                "\nA PREVIOUS ATTEMPT WAS REJECTED for introducing terms that are not in the "
                f"resume: {', '.join(avoid)}.\nDo not mention "
                f"{'them' if len(avoid) > 1 else 'it'} anywhere in your output, in any form.\n"
            )

        prompt = f"""You are a professional resume writer tailoring a resume for one job.

THE ONE RULE THAT MATTERS:
You may not name any technology, tool, platform, certification or employer that does
not already appear in the original resume. Not in a bullet, not in a skills list, not
as "familiar with" or "exposure to". If the job description asks for something the
candidate does not have, leave it out entirely — a missing keyword is fine, an invented
one is fraud and gets this application thrown away.

The same applies to NUMBERS. Never state a quantity the original does not — not years
of experience, team sizes, percentages, user counts or project counts. If the original
says two years, it is two years, even where the posting asks for five. Do not round up,
do not aggregate separate figures into a larger one, and do not add a number where the
original gave none.

TECHNOLOGIES THE CANDIDATE ACTUALLY HAS (the complete allowed set):
{inventory}
{correction}
ORIGINAL RESUME:
{resume_text}

TARGET JOB: {title} at {company}
JOB DESCRIPTION:
{job_description}

What you SHOULD do:
1. Move the most relevant existing experience to the top
2. Rephrase existing bullets in the job's own vocabulary — but only where the
   underlying work genuinely matches
3. Foreground the skills above that the job asks for; drop emphasis on ones it does not
4. Keep every date, company, job title and qualification exactly as written

Return the full tailored resume text only. No explanations, no commentary."""

        return clean_resume_text(await asyncio.to_thread(self.llm.complete, prompt))
