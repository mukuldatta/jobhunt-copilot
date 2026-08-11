"""
Validate an LLM-tailored resume before it is turned into a PDF and submitted.

Two jobs:
  clean_resume_text()      -> strip LLM preamble/postamble/markdown wrappers
  validate_tailored_resume -> integrity gate (no fabricated skills, nothing
                              truncated, name/email/education preserved)

The apply pipeline treats a 'fail' as "do not submit this" and falls back to
the original resume, so the "never fabricate" rule holds even when the LLM
misbehaves.
"""

import re
from utils.resume_parser import _extract_skills

# A tailored resume legitimately expands abbreviations and uses synonyms — a
# resume saying "ML", "vector search" and "RESTful" is not fabricating when the
# rewrite says "Machine Learning", "Vector Database" and "REST API". Without
# these, honest rewrites were rejected and the original was submitted instead.
_SKILL_ALIASES = {
    "machine learning": ["machine learning", "ml", "mlops", "ml engineer"],
    "deep learning": ["deep learning", "dl", "neural network", "pytorch", "tensorflow"],
    "vector database": ["vector database", "vector db", "vector store", "vector index",
                        "vector search", "pinecone", "faiss", "chroma", "weaviate", "qdrant", "milvus"],
    "embeddings": ["embedding", "embeddings", "vector embedding"],
    "rest api": ["rest api", "rest apis", "restful", "rest", "api", "apis"],
    "nlp": ["nlp", "natural language processing"],
    "data engineering": ["data engineering", "data pipeline", "etl", "elt", "airflow", "spark"],
    "ci/cd": ["ci/cd", "cicd", "continuous integration", "github actions", "jenkins"],
    "microservices": ["microservice", "microservices"],
    "graphql": ["graphql"],
    "kubernetes": ["kubernetes", "k8s", "eks", "gke"],
    "docker": ["docker", "container"],
    "fine-tuning": ["fine-tuning", "fine tuning", "finetune", "lora", "peft"],
    "sql": ["sql", "postgres", "postgresql", "mysql"],
    "nosql": ["nosql", "mongodb", "dynamodb", "cassandra"],
}


def _mentions_skill(text_norm: str, skill: str) -> bool:
    """True if the original resume refers to this skill in any recognised form."""
    aliases = _SKILL_ALIASES.get(skill.lower(), [skill.lower()])
    return any(re.search(r"(?<!\w)" + re.escape(a) + r"(?!\w)", text_norm) for a in aliases)

_PREAMBLE = [
    r"here('?s| is)\b", r"sure[,!]", r"certainly[,!]", r"absolutely[,!]",
    r"(below|the following) is\b", r"tailored resume\s*:?\s*$",
    r"(i have|i've)\b.*(tailored|rewritten|updated|reframed)",
    r"^\s*resume\s*:?\s*$",
]
_POSTAMBLE = [
    r"(let me know|feel free|i hope this|hope this helps)",
    r"^\s*(note|please note)\s*:", r"this (resume|version)\b.*(highlight|emphasi|tailor|match)",
]


def clean_resume_text(text: str) -> str:
    if not text:
        return ""
    # drop markdown code fences
    text = re.sub(r"```[a-zA-Z]*\n?", "", text)
    lines = text.split("\n")

    def is_pre(ln):
        s = ln.strip().lower()
        return not s or any(re.search(p, s) for p in _PREAMBLE)

    def is_post(ln):
        s = ln.strip().lower()
        return not s or any(re.search(p, s) for p in _POSTAMBLE)

    while lines and is_pre(lines[0]):
        lines.pop(0)
    while lines and is_post(lines[-1]):
        lines.pop()
    return "\n".join(lines).strip()


def _distinctive_tokens(lines: list) -> list:
    """
    Institution acronyms worth preserving (e.g. UMBC, JNTU). Restricted to
    all-caps acronyms of length >= 3 so common short tokens (IT, US) and
    ordinary words (Data, Tech) can't produce false 'preserved' matches. If an
    education line has no such acronym, it contributes nothing and the check is
    simply skipped rather than firing a false failure.
    """
    stop = {"the", "and", "for", "usa", "btech", "mtech", "phd", "gpa", "cgpa"}
    toks = set()
    for ln in lines or []:
        for m in re.findall(r"\b[A-Z]{3,}\b", ln):
            t = m.lower()
            if t not in stop:
                toks.add(t)
    return list(toks)


def validate_tailored_resume(tailored: str, resume: dict, *, user_name: str = None,
                             user_email: str = None) -> dict:
    """
    Returns {"ok": bool, "text": cleaned, "severity": ok|warn|fail, "issues": [..]}.
    ok is False only for 'fail' (integrity-breaking) problems.
    """
    cleaned = clean_resume_text(tailored or "")
    resume = resume or {}
    original = resume.get("parsed_text", "") or ""
    orig_skills = {s.lower() for s in (resume.get("skills") or [])}

    issues, severity = [], "ok"

    def escalate(level):
        nonlocal severity
        rank = {"ok": 0, "warn": 1, "fail": 2}
        if rank[level] > rank[severity]:
            severity = level

    if not cleaned or len(cleaned) < 200:
        return {"ok": False, "text": cleaned, "severity": "fail",
                "issues": ["Tailored resume is empty or far too short."]}

    # 1) Fabricated skills — the core integrity check. A skill counts as present
    # if the original lists it OR mentions it in any recognised form, so honest
    # expansions ("ML" -> "Machine Learning") aren't treated as fabrication.
    original_norm = " ".join(original.lower().split())
    added = [s for s in _extract_skills(cleaned)
             if s.lower() not in orig_skills and not _mentions_skill(original_norm, s)]
    if added and orig_skills:
        issues.append(f"Introduces skills not in the original resume: {', '.join(added)}")
        escalate("fail")

    # 2) Length sanity (truncation / bloat).
    if original:
        ratio = len(cleaned) / max(len(original), 1)
        if ratio < 0.45:
            issues.append(f"Only {int(ratio * 100)}% of original length — likely truncated.")
            escalate("fail")
        elif ratio > 1.8:
            issues.append(f"{int(ratio * 100)}% of original length — possible bloat/hallucination.")
            escalate("warn")

    # 3) Identity preserved.
    if user_name:
        parts = [p for p in user_name.split() if len(p) > 1]
        if parts and not all(p.lower() in cleaned.lower() for p in parts):
            issues.append("Candidate name is not fully present.")
            escalate("warn")
    if user_email and user_email.lower() not in cleaned.lower():
        issues.append("Contact email is missing.")
        escalate("warn")

    # 4) Education preserved.
    edu_tokens = _distinctive_tokens(resume.get("education") or [])
    if edu_tokens and not any(t in cleaned.lower() for t in edu_tokens):
        issues.append("Education section appears to be dropped.")
        escalate("fail")

    return {"ok": severity != "fail", "text": cleaned, "severity": severity, "issues": issues}
