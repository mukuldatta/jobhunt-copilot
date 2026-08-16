"""
Spending a run's time on the jobs that can actually use it.

The delay between applications exists so a burst of them does not look like a
burst of them. It was applied after every candidate, including the ones that
never reached a form: a posting that hands off to the employer's own site costs
a page load and nothing else, and there is no pattern to disguise.

It showed up as a batch of two that submitted nothing and still took a minute —
two hand-offs, seven seconds of work, and thirty-two seconds of sleeping in the
middle of it.
"""

import asyncio

import pytest

from services import orchestrator
from services.orchestrator import NO_FORM_OUTCOMES, _apply_all


class StubAgent:
    """Returns a scripted outcome per job, and records what it was asked to do."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.seen = []

    async def apply(self, job):
        self.seen.append(job.get("title"))
        return {"status": self.outcomes.pop(0), "message": ""}


@pytest.fixture
def slept(monkeypatch):
    """Collects every pause the cycle takes, without taking any of them."""
    naps = []

    async def fake_sleep(seconds):
        naps.append(seconds)

    async def no_count():
        return 0

    monkeypatch.setattr(orchestrator.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(orchestrator, "count_applications_today", no_count)
    return naps


def run(outcomes, slept, n=None):
    jobs = [{"title": f"job{i}", "company": "c", "source": "naukri", "match_score": 90}
            for i in range(n or len(outcomes))]
    agent = StubAgent(outcomes)
    summary = asyncio.run(_apply_all(agent, jobs, daily_cap=70))
    return summary, agent, slept


class TestPacing:
    def test_the_outcomes_are_still_counted(self, slept):
        summary, _, _ = run(["manual_required", "manual_required"], slept)
        assert summary["results"] == {"manual_required": 2}

    def test_a_hand_off_costs_no_delay(self, slept):
        run(["manual_required", "manual_required"], slept)
        assert slept == [], f"slept {slept} between two page loads"

    def test_nothing_is_paced_after_the_last_job(self, slept):
        # The pause spaces one application from the next. After the final one
        # there is no next, and the run just sat there before reporting.
        run(["applied"], slept)
        assert slept == []

    def test_a_real_application_still_paces(self, slept):
        run(["applied", "applied"], slept)
        assert len(slept) == 1, "the delay between real applications is deliberate"

    def test_only_the_formless_outcome_is_skipped(self, slept):
        # applied, then a hand-off, then applied: one pause, after the first.
        run(["applied", "manual_required", "applied"], slept)
        assert len(slept) == 1

    @pytest.mark.parametrize("outcome", sorted(NO_FORM_OUTCOMES - {"login_required"}))
    def test_every_form_less_outcome(self, outcome, slept):
        # First is form-less (no pause), second is last (no pause either).
        run([outcome, "applied"], slept)
        assert slept == [], f"{outcome} paused despite never opening a form"

    def test_a_deferred_question_still_paces(self, slept):
        # It reached the form, filled it, and stopped at a question — that is a
        # session with the site, and it should look like one.
        run(["question_pending", "applied"], slept)
        assert len(slept) == 1

    def test_needs_review_still_paces(self, slept):
        run(["needs_review", "applied"], slept)
        assert len(slept) == 1


class TestOutcomeSet:
    def test_submitting_outcomes_are_not_in_it(self):
        # If "applied" ever landed here the agent would fire applications back
        # to back with no delay at all.
        assert "applied" not in NO_FORM_OUTCOMES
        assert "question_pending" not in NO_FORM_OUTCOMES
        assert "needs_review" not in NO_FORM_OUTCOMES

    def test_the_cheap_outcomes_are(self):
        assert {"manual_required", "already_applied", "expired"} <= NO_FORM_OUTCOMES
