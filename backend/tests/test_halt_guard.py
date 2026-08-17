"""
When a cycle should stop, and when stopping is the bug.

The halt guard exists for one situation: an unattended bot check. Each of those
burns a full human-wait timeout, and if nobody cleared the first there is no
reason to think they will clear the next — so stopping saves the batch.

It was counting every needs_review instead. Two consecutive resume-guard
refusals — a LinkedIn modal with no upload field, decided in a second, costing
nothing — read as "nobody is at the machine", and two entire 90-minute cycles
halted after three jobs while twenty-five untried candidates sat behind them.
"""

import asyncio

import pytest

from services import orchestrator
from services.orchestrator import _apply_all


class StubAgent:
    """Returns scripted results; `unattended` marks a real human-wait timeout."""

    def __init__(self, results):
        self.results = list(results)
        self.seen = 0

    async def apply(self, job):
        self.seen += 1
        return self.results.pop(0)


def needs_review(unattended=False):
    r = {"status": "needs_review", "message": "x"}
    if unattended:
        r["unattended"] = True
    return r


APPLIED = {"status": "applied", "message": "ok"}
DEFERRED = {"status": "question_pending", "message": "x"}


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    async def fake_sleep(_s):
        return None

    async def no_count():
        return 0

    monkeypatch.setattr(orchestrator.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(orchestrator, "count_applications_today", no_count)


def run(results):
    jobs = [{"title": f"job{i}", "company": "c", "source": "naukri", "match_score": 90}
            for i in range(len(results))]
    agent = StubAgent(results)
    summary = asyncio.run(_apply_all(agent, jobs, daily_cap=70))
    return summary, agent.seen


class TestDeterministicRefusalsDoNotHalt:
    def test_two_resume_guard_refusals_do_not_stop_the_run(self):
        # The exact shape of the two dead cycles: BayOne then Hiringhood.
        _, seen = run([needs_review(), needs_review(), APPLIED, APPLIED])
        assert seen == 4, "halted on refusals that cost nothing"

    def test_the_applications_behind_them_still_happen(self):
        summary, _ = run([needs_review(), needs_review(), APPLIED, APPLIED])
        assert summary["results"].get("applied") == 2

    def test_deferrals_do_not_halt_either(self):
        _, seen = run([DEFERRED, DEFERRED, DEFERRED, APPLIED])
        assert seen == 4


class TestUnattendedBotChecksStillHalt:
    def test_two_in_a_row_stops_the_cycle(self):
        _, seen = run([needs_review(True), needs_review(True), APPLIED, APPLIED])
        assert seen == 2, "an unattended CAPTCHA must still stop the batch"

    def test_the_halt_is_recorded(self):
        summary, _ = run([needs_review(True), needs_review(True), APPLIED])
        assert any(e.get("result") == "halted" for e in summary["log"])

    def test_an_application_between_them_resets_the_count(self):
        _, seen = run([needs_review(True), APPLIED, needs_review(True), APPLIED])
        assert seen == 4

    def test_a_deterministic_refusal_does_not_add_to_the_captcha_count(self):
        # One real timeout plus one refusal is not two timeouts.
        _, seen = run([needs_review(True), needs_review(), needs_review(True), APPLIED])
        assert seen == 4


class TestTheBarrenBackstop:
    def test_a_long_run_of_nothing_still_stops(self):
        # If the session is dead every posting fails, and grinding through 60 of
        # them helps nobody.
        _, seen = run([needs_review()] * 12)
        assert seen == 8, f"expected the barren backstop at 8, stopped at {seen}"

    def test_it_is_configurable(self, monkeypatch):
        monkeypatch.setenv("AUTO_APPLY_BARREN_LIMIT", "3")
        _, seen = run([DEFERRED] * 10)
        assert seen == 3

    def test_an_application_resets_it(self):
        _, seen = run([needs_review()] * 5 + [APPLIED] + [needs_review()] * 5)
        assert seen == 11, "a success in the middle should clear the streak"
