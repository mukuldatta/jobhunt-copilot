import pdfplumber
import re
from pathlib import Path


RESUME_PATH = Path(__file__).parent.parent.parent / "resume" / "resume.pdf"


def parse_resume_pdf(path: str = None) -> dict:
    pdf_path = path or RESUME_PATH
    text = _extract_text(str(pdf_path))
    return {
        "parsed_text": text,
        "skills": _extract_skills(text),
        "experience": _extract_experience(text),
        "education": _extract_education(text),
    }


def _extract_text(path: str) -> str:
    text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text.strip()


def _extract_skills(text: str) -> list[str]:
    known_skills = [
        "Python", "FastAPI", "Django", "Flask",
        "JavaScript", "TypeScript", "React", "Node.js",
        "MongoDB", "PostgreSQL", "MySQL", "Redis", "Pinecone",
        "Docker", "Kubernetes", "AWS", "GCP", "Azure",
        "LangChain", "LangGraph", "CrewAI", "LlamaIndex",
        "OpenAI", "Anthropic", "Groq", "Hugging Face",
        "PyTorch", "TensorFlow", "scikit-learn", "pandas", "numpy",
        "Playwright", "Selenium", "BeautifulSoup",
        "Apache Kafka", "Apache Spark", "Airflow",
        "REST API", "GraphQL", "gRPC",
        "Git", "CI/CD", "GitHub Actions",
        "Machine Learning", "Deep Learning", "NLP", "RAG",
        "Vector Database", "Embeddings", "Fine-tuning",
        "SQL", "NoSQL", "Data Engineering", "ETL",
        "Pydantic", "SQLAlchemy", "Celery",
        "Linux", "Bash", "PowerShell",
    ]
    found = []
    text_lower = text.lower()
    for skill in known_skills:
        if skill.lower() in text_lower:
            found.append(skill)
    return found


def _extract_experience(text: str) -> list[str]:
    lines = text.split("\n")
    experience = []
    capture = False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if re.search(r"\b(experience|work history|employment)\b", line, re.IGNORECASE):
            capture = True
            continue
        if capture and re.search(r"\b(education|skills|projects|certifications)\b", line, re.IGNORECASE):
            capture = False
        if capture and len(line) > 20:
            experience.append(line)
    return experience[:10]


def _extract_education(text: str) -> list[str]:
    lines = text.split("\n")
    education = []
    capture = False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if re.search(r"\beducation\b", line, re.IGNORECASE):
            capture = True
            continue
        if capture and re.search(r"\b(experience|skills|projects|certifications)\b", line, re.IGNORECASE):
            capture = False
        if capture and len(line) > 10:
            education.append(line)
    return education[:5]
