"""
Making the model show its working, and discarding the answer when it cannot.

The model is the only part of the answering path that can invent. The rules
read fields and the learned store replays what you typed, but the LLM leg is
asked to map an unfamiliar question onto the material — and a model that maps
badly does not fail loudly, it produces a confident number. That number is
typed into a real application that cannot be withdrawn.

So an answer now arrives with the span of profile or resume that establishes
it, and a span that is not found in the source is treated exactly like knowing
nothing: the question goes to you.

The asymmetry in the checks is deliberate. Any digit in an answer must be
present in the quote and the quote present in the source — that is the class
that must never be invented, and it is mechanically checkable. "Yes" is not
checkable that way; nothing in a resume says "yes". Requiring a real citation
there still separates an inference from an invention, and being stricter would
only mean more postings waiting on email.

Every question below is one no deterministic rule answers. That matters: the
rules run first, and a question they cover never reaches the model at all — an
earlier draft of this file tested "How many years have you worked?" and was
really only testing total_years_experience.
"""

import asyncio

import pytest

from services.answer_service import AnswerResolver, _parse_answer, _flat


RESUME = """
Venkata Naga Santosh Mokkapati
AI Software Engineer at Incrivelsoft, 2023 - present
Built retrieval pipelines in Python with LangChain and FastAPI.
M.S. Data Science, UMBC, 2023
Skills: Python, FastAPI, Docker, Kubernetes, AWS, PyTorch
"""

PROFILE = {
    "total_years_experience": "3",
    "current_ctc": "38 LPA",
    "current_city": "Hyderabad",
    "requires_sponsorship": True,
    "qa": [],
}

# Questions the rules deliberately have no opinion about.
NUMERIC_Q = "How many production ML systems have you deployed?"
TEXT_Q = "What is your preferred deployment stack?"
YESNO_Q = "Have you shipped a product to production?"


def ask(reply, question=NUMERIC_Q, kind="text", options=None):
    r = AnswerResolver(profile=dict(PROFILE), resume_text=RESUME)

    class Stub:
        def complete(self, _prompt):
            return reply

    r._llm = Stub()          # the lazy property's backing attribute
    return asyncio.run(r.answer(question, options=options, kind=kind))


class TestRulesStillWinFirst:
    def test_the_model_is_not_consulted_when_a_rule_answers(self):
        # Guards the premise of every other test in this file.
        def explode(_prompt):
            raise AssertionError("the LLM should not have been called")

        r = AnswerResolver(profile=dict(PROFILE), resume_text=RESUME)
        r._llm = type("S", (), {"complete": staticmethod(explode)})()
        assert asyncio.run(r.answer("What is your current CTC?", kind="number")) == "38"


class TestGroundedNumbers:
    def test_a_number_backed_by_the_resume_is_accepted(self):
        reply = '{"answer": "2023", "evidence": "M.S. Data Science, UMBC, 2023"}'
        assert ask(reply, "Which year did you finish your masters?", kind="number") == "2023"

    def test_a_number_backed_by_the_profile_is_accepted(self):
        reply = '{"answer": "3", "evidence": "total_years_experience: 3"}'
        assert ask(reply, NUMERIC_Q, kind="number") == "3"

    def test_an_invented_number_is_discarded(self):
        # The shape that gets a 3-year candidate applied to a 10-year role: a
        # plausible figure with a sentence written to justify it.
        reply = ('{"answer": "8", "evidence": "The candidate has eight years of '
                 'experience in machine learning."}')
        assert ask(reply, NUMERIC_Q, kind="number") is None

    def test_a_number_with_no_evidence_at_all_is_discarded(self):
        assert ask('{"answer": "8", "evidence": ""}', NUMERIC_Q, kind="number") is None

    def test_a_real_quote_that_does_not_contain_the_number_is_discarded(self):
        # Quoting the resume accurately, then attaching an unrelated figure.
        reply = ('{"answer": "9", "evidence": "Skills: Python, FastAPI, Docker, '
                 'Kubernetes, AWS, PyTorch"}')
        assert ask(reply, NUMERIC_Q, kind="number") is None

    def test_a_year_in_the_quote_does_not_vouch_for_a_different_number(self):
        # "3" is a substring of "2023". Plain substring matching accepted that,
        # which is how an invented figure passes a check that looks strict.
        reply = '{"answer": "3", "evidence": "M.S. Data Science, UMBC, 2023"}'
        assert ask(reply, NUMERIC_Q, kind="number") is None

    def test_typography_does_not_break_a_real_quote(self):
        # Same sentence, different spacing and dash — still the resume's.
        reply = '{"answer": "2023", "evidence": "M.S.  Data Science,   UMBC,  2023"}'
        assert ask(reply, "Year of graduation?", kind="number") == "2023"


class TestYesNo:
    def test_yes_with_a_real_citation_is_accepted(self):
        reply = '{"answer": "Yes", "evidence": "Skills: Python, FastAPI, Docker, Kubernetes"}'
        assert ask(reply, YESNO_Q) == "Yes"

    def test_yes_with_an_invented_citation_is_discarded(self):
        reply = '{"answer": "Yes", "evidence": "Certified Kubernetes Administrator, 2021"}'
        assert ask(reply, YESNO_Q) is None

    def test_a_bare_yes_is_allowed_through(self):
        # Nothing in a resume says "yes", and the rules answer most of these
        # already. Blocking it would email you about questions no source can
        # ever quote for.
        assert ask('{"answer": "No", "evidence": ""}', YESNO_Q) == "No"


class TestOptions:
    def test_choosing_one_of_the_form_s_own_options_is_allowed(self):
        # The form wrote the words; picking one is not a claim about a fact.
        reply = '{"answer": "Native or bilingual proficiency", "evidence": ""}'
        assert ask(reply, "What is your level of proficiency in English?",
                   options=["Native or bilingual proficiency", "Limited working"]) \
            == "Native or bilingual proficiency"

    def test_but_a_numeric_option_still_needs_backing(self):
        reply = '{"answer": "10", "evidence": ""}'
        assert ask(reply, NUMERIC_Q, options=["3", "10"], kind="number") is None


class TestFreeText:
    def test_free_text_quoted_from_the_resume_is_accepted(self):
        reply = '{"answer": "FastAPI", "evidence": "Built retrieval pipelines in Python with LangChain and FastAPI."}'
        assert ask(reply, TEXT_Q) == "FastAPI"

    def test_free_text_from_nowhere_is_discarded(self):
        reply = '{"answer": "Ruby on Rails", "evidence": "The candidate prefers Rails."}'
        assert ask(reply, TEXT_Q) is None


class TestReplyParsing:
    def test_reads_the_json_object(self):
        assert _parse_answer('{"answer": "3", "evidence": "x"}') == ("3", "x")

    def test_reads_json_wrapped_in_prose_or_fences(self):
        raw = 'Sure! ```json\n{"answer": "3", "evidence": "x"}\n``` hope that helps'
        assert _parse_answer(raw) == ("3", "x")

    def test_a_bare_answer_survives_as_an_unevidenced_one(self):
        # Not an outage: it still has to pass grounding, which a bare number
        # will not. A bare "Yes" remains usable.
        assert _parse_answer("Yes") == ("Yes", "")

    def test_a_model_that_ignores_the_format_does_not_take_the_pipeline_down(self):
        assert ask("Yes", YESNO_Q) == "Yes"
        assert ask("8", NUMERIC_Q, kind="number") is None

    def test_empty_reply(self):
        assert _parse_answer("") == ("", "")
        assert _parse_answer(None) == ("", "")

    def test_malformed_json_falls_back_to_the_first_line(self):
        assert _parse_answer('{"answer": "3", ') == ('{"answer": "3",', "")


class TestFlatten:
    def test_ignores_punctuation_and_case_and_spacing(self):
        assert _flat("M.S.  Data Science,   UMBC") == _flat("m s data science umbc")
        assert _flat("Hello,   World!") == "hello world"

    def test_keeps_digits(self):
        assert "38" in _flat("current_ctc: 38 LPA")


class TestUnknownStillWorks:
    def test_explicit_unknown_is_respected(self):
        assert ask('{"answer": "UNKNOWN", "evidence": ""}', NUMERIC_Q) is None
