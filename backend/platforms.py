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
