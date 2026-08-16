"""
Answering in the currency the field asks for.

Every value in this file is the real one from the apply profile on the day the
pipeline stalled:

    expected_ctc            '30 LPA'
    earliest_start          'Immediately'
    notice_period_days      '0'

Nothing was unknown. "What is your expected CTC?" was answered "30 LPA" and
"How soon can you join?" was answered "Immediately", both into boxes that
validate as decimals, and both rejected — while notice_period_days sat in the
same profile holding exactly what the second field wanted. The knowledge was
never the problem; the format and the source were.
"""

import asyncio

import pytest

from services.answer_service import AnswerResolver, numeric_text
from utils.question_key import question_key


PROFILE = {
    "expected_ctc": "30 LPA",
    "current_ctc": "38 LPA",
    "notice_period_days": "0",
    "earliest_start": "Immediately",
    "total_years_experience": "3",
    "languages": {"English": "Native or bilingual proficiency"},
    "qa": [
        {"question": "What is your current CTC (in lakhs)?", "answer": "38 LPA"},
        {"question": "What is your level of proficiency in Hindi?*",
         "answer": "Native or bilingual proficiency"},
    ],
}


def ask(question, kind="text", options=None, profile=None):
    r = AnswerResolver(profile=profile if profile is not None else dict(PROFILE))
    # No LLM in these tests: the point is what the profile and the store can
    # answer on their own, and a network call would decide it instead.
    r._from_llm = lambda *a, **k: None
    return asyncio.run(r.answer(question, options=options, kind=kind))


class TestNumericFields:
    def test_expected_ctc_loses_its_unit(self):
        assert ask("What is your expected CTC?", kind="number") == "30"

    def test_current_ctc_loses_its_unit(self):
        assert ask("What is your current CTC?", kind="number") == "38"

    def test_the_same_question_keeps_its_unit_in_a_text_field(self):
        # A text box is where "30 LPA" is the better answer, not the worse one.
        assert ask("What is your expected CTC?") == "30 LPA"

    def test_a_learned_answer_is_coerced_too(self):
        # The store holds "38 LPA" against a question that says *(in lakhs)*, so
        # reuse was carrying the rejection forward into every future posting.
        assert ask("What is your current CTC (in lakhs)?", kind="number") == "38"

    def test_years_answer_is_a_bare_number(self):
        assert ask("How many years of experience do you have?", kind="number") == "3"

    def test_prose_in_a_number_field_becomes_a_question_for_you(self):
        # Rather than a number invented to satisfy the validator.
        assert ask("What is your level of proficiency in English?", kind="number") is None


class TestWhichSalary:
    def test_unqualified_ctc_means_the_one_you_are_on(self):
        # The convention on every Indian posting. It matched no rule before, so
        # a fact sitting in the profile was sent to the model to guess at.
        assert ask("What is your CTC?", kind="number") == "38"

    def test_expected_is_still_told_apart(self):
        assert ask("What is your expected CTC?", kind="number") == "30"

    @pytest.mark.parametrize("q,expected", [
        ("Current annual package", "38"),
        ("Expected salary", "30"),
        ("What compensation are you asking for?", "30"),
        ("In hand salary", "38"),
    ])
    def test_the_phrasings_that_turn_up(self, q, expected):
        assert ask(q, kind="number") == expected


class TestJoiningQuestions:
    def test_how_soon_in_a_number_field_reads_the_days(self):
        # The failure verbatim: this matched no rule at all, fell through to the
        # LLM, and came back "Immediately".
        assert ask("How soon can you join?", kind="number") == "0"

    def test_how_soon_in_a_text_field_reads_the_words(self):
        assert ask("How soon can you join?") == "Immediately"

    @pytest.mark.parametrize("q", [
        "What is your notice period?",
        "Notice period in days",
        "When can you join us?",
        "What is your date of joining?",
        "Earliest start date",
    ])
    def test_the_family_is_recognised(self, q):
        assert ask(q, kind="number") == "0"

    def test_immediate_joiner_is_still_a_yes_no_question(self):
        assert ask("Are you an immediate joiner?") == "Yes"

    def test_but_not_when_the_box_wants_a_number(self):
        # "Yes" in a decimal field is the same failure wearing a different hat.
        assert ask("Are you an immediate joiner?", kind="number") == "0"

    def test_falls_back_to_the_other_field_when_one_is_missing(self):
        profile = dict(PROFILE)
        profile.pop("notice_period_days")
        assert ask("How soon can you join?", kind="number", profile=profile) is None, \
            "'Immediately' is not a number and must not be typed as one"


class TestQuestionKey:
    def test_a_required_marker_is_not_a_different_question(self):
        assert question_key("What is your level of proficiency in Hindi?*") == \
               question_key("What is your level of proficiency in Hindi?")

    def test_the_learned_answer_is_found_despite_the_marker(self):
        assert ask("What is your level of proficiency in Hindi?") == \
               "Native or bilingual proficiency"

    def test_a_duplicated_label_fragment_is_trimmed(self):
        # Stored in the profile in exactly this shape.
        assert question_key("Do you have 4+ years of coding experience in Python?\nDo you have 4") == \
               question_key("Do you have 4+ years of coding experience in Python?")

    def test_case_and_spacing_do_not_matter(self):
        assert question_key("  What   Is Your  CTC? ") == question_key("what is your ctc?")

    def test_distinct_questions_stay_distinct(self):
        assert question_key("What is your current CTC?") != question_key("What is your expected CTC?")

    def test_empty_input_is_empty(self):
        assert question_key(None) == "" and question_key("") == ""


class TestNumericText:
    @pytest.mark.parametrize("raw,expected", [
        ("38 LPA", "38"), ("3.5 years", "3.5"), ("INR 30,00,000", "30"),
        ("0", "0"), (30, "30"),
    ])
    def test_pulls_the_number_out(self, raw, expected):
        assert numeric_text(raw) == expected

    @pytest.mark.parametrize("raw", ["Immediately", "Negotiable", "", None, "N/A"])
    def test_says_nothing_when_there_is_no_number(self, raw):
        assert numeric_text(raw) is None
