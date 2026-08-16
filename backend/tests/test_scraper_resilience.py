"""
Telling "the browser is gone" apart from "the page said no".

Both arrive as an ordinary exception, and the scrape loops treated them the
same: log it, try the next query. So closing the window mid-run produced one
error per remaining query/city pair — seventeen in a row against a browser that
no longer existed — and the source finished with 0 jobs and seventeen lines
that each read like an unrelated page-load problem.

The costlier half was the bot-check wait, which polled a page it could no
longer read: the read failed, the failure was reported as "no challenge
present", and a window you closed was announced as a bot check you had solved.
"""

import asyncio

import pytest

from agents.scraper_agent import ScraperAgent, is_closed_error


CLOSED = "Page.goto: Target page, context or browser has been closed"


@pytest.fixture
def no_waiting(monkeypatch):
    """The challenge poll sleeps 5s a turn; nothing here needs real seconds."""
    async def instant(_seconds):
        return None

    monkeypatch.setattr("agents.scraper_agent.asyncio.sleep", instant)


class FakePage:
    def __init__(self, closed=False, title="", body="", raises=None):
        self._closed = closed
        self._title = title
        self._body = body
        self._raises = raises

    def is_closed(self):
        return self._closed

    async def title(self):
        if self._raises:
            raise self._raises
        return self._title

    async def inner_text(self, _sel):
        if self._raises:
            raise self._raises
        return self._body


class TestChallengeWait:
    """
    The wait must end on three different answers, and only one of them means
    the scrape may continue.
    """

    def test_a_closed_window_is_not_a_solved_challenge(self, no_waiting):
        agent = ScraperAgent()
        agent._challenge_gave_up = False
        assert asyncio.run(agent._solve_challenge(FakePage(closed=True), "Indeed 'X' Y")) is False

    def test_a_dead_browser_mid_poll_is_not_a_solved_challenge(self, no_waiting):
        # The page object still answers is_closed() falsely while the browser
        # behind it is gone — the read is what fails.
        agent = ScraperAgent()
        agent._challenge_gave_up = False
        page = FakePage(raises=Exception(CLOSED))
        assert asyncio.run(agent._solve_challenge(page, "Indeed 'X' Y")) is False

    def test_giving_up_is_remembered_so_the_next_page_does_not_wait_again(self, no_waiting):
        agent = ScraperAgent()
        agent._challenge_gave_up = False
        asyncio.run(agent._solve_challenge(FakePage(closed=True), "Indeed 'X' Y"))
        assert agent._challenge_gave_up is True

    def test_a_cleared_challenge_still_resumes(self, no_waiting):
        # The behaviour being protected: a page that stops looking like a
        # challenge is the one case that lets the scrape carry on.
        agent = ScraperAgent()
        agent._challenge_gave_up = False
        page = FakePage(title="AI Engineer jobs", body="20 results")
        assert asyncio.run(agent._solve_challenge(page, "Indeed 'X' Y")) is True


class FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self.closed = False

    async def close(self):
        self.closed = True


class FakePlaywright:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class TestSourceStopsWhenTheBrowserDies:
    """
    The count is the point. Seventeen attempts against a dead browser is not a
    slower path to the same answer — it is seventeen misleading error lines
    burying the one fact that mattered.
    """

    @pytest.fixture
    def dead_browser_run(self, monkeypatch, no_waiting):
        page = FakePage()
        ctx = FakeContext(page)
        calls = []

        monkeypatch.setattr("agents.scraper_agent.async_playwright", lambda: FakePlaywright())

        async def fake_context(self, pw, name):
            return ctx

        async def always_closed(self, page, query, city):
            calls.append((query, city))
            raise Exception(CLOSED)

        monkeypatch.setattr(ScraperAgent, "_headed_context", fake_context)
        monkeypatch.setattr(ScraperAgent, "_load_indeed_page", always_closed)
        monkeypatch.setattr(ScraperAgent, "_load_naukri_page", always_closed)
        return calls, ctx

    def test_indeed_gives_up_after_the_first_dead_page(self, dead_browser_run):
        calls, _ = dead_browser_run
        jobs = asyncio.run(ScraperAgent()._scrape_indeed_india())
        assert jobs == []
        assert len(calls) == 1, f"kept going against a dead browser: {calls}"

    def test_naukri_gives_up_after_the_first_dead_page(self, dead_browser_run, monkeypatch):
        calls, _ = dead_browser_run
        # The description pass reuses the same dead page, so it must be skipped
        # too rather than failing its way to a "3 consecutive failures" verdict.
        fetched = []

        async def fetch(self, page, jobs):
            fetched.append(jobs)

        monkeypatch.setattr(ScraperAgent, "_fetch_naukri_descriptions", fetch)
        asyncio.run(ScraperAgent()._scrape_naukri())
        assert len(calls) == 1, f"kept going against a dead browser: {calls}"
        assert fetched == [], "tried to fetch descriptions through a dead browser"

    def test_the_context_is_still_closed_on_the_way_out(self, dead_browser_run):
        _, ctx = dead_browser_run
        asyncio.run(ScraperAgent()._scrape_indeed_india())
        assert ctx.closed is True


class TestChallengeDetection:
    def test_a_challenge_page_is_recognised(self):
        page = FakePage(title="Just a moment...")
        assert asyncio.run(ScraperAgent()._is_challenged(page)) is True

    def test_an_ordinary_read_failure_still_means_no_challenge(self):
        # Unreadable for a mundane reason — say the body never rendered. The old
        # blanket except lives on for exactly this, and only this.
        page = FakePage(raises=Exception("Timeout 12000ms exceeded"))
        assert asyncio.run(ScraperAgent()._is_challenged(page)) is False

    def test_a_dead_browser_refuses_to_answer(self):
        # Rather than answering "no challenge", which is what made a closed
        # window read as a cleared one.
        page = FakePage(raises=Exception(CLOSED))
        with pytest.raises(Exception, match="has been closed"):
            asyncio.run(ScraperAgent()._is_challenged(page))


class TestIsClosedError:
    @pytest.mark.parametrize("message", [
        # Verbatim from the run that prompted this — seventeen times.
        "Page.goto: Target page, context or browser has been closed",
        "Target page, context or browser has been closed",
        "browserContext.newPage: Target closed",
        "Browser closed unexpectedly",
        "Connection closed while reading from the driver",
    ])
    def test_recognises_a_dead_browser(self, message):
        assert is_closed_error(Exception(message)) is True

    @pytest.mark.parametrize("message", [
        # Ordinary scrape weather: retryable, and not a reason to abandon a source.
        "Page.goto: net::ERR_NAME_NOT_RESOLVED",
        "Timeout 45000ms exceeded",
        "Page.wait_for_selector: Timeout 12000ms exceeded",
        "Access Denied",
        "",
    ])
    def test_leaves_ordinary_failures_alone(self, message):
        assert is_closed_error(Exception(message)) is False

    def test_is_not_fooled_by_the_word_closed_in_other_contexts(self):
        # "closed" appears in job text all the time — "applications closed",
        # "closed-loop control". Only the browser's own phrasings count.
        assert is_closed_error(Exception("Applications are closed for this role")) is False
        assert is_closed_error(Exception("closed-loop control systems")) is False

    def test_matches_regardless_of_case(self):
        assert is_closed_error(Exception("TARGET PAGE, CONTEXT OR BROWSER HAS BEEN CLOSED")) is True

    def test_survives_an_exception_whose_str_is_not_a_message(self):
        class Odd(Exception):
            def __str__(self):
                return repr(self)

        assert is_closed_error(Odd()) is False
