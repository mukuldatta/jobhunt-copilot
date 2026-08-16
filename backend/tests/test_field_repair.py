"""
Reacting to what the form said, instead of stalling in front of it.

Drawn from the 22 saved `linkedin_stalled` screenshots, which all show the same
thing: LinkedIn had already explained itself in the modal — "Enter a decimal
number larger than 0.0" under a box containing "30 LPA" — and the agent read
none of it. Every one of those runs ended as needs_review, and five apply
cycles in one day submitted nothing at all.

Two rules the repair must keep. A value we can convert is converted, because
"30 LPA" and "30" are the same answer in different clothes. A value we cannot
is handed to you — never replaced with a number chosen to make the form move,
which would be exactly the fabrication the apply path exists to prevent.
"""

import asyncio

import pytest

from agents.apply_agent import (
    ApplyAgent, NUMERIC_QUESTION_HINTS, _WANTS_NUMBER_RE, _FIELD_ERROR_RE,
)


DECIMAL_ERR = "Enter a decimal number larger than 0.0"
VALID_ERR = "Please enter a valid answer"


class StubAnswers:
    """Stands in for AnswerResolver: returns what it is told, or nothing."""

    def __init__(self, reply=None):
        self.reply = reply
        self.asked = []

    async def answer(self, question, options=None, kind="text"):
        self.asked.append((question, kind))
        return self.reply


class StubLocator:
    def __init__(self, sink, mark):
        self.sink, self.mark = sink, mark

    async def fill(self, value):
        self.sink[self.mark] = value


class StubPage:
    def __init__(self, rejected):
        self._rejected = rejected
        self.filled = {}
        self.url = "https://www.linkedin.com/jobs/view/1"

    def locator(self, sel):
        mark = sel.split('"')[1]
        return StubLocator(self.filled, mark)


def repair(rejected, reply=None):
    agent = ApplyAgent.__new__(ApplyAgent)
    agent.answers = StubAnswers(reply)
    agent._say = lambda *_a, **_k: None

    async def rejected_fields(_page):
        return rejected

    agent._rejected_fields = rejected_fields
    page = StubPage(rejected)
    fixed, unanswerable = asyncio.run(agent._repair_rejected_fields(page))
    return fixed, unanswerable, page.filled, agent.answers


class TestNumericRepair:
    def test_strips_the_unit_the_field_rejected(self):
        # The exact case in the screenshot: expected CTC "30 LPA".
        fixed, unanswerable, filled, _ = repair([
            {"mark": "jhv0", "q": "What is your expected CTC?", "tag": "input",
             "value": "30 LPA", "error": DECIMAL_ERR, "options": []},
        ])
        assert (fixed, unanswerable) == (1, [])
        assert filled["jhv0"] == "30"

    def test_asks_again_numerically_when_the_answer_has_no_number(self):
        # "How soon can you join?" -> "Immediately", in a field wanting days.
        fixed, unanswerable, filled, answers = repair([
            {"mark": "jhv0", "q": "How soon can you join?", "tag": "input",
             "value": "Immediately", "error": DECIMAL_ERR, "options": []},
        ], reply="0")
        assert (fixed, unanswerable) == (1, [])
        assert filled["jhv0"] == "0"
        assert answers.asked == [("How soon can you join?", "number")]

    def test_hands_it_over_when_no_number_can_be_had(self):
        fixed, unanswerable, filled, _ = repair([
            {"mark": "jhv0", "q": "How soon can you join?", "tag": "input",
             "value": "Immediately", "error": DECIMAL_ERR, "options": []},
        ], reply=None)
        assert fixed == 0
        assert filled == {}, "invented a number to clear the error"
        assert len(unanswerable) == 1
        assert "How soon can you join?" in unanswerable[0]["question"]

    def test_the_error_travels_beside_the_question_not_inside_it(self):
        # The email should say what the form wanted. But the question text is
        # the key a learned answer is stored under, so the error goes in `hint`:
        # folding it in would mint a new question every time LinkedIn reworded
        # its validation message, and silently undo the reuse.
        _, unanswerable, _, _ = repair([
            {"mark": "jhv0", "q": "Expected CTC", "tag": "input",
             "value": "negotiable", "error": DECIMAL_ERR, "options": []},
        ], reply="not a number either")
        assert unanswerable[0]["question"] == "Expected CTC"
        assert unanswerable[0]["hint"] == DECIMAL_ERR


class TestUnselectedDropdowns:
    def test_a_required_select_becomes_a_question(self):
        fixed, unanswerable, _, _ = repair([
            {"mark": "jhv0", "q": "Have you built agentic AI Product from scratch",
             "tag": "select", "value": "", "error": VALID_ERR,
             "options": ["Yes", "No"]},
        ])
        assert fixed == 0
        assert unanswerable[0]["options"] == ["Yes", "No"]

    def test_several_rejections_are_all_reported(self):
        # The screenshot had four at once; asking about one and stalling on the
        # rest would take four round trips to clear a single application.
        fixed, unanswerable, _, _ = repair([
            {"mark": "jhv0", "q": "A?", "tag": "select", "value": "",
             "error": VALID_ERR, "options": ["Yes", "No"]},
            {"mark": "jhv1", "q": "B?", "tag": "select", "value": "",
             "error": VALID_ERR, "options": ["Yes", "No"]},
        ])
        assert fixed == 0 and len(unanswerable) == 2


class TestResilience:
    def test_a_page_that_cannot_be_read_repairs_nothing_and_asks_nothing(self):
        agent = ApplyAgent.__new__(ApplyAgent)
        agent._say = lambda *_a, **_k: None

        async def boom(_page):
            raise Exception("Target page, context or browser has been closed")

        agent._rejected_fields = boom
        fixed, unanswerable = asyncio.run(agent._repair_rejected_fields(object()))
        # Not "nothing was rejected" and not a question either — no claim at all,
        # so the caller falls through to its own stall handling.
        assert (fixed, unanswerable) == (0, [])


class TestPatterns:
    @pytest.mark.parametrize("err", [
        DECIMAL_ERR, "Enter a whole number", "Please enter a number",
    ])
    def test_number_demands_are_recognised(self, err):
        assert _WANTS_NUMBER_RE.search(err)

    def test_a_plain_validity_complaint_is_not_a_number_demand(self):
        assert not _WANTS_NUMBER_RE.search(VALID_ERR)

    @pytest.mark.parametrize("err", [DECIMAL_ERR, VALID_ERR, "This field is required"])
    def test_error_text_is_recognised_as_an_error(self, err):
        assert _FIELD_ERROR_RE.search(err)

    def test_ordinary_question_text_is_not_mistaken_for_an_error(self):
        # The detector runs over the field's container, which holds the question
        # too; matching it would flag every field on the form.
        for q in ("What is your expected CTC?",
                  "How many years of experience do you have with Python?",
                  "Have you built agentic AI Product from scratch"):
            assert not _FIELD_ERROR_RE.search(q)


class TestNumericQuestionHints:
    @pytest.mark.parametrize("q", [
        "what is your expected ctc?",
        "what is your current ctc?",
        "how soon can you join?",
        "notice period",
        "expected salary",
    ])
    def test_the_questions_that_stalled_are_now_treated_as_numeric(self, q):
        assert any(k in q for k in NUMERIC_QUESTION_HINTS), q

    @pytest.mark.parametrize("q", [
        "are you willing to relocate?",
        "do you have a valid passport?",
        "which city do you prefer?",
    ])
    def test_ordinary_questions_are_left_alone(self, q):
        assert not any(k in q for k in NUMERIC_QUESTION_HINTS), q
