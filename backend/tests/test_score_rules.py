"""
The computable half of a match score.

Skills and location are facts, not judgements — asking an LLM for a number it
cannot derive is how a Java/Spring role once scored 40/40 on skills while
naming Java as a gap. These are the rules that replaced that.
"""

import pytest


from utils.score_rules import (
    EXPERIENCE_MAX,
    SCORER_VERSION,
    SKILLS_MAX,
    experience_points,
    location_points,
    mentions,
    required_years,
    skills_match,
)


class TestRequiredYearsDecimals:
    """
    Job boards emit "6.0-10.0 Years". The pattern only understood integers, so
    the sole thing that matched was the "0" of the trailing ".0" immediately
    before "Years" — a posting demanding six to ten years was recorded as
    demanding none. required_years gates APPLY_YEARS_STRETCH, so the guardrail
    switched itself off for precisely the postings it exists to catch, and a
    6-10 year role was auto-applied to against a 3-year resume.
    """

    @pytest.mark.parametrize("text,expected", [
        ("Years of Experience: 6.0-10.0 Years", 6),
        ("6.0-10.0 Years of experience required", 6),
        ("Minimum 5.5 years experience", 5),
        ("10.0 years minimum", 10),
        ("Experience 2.5 to 4.0 yrs", 2),
    ])
    def test_decimal_forms_read_the_lower_bound(self, text, expected):
        assert required_years(text) == expected

    @pytest.mark.parametrize("text,expected", [
        ("Years of Experience: 6-10 Years", 6),
        ("3+ years of experience", 3),
        ("We need 6 to 10 years of experience", 6),
        ("Experience: 0-5 Yrs", 0),          # zero is a real answer
    ])
    def test_integer_forms_still_work(self, text, expected):
        assert required_years(text) == expected

    @pytest.mark.parametrize("text", [
        "a 35 year old IT services organization",
        "founded more than 40 years ago",
        "15 years full time education",
        "no mention of duration at all",
    ])
    def test_non_requirements_stay_unknown(self, text):
        # None means unknown and must never collapse into 0, which would read
        # as "this posting asks for no experience" and always be eligible.
        assert required_years(text) is None

    def test_fraction_is_never_a_figure_of_its_own(self):
        # The exact regression: ".0" before "Years" must not surface as 0.
        assert required_years("6.0-10.0 Years") != 0


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


class TestRequiredYears:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Preferred candidate profile 3-5 years of Data Engineering", 3),
            ("Job title: ML Engineer Experience: 3 - 6 Years Location: Pune", 3),
            ("Experience: 6 to 12 years Job Type: Full-Time", 6),
            ("Bachelors in CS with 5-9 years experience", 5),
            ("Generative AI Lead or Engineer with 6+ years of experience", 6),
            ("Key skill: Python, Gen AI Experience: 7+ Years Notice Period", 7),
            ("Skills for success 12+ years experience in machine learning", 12),
            ("Requirements 8-13 years of general IT experience", 8),
            ("Additional Responsibilities 5 years of experience in Python", 5),
            ("Experience Level : Mid to Senior [5+ Years] Department", 5),
            ("looking for a GenAI Platform Engineer with 3–6 years", 3),
            ("Gen AI, NLP, or Computer Vision; 4 to 27 years Openings", 4),
        ],
    )
    def test_reads_the_lower_bound(self, text, expected):
        assert required_years(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "Iris Software is a 35 year old IT Service organization",
            "founded the biotechnology industry more than 40 years ago",
            "A 15 years full time education is required for this role",
            "We are a mission-driven company. Apply today.",
        ],
    )
    def test_ignores_text_that_is_not_a_requirement(self, text):
        """
        Company age, company history and Indian schooling notation (10+2+3 =
        "15 years full time education") all print "N years" without asking for
        any. Read as requirements they would gate away legitimate roles.
        """
        assert required_years(text) is None

    def test_entry_level_zero_survives(self):
        # 0 is a real answer and must not be confused with "unknown".
        assert required_years("Experience: 0-5 Yrs. Any graduate.") == 0
        assert required_years("0-3 years, fresh graduates welcome") == 0

    def test_the_smallest_stated_requirement_wins(self):
        # An incidental mention must not disqualify a job on its own.
        assert required_years("3+ years Python, 8+ years leadership") == 3

    def test_implausible_values_are_rejected(self):
        """
        Real rows carry "58 years" and "48 years" — ranges whose dash was
        deleted back when clean_description stripped non-ASCII. Unknown is the
        honest answer; guessing would gate the job away.
        """
        assert required_years("Masters degree and 58 years of experience") is None

    def test_a_corrupted_row_is_rescued_by_a_second_mention(self):
        # This is a real posting: "0-5 Yrs" in the highlights, mangled "2-4"
        # further down. Taking the minimum reads it correctly as entry level.
        text = "Experience: 0-5 Yrs. Job highlights Experience: 24 Years. Education: Any Graduate"
        assert required_years(text) == 0

    def test_handles_empty_input(self):
        assert required_years("") is None
        assert required_years(None) is None


class TestExperiencePoints:
    MINE = 3

    def test_a_role_at_or_below_the_candidate_scores_full(self):
        assert experience_points("3-5 years of experience", self.MINE) == EXPERIENCE_MAX
        assert experience_points("2-4 years of experience", self.MINE) == EXPERIENCE_MAX

    def test_the_curve_falls_as_the_gap_widens(self):
        seq = [experience_points(f"{n}+ years of experience", self.MINE) for n in (4, 5, 6, 7, 8)]
        assert seq == sorted(seq, reverse=True), seq
        assert seq[0] > seq[-1]

    def test_a_role_years_out_of_reach_scores_zero(self):
        """
        The case that started this: a Gen AI Engineer wanting 10-12 years took
        28/30 on experience from the LLM, scored 94 overall, and was applied
        to against a 3-year resume.
        """
        assert experience_points("10-12 years of experience", self.MINE) == 0
        assert experience_points("12+ years experience in ML", self.MINE) == 0

    def test_an_unstated_requirement_is_neutral(self):
        # Silence is not evidence either way. Zero would bury every posting
        # that simply does not mention years.
        pts = experience_points("We build great products.", self.MINE)
        assert 0 < pts < EXPERIENCE_MAX

    def test_unknown_candidate_years_is_neutral(self):
        assert experience_points("8+ years required", None) == 20
        assert experience_points("8+ years required", "") == 20

    def test_accepts_the_profile_string_form(self):
        # total_years_experience is stored as a string, by design.
        assert experience_points("10+ years", "3") == 0
        assert experience_points("3+ years", "3") == EXPERIENCE_MAX

    def test_never_leaves_its_range(self):
        for text in ["1 year", "3-5 years", "20+ years", "no mention", ""]:
            assert 0 <= experience_points(text, self.MINE) <= EXPERIENCE_MAX


def test_scorer_version_is_a_positive_int():
    # get_apply_candidates filters on equality with this, and
    # get_jobs_needing_score re-scores anything that differs. A non-int or a
    # zero would make both selectors behave in ways nobody intended.
    assert isinstance(SCORER_VERSION, int)
    assert SCORER_VERSION >= 1
