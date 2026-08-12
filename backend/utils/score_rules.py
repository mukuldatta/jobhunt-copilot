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


def _mentions(haystack: str, term: str) -> bool:
    term = term.lower().strip()
    if not term:
        return False
    if term in _NEEDS_WORD_BOUNDARY or len(term) <= 3:
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", haystack) is not None
    return term in haystack


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
