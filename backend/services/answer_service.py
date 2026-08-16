"""
Resolve job-application form questions into answers from your stored profile.

Resolution order (cheapest and most certain first):
  1. Learned Q&A — you already answered this exact question before.
  2. Deterministic profile rules — unambiguous questions (sponsorship, notice
     period, CTC, relocation, ...) answered straight from structured fields.
  3. LLM semantic mapping — reworded/novel questions mapped onto your profile.
     The prompt is instructed to return UNKNOWN rather than invent anything.
  4. UNKNOWN — recorded as a pending question so you answer it once in the
     Profile page and it is reused forever after.

Never guesses: an unresolved question makes the apply flow pause for a human.
"""

import os
import re
import json
import hashlib
from llm_provider import LLMProvider, RateLimited
from db.mongodb import get_apply_profile, record_pending_question
from utils.question_key import question_key


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.S)


def _parse_answer(raw: str):
    """
    Pull (answer, evidence) out of the model's reply.

    Tolerant on the way in, strict on what it returns. Models wrap JSON in
    prose or fences often enough that failing on it would turn the grounding
    check into an outage: every answer discarded, every question emailed. A
    reply that is not JSON at all is still read as a bare answer — it then has
    to survive the grounding check with no evidence, which is exactly the
    treatment an unsupported answer deserves.
    """
    raw = (raw or "").strip()
    if not raw:
        return "", ""
    m = _JSON_OBJ_RE.search(raw)
    if m:
        try:
            obj = json.loads(m.group())
            if isinstance(obj, dict):
                return (str(obj.get("answer", "")).strip(),
                        str(obj.get("evidence", "")).strip())
        except (ValueError, TypeError):
            pass
    return raw.splitlines()[0].strip().strip('"').strip("'"), ""


_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _flat(text) -> str:
    """
    Lowercase, punctuation-free, single-spaced — for asking "does this text
    appear in that text" without a quote mark or a colon deciding the answer.
    """
    return " ".join(_ALNUM_RE.sub(" ", str(text or "").lower()).split())


def numeric_text(value):
    """
    '38 LPA' -> '38', '3.5 years' -> '3.5', 'Immediately' -> None.

    The single definition, because the profile stores its values with the unit
    attached — expected_ctc is literally the string "30 LPA" — and a LinkedIn
    salary box is a text input that validates as a decimal. Every source of an
    answer therefore needs the same coercion on the way out, and a second copy
    of this rule living somewhere else is how the two would stop agreeing.
    """
    if value is None:
        return None
    m = _NUMBER_RE.search(str(value))
    return m.group() if m else None


def question_token(question: str) -> str:
    """
    Short stable id for a question, derived from its normalised text.

    Used to tie an emailed question to the reply that answers it. Derived rather
    than stored so it needs no schema change and stays correct for questions
    recorded before any of this existed: the same wording always yields the same
    token, and pending questions are already keyed on the same normalisation.
    """
    return hashlib.sha1(question_key(question).encode("utf-8")).hexdigest()[:10]


def _yn(flag: bool) -> str:
    return "Yes" if flag else "No"


# Words that appear in nearly every form question, so matching a resume line on
# them ranks the whole resume equally and selects nothing.
_STOPWORDS = {
    "a", "an", "and", "any", "are", "as", "at", "be", "by", "can", "do", "does",
    "experience", "for", "from", "have", "how", "in", "is", "it", "many", "much",
    "of", "on", "or", "please", "the", "this", "to", "what", "which", "will",
    "with", "years", "year", "you", "your", "yes", "no", "if", "that", "we",
}


def _tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9+#.]{2,}", (text or "").lower())
            if w not in _STOPWORDS}


def _relevant_resume(resume_text: str, question: str, budget: int = 6000) -> str:
    """
    Return the parts of the resume that bear on this question.

    The prompt used to carry resume_text[:1200] — the first 1200 characters,
    which on a real resume is the name, contact block and summary. A question
    about years of Kubernetes never saw the line naming Kubernetes, so the LLM
    correctly answered UNKNOWN and the question landed in the pending list even
    though the resume said it plainly.

    Ranking is keyword overlap, not embeddings: it is deterministic, needs no
    extra service, and this only has to beat "the first 1200 characters".
    Selected blocks are emitted in their original order so dates and employers
    stay attached to the bullets underneath them.
    """
    if not resume_text:
        return ""
    if len(resume_text) <= budget:
        return resume_text

    blocks = [b.strip() for b in re.split(r"\n\s*\n", resume_text) if b.strip()]
    # A resume with no blank lines is one block; fall back to lines so there is
    # something to rank at all.
    if len(blocks) < 3:
        blocks = [b.strip() for b in resume_text.splitlines() if b.strip()]

    qt = _tokens(question)
    if not qt:
        return resume_text[:budget]

    scored = []
    for i, b in enumerate(blocks):
        overlap = len(qt & _tokens(b))
        if overlap:
            # Normalise by length so a long block does not win on volume alone.
            scored.append((overlap / (1 + len(b) / 400.0), i, b))
    scored.sort(reverse=True)

    chosen, used = set(), 0
    for _, i, b in scored:
        # A matched heading is worthless without the lines beneath it: the block
        # "EDUCATION" is what a question about education scores against, while
        # the degree that answers it sits in the next block and shares no words
        # with the question at all.
        candidates = (i, i + 1) if len(b) < 60 else (i,)
        for j in candidates:
            if j in chosen or j >= len(blocks):
                continue
            if used + len(blocks[j]) > budget:
                continue
            chosen.add(j)
            used += len(blocks[j])
    picked = [(j, blocks[j]) for j in chosen]
    if not picked:
        # Nothing matched — the head of the resume is still the best guess, and
        # the prompt's UNKNOWN rule keeps a miss from turning into a fabrication.
        return resume_text[:budget]
    picked.sort()
    return "\n\n".join(b for _, b in picked)


class AnswerResolver:
    def __init__(self, profile: dict = None, resume_text: str = ""):
        self.profile = profile or {}
        self.resume_text = resume_text or ""
        self._llm = None
        self._llm_cache = {}
        self._rate_limited = False

    @classmethod
    async def load(cls, resume_text: str = ""):
        return cls(await get_apply_profile(), resume_text)

    @property
    def llm(self):
        if self._llm is None:
            self._llm = LLMProvider(provider=os.getenv("LLM_PROVIDER", "groq"))
        return self._llm

    # ── public ───────────────────────────────────────────────────────────────

    async def answer(self, question: str, options: list = None, kind: str = "text"):
        """
        Return an answer string, or None if it can't be answered safely.
        `options` are the choices for a radio/select; `kind` is text|number|radio|select.
        """
        if not question or not question.strip():
            return None

        self._rate_limited = False
        ans = self._from_learned(question)
        if ans is None:
            ans = self._from_rules(question, kind)
        if ans is None:
            ans = self._from_llm(question, options, kind)

        # A numeric field takes a number from whichever source answered. Doing
        # this here rather than at each source is the point: the profile holds
        # "30 LPA", the learned store holds "38 LPA" against a question that
        # says *(in lakhs)*, and the LLM will happily write "3 years" — three
        # sources, one field, one rule. Every one of those was typed verbatim
        # into a box that wanted a decimal and rejected it.
        if ans is not None and kind == "number":
            coerced = numeric_text(ans)
            if coerced is None:
                # "Immediately" in a field counting days. We have an answer and
                # it is not the kind of thing being asked for, so this is a
                # question for you — not a number to be invented.
                ans = None
            else:
                ans = coerced

        if ans is None:
            # Only record a question we genuinely couldn't answer. A rate-limited
            # call never got to try, so recording it would pollute your pending
            # list with questions the profile may well cover.
            if not self._rate_limited:
                try:
                    await record_pending_question(question)
                except Exception:
                    pass
            return None

        # If the field has fixed options, snap the answer to the closest one.
        if options:
            ans = self._snap_to_option(ans, options) or ans
        return ans

    # ── 1. learned answers ───────────────────────────────────────────────────

    def _from_learned(self, question: str):
        # question_key, not _norm: the store is keyed the same way on write, and
        # a trailing "*" or a duplicated fragment of the label must not read as
        # a different question. It already did, four times out of eight.
        q = question_key(question)
        for entry in self.profile.get("qa", []) or []:
            eq = question_key(entry.get("question", ""))
            if not eq:
                continue
            if eq == q or (len(eq) > 15 and (eq in q or q in eq)):
                val = (entry.get("answer") or "").strip()
                if val:
                    return val
        return None

    # ── 2. deterministic rules ───────────────────────────────────────────────

    def _from_rules(self, question: str, kind: str = "text"):
        p = self.profile
        q = _norm(question)

        def any_(*w):
            return any(x in q for x in w)

        # Sponsorship — check before generic work-authorization wording.
        if any_("sponsor", "sponsorship", "visa"):
            requires = bool(p.get("requires_sponsorship", False))
            if any_("without", "not require", "no sponsorship"):
                return _yn(not requires)
            if any_("require", "need", "will you", "would you", "future"):
                return _yn(requires)

        if any_("authorized", "authorised", "eligible", "legal right", "legally") and any_("work", "employ"):
            return _yn(bool(p.get("authorized_to_work", True)))

        if any_("felony", "convicted", "criminal record", "criminal history"):
            return "No"

        # "Are you an immediate joiner?" is a yes/no question. The same words in
        # a numeric box are asking how many days, so the answer "Yes" would be
        # thrown straight out.
        if kind != "number" and any_("immediate") and any_("join", "joiner", "available", "start"):
            return "Yes"

        # When you can start, asked in either currency: a number of days, or a
        # date/word. The profile carries both — notice_period_days is "0" and
        # earliest_start is "Immediately" — and the rules only ever reached for
        # the second. "How soon can you join?" matched no rule at all, so it
        # fell through to the LLM, which answered "Immediately" into a field
        # that wanted a decimal. The right answer was in the profile the whole
        # time; nothing was asking for it.
        if any_("notice period", "how soon", "when can you join", "when could you join",
                "joining time", "join us", "start date", "when can you start",
                "available to start", "earliest start", "date of joining",
                # "Are you an immediate joiner?" is answered "Yes" above when
                # there is somewhere to put a yes. In a numeric box the same
                # question is asking how many days, and the answer is 0.
                "immediate joiner", "joiner") or (
                "notice" in q and any_("period", "days", "how many", "how long", "serving")):
            wants_days = kind == "number" or any_("days", "notice")
            first = "notice_period_days" if wants_days else "earliest_start"
            second = "earliest_start" if wants_days else "notice_period_days"
            for key in (first, second):
                val = str(p.get(key) or "").strip()
                if val:
                    return val
            return None

        if any_("ctc", "salary", "compensation", "annual package", "in hand"):
            # Which one is being asked for, and the unqualified case. A form
            # that just says "What is your CTC?" means the one you are on —
            # that is the convention every Indian posting uses, and it matched
            # no rule at all before, so a fact already in the profile went to
            # the model to guess at.
            if any_("expected", "desired", "expecting", "asking"):
                return str(p.get("expected_ctc") or "") or None
            return str(p.get("current_ctc") or "") or None

        # Match on stems: "commute" never matches "commuting".
        if "relocat" in q:
            return _yn(bool(p.get("willing_to_relocate", True)))
        if any_("commut", "work from office", "on-site", "onsite", "on site",
                "hybrid", "in office", "in-office", "willing to travel", "travel to"):
            return _yn(bool(p.get("willing_onsite_hybrid", True)))
        if any_("night shift", "rotational shift", "work from home", "remote"):
            return _yn(bool(p.get("willing_onsite_hybrid", True)))

        if any_("current location", "current city", "which city", "where are you located", "your location"):
            return str(p.get("current_city") or "") or None

        if any_("bachelor", "degree", "graduate", "graduated", "qualification"):
            return _yn(bool(p.get("has_bachelors", True)))

        if any_("background check", "background verification", "drug test", "willing to undergo"):
            return "Yes"

        # Years of experience — checked before the language rule, because forms
        # phrase skills as "Python (Programming Language)" and that word would
        # otherwise be treated as a spoken-language question.
        years_q = (any_("how many years", "years of experience", "years experience",
                        "total experience", "yrs of experience")
                   or ("years" in q and any_("experience", "exp")))
        if years_q:
            for skill, yrs in (p.get("skill_years") or {}).items():
                if skill and _norm(skill) in q and str(yrs).strip():
                    return str(yrs)
            return str(p.get("total_years_experience") or "") or None

        # Spoken-language proficiency. "programming language" is not one.
        if any_("proficiency", "fluency", "do you speak", "language") and "programming" not in q:
            langs = p.get("languages") or {}
            for lang, level in langs.items():
                if lang and lang != "*" and _norm(lang) in q:
                    return str(level)
            default = langs.get("*") or langs.get("default")
            if default:
                return str(default)

        return None

    # ── 3. LLM semantic mapping ──────────────────────────────────────────────

    def _from_llm(self, question: str, options: list = None, kind: str = "text"):
        cache_key = (_norm(question), tuple(options or []))
        if cache_key in self._llm_cache:
            return self._llm_cache[cache_key]

        profile_json = json.dumps(
            {k: v for k, v in self.profile.items() if k not in ("qa", "_id", "updated_at")},
            default=str, indent=2,
        )
        learned = "\n".join(
            f"- {e.get('question')} => {e.get('answer')}"
            for e in (self.profile.get("qa") or [])[:25]
        ) or "(none)"
        opts = f"\nALLOWED ANSWERS (choose exactly one): {options}" if options else ""

        prompt = f"""You are filling a job application form on behalf of a candidate.
Answer the question using ONLY the candidate profile and resume below.

CANDIDATE PROFILE (JSON):
{profile_json}

PREVIOUSLY ANSWERED QUESTIONS:
{learned}

RESUME (the passages bearing on this question):
{_relevant_resume(self.resume_text, question)}

FORM QUESTION ({kind}): {question}{opts}

Reply with a JSON object and nothing else:
{{"answer": "<the value to type>", "evidence": "<text copied word-for-word from the profile or resume above that establishes it>"}}

RULES:
- If the profile and resume do not establish an answer, reply exactly: {{"answer": "UNKNOWN", "evidence": ""}}
- `evidence` must be copied verbatim from the material above. Do not paraphrase
  it, do not summarise it, and do not write a sentence of your own. It is
  checked against the source, and an answer whose evidence is not found there
  is discarded.
- Never invent salary, years of experience, dates, or qualifications. If a
  number is not written above, the answer is UNKNOWN.
- For yes/no questions the answer is exactly Yes or No.
- For numeric questions the answer is digits only (e.g. 3)."""

        try:
            raw = (self.llm.complete(prompt) or "").strip()
        except RateLimited as e:
            self._rate_limited = True
            print(f"    AnswerResolver rate limited: {e}")
            return None
        except Exception as e:
            print(f"    AnswerResolver LLM error: {e}")
            return None

        answer, evidence = _parse_answer(raw)
        if not answer or answer.upper().startswith("UNKNOWN") or len(answer) > 120:
            self._llm_cache[cache_key] = None
            return None

        if not self._is_grounded(answer, evidence, options):
            # The model produced something the material does not support. That
            # is the case this whole path exists to catch, and the honest
            # outcome is the same as knowing nothing: ask.
            print(f"    [ungrounded] {question[:52]} -> {answer!r} "
                  f"(evidence: {evidence[:48]!r})")
            self._llm_cache[cache_key] = None
            return None

        if kind == "number":
            m = re.search(r"\d+", answer)
            answer = m.group() if m else answer
        self._llm_cache[cache_key] = answer
        return answer

    def _is_grounded(self, answer: str, evidence: str, options: list = None) -> bool:
        """
        Is this answer supported by the profile or the resume?

        Two checks, deliberately unequal, because the cost of being wrong is
        unequal. A number is the thing that must never be invented — a
        fabricated salary or year of experience goes onto a real application
        and cannot be withdrawn — so any digit in the answer has to appear in
        the evidence, and the evidence has to appear in the source material.

        A "Yes" cannot be verified that way; nothing in a resume literally says
        "yes". There the requirement is weaker but not nothing: the model still
        has to point at real text, which is what separates an inference from an
        invention.

        Being too strict here has its own cost. Every rejection is an email to
        you and a posting that waits, so the rule is aimed at fabrication
        rather than at imperfect quoting.
        """
        haystack = _flat(self.resume_text) + " " + _flat(
            json.dumps({k: v for k, v in self.profile.items()
                        if k not in ("_id", "updated_at")}, default=str)
        )
        ev = _flat(evidence)

        # An answer that is one of the form's own options is a choice, not a
        # claim about a fact — the form wrote the words, we only picked one.
        chosen_option = bool(options) and any(_flat(o) == _flat(answer) for o in options)

        digits = re.findall(r"\d+(?:\.\d+)?", str(answer))
        if digits:
            if not ev:
                return False
            # The number has to be in the quote as a number in its own right,
            # and the quote has to be in the source. Plain substring matching
            # accepted "3" on the strength of a graduation year of 2023, which
            # is how a fabricated figure sneaks past a check that looks strict.
            for d in digits:
                if not re.search(rf"(?<!\d){re.escape(d)}(?!\d)", ev):
                    return False
            return ev in haystack

        if chosen_option or _flat(answer) in ("yes", "no"):
            # Judgement from the material rather than a quote of it. Require a
            # real citation when one was offered, and let a bare choice stand
            # when it was not — the rules layer answers most of these anyway.
            return not ev or ev in haystack

        return bool(ev) and ev in haystack

    # ── option snapping ──────────────────────────────────────────────────────

    def _snap_to_option(self, answer: str, options: list):
        a = _norm(answer)
        for o in options:
            if _norm(o) == a:
                return o
        for o in options:
            no = _norm(o)
            if no and (a in no or no in a):
                return o
        return None
