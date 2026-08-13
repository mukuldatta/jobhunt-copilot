import re
import hashlib
import unicodedata

# The one list of what counts as an Indian location. mongodb.py builds its
# region filter from this; it used to keep a second, longer list of its own, so
# a job in Gurugram was "in region" to the query layer and not to the parser.
INDIA_LOCATIONS = (
    "india", "hyderabad", "bangalore", "bengaluru", "pune", "chennai",
    "mumbai", "delhi", "noida", "gurugram", "gurgaon", "kolkata", "ahmedabad",
)

INDIA_CITIES = set(INDIA_LOCATIONS)


def india_location_regex() -> str:
    """Alternation for a case-insensitive Mongo $regex on the location field."""
    return "|".join(INDIA_LOCATIONS)


def clean_description(text: str) -> str:
    """
    Strip markup and control characters, and leave the words alone.

    This used to end with encode("ascii", "ignore"), which silently deleted
    every non-Latin character from every posting — including the rupee sign, so
    an Indian salary line read "12,00,000 per annum" with no unit at all. Only
    control characters are unsafe to keep; the rest is the content.
    """
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+", " ", text)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
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


_RELEVANT_KEYWORDS = [
    "ai", "ml", "machine learning", "data", "engineer",
    "software", "python", "backend", "llm", "genai",
    "nlp", "deep learning", "analyst", "scientist",
    "developer", "full stack", "fullstack", "devops",
    "mlops", "platform", "infrastructure", "cloud",
    "api", "microservices", "architect",
]

_IRRELEVANT_KEYWORDS = [
    "sales", "marketing", "hr", "recruiter", "finance",
    "accountant", "graphic design", "ux designer", "ui designer",
    "product manager", "project manager", "business analyst",
    "content writer", "seo", "social media",
]


def is_relevant_job(title: str) -> bool:
    """
    Is this posting worth spending a scoring call on?

    Matched on word boundaries via score_rules.mentions. As bare substrings,
    "ai" admitted Maintenance Technician, Trainee and Captain; "ml" admitted
    anything with "html"; "api" admitted Therapist and Rapid. Each one then
    cost an LLM call to score and rank a job that was never a candidate.
    """
    from utils.score_rules import mentions

    title_lower = (title or "").lower()
    has_relevant = any(mentions(title_lower, kw) for kw in _RELEVANT_KEYWORDS)
    has_irrelevant = any(mentions(title_lower, kw) for kw in _IRRELEVANT_KEYWORDS)
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
