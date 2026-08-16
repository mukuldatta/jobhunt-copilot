"""
Putting a number into the band a form offers instead of a box.

Naukri's questionnaire asks for experience as buckets — "No experience",
"<4 years", "4-5 years", ">8 years" — while the profile holds a single figure.
The resolver answers "3", nothing matches it literally, and a question the
profile settles outright gets handed to a human.

Measured on the live drawer: "How many years of experience do you have in
RAG?" offered exactly those buckets and the resolver produced "3".
"""

import pytest

from services.answer_service import _option_matches_number, _snap_number_to_range

BUCKETS = ["No experience", "<4 years", "4-5 years", "5-6 years",
           "6-7 years", "7-8 years", ">8 years"]


class TestTheLiveBuckets:
    @pytest.mark.parametrize("value,expected", [
        (0, "No experience"),
        (1, "<4 years"),
        (3, "<4 years"),          # the real case
        (4, "4-5 years"),
        (5, "4-5 years"),         # first band that contains it wins
        (6.5, "6-7 years"),
        (9, ">8 years"),
    ])
    def test_a_number_lands_in_its_band(self, value, expected):
        assert _snap_number_to_range(str(value), BUCKETS) == expected

    def test_the_boundary_is_not_swallowed_by_the_under_band(self):
        # "<4 years" must not claim 4 itself.
        assert not _option_matches_number("<4 years", 4)
        assert _option_matches_number("<4 years", 3.9)


class TestPhrasings:
    @pytest.mark.parametrize("option,value,ok", [
        ("Less than 2 years", 1, True),
        ("Less than 2 years", 2, False),
        ("Up to 5 years", 5, True),
        ("More than 10 years", 11, True),
        ("More than 10 years", 10, False),
        ("Above 3 years", 4, True),
        ("5+ years", 5, True),
        ("5+ years", 4, False),
        ("2 to 4 years", 3, True),
        ("2 – 4 years", 4, True),
        ("Fresher", 0, True),
        ("Fresher", 2, False),
        ("3", 3, True),
        ("3", 4, False),
    ])
    def test_each_shape(self, option, value, ok):
        assert _option_matches_number(option, value) is ok

    def test_text_without_a_number_never_matches(self):
        for o in ("Yes", "No", "Remote", ""):
            assert not _option_matches_number(o, 3)


class TestSafety:
    def test_a_non_numeric_answer_snaps_to_nothing(self):
        assert _snap_number_to_range("Immediately", BUCKETS) is None
        assert _snap_number_to_range("Yes", ["Yes", "No"]) is None

    def test_no_options_snaps_to_nothing(self):
        assert _snap_number_to_range("3", []) is None

    def test_a_number_with_no_home_snaps_to_nothing(self):
        # Better to ask than to file 12 years under "4-5 years".
        assert _snap_number_to_range("12", ["<4 years", "4-5 years"]) is None

    def test_yes_no_options_are_untouched_by_a_numeric_answer(self):
        assert _snap_number_to_range("3", ["Yes", "No"]) is None
