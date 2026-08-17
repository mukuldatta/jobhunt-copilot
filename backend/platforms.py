"""
Which boards — and which employers — the agent will submit applications to.

Kept separate from ApplyAgent so the orchestrator can consult it without
importing Playwright — a dry-run preview should never pay for a browser stack.

Scraping is unaffected by anything here. A board on this list is still scraped,
scored, ranked and shown in Review; it just never gets an automated submission,
and its jobs come back to you to finish by hand.
"""

import os
import re

# Employers the agent never applies to on your behalf, whatever the score.
#
# Not a quality judgement the code is entitled to make — it is yours, and the
# only place it can be honoured is before an application is spent. Scraping,
# scoring and Review are untouched: an excluded employer's postings still show
# up to read, they simply never get an automated submission.
#
# Override the whole list with APPLY_EXCLUDE_COMPANIES (comma-separated).
EXCLUDED_COMPANIES = [
    c.strip() for c in os.environ.get(
        "APPLY_EXCLUDE_COMPANIES", "Tata Consultancy Services, TCS").split(",")
    if c.strip()
]


def excluded_company_pattern() -> str:
    """
    A regex matching any excluded employer, for the candidate query.

    Short names are anchored to word boundaries — "TCS" must not match
    "TCSion" or a company whose name merely contains those letters, while
    "Tata Consultancy Services" is distinctive enough to match as a substring
    and so survives the board writing it as "Tata Consultancy Services Ltd".
    """
    parts = []
    for name in EXCLUDED_COMPANIES:
        esc = re.escape(name)
        parts.append(rf"\b{esc}\b" if len(name) <= 5 else esc)
    return "|".join(parts)


def company_excluded(company: str) -> bool:
    """Is this employer on the do-not-apply list?"""
    pattern = excluded_company_pattern()
    if not pattern or not company:
        return False
    return re.search(pattern, str(company), re.I) is not None


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


# Browser profile directory names, under backend/.browser_profiles/.
#
# The scraper and the apply agent deliberately keep separate Naukri profiles:
# Chrome cannot open one profile directory twice, and scraping (every 30 min)
# and applying (every 90 min) are independent scheduler jobs that will
# eventually overlap. Sharing one directory would mean whichever started second
# failed to launch at all.
#
# The names live here — the one module both agents already import and that
# costs nothing to import — because the split only works if both sides agree on
# the strings. They did not: the scraper's profile was a bare literal in
# scraper_agent, so it was never offered a sign-in, and Naukri was scraped
# signed out for as long as it has existed.
NAUKRI_APPLY_PROFILE = "naukri"
NAUKRI_SCRAPE_PROFILE = "naukri_scrape"


def apply_supported(source: str) -> bool:
    return source not in APPLY_DISABLED


def disabled_reason(source: str) -> str:
    return APPLY_DISABLED.get(source, "")
