import pdfplumber
import re
from pathlib import Path
from urllib.parse import urlparse


RESUME_PATH = Path(__file__).parent.parent.parent / "resume" / "resume.pdf"


def parse_resume_pdf(path: str = None) -> dict:
    pdf_path = path or RESUME_PATH
    text = _extract_text(str(pdf_path))
    return {
        "parsed_text": text,
        "skills": _extract_skills(text),
        "experience": _extract_experience(text),
        "education": _extract_education(text),
        "links": extract_links(str(pdf_path)),
    }


def extract_links(path: str) -> dict:
    """
    {"linkedin": "https://...", "github": "https://..."} from the PDF's link
    annotations. Text extraction only sees the words "LinkedIn" and "Github" —
    the URLs behind them live in the annotations, so without this the tailored
    copy prints a reference no recruiter can follow.

    Keyed by the host's first label so the key matches the word printed on the
    contact line, which is what the renderer has to work from.
    """
    links = {}
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                for annotation in (page.hyperlinks or []):
                    uri = (annotation.get("uri") or "").strip()
                    if not uri:
                        continue
                    host = urlparse(uri).netloc.lower().removeprefix("www.")
                    key = host.split(".")[0]
                    if key:
                        links.setdefault(key, uri)
    except Exception:
        pass  # a resume with no links is normal; a parse failure must not block upload
    return links


def resume_links(resume: dict) -> dict:
    """
    The links to render on a tailored resume. Resumes uploaded before this was
    captured have none stored, so fall back to the annotations in the resume
    PDF on disk rather than dropping the links entirely.
    """
    stored = (resume or {}).get("links")
    if stored:
        return stored
    return extract_links(str(RESUME_PATH))


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
