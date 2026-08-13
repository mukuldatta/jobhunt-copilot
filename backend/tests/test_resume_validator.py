"""
The resume-integrity checks.

This is the code that stands between an LLM and a document submitted under the
user's name, so it gets the most attention in the suite. The headline case is
the one at the bottom of `TestInventedQuantities`: a real regression that
silently disabled the invented-numbers check.
"""

import pytest

from utils.resume_validator import (
    _norm_num,
    _numbers_in,
    _invented_quantities,
    clean_resume_text,
    validate_tailored_resume,
)


class TestNormNum:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("100", "100"),
            ("2020", "2020"),
            ("10", "10"),
            ("40", "40"),
            ("300", "300"),
            ("1,200", "1200"),
            ("3.0", "3"),
            ("2.50", "2.5"),
            ("0", "0"),
        ],
    )
    def test_normalises_without_mangling(self, raw, expected):
        assert _norm_num(raw) == expected

    def test_only_decimals_lose_trailing_zeroes(self):
        # The whole point. rstrip(".0") on a plain integer eats real digits.
        assert _norm_num("100") != "1"
        assert _norm_num("2020") != "202"
        assert _norm_num("50") != "5"


class TestNumbersIn:
    def test_reads_digits_and_words(self):
        found = _numbers_in("Led three teams over 5 years, shipping 1,200 models.")
        assert {"3", "5", "1200"} <= found

    def test_does_not_invent_numbers(self):
        # Every value in the set must appear in the text in some form. The old
        # implementation manufactured "4" from "40" and "202" from "2020",
        # which then vouched for claims the resume never made.
        found = _numbers_in("Improved throughput by 40%. Graduated 2020. Cut latency 300ms.")
        assert found == {"40", "2020", "300"}


class TestInventedQuantities:
    original = (
        "Built data pipelines at Incrivelsoft. Led a team of 2 engineers. "
        "Improved throughput by 40%. Reduced latency 300ms. Graduated 2020."
    )

    def test_flags_an_inflated_team(self):
        assert _invented_quantities("Led a team of 12 engineers.", self.original) == [
            "12 engineers"
        ]

    def test_allows_a_quantity_the_original_states(self):
        assert _invented_quantities("Led a team of 2 engineers.", self.original) == []

    def test_ignores_bare_numbers_with_no_unit(self):
        # An address or a street number is not a claim about achievement.
        assert _invented_quantities("Suite 900, 47 Residency Road.", self.original) == []

    def test_no_original_means_no_opinion(self):
        assert _invented_quantities("Led a team of 40 engineers.", "") == []

    def test_caps_the_report(self):
        tailored = " ".join(f"{n} engineers" for n in range(11, 30))
        assert len(_invented_quantities(tailored, self.original)) <= 5

    @pytest.mark.parametrize(
        "claim,phantom_from",
        [("4 years", "40%"), ("3 engineers", "300ms"), ("202 users", "2020")],
    )
    def test_regression_a_truncated_number_does_not_license_a_claim(self, claim, phantom_from):
        """
        The bug this suite exists for.

        `rstrip(".0")` truncated characters rather than a suffix, so the "40%"
        in the original registered as a known "4", "300ms" as "3", and the
        graduation year "2020" as "202". Any claim matching one of those
        phantoms passed silently — and this is the only automated check on
        whether a tailored resume inflates the user's experience.

        Note the contrast with `test_allows_a_quantity_the_original_states`:
        a number the resume really does state should pass. Only the invented
        ones must be caught.
        """
        assert _invented_quantities(f"Delivered with {claim} of impact.", self.original) == [claim]


class TestCleanResumeText:
    def test_strips_a_conversational_preamble(self):
        raw = "Here is the tailored resume:\n\nMukul Mokkapati\nAI Engineer"
        assert clean_resume_text(raw).startswith("Mukul Mokkapati")

    def test_strips_code_fences(self):
        assert "```" not in clean_resume_text("```\nMukul Mokkapati\n```")

    def test_leaves_a_clean_document_alone(self):
        text = "Mukul Mokkapati\nAI Engineer\nPython, FastAPI"
        assert clean_resume_text(text) == text


class TestValidateTailoredResume:
    resume = {
        "parsed_text": (
            "Mukul Mokkapati — mukulmokkapati@gmail.com\n"
            "AI Software Engineer at Incrivelsoft. Built retrieval pipelines with "
            "Python, FastAPI and CrewAI. Led a team of 2 engineers and improved "
            "throughput by 40%.\n"
            "M.S. Data Science, UMBC. B.Tech IT, JNTU.\n"
        ) * 4,
        "skills": ["Python", "FastAPI", "CrewAI"],
        "education": ["M.S. Data Science UMBC", "B.Tech IT JNTU"],
    }

    def _tailored(self, body):
        # Long enough to clear the length floor, so each test isolates the
        # check it is actually about.
        return (
            "Mukul Mokkapati — mukulmokkapati@gmail.com\n"
            f"{body}\n"
            "M.S. Data Science, UMBC. B.Tech IT, JNTU.\n"
        ) * 4

    def test_accepts_an_honest_rewrite(self):
        out = validate_tailored_resume(
            self._tailored(
                "AI Software Engineer at Incrivelsoft. Designed retrieval pipelines "
                "in Python and FastAPI, using CrewAI. Led 2 engineers; throughput up 40%."
            ),
            self.resume,
            user_name="Mukul Mokkapati",
            user_email="mukulmokkapati@gmail.com",
        )
        assert out["ok"], out["issues"]
        assert out["severity"] == "ok"

    def test_rejects_a_fabricated_skill(self):
        out = validate_tailored_resume(
            self._tailored(
                "AI Software Engineer. Expert in Python, FastAPI, CrewAI and Kubernetes."
            ),
            self.resume,
            user_name="Mukul Mokkapati",
            user_email="mukulmokkapati@gmail.com",
        )
        assert not out["ok"]
        assert any("not in the original" in i for i in out["issues"])

    def test_rejects_an_inflated_quantity(self):
        out = validate_tailored_resume(
            self._tailored(
                "AI Software Engineer at Incrivelsoft. Python, FastAPI, CrewAI. "
                "Led a team of 15 engineers."
            ),
            self.resume,
            user_name="Mukul Mokkapati",
            user_email="mukulmokkapati@gmail.com",
        )
        assert not out["ok"]
        assert any("quantities" in i for i in out["issues"])

    def test_rejects_an_empty_document(self):
        out = validate_tailored_resume("", self.resume)
        assert not out["ok"]
        assert out["severity"] == "fail"

    def test_rejects_truncation(self):
        out = validate_tailored_resume(
            "Mukul Mokkapati\nAI Software Engineer at Incrivelsoft using Python. " * 2,
            self.resume,
        )
        assert not out["ok"]

    def test_warns_when_the_name_is_dropped(self):
        out = validate_tailored_resume(
            self._tailored("AI Software Engineer at Incrivelsoft. Python, FastAPI, CrewAI."),
            self.resume,
            user_name="Someone Else",
        )
        # A missing name is a warning, not an integrity failure.
        assert out["ok"]
        assert out["severity"] == "warn"

    def test_rejects_a_dropped_education_section(self):
        out = validate_tailored_resume(
            (
                "Mukul Mokkapati — mukulmokkapati@gmail.com\n"
                "AI Software Engineer at Incrivelsoft. Python, FastAPI, CrewAI. "
                "Led a team of 2 engineers and improved throughput by 40%.\n"
            ) * 4,
            self.resume,
            user_name="Mukul Mokkapati",
            user_email="mukulmokkapati@gmail.com",
        )
        assert not out["ok"]
        assert any("Education" in i for i in out["issues"])
