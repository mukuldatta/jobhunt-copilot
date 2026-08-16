"""
Turning an email reply into a form answer.

Whatever comes out of extract_answer is typed into a real job application, so
it must be the line you wrote and nothing else — not the quoted original, not
your signature, not the whole thread. The token pairing is checked too: an
answer filed against the wrong question is worse than no answer.
"""

import pytest

from services.answer_service import question_token
from services.inbox_service import extract_answer, MAX_ANSWER_CHARS


class TestExtractAnswer:
    def test_plain_one_line(self):
        assert extract_answer("Yes") == "Yes"

    def test_skips_leading_blank_lines(self):
        assert extract_answer("\n\n  3 years  \n") == "3 years"

    def test_stops_before_quoted_original(self):
        body = "5\n\nOn Fri, Aug 14, 2026 at 9:02 PM JobHunt wrote:\n> What is your notice period?"
        assert extract_answer(body) == "5"

    def test_ignores_angle_quoted_thread_when_answer_is_first(self):
        body = "Immediately\n> [JHQ:abc1234567] When can you start?\n> reply below"
        assert extract_answer(body) == "Immediately"

    def test_quote_first_means_no_answer(self):
        # Top-posting is the norm; if the quote comes first there is no reply.
        body = "> What is your expected CTC?\n> reply below\n"
        assert extract_answer(body) == ""

    def test_stops_at_signature_delimiter(self):
        assert extract_answer("--\nMukul\nAI Engineer") == ""

    def test_signature_after_answer_is_not_included(self):
        assert extract_answer("18 LPA\n--\nMukul") == "18 LPA"

    def test_forwarded_header_is_not_an_answer(self):
        assert extract_answer("From: JobHunt <no-reply@x>\nSubject: hi") == ""

    def test_empty_body(self):
        assert extract_answer("") == ""
        assert extract_answer(None) == ""

    def test_caps_runaway_length(self):
        assert len(extract_answer("x" * 5000)) == MAX_ANSWER_CHARS


class TestQuestionToken:
    def test_stable_across_calls(self):
        q = "How many years of experience do you have with Python?"
        assert question_token(q) == question_token(q)

    def test_insensitive_to_case_and_whitespace(self):
        # Pending questions are keyed on the same normalisation, so the token
        # has to agree with it or a reply matches nothing.
        assert question_token("Expected CTC?") == question_token("  expected   ctc?  ")

    def test_different_questions_differ(self):
        assert question_token("Expected CTC?") != question_token("Current CTC?")

    def test_shape_matches_what_the_subject_regex_accepts(self):
        import re
        from services.inbox_service import _TOKEN_RE
        token = question_token("Do you require sponsorship?")
        assert re.fullmatch(r"[0-9a-f]{10}", token)
        assert _TOKEN_RE.search(f"[JHQ:{token}] Do you require sponsorship?").group(1) == token
