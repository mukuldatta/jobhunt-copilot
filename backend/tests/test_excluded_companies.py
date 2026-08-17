"""
Employers the agent never applies to.

Not a judgement the code is entitled to make — it is the user's, and the only
place it can be honoured is before an application is spent, because an
application cannot be withdrawn. Scraping, scoring and Review are untouched: an
excluded employer's postings still appear to read.

Two properties matter. A name must match the variants a job board actually
writes ("Tata Consultancy Services Ltd", "TCS Digital"), and a short name must
not match a different company that happens to contain those letters.
"""

import pytest

from platforms import company_excluded, excluded_company_pattern


class TestMatching:
    @pytest.mark.parametrize("company", [
        "Tata Consultancy Services",
        "Tata Consultancy Services Ltd",
        "TATA CONSULTANCY SERVICES",
        "TCS",
        "TCS Digital",
        "Careers at TCS",
    ])
    def test_the_excluded_employer_and_its_variants(self, company):
        assert company_excluded(company)

    @pytest.mark.parametrize("company", [
        "Infosys",
        "Datazoic Machines",
        "TCSion Learning",        # contains the letters, different company
        "Multitcs Solutions",
        "Tata Elxsi",             # a different Tata company
        "Tata Consultancy",       # not the excluded full name, and not "TCS"
    ])
    def test_other_employers_are_untouched(self, company):
        assert not company_excluded(company)

    def test_empty_and_missing_names(self):
        assert not company_excluded("")
        assert not company_excluded(None)


class TestPattern:
    def test_short_names_are_word_anchored(self):
        # Without the boundary, "TCS" matches half the companies with those
        # three letters anywhere in the name.
        assert r"\bTCS\b" in excluded_company_pattern()

    def test_long_names_match_as_substrings(self):
        # So a board writing "… Ltd" or "… (India)" still matches.
        assert r"Tata\ Consultancy\ Services" in excluded_company_pattern()

    def test_the_pattern_is_a_valid_regex(self):
        import re
        re.compile(excluded_company_pattern())


class TestConfigurability:
    def test_the_list_comes_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("APPLY_EXCLUDE_COMPANIES", "Acme Corp, Foo")
        import importlib
        import platforms
        importlib.reload(platforms)
        try:
            assert platforms.company_excluded("Acme Corp India")
            assert platforms.company_excluded("Foo")
            assert not platforms.company_excluded("Tata Consultancy Services")
        finally:
            monkeypatch.delenv("APPLY_EXCLUDE_COMPANIES")
            importlib.reload(platforms)

    def test_an_empty_list_excludes_nobody(self, monkeypatch):
        monkeypatch.setenv("APPLY_EXCLUDE_COMPANIES", "")
        import importlib
        import platforms
        importlib.reload(platforms)
        try:
            assert platforms.excluded_company_pattern() == ""
            assert not platforms.company_excluded("Tata Consultancy Services")
        finally:
            monkeypatch.delenv("APPLY_EXCLUDE_COMPANIES")
            importlib.reload(platforms)
