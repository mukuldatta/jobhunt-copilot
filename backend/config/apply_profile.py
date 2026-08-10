"""
Structured answer profile for auto-apply screening questions.

Replaces blind "tick Yes / type 3" form-filling with intentional answers, and
returns None for anything it doesn't recognise so the caller can pause for a
human instead of guessing. All personal / sensitive values are env-overridable
so nothing sensitive is hardcoded.

Env overrides (all optional):
    APPLY_YEARS_EXPERIENCE     default "3"
    APPLY_NOTICE_DAYS          default "30"
    APPLY_CURRENT_CTC          default ""   (blank => unknown => ask human)
    APPLY_EXPECTED_CTC         default ""
    APPLY_CURRENT_CITY         default "Hyderabad"
    APPLY_WILLING_RELOCATE     default "yes"
    APPLY_REQUIRES_SPONSORSHIP default "no"  (user is in India — needs none)
    APPLY_AUTHORIZED_TO_WORK   default "yes"
"""

import os
import re


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "y")


class AnswerProfile:
    def __init__(self):
        self.years = os.environ.get("APPLY_YEARS_EXPERIENCE", "3").strip()
        self.notice_days = os.environ.get("APPLY_NOTICE_DAYS", "30").strip()
        self.current_ctc = os.environ.get("APPLY_CURRENT_CTC", "").strip()
        self.expected_ctc = os.environ.get("APPLY_EXPECTED_CTC", "").strip()
        self.city = os.environ.get("APPLY_CURRENT_CITY", "Hyderabad").strip()
        self.willing_relocate = _flag("APPLY_WILLING_RELOCATE", "yes")
        self.requires_sponsorship = _flag("APPLY_REQUIRES_SPONSORSHIP", "no")
        self.authorized = _flag("APPLY_AUTHORIZED_TO_WORK", "yes")

    def answer(self, question: str):
        """
        Map a screening question to an answer.
          - Yes/No questions  -> "Yes" / "No"
          - numeric / text    -> a string value
          - unrecognised      -> None  (caller should pause for a human)
        """
        if not question:
            return None
        q = re.sub(r"\s+", " ", question).strip().lower()

        def any_(*words):
            return any(w in q for w in words)

        def has(*words):
            return all(w in q for w in words)

        # --- Sponsorship (check BEFORE generic work-authorization) ---
        if any_("sponsor", "sponsorship", "visa"):
            # "do you now or in the future require sponsorship?"
            if any_("require", "need", "will you need", "would you need", "future"):
                return "Yes" if self.requires_sponsorship else "No"
            # "are you able to work without sponsorship?"
            if any_("without", "not require", "no sponsorship"):
                return "No" if self.requires_sponsorship else "Yes"

        # --- Work authorization / eligibility ---
        if any_("authorized", "authorised", "eligible", "legal right", "legally") and any_("work", "employ"):
            return "Yes" if self.authorized else "No"

        # --- Criminal / integrity: always answer truthfully "No" ---
        if any_("felony", "convicted", "criminal record", "criminal history"):
            return "No"

        # --- Notice period / availability (numeric or immediate) ---
        if any_("immediate") and any_("join", "joiner", "available", "start"):
            return "Yes"
        if has("notice") and any_("period", "days", "how many", "how long", "serving"):
            return self.notice_days

        # --- Compensation ---
        if any_("current") and any_("ctc", "salary", "compensation", "pay"):
            return self.current_ctc or None
        if any_("expected", "desired", "expecting") and any_("ctc", "salary", "compensation", "pay"):
            return self.expected_ctc or None

        # --- Relocation / commute / on-site ---
        if any_("relocat"):
            return "Yes" if self.willing_relocate else "No"
        if any_("commute", "willing to travel", "work from office", "on-site", "onsite", "hybrid", "in office"):
            return "Yes"

        # --- Location / city ---
        if any_("current location", "current city", "which city", "where are you located", "your location"):
            return self.city

        # --- Education ---
        if any_("bachelor", "master", "degree", "graduate", "graduated", "qualification"):
            return "Yes"

        # --- Years of experience (numeric) ---
        if any_("how many years", "years of experience", "years experience", "total experience", "yrs of experience"):
            return self.years
        if has("years") and any_("experience", "exp"):
            return self.years

        # --- Start date ---
        if any_("start date", "when can you start", "available to start", "earliest start"):
            return "Immediately"

        # --- Background check / assessments ---
        if any_("background check", "background verification", "drug test", "willing to undergo", "assessment"):
            return "Yes"

        # --- Generic skill / comfort questions (kept last, deliberately broad) ---
        if any_("do you have experience", "experience with", "experience in", "are you comfortable",
                "proficient", "familiar with", "knowledge of", "hands-on", "have you worked"):
            return "Yes"

        return None
