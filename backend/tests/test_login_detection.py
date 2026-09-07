"""
Deciding whether a session is live.

This is the check that stands between the agent and a form it cannot fill. A
false "signed in" does not fail cleanly: the run proceeds, opens a posting,
tailors a resume against it, and only then discovers there is no session — and
on the boards where the apply control simply never renders, that surfaced as
"the modal did not open", a bug report about our selectors rather than about
the login it actually was.

No browser here: the decision is a function of the URL and one selector, and
the page is faked accordingly.
"""

import asyncio
import pytest

from agents.apply_agent import ApplyAgent, LOGIN, SUPPORTED_PLATFORMS, platform_label
from platforms import NAUKRI_SCRAPE_PROFILE


class FakePage:
    """Just enough page: the URL it settled on and whether the marker is there."""

    def __init__(self, url, has_marker=False):
        self.url = url
        self._has_marker = has_marker

    async def query_selector(self, sel):
        return object() if self._has_marker else None


def verdict(url, platform, has_marker=False):
    agent = ApplyAgent.__new__(ApplyAgent)   # no __init__: no env, no browser
    return asyncio.run(agent._is_logged_in(FakePage(url, has_marker), LOGIN[platform]))


class TestPrivateHomePages:
    """Naukri and LinkedIn bounce a signed-out visitor, so arriving is evidence."""

    def test_reaching_the_private_home_page_counts_as_signed_in(self):
        assert verdict("https://www.naukri.com/mnjuser/homepage", "naukri") is True

    def test_query_string_does_not_break_the_match(self):
        assert verdict("https://www.linkedin.com/feed/?trk=nav", "linkedin") is True

    def test_being_bounced_to_login_is_signed_out(self):
        assert verdict("https://www.naukri.com/nlogin/login?URL=x", "naukri") is False

    def test_a_page_that_never_loaded_is_not_evidence_of_anything(self):
        # The bug this file exists for. A goto that failed leaves about:blank,
        # which carries no fail-url token either — and the inference used to be
        # drawn from the config alone, so "signed in" was concluded from a blank
        # tab. Every navigation failure became a confusing downstream error.
        assert verdict("about:blank", "naukri") is False
        assert verdict("about:blank", "linkedin") is False

    def test_some_other_page_on_the_same_site_is_not_evidence(self):
        assert verdict("https://www.naukri.com/", "naukri") is False


class TestPublicHomePages:
    """Indeed's home page loads for everyone, so only the account marker counts."""

    def test_the_marker_is_required(self):
        assert verdict("https://in.indeed.com/", "indeed") is False

    def test_the_marker_is_sufficient(self):
        assert verdict("https://in.indeed.com/", "indeed", has_marker=True) is True


class TestSignInTargets:
    def test_the_scrape_profile_can_be_signed_in(self):
        # It has its own Chrome profile, so it is its own session; before it was
        # listed here nothing could sign it in, and Naukri was scraped signed out.
        assert NAUKRI_SCRAPE_PROFILE in SUPPORTED_PLATFORMS

    def test_it_is_judged_by_the_board_it_belongs_to(self):
        # Not by its own name: `apply_supported("naukri_scrape")` is vacuously
        # true, which would be the right answer for the wrong reason.
        assert LOGIN[NAUKRI_SCRAPE_PROFILE]["applies_as"] == "naukri"

    def test_the_two_naukri_sessions_are_told_apart_on_screen(self):
        labels = {platform_label(p) for p in SUPPORTED_PLATFORMS}
        assert {"Naukri (apply)", "Naukri (scrape)"} <= labels

    def test_every_target_has_a_label(self):
        assert all(platform_label(p) for p in SUPPORTED_PLATFORMS)

    def test_a_disabled_board_is_not_offered_a_sign_in(self):
        # Indeed refuses automated sessions, so asking you to fight its login
        # would be asking for nothing.
        assert "indeed" not in SUPPORTED_PLATFORMS

    @pytest.mark.parametrize("platform", list(LOGIN))
    def test_every_config_can_answer_both_questions(self, platform):
        cfg = LOGIN[platform]
        assert cfg["home_url"] and cfg["login_url"]
        assert cfg["fail_url_tokens"]
        # Without one of these two there is no positive signal at all, and the
        # check can only ever return False.
        assert cfg.get("logged_in_sel") or cfg.get("home_is_private")
