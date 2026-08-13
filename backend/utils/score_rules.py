"""
The parts of a match score that are facts, not judgements.

Skills and location were being asked of an LLM even though both are computable:
location is a lookup table that was literally written out in the prompt as
instructions, and skills is a set intersection against the resume the parser
already extracted. Asking a model for a number it cannot derive means it will
invent one — which is how a Java/Spring banking role scored 40/40 on skills
while naming Java as a gap.

Computing them here has three consequences beyond accuracy: the gap list is
derived from the same pass as the score so the two cannot contradict each other,
the result is auditable (you can show which skills matched), and the model is
left with only the two dimensions that genuinely need reading comprehension.
"""

import re
from typing import Optional

# Bump whenever scoring changes in a way that makes old numbers incomparable.
# Scores carry this, so stale ones can be found and re-run without wiping the
# collection — and, more importantly, so the apply queue can refuse to select
# from a judgement produced by a scorer we no longer trust.
#   1 = single LLM call, "<0-40>" placeholders (produced the fabricated 100s)
#   2 = skills and location computed; LLM judges experience and domain only
#   3 = experience computed from the demanded years too; LLM judges domain only
#   4 = computed dimensions read the whole posting, not the truncated prompt
SCORER_VERSION = 4

SKILLS_MAX = 40
LOCATION_MAX = 10

# Skills whose plain name is too common to match on as a bare word.
_NEEDS_WORD_BOUNDARY = {"r", "go", "c", "ai", "ml", "sql", "aws", "git", "nlp", "rag"}

# Alternate spellings the JD may use for a skill the resume names once.
_ALIASES = {
    "node.js": ["node", "nodejs"],
    "scikit-learn": ["sklearn", "scikit learn"],
    "postgresql": ["postgres"],
    "ci/cd": ["ci cd", "cicd", "continuous integration"],
    "fine-tuning": ["fine tuning", "finetuning"],
    "embeddings": ["embedding", "vector search"],
    "rag": ["retrieval augmented generation", "retrieval-augmented"],
    "nlp": ["natural language processing"],
    "openai": ["gpt-4", "gpt4", "chatgpt"],
    "pytorch": ["torch"],
    "kubernetes": ["k8s"],
}


def mentions(haystack: str, term: str) -> bool:
    """
    Does `haystack` name `term`?

    Short and ambiguous terms are matched on word boundaries — bare substring
    matching for "ai" finds it inside "maintenance", "trainee" and "captain",
    and for "go" inside "algorithm". Longer, more distinctive terms stay on
    substring matching so "kubernetes" still matches "kubernetes-based".
    """
    term = term.lower().strip()
    if not term:
        return False
    # _ALIASES was written for exactly this and then never consulted, so a
    # posting asking for "sklearn" or "k8s" counted as a gap against a resume
    # that lists scikit-learn and Kubernetes.
    for form in [term] + _ALIASES.get(term, []):
        if form in _NEEDS_WORD_BOUNDARY or len(form) <= 3:
            if re.search(rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9])", haystack):
                return True
        elif form in haystack:
            return True
    return False


_mentions = mentions        # the original private name, still used below


def skills_match(description: str, resume_skills: list) -> dict:
    """
    Score the skills dimension by what the posting actually asks for that the
    resume can evidence.

    Returns points out of SKILLS_MAX, plus the matched and missing lists. The
    denominator is the resume skills the JD mentions at all — a posting that
    names four of your skills and nothing else is a full match on skills, even
    though you know twenty more.
    """
    text = (description or "").lower()
    matched = [s for s in (resume_skills or []) if _mentions(text, s)]

    # What the posting asks for that the resume cannot evidence. Kept to terms
    # the JD emphasises, so a single passing mention does not become a "gap".
    missing = []
    for term, count in _demanded_terms(text).items():
        # Arguments are deliberately the other way round here: the question is
        # whether some resume skill *covers* the demanded term ("spring" covers
        # a JD asking for "spring boot"), so the term is the haystack.
        if count >= 2 and not any(_mentions(term, s.lower()) for s in (resume_skills or [])):
            if not _mentions(text_of(matched), term):
                missing.append(term)

    total_signal = len(matched) + len(missing)
    if total_signal == 0:
        # The JD names nothing recognisable either way — no evidence, so no
        # opinion. Caller decides what to do with a None.
        return {"points": None, "matched": [], "missing": []}

    ratio = len(matched) / total_signal
    # Ratio alone is too kind to a thin overlap: a JD that happens to name
    # Python and nothing else scores 1.0 and takes full marks. Cap by how much
    # evidence there actually is, so full marks need a real overlap rather than
    # an unopposed one.
    points = min(round(ratio * SKILLS_MAX), len(matched) * 10)
    return {
        "points": points,
        "matched": matched,
        "missing": missing[:5],
    }


def text_of(items) -> str:
    return " ".join(items).lower()


# --- Experience -------------------------------------------------------------
#
# Years were being judged by the LLM, and it was not good at it: a Gen AI
# Engineer posting asking for 10-12 years scored 28/30 on experience against a
# 3-year resume, took a 94 overall, and got applied to. The demanded range is
# printed in the posting and the candidate's years are in the profile, so this
# is the same class of thing as skills and location — a fact, not a judgement.

EXPERIENCE_MAX = 30

# "3-5 years", "3 - 6 Years", "6 to 12 years", "5+ yrs", "5 years of experience".
_YEARS_RE = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:[-–—]|to)?\s*(\d{1,2})?\s*(?:\+|plus)?\s*"
    r"(?:year|yr)s?\b",
    re.I,
)

# Not every "N years" in a posting is a requirement:
#   "a 35 year old IT services organization"  — corporate history
#   "founded more than 40 years ago"          — the same, phrased differently
#   "15 years full time education"            — Indian schooling notation (10+2+3)
_NOT_A_REQUIREMENT = re.compile(
    r"\byears?\s*old\b|\byears?\s+ago\b|\byears?\b[^.]{0,24}\beducation\b",
    re.I,
)

# Nobody requires 30 years. A number this large is a scrape artefact: these are
# ranges whose dash was destroyed back when clean_description stripped
# non-ASCII, so "5–8 years" arrived as "58 years" and "3–5" as "35". Existing
# rows still carry them; a re-scrape fixes the source.
_MAX_PLAUSIBLE_YEARS = 25


def required_years(description: str) -> Optional[int]:
    """
    The fewest years the posting asks for, or None when it does not say.

    The lower bound is what matters — a "5-9 years" role is gated at 5. Where a
    posting names several figures the smallest wins, so an incidental mention
    cannot disqualify a job on its own. That also rescues the corrupted rows:
    a listing reading "Experience: 0-5 Yrs" in one place and a mangled
    "24 Years" in another is correctly read as entry level.

    Zero is a real answer ("0-5 Yrs" is a graduate posting) and must survive as
    0; None means unknown and must never be read as zero.
    """
    text = description or ""
    found = []
    for m in _YEARS_RE.finditer(text):
        window = text[max(0, m.start() - 14): m.end() + 30]
        if _NOT_A_REQUIREMENT.search(window):
            continue
        lo = int(m.group(1))
        if 0 <= lo <= _MAX_PLAUSIBLE_YEARS:
            found.append(lo)
    return min(found) if found else None


def experience_points(description: str, candidate_years) -> int:
    """
    Points out of EXPERIENCE_MAX for how the demanded years line up.

    Postings inflate, and a year or two over is routine, so the curve starts
    gently and then falls hard: by five years past the candidate's experience
    the role is for somebody else, and a high skills overlap should not be able
    to carry it into the apply queue.

    An unstated requirement scores in the middle. Silence is not evidence of a
    match, but it is not evidence against one either, and treating it as zero
    would bury every posting that simply does not mention years.
    """
    try:
        mine = float(candidate_years)
    except (TypeError, ValueError):
        mine = None

    need = required_years(description)
    if need is None or mine is None:
        return 20

    gap = need - mine
    if gap <= 0:
        return EXPERIENCE_MAX
    if gap <= 1:
        return 24
    if gap <= 2:
        return 18
    if gap <= 3:
        return 11
    if gap <= 4:
        return 5
    return 0


# Technologies worth noticing in a JD when the resume does not have them. Kept
# deliberately short: an unbounded keyword list produces noise, and the LLM
# still reports gaps of its own for anything outside it.
_KNOWN_TECH = [
    "java", "spring boot", "spring", ".net", "c#", "golang", "rust", "scala", "ruby",
    "php", "kotlin", "swift", "angular", "vue", "django", "flask", "spark", "hadoop",
    "kafka", "airflow", "snowflake", "databricks", "tableau", "power bi", "looker",
    "tensorflow", "keras", "hugging face", "mlflow", "kubeflow", "sagemaker",
    "terraform", "jenkins", "ansible", "gcp", "google cloud", "salesforce", "sap",
    "hipaa", "hl7", "fhir", "pci", "sox", "data governance", "master data",
    "selenium", "cypress", "graphql", "elasticsearch", "cassandra", "dynamodb",
]


def _demanded_terms(text: str) -> dict:
    return {t: text.count(t) for t in _KNOWN_TECH if _mentions(text, t)}


# Location is a rule, and was already written as one inside the prompt.
_TIER_10 = ("hyderabad", "bangalore", "bengaluru", "pune")
_REMOTE = ("remote", "work from home", "wfh", "anywhere")


def location_points(location: str, description: str = "") -> int:
    loc = f"{location or ''} {description[:400] if description else ''}".lower()
    if any(c in loc for c in _TIER_10):
        return 10
    if "india" in loc:
        return 8
    if any(r in loc for r in _REMOTE):
        return 5
    if any(t in loc for t in ("united states", "usa", " us ", "california", "texas", "new york")):
        return 3
    return 2
