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


def dedup_key(title: str, company: str) -> str:
    """
    Identity of a *role*, independent of the listing URL. The same job is
    re-posted under different URLs (Naukri re-lists, Indeed varies its `jk`
    tracking key), which slipped past URL-based job_ids and wasted scoring
    quota on duplicates — and would have applied to the same role repeatedly.
    """
    def norm(s: str) -> str:
        s = (s or "").lower()
        s = re.sub(r"[^a-z0-9 ]+", " ", s)      # drop punctuation
        s = re.sub(r"\b(pvt|private|ltd|limited|inc|llp|technologies|solutions)\b", " ", s)
        return " ".join(s.split())
    return hashlib.md5(f"{norm(title)}|{norm(company)}".encode()).hexdigest()


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


# Where a posting starts saying what it actually wants. Everything before this is
# usually company boilerplate.
_REQUIREMENT_MARKERS = (
    "requirement", "qualification", "what you'll need", "what you will need",
    "what we're looking for", "what we are looking for", "must have", "must-have",
    "skills required", "required skills", "you should have", "you have",
    "key skills", "desired skills", "eligibility", "who you are",
)


def truncate_description(text: str, max_chars: int = 4000) -> str:
    """
    Keep the opening AND the requirements when a posting is too long to send.

    Plain head truncation was harmless while every description was a ~100-char
    teaser. Real postings run 800-7000 chars and put the requirements last, so
    cutting from the front threw away the only part that says what the employer
    wants — leaving the scorer and the tailor working from company boilerplate.
    """
    if len(text) <= max_chars:
        return text

    head_budget = max_chars // 2
    lowered = text.lower()
    starts = [lowered.find(m) for m in _REQUIREMENT_MARKERS]
    starts = [i for i in starts if i > head_budget]

    if not starts:
        return text[:max_chars] + "..."

    cut = min(starts)
    tail_budget = max_chars - head_budget
    return f"{text[:head_budget].rstrip()}\n...\n{text[cut:cut + tail_budget]}"
