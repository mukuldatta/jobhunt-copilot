"""
Reading "you already applied" off the board.

The agent's record of what it has applied to is not complete, and cannot be: an
application submitted in a run that died before record_application leaves no
trace on our side. The board's does. Without reading it, such a job returns to
the queue, finds no apply button — because the board replaced it with
"Application submitted" — and reports a drifted selector, then spends its
retries rediscovering the same thing. That is exactly what the LinkedIn
Generative AI Engineer posting did tonight, three days after it was applied to.

The phrases have to be narrow. This verdict is terminal and it writes an
application record, so a false positive marks a job done that was never sent —
strictly worse than missing one, which only costs a wasted page load.
"""

import asyncio

import pytest

from agents.apply_agent import ApplyAgent


class FakePage:
    def __init__(self, text="", raises=False):
        self._text, self._raises = text, raises

    async def inner_text(self, _sel):
        if self._raises:
            raise Exception("Target page, context or browser has been closed")
        return self._text


def seen(text, platform="linkedin", raises=False):
    agent = ApplyAgent.__new__(ApplyAgent)
    return asyncio.run(agent._already_applied_on_page(FakePage(text, raises), platform))


class TestRecognised:
    def test_the_linkedin_banner(self):
        # Verbatim from the screenshot that started this.
        assert seen("Application status\nApplication submitted\n3 days ago\nView resume")

    @pytest.mark.parametrize("text", ["You've applied to this job", "You applied 2 weeks ago"])
    def test_the_other_linkedin_wordings(self, text):
        assert seen(text)

    @pytest.mark.parametrize("text", [
        "You have already applied to this job", "Application sent", "Already applied",
    ])
    def test_naukri_wordings(self, text):
        assert seen(text, "naukri")

    def test_case_does_not_matter(self):
        assert seen("APPLICATION SUBMITTED")


class TestNotRecognised:
    """Each of these would mark a live posting as done and never offer it again."""

    @pytest.mark.parametrize("text", [
        "Easy Apply",
        "Apply on company site",
        "Be among the first 25 applicants",
        "Over 100 applicants",
        "1,234 people applied on this job",          # why "applied on" is not a marker
        "Applications are reviewed within 2 weeks",
        "Submit your application below",
        "",
    ])
    def test_ordinary_posting_text(self, text):
        assert not seen(text)

    def test_a_marker_for_another_board_does_not_count(self):
        # Naukri's phrasing on a LinkedIn page is not LinkedIn saying it.
        assert not seen("You have already applied to this job", "linkedin")

    def test_an_unknown_platform_matches_nothing(self):
        assert not seen("Application submitted", "wellfound")


class TestUnreadablePage:
    def test_says_no_rather_than_guessing(self):
        # Unreadable proves nothing, and answering "yes" here would file a job
        # as applied on the strength of a failed read.
        assert not seen("", raises=True)


class TestMarkerHygiene:
    def test_every_platform_has_markers(self):
        from agents.apply_agent import LOGIN
        for platform in LOGIN:
            if platform.endswith("_scrape"):
                continue
            assert ApplyAgent.APPLIED_MARKERS.get(platform), platform

    def test_no_marker_is_a_bare_word(self):
        # The danger is a marker like "applied", which appears in "Easy Apply",
        # applicant counts, and half the furniture on a job page. Every phrase
        # must carry a subject or an object with it.
        for platform, phrases in ApplyAgent.APPLIED_MARKERS.items():
            for p in phrases:
                assert len(p.split()) >= 2, f"{platform}: {p!r} is a bare word"
                assert len(p) >= 10, f"{platform}: {p!r} is too short to be safe"
