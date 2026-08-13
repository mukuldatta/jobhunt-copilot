"""
The filters that decide which postings are worth spending LLM quota on, and
what text the scorer gets to read.
"""

import pytest

from utils.job_parser import (
    clean_description,
    dedup_key,
    extract_contract_type,
    generate_job_id,
    india_location_regex,
    is_relevant_job,
    truncate_description,
)


class TestIsRelevantJob:
    @pytest.mark.parametrize(
        "title",
        [
            "AI Engineer",
            "Senior ML Engineer",
            "Machine Learning Engineer",
            "Data Scientist",
            "Backend Developer (Python)",
            "GenAI Platform Engineer",
            "MLOps Engineer",
            "Full Stack Developer",
            "Software Architect",
            "API Platform Engineer",
        ],
    )
    def test_accepts_target_roles(self, title):
        assert is_relevant_job(title)

    @pytest.mark.parametrize(
        "title,substring_that_used_to_match",
        [
            ("Maintenance Technician", "ai"),
            ("Trainee Associate", "ai"),
            ("Captain — Fleet Operations", "ai"),
            ("Retail Manager", "ai"),
            ("Physiotherapist", "api"),
        ],
    )
    def test_rejects_incidental_substring_matches(self, title, substring_that_used_to_match):
        """
        Keywords were matched as bare substrings, so "ai" found Maintenance,
        Trainee and Captain, and "api" found Therapist. Each false positive
        then cost a scoring call and a row in the review queue.
        """
        assert not is_relevant_job(title)

    @pytest.mark.parametrize(
        "title",
        [
            "Sales Executive",
            "HR Manager",
            "Content Writer",
            "Product Manager",
            "Business Analyst",
            "UX Designer",
        ],
    )
    def test_rejects_explicitly_excluded_roles(self, title):
        assert not is_relevant_job(title)

    def test_exclusion_beats_inclusion(self):
        # "Engineer" is relevant, "sales" is not — the veto wins.
        assert not is_relevant_job("Sales Engineer")

    def test_handles_missing_input(self):
        assert not is_relevant_job("")
        assert not is_relevant_job(None)


class TestCleanDescription:
    def test_keeps_the_rupee_sign(self):
        # encode("ascii", "ignore") deleted this, leaving salary figures with
        # no unit on an India-targeted product.
        assert "₹" in clean_description("<p>CTC: ₹12,00,000 per annum</p>")

    def test_keeps_accented_and_non_latin_text(self):
        assert clean_description("Café · Zürich · बेंगलुरु") == "Café · Zürich · बेंगलुरु"

    def test_strips_markup_urls_and_control_characters(self):
        out = clean_description("<div>Apply at https://example.com/x now</div>\x00\x07")
        assert "<div>" not in out
        assert "https://" not in out
        assert "\x00" not in out and "\x07" not in out
        assert "Apply at" in out

    def test_collapses_whitespace(self):
        assert clean_description("a   \n\n  b") == "a b"

    def test_handles_empty(self):
        assert clean_description("") == ""
        assert clean_description(None) == ""


class TestDedupKey:
    def test_same_role_different_listing_collides(self):
        a = dedup_key("AI Engineer", "Incrivelsoft Pvt Ltd")
        b = dedup_key("ai engineer", "Incrivelsoft Private Limited")
        assert a == b

    def test_different_roles_do_not_collide(self):
        assert dedup_key("AI Engineer", "Acme") != dedup_key("Data Engineer", "Acme")

    def test_different_companies_do_not_collide(self):
        assert dedup_key("AI Engineer", "Acme") != dedup_key("AI Engineer", "Globex")

    def test_job_id_follows_the_url(self):
        # job_id is listing identity; dedup_key is role identity. A re-post
        # under a new URL must be a new job_id but the same dedup_key.
        a = generate_job_id("https://x.com/1", "AI Engineer", "Acme")
        b = generate_job_id("https://x.com/2", "AI Engineer", "Acme")
        assert a != b
        assert dedup_key("AI Engineer", "Acme") == dedup_key("AI Engineer", "Acme")


class TestExtractContractType:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("C2C only", "c2c"),
            ("Corp to Corp welcome", "c2c"),
            ("Contract to hire role", "contract_to_hire"),
            ("W2 contract, 12 months", "w2_contract"),
            ("Contractor position", "w2_contract"),
            ("Full time permanent role", "fulltime"),
        ],
    )
    def test_classifies(self, text, expected):
        assert extract_contract_type("Engineer", text) == expected


class TestTruncateDescription:
    def test_short_text_is_untouched(self):
        assert truncate_description("short", max_chars=100) == "short"

    def test_keeps_the_requirements_tail(self):
        """
        Real postings put the requirements last. Head-only truncation threw
        away the only part that says what the employer actually wants, leaving
        the scorer working from company boilerplate.
        """
        boilerplate = "We are a mission-driven company. " * 200
        text = boilerplate + "Requirements: Python, FastAPI, CrewAI, Kubernetes."
        out = truncate_description(text, max_chars=600)
        assert len(out) <= 700
        assert "Requirements" in out
        assert "CrewAI" in out

    def test_falls_back_to_head_truncation(self):
        text = "no marker here. " * 500
        out = truncate_description(text, max_chars=400)
        assert out.endswith("...")
        assert len(out) <= 403


def test_india_regex_covers_the_target_cities():
    rx = india_location_regex()
    for city in ("india", "hyderabad", "bengaluru", "gurugram", "gurgaon"):
        assert city in rx
