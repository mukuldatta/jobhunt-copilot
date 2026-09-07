"""
The tailored-resume cache key.

`build_tailored_resume` serves a stored tailoring whenever its recorded version
still matches the resume's. So the version has to change whenever the resume
does — otherwise an uploaded revision is silently applied to with resumes
tailored against the document it replaced.
"""

from datetime import datetime, timezone

from services.resume_service import _resume_version


def test_two_resumes_of_the_same_length_get_different_versions():
    """The reason `save_resume` stamps `uploaded_at`: without it the key falls
    back to len(parsed_text), and an edit that happens to preserve the length
    would reuse every tailored resume already stored."""
    first = {"uploaded_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
             "parsed_text": "A" * 4773}
    second = {"uploaded_at": datetime(2026, 9, 7, tzinfo=timezone.utc),
              "parsed_text": "B" * 4773}
    assert _resume_version(first) != _resume_version(second)


def test_the_same_resume_keeps_its_version():
    resume = {"uploaded_at": datetime(2026, 9, 7, tzinfo=timezone.utc),
              "parsed_text": "resume"}
    assert _resume_version(resume) == _resume_version(dict(resume))


def test_a_resume_stored_before_stamping_still_gets_a_version():
    """Documents saved before `uploaded_at` existed must not crash the cache —
    they fall back to the length, which is what they have always used."""
    assert _resume_version({"parsed_text": "x" * 12}) == "12"
    assert _resume_version(None) == "none"
