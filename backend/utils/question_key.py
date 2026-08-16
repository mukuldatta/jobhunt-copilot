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
