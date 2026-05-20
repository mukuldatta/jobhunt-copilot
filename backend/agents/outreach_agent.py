import os
from llm_provider import LLMProvider
from db.mongodb import get_resume
from dotenv import load_dotenv

load_dotenv()


class OutreachAgent:
    def __init__(self):
        self.llm = LLMProvider(provider=os.getenv("LLM_PROVIDER", "groq"))

    async def generate(self, job: dict) -> str:
        resume = await get_resume()
        resume_text = resume.get("parsed_text", "")[:800] if resume else ""

        title = job.get("title", "")
        company = job.get("company", "")
        match_score = job.get("match_score", 0)

        prompt = f"""Write a short, personalized LinkedIn outreach message to a recruiter or hiring manager at {company} for the {title} role.

APPLICANT BACKGROUND:
{resume_text}

MATCH SCORE: {match_score}%

Rules:
- Keep it under 300 characters (LinkedIn connection request limit)
- Be specific about the role and company
- Mention one concrete relevant skill or achievement
- Sound human, not templated
- Do NOT start with "I hope this message finds you well"
- Do NOT use "I am reaching out because..."

Just output the message text, nothing else."""

        return self.llm.complete(prompt)
