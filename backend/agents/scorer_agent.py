import os
import json
import re
import asyncio
from llm_provider import LLMProvider, RateLimited
from db.mongodb import get_resume
from utils.job_parser import truncate_description
from utils.score_rules import (
    skills_match, location_points, experience_points, required_years,
    SKILLS_MAX, EXPERIENCE_MAX, SCORER_VERSION,
)
from dotenv import load_dotenv

load_dotenv()


def _dedupe(gaps: list) -> list:
    """
    Drop repeats and near-repeats. The model is shown the computed gaps and
    routinely echoes them back expanded ("scala" -> "Scala", "gcp" -> "Google
    Cloud Platform (GCP)"), which reads as four gaps where there are two.
    """
    out, seen = [], []
    for g in gaps:
        g = (g or "").strip()
        if not g:
            continue
        key = g.lower()
        if any(key == s or key in s or s in key for s in seen):
            continue
        seen.append(key)
        out.append(g)
    return out


class ScorerAgent:
    def __init__(self):
        self.llm = LLMProvider(provider=os.getenv("LLM_PROVIDER", "groq"))
        self._resume_cache = None
        self._years_cache = None
        self.last_retry_after = None   # seconds hinted by the provider, if any
        # score() returns None for two unrelated reasons: this job cannot be
        # scored at all (no description, no recognisable technology), or the
        # attempt failed and is worth retrying. The caller has to tell them
        # apart — treating a skip as a rate limit burns a backoff per empty
        # posting and halts the run.
        self.last_skipped = False

    async def _get_resume_text(self) -> str:
        if self._resume_cache:
            return self._resume_cache
        resume = await get_resume()
        if resume:
            self._resume_cache = resume.get("parsed_text", "")
        return self._resume_cache or ""

    async def _candidate_years(self):
        """
        Years of experience to score against, from Setup > You.

        Same number the apply agent types into forms, so the score and the
        application cannot disagree about it. None when it has never been set,
        which experience_points reads as "unknown" rather than as zero.
        """
        if self._years_cache is not None:
            return self._years_cache
        from db.mongodb import get_apply_profile

        profile = await get_apply_profile() or {}
        raw = profile.get("total_years_experience")
        if raw in (None, ""):
            raw = os.getenv("APPLY_YEARS_EXPERIENCE")
        try:
            self._years_cache = float(raw)
        except (TypeError, ValueError):
            self._years_cache = None
        return self._years_cache

    # Below this a "description" is a title echo or a location string, not
    # something you can match a resume against.
    MIN_DESCRIPTION_CHARS = 200

    async def score(self, job: dict) -> dict:
        # Scoring a job with no description is not a hard match, it is a
        # fabrication: the model has nothing to deduct from, so it returns the
        # top of the range and the job outranks everything real. Those scores
        # then drive auto-apply selection. Leave it unscored instead — same
        # contract as an error below, so it stays out of the apply pool and gets
        # picked up again once the description arrives.
        self.last_skipped = False
        description = (job.get("description") or "").strip()
        if len(description) < self.MIN_DESCRIPTION_CHARS:
            self.last_retry_after = None
            self.last_skipped = True
            print(f"Scorer: skipping {job.get('job_id')} — description is "
                  f"{len(description)} chars, nothing to score against.")
            return None

        resume_text = await self._get_resume_text()
        # Keep the PROMPT lean — the free Groq tier has a daily token budget and
        # a long resume+JD per job exhausts it after ~100 jobs. Only the model
        # sees the truncated copy; see below.
        job_description = truncate_description(job.get("description", ""), max_chars=1500)
        title = job.get("title", "")
        company = job.get("company", "")

        location = job.get("location", "")

        # Facts first — computed, not asked for, and computed from the WHOLE
        # posting. These are local regex passes that cost no tokens, so there
        # was never a reason to feed them the truncated copy, and doing so was
        # actively wrong: truncate_description looks for a requirements marker
        # and will happily match "qualifications" inside the EEO boilerplate at
        # the foot of a posting, handing the scorer HR legal text while the
        # skills list sits untouched in the middle. Measured across the stored
        # jobs, that cost nine postings their entire skill signal — they were
        # skipped as naming no technology at all — understated another 51, and
        # inflated 34 more by hiding the gaps that would have counted against
        # them. Token budget constrains the prompt, not the arithmetic.
        full_description = job.get("description", "") or ""
        resume = await get_resume() or {}
        skills = skills_match(full_description, resume.get("skills", []))
        location_score = location_points(location, full_description)

        # Years are read from the posting and compared against the profile.
        # Asked of the model, a 10-12 year role scored 28/30 against a 3-year
        # resume and was applied to.
        my_years = await self._candidate_years()
        experience_score = experience_points(full_description, my_years)
        needs_years = required_years(full_description)

        if skills["points"] is None:
            self.last_skipped = True
            print(f"Scorer: skipping {job.get('job_id')} — JD names no recognisable "
                  f"technology, nothing to match on.")
            return None

        prompt = f"""You are a resume-job matcher. Judge ONE dimension only.

RESUME:
{resume_text[:1200]}

JOB TITLE: {title}
COMPANY: {company}
JOB DESCRIPTION:
{job_description}

domain_score (max 20): is the industry and problem space relevant to this
candidate's background? A different domain scores low even when the job title
looks similar.

Also list up to 3 requirements this resume does not evidence, beyond the
technologies already accounted for: {', '.join(skills['missing'][:5]) or 'none found'}.
Do not comment on years of experience — that is measured separately.

Be strict. A maximum means nothing is missing on that dimension. Most genuine
matches land in the middle of the range.

Return only JSON. The number is an EXAMPLE of shape, not a value to copy:
{{"domain_score": 12, "gap_analysis": ["gap1", "gap2"]}}"""

        try:
            # llm.complete() is synchronous; run it off the event loop so the API
            # stays responsive while a long scoring batch runs in the background.
            response = await asyncio.to_thread(self.llm.complete, prompt)
            data = self._parse_json(response)
            domain = max(0, min(int(data.get("domain_score", 0)), 20))

            # A model that maxes the one dimension it was given, on a job that
            # is also a perfect skills and experience match, is reciting the
            # template rather than judging. Refuse it rather than bank a 100.
            if (domain == 20 and skills["points"] == SKILLS_MAX
                    and experience_score == EXPERIENCE_MAX):
                print(f"Scorer: rejecting degenerate all-maximum score for {job.get('job_id')}")
                return None

            total = skills["points"] + experience_score + domain + location_score
            gaps = [g for g in (data.get("gap_analysis") or []) if isinstance(g, str)]
            if needs_years is not None and my_years is not None and needs_years - my_years >= 2:
                # Stated first: it is the reason the score dropped, and it is
                # the one gap no amount of tailoring can close.
                gaps.insert(0, f"wants {needs_years}+ years, resume shows {my_years:g}")

            return {
                "match_score": total,
                "score_breakdown": {
                    "skills_score": skills["points"],
                    "experience_score": experience_score,
                    "domain_score": domain,
                    "location_score": location_score,
                },
                # Stored so the apply queue can refuse a role that is years out
                # of reach without re-parsing every description.
                "required_years": needs_years,
                # Derived from the same pass as the score, so a named gap can
                # never sit next to a perfect skills mark again. Deduped because
                # the model tends to restate the ones it was already shown.
                "gap_analysis": _dedupe(skills["missing"] + gaps)[:5],
                "skills_matched": skills["matched"],
                "scorer_version": SCORER_VERSION,
            }
        except RateLimited as e:
            # Providers are throttled — surface the hint so the caller can back
            # off for the right amount of time.
            m = re.search(r"retry_after=(\d+)", str(e))
            self.last_retry_after = float(m.group(1)) if m else None
            print(f"Scorer rate limited for {job.get('job_id')}: {e}")
            return None
        except Exception as e:
            # Do NOT return a 0 score here: a saved 0 is indistinguishable from a
            # genuine poor match, so the job would look scored and never be
            # retried. Signal failure and let the caller leave it unscored.
            self.last_retry_after = None
            print(f"Scorer error for {job.get('job_id')}: {e}")
            return None

    def _parse_json(self, text: str) -> dict:
        text = text.strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
        return json.loads(text)
