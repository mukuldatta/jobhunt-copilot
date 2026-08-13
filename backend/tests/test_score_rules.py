"""
The computable half of a match score.

Skills and location are facts, not judgements — asking an LLM for a number it
cannot derive is how a Java/Spring role once scored 40/40 on skills while
naming Java as a gap. These are the rules that replaced that.
"""

import pytest

from utils.score_rules import (
    SCORER_VERSION,
    SKILLS_MAX,
    location_points,
    mentions,
    skills_match,
)


class TestMentions:
    @pytest.mark.parametrize(
        "haystack,term",
        [
            ("we use python and fastapi", "python"),
            ("kubernetes-based platform", "kubernetes"),
            ("strong ai background", "ai"),
            ("ml pipelines in production", "ml"),
            ("we write go and rust", "go"),
        ],
    )
    def test_finds_a_real_mention(self, haystack, term):
        assert mentions(haystack, term)

    @pytest.mark.parametrize(
        "haystack,term",
        [
            ("maintenance technician", "ai"),
            ("html templates", "ml"),
            ("algorithm design", "go"),
            ("golang services", "go"),   # "golang" is its own term, not "go"
            ("therapist role", "api"),
        ],
    )
    def test_short_terms_need_word_boundaries(self, haystack, term):
        assert not mentions(haystack, term)

    @pytest.mark.parametrize(
        "haystack,term",
        [
            ("experience with sklearn", "scikit-learn"),
            ("k8s in production", "kubernetes"),
            ("we run postgres", "postgresql"),
            ("nodejs services", "node.js"),
            ("retrieval augmented generation", "rag"),
        ],
    )
    def test_aliases_resolve(self, haystack, term):
        """
        _ALIASES existed but was never consulted, so a posting asking for
        "sklearn" or "k8s" counted as a gap against a resume that lists
        scikit-learn and Kubernetes.
        """
        assert mentions(haystack, term)

    def test_empty_term_matches_nothing(self):
        assert not mentions("anything at all", "")
        assert not mentions("anything at all", "   ")


class TestSkillsMatch:
    resume_skills = ["Python", "FastAPI", "CrewAI", "MongoDB", "Docker"]

    def test_scores_a_strong_overlap_highly(self):
        jd = "Looking for Python, FastAPI and Docker experience building services."
        out = skills_match(jd, self.resume_skills)
        assert out["points"] is not None
        assert out["points"] > 20
        assert set(out["matched"]) == {"Python", "FastAPI", "Docker"}

    def test_reports_what_the_posting_demands_and_the_resume_lacks(self):
        jd = "Java and Spring Boot required. Java experience essential, Spring Boot daily."
        out = skills_match(jd, self.resume_skills)
        assert any("java" in m for m in out["missing"])

    def test_a_single_passing_mention_is_not_a_gap(self):
        # The threshold is two mentions, so an aside does not become a gap.
        jd = "Python service work. Nice to have: exposure to terraform."
        out = skills_match(jd, self.resume_skills)
        assert "terraform" not in out["missing"]

    def test_no_recognisable_signal_returns_no_opinion(self):
        out = skills_match("A wonderful place to grow your career.", self.resume_skills)
        assert out["points"] is None
        assert out["matched"] == [] and out["missing"] == []

    def test_thin_overlap_cannot_take_full_marks(self):
        """
        Ratio alone is too kind: a JD naming Python and nothing else scores
        1.0. Points are capped by how much evidence there actually is.
        """
        out = skills_match("Python role.", self.resume_skills)
        assert out["points"] <= 10

    def test_never_exceeds_the_dimension_maximum(self):
        jd = " ".join(self.resume_skills * 3)
        out = skills_match(jd, self.resume_skills)
        assert 0 <= out["points"] <= SKILLS_MAX

    def test_missing_list_is_capped(self):
        jd = " ".join(f"{t} {t}" for t in ["java", "scala", "ruby", "php", "kotlin", "swift", "vue"])
        assert len(skills_match(jd, self.resume_skills)["missing"]) <= 5

    def test_handles_an_empty_resume(self):
        out = skills_match("Python and FastAPI.", [])
        assert out["matched"] == []


class TestLocationPoints:
    @pytest.mark.parametrize(
        "location,expected",
        [
            ("Hyderabad, India", 10),
            ("Bengaluru", 10),
            ("Pune, Maharashtra", 10),
            ("Kolkata, India", 8),
            ("Remote", 5),
            ("Work from home", 5),
            ("Austin, Texas", 3),
        ],
    )
    def test_tiers(self, location, expected):
        assert location_points(location) == expected

    def test_never_exceeds_its_share_of_the_score(self):
        for loc in ["Hyderabad", "Remote", "London", "", None]:
            assert 0 <= location_points(loc) <= 10

    def test_reads_the_description_when_the_location_is_vague(self):
        assert location_points("", "This is a fully remote position.") == 5


def test_scorer_version_is_a_positive_int():
    # get_apply_candidates filters on equality with this, and
    # get_jobs_needing_score re-scores anything that differs. A non-int or a
    # zero would make both selectors behave in ways nobody intended.
    assert isinstance(SCORER_VERSION, int)
    assert SCORER_VERSION >= 1
