"""
Which boards the agent will submit applications to.

Kept separate from ApplyAgent so the orchestrator can consult it without
importing Playwright — a dry-run preview should never pay for a browser stack.

Scraping is unaffected by anything here. A board on this list is still scraped,
scored, ranked and shown in Review; it just never gets an automated submission,
and its jobs come back to you to finish by hand.
"""

# Indeed serves a bot challenge that cannot be cleared from an automated
# session — it fingerprints the automation channel itself, so the CAPTCHA is a
# refusal rather than a puzzle, and a headed window loops on it indefinitely.
# That is Indeed declining automated access, and we take the refusal.
#
# It costs less than its volume suggests: 176 scraped for 55 high matches (31%
# density) against LinkedIn's 53 from 69 (77%). To re-enable, drop the entry —
# ApplyAgent._apply_indeed is still here and wired.
APPLY_DISABLED = {
    "indeed": "Indeed blocks automated sessions — open the link and apply by hand.",
}


def apply_supported(source: str) -> bool:
    return source not in APPLY_DISABLED


def disabled_reason(source: str) -> str:
    return APPLY_DISABLED.get(source, "")
