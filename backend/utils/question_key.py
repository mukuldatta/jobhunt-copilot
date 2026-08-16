"""
The one way a screening question is turned into a key.

A learned answer is only worth learning if the next posting can find it, and
"find" means this key matching. It was `" ".join(text.lower().split())` written
out separately in three places, which is exact-string matching under a
different name — so the store already holds:

    "What is your level of proficiency in Hindi?*"   -> Native or bilingual
    "What is your level of proficiency in Hindi?"    -> Native or bilingual
    "What is your current annual CTC in INR?"        -> 38 LPA
    "What is your current CTC (in lakhs)?"           -> 38 LPA

Four entries, two questions. A trailing asterisk — LinkedIn's required marker —
was enough to mint a second one and ask you again.

Kept deliberately dumb. Anything cleverer than this belongs in a semantic match
over the whole store, not in a key function that also has to agree with itself
across a database write and a later read.
"""

import re

# The question ends at its question mark. What follows is the form's own
# furniture: the required marker, a repeated fragment of the label, the stray
# second copy that the DOM walk sometimes picks up —
#   "Do you have 4+ years of coding experience in Python?\nDo you have 4"
# which is stored in the profile right now as its own distinct question.
_UPTO_QMARK = re.compile(r"^(.*?\?)")


def question_key(text: str) -> str:
    """Normalised lookup key for a form question. Empty string if there is none."""
    if not text:
        return ""
    s = " ".join(str(text).split()).lower()
    m = _UPTO_QMARK.match(s)
    if m:
        s = m.group(1)
    # Trailing required-markers and punctuation carry no meaning for matching.
    return s.rstrip(" *:•-–—").strip()


# Words that carry no referent: they change how a question is phrased, never
# what it is about. Removing them is what lets "current annual CTC in INR" and
# "current CTC (in lakhs)" be recognised as one question.
_NOISE = {
    # sentence furniture
    "a", "an", "the", "is", "are", "was", "do", "does", "did", "have", "has",
    "had", "in", "of", "for", "on", "at", "to", "with", "and", "or", "if",
    "what", "which", "how", "many", "much", "this", "that", "we", "us", "it",
    "be", "can", "will", "would", "your", "you", "yours", "please", "kindly",
    "specify", "mention", "state", "provide", "enter", "select", "choose",
    "note", "any", "some", "there", "their", "here", "about", "from", "as",
    # units and formats — "in lakhs" and "in INR" ask the same thing
    "inr", "rs", "rupees", "rupee", "usd", "dollar", "dollars", "lakh",
    "lakhs", "lpa", "annual", "annually", "annum", "per", "pa", "monthly",
    "yearly", "figure", "amount", "value", "number", "total",
    # "level of proficiency in X" and "how proficient in X" are one question;
    # the noun between them carries nothing.
    "level",
}

# Different spellings of one idea. Deliberately tiny: every entry here is a
# claim that two words mean the same thing, and a wrong claim answers a
# question with the wrong fact.
_SYNONYMS = {
    "currently": "current", "presently": "current", "present": "current",
    "expecting": "expected", "desired": "expected", "asking": "expected",
    "yrs": "years", "yr": "years", "year": "years",
    "exp": "experience", "experiance": "experience",
    "proficient": "proficiency", "fluent": "proficiency", "fluency": "proficiency",
    "ctc": "ctc", "salary": "ctc", "compensation": "ctc", "package": "ctc",
}

_TOKEN_RE = re.compile(r"[a-z0-9+.#]+")


def question_signature(text: str) -> frozenset:
    """
    The content of a question, as a set of tokens.

    Two questions with the same signature are the same question. This is
    equality after canonicalisation, NOT a similarity score, and that is the
    whole point — measured on the real store, no threshold exists that
    separates the pairs correctly:

        current annual CTC in INR / current CTC (in lakhs)   jaccard 0.40  (same question)
        proficiency in Hindi      / proficiency in English   jaccard 0.50  (different answers)
        CTC                       / expected CTC             overlap 1.00  (different answers)

    The pair that must match scores *lower* than two pairs that must not, so
    any threshold either misses the first or merges the others. Embeddings make
    this worse rather than better: "Hindi" and "English" sit next to each other
    in vector space precisely because they are the same kind of thing, and so
    do "current" and "expected".

    Numbers are kept, because "4+ years" and "2+ years" are different
    questions, and a "+" is dropped so that "4+" and "4" are not.
    """
    if not text:
        return frozenset()
    out = set()
    for tok in _TOKEN_RE.findall(question_key(text)):
        tok = tok.strip(".+#")
        if not tok or tok in _NOISE:
            continue
        out.add(_SYNONYMS.get(tok, tok))
    return frozenset(out)
