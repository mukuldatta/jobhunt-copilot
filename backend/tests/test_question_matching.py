"""
Recognising a question you have already answered, wearing different words.

Every posting phrases the same handful of screening questions differently, and
an answer that is only found under its original wording is asked again on the
next posting. That is the whole value of the learned store, and exact-string
matching gave up most of it.

What this is NOT is a similarity score. Measured on the real store, no
threshold separates the cases correctly — the pair that must match scores lower
than two pairs that must not:

    current annual CTC in INR / current CTC (in lakhs)   jaccard 0.40  same question
    proficiency in Hindi      / proficiency in English   jaccard 0.50  different answers
    CTC                       / expected CTC             overlap 1.00  different answers

Embeddings would widen the problem rather than narrow it: "Hindi" and "English"
are neighbours in vector space precisely because they are the same kind of
thing, and so are "current" and "expected". The distinguishing token is the
whole question, so the rule is equality after canonicalisation — precision
first, because a false match answers a form with the wrong fact and cannot be
withdrawn.
"""

import asyncio

import pytest

from services.answer_service import AnswerResolver
from utils.question_key import question_signature as sig


def store(*pairs):
    return {"qa": [{"question": q, "answer": a} for q, a in pairs]}


def ask(question, profile):
    r = AnswerResolver(profile=profile)
    r._from_llm = lambda *a, **k: None
    return asyncio.run(r.answer(question))


class TestSameQuestionDifferentWords:
    def test_the_currency_a_salary_is_asked_in_does_not_matter(self):
        # The surviving duplicate pair in the real store.
        p = store(("What is your current annual CTC in INR?", "38 LPA"))
        assert ask("What is your current CTC (in lakhs)?", p) == "38 LPA"

    def test_salary_and_ctc_and_compensation_are_one_word(self):
        p = store(("What is your expected CTC?", "30 LPA"))
        assert ask("What is your expected compensation?", p) == "30 LPA"
        assert ask("Expected salary", p) == "30 LPA"

    def test_word_order_and_filler_do_not_matter(self):
        p = store(("How many years of experience do you have with Python?", "3"))
        assert ask("Years of Python experience", p) == "3"

    def test_spelling_variants_of_experience(self):
        p = store(("Total experiance with Django", "2"))
        assert ask("Total experience with Django", p) == "2"

    def test_currently_and_current_are_one_word(self):
        p = store(("What is your current city?", "Hyderabad"))
        assert ask("Which city are you currently in?", p) == "Hyderabad"


class TestQuestionsThatMustStayApart:
    """Each of these would put a wrong fact on a real application."""

    def test_current_is_not_expected(self):
        p = store(("What is your expected CTC?", "30 LPA"))
        assert ask("What is your current CTC?", p) is None

    def test_an_unqualified_salary_does_not_borrow_the_expected_one(self):
        # It falls through to the rules, which read current_ctc.
        p = store(("What is your expected CTC?", "30 LPA"))
        assert ask("What is your CTC?", p) is None

    def test_one_language_does_not_answer_for_another(self):
        p = store(("What is your level of proficiency in Hindi?", "Native"))
        assert ask("What is your level of proficiency in English?", p) is None

    def test_a_different_threshold_is_a_different_question(self):
        p = store(("Do you have 4+ years of coding experience in Python?", "Yes"))
        assert ask("Do you have 2+ years of coding experience in Python?", p) is None

    def test_a_different_skill_is_a_different_question(self):
        p = store(("How many years of experience with Python?", "3"))
        assert ask("How many years of experience with Kubernetes?", p) is None

    def test_a_different_time_unit_is_a_different_question(self):
        # Why time units are not treated as noise, though it costs the match
        # between "notice period" and "notice period in days".
        assert sig("Do you have 2 years of experience?") != \
               sig("Do you have 2 months of experience?")


class TestThresholdQuestions:
    """
    "Do you have 4+ years of Python?" is a yes/no question that happens to
    contain a number. The years rule saw the number and answered "3" — a bare
    figure typed into a Yes/No control, the same category error as "30 LPA" in
    a decimal box.
    """

    PROFILE = {"skill_years": {"Python": "4", "LangChain": "2"},
               "total_years_experience": "3", "qa": []}

    def answer(self, q):
        r = AnswerResolver(profile=dict(self.PROFILE))
        r._from_llm = lambda *a, **k: None
        return asyncio.run(r.answer(q))

    def test_meeting_the_bar_is_a_yes(self):
        assert self.answer("Do you have 4+ years of coding experience in Python?") == "Yes"

    def test_missing_the_bar_is_a_no(self):
        assert self.answer("Do you have 8+ years of experience in Python?") == "No"

    def test_an_unnamed_skill_falls_back_to_total_experience(self):
        assert self.answer("Do you have 2+ years hands on experience in latest technologies?") == "Yes"

    @pytest.mark.parametrize("q", [
        "Do you have at least 5 years of experience?",
        "Are you having minimum 5 years experience?",
    ])
    def test_the_other_phrasings_of_a_threshold(self, q):
        assert self.answer(q) == "No"

    def test_how_many_years_is_still_answered_with_a_number(self):
        # The threshold rule must not swallow the question it sits in front of.
        assert self.answer("How many years of experience do you have with Python?") == "4"


class TestSignature:
    def test_required_markers_and_punctuation_are_ignored(self):
        assert sig("What is your expected CTC?*") == sig("expected ctc")

    def test_empty_input(self):
        assert sig("") == frozenset() and sig(None) == frozenset()

    def test_an_empty_signature_never_matches_anything(self):
        # Otherwise a stored question of pure filler would answer everything.
        # Asked with a question no rule covers, so the store is what decides.
        p = store(("the of and", "Yes"))
        assert ask("What is your preferred deployment stack?", p) is None
