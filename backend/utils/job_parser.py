import re
import hashlib

INDIA_CITIES = {"hyderabad", "bangalore", "bengaluru", "pune", "mumbai", "chennai", "delhi", "gurgaon", "noida"}


def clean_description(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+", " ", text)
    text = text.encode("ascii", "ignore").decode()
    return text.strip()


def generate_job_id(url: str, title: str, company: str) -> str:
    raw = f"{url}{title}{company}".lower().strip()
    return hashlib.md5(raw.encode()).hexdigest()


def extract_contract_type(title: str, description: str) -> str:
    text = f"{title} {description}".lower()
    if any(w in text for w in ["c2c", "corp to corp", "corp-to-corp"]):
        return "c2c"
    if any(w in text for w in ["contract to hire", "contract-to-hire", "c2h"]):
        return "contract_to_hire"
    if any(w in text for w in ["w2 contract", "w-2 contract", "w2 only"]):
        return "w2_contract"
    if any(w in text for w in ["contract", "contractor", "freelance"]):
        return "w2_contract"
    return "fulltime"


def is_india_job(location: str) -> bool:
    loc = location.lower()
    return any(city in loc for city in INDIA_CITIES) or "india" in loc


def is_priority_india_city(location: str) -> bool:
    loc = location.lower()
    return any(city in loc for city in {"hyderabad", "bangalore", "bengaluru", "pune"})


def is_relevant_job(title: str) -> bool:
    title_lower = title.lower()
    relevant_keywords = [
        "ai", "ml", "machine learning", "data", "engineer",
        "software", "python", "backend", "llm", "genai",
        "nlp", "deep learning", "analyst", "scientist",
        "developer", "full stack", "fullstack", "devops",
        "mlops", "platform", "infrastructure", "cloud",
        "api", "microservices", "architect",
    ]
    irrelevant_keywords = [
        "sales", "marketing", "hr ", "recruiter", "finance",
        "accountant", "graphic design", "ux designer", "ui designer",
        "product manager", "project manager", "business analyst",
        "content writer", "seo", "social media",
    ]
    has_relevant = any(kw in title_lower for kw in relevant_keywords)
    has_irrelevant = any(kw in title_lower for kw in irrelevant_keywords)
    return has_relevant and not has_irrelevant


def truncate_description(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."
