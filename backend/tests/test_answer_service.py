"""
Resume passage selection for screening answers.

The LLM leg answers form questions from the resume, and it can only answer from
what it is shown. It used to be shown resume_text[:1200] — the contact block and
the summary — so a question about a skill named on page two saw nothing about
that skill, returned UNKNOWN, and landed in the pending list even though the
resume answered it plainly. These tests pin the part that decides what gets
shown.
"""

import pytest

from services.answer_service import _relevant_resume, _tokens


RESUME = """Venkata Naga Santosh Mokkapati
mukulmokkapati@gmail.com | Hyderabad, India

SUMMARY
AI Software Engineer building agentic systems and retrieval pipelines.

EXPERIENCE
AI Software Engineer, Incrivelsoft (2023-2026)
Built a fraud detection model in PyTorch serving 2M daily events.
Shipped FastAPI services deployed on Kubernetes across three regions.

Data Engineer, Earlier Co (2022-2023)
Maintained Airflow DAGs feeding a Snowflake warehouse.

EDUCATION
M.S. Data Science, UMBC
B.Tech Information Technology, JNTU

SKILLS
Python, CrewAI, LangGraph, FastAPI, Kubernetes, Airflow, Snowflake, PyTorch
"""


class TestTokens:
    def test_drops_stopwords(self):
        assert _tokens("How many years of experience with Kubernetes") == {"kubernetes"}

    def test_keeps_symbol_bearing_tech_names(self):
        toks = _tokens("Do you know C++ and C# and .net")
        assert "c++" in toks and "c#" in toks


class TestRelevantResume:
    def test_short_resume_returned_whole(self):
        short = "Python and FastAPI."
        assert _relevant_resume(short, "Do you know Python?") == short

    def test_empty_resume_is_empty(self):
        assert _relevant_resume("", "anything") == ""

    def test_surfaces_a_late_skill_the_old_head_slice_missed(self):
        # Kubernetes appears ~700 chars in, past nothing in particular — the
        # point is that selection is by relevance, not position.
        out = _relevant_resume(RESUME, "How many years of Kubernetes experience?",
                               budget=300)
        assert "Kubernetes" in out
        assert len(out) <= 300

    def test_picks_the_block_naming_the_asked_skill(self):
        out = _relevant_resume(RESUME, "Have you used Airflow?", budget=200)
        assert "Airflow" in out

    def test_respects_the_budget(self):
        out = _relevant_resume(RESUME, "Python Kubernetes Airflow Snowflake PyTorch",
                               budget=150)
        assert len(out) <= 150

    def test_keeps_original_order_so_dates_stay_with_bullets(self):
        out = _relevant_resume(RESUME, "Tell me about PyTorch and Snowflake", budget=600)
        assert out.index("PyTorch") < out.index("Snowflake")

    def test_matched_heading_pulls_in_the_lines_under_it(self):
        # "EDUCATION" is the only block sharing a word with the question; the
        # degrees that actually answer it share none. A heading on its own is
        # not an answer, so the block after it comes too.
        out = _relevant_resume(RESUME, "What is your highest level of education?",
                               budget=400)
        assert "EDUCATION" in out
        assert "UMBC" in out

    def test_unmatched_question_falls_back_to_the_head(self):
        # Nothing in the resume is about horticulture; returning the head is the
        # honest default, and the prompt's UNKNOWN rule stops it fabricating.
        out = _relevant_resume(RESUME, "Describe your horticulture certification",
                               budget=120)
        assert out == RESUME[:120]

    def test_question_of_pure_stopwords_falls_back_to_the_head(self):
        out = _relevant_resume(RESUME, "How many years?", budget=120)
        assert out == RESUME[:120]
