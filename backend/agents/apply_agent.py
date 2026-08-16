import os
import re
import asyncio
import tempfile
import time
from datetime import datetime
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright
from agents.tailor_agent import TailorAgent
from agents.cover_letter_agent import CoverLetterAgent
from services.answer_service import AnswerResolver
from utils.pdf_generator import generate_resume_pdf
from utils.resume_validator import validate_tailored_resume, clean_resume_text
from services.alert_service import send_manual_action_alert, send_question_email
from services import agent_state
from llm_provider import RateLimited, is_rate_limited, cooldown_remaining
from platforms import apply_supported, disabled_reason
from db.mongodb import (
    get_application_by_job_id, claim_job_for_apply, finish_job_apply, record_application,
    update_job, bump_apply_attempt, mark_question_emailed,
    save_auth_state, clear_auth_state,
)
from dotenv import load_dotenv

load_dotenv()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

STEALTH_JS = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
    window.chrome = {runtime: {}};
"""

# Persistent browser profiles keep you logged in between runs, so login (and any
# CAPTCHA/2FA) happens once by hand, then the warm session is reused. One dir per
# platform to keep cookies isolated.
PROFILE_ROOT = os.environ.get("BROWSER_PROFILE_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), ".browser_profiles"
)
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "apply")

# How each board says "this one is applied for on the employer's own site".
# These are the only grounds for calling a posting external — without a positive
# match the agent reports an honest failure instead, because a wrong "external"
# is terminal (see ApplyAgent._no_apply_control) and reads like a real outcome.
EXTERNAL_MARKERS = {
    "naukri": (
        "#company-site-button",
        'button:has-text("Apply on company site")',
        'a:has-text("Apply on company site")',
    ),
    "linkedin": (
        # LinkedIn's off-site postings show a plain "Apply" with an external-link
        # glyph plus this line, rather than the Easy Apply button.
        ':text("Responses managed off LinkedIn")',
        'button[aria-label*="company website" i]',
        'a[aria-label*="company website" i]',
        'a.jobs-apply-button[target="_blank"]',
    ),
    "indeed": (
        'a[data-testid="applyButtonLinkContainer"]',
        ':text("Apply on company site")',
    ),
    "dice": (
        ':text("Apply on company site")',
        'a[data-cy="external-apply"]',
    ),
}

# LinkedIn's Easy Apply modal was a native <dialog open>, and everything that
# drove the form was scoped to it. It no longer carries dialog semantics at all
# — no <dialog>, no role=dialog, no aria-modal — so all of it silently stopped
# matching and the agent reported "the modal did not open" over a screenshot
# showing it plainly open, populated and waiting on Next.
#
# Each of these is present ONLY while the modal is up: measured 0 before the
# click, 1 while open, 0 after closing. dialog[open] stays first so this keeps
# working if LinkedIn reverts or A/B tests the old markup back.
#
# One selector, used by both the Python locators and the in-page JS below — the
# duplicated copy of the apply-button selector is exactly how the last drift
# went unnoticed on one path while the other was fixed.
LINKEDIN_MODAL_SEL = "dialog[open], [data-test-modal], .artdeco-modal"

# Playwright's own selector engine pierces open shadow roots. document.querySelector
# does not, and LinkedIn now renders the Easy Apply modal inside a shadow root on a
# div.theme--light. So every page.evaluate() that looked for the modal found nothing
# while the Playwright locators beside it found it fine — and the ones that fell back
# to `|| document` went on to scrape the entire page instead. That is how a run
# reported filling the form and its only "field" was the notification bell:
#
#     fields found in modal: ['Notifications', 'Gen AI Engineer, Bengaluru...']
#     [ok] Notifications -> 0
#
# Anything reaching into the modal from in-page JS has to walk shadow roots, and
# must return nothing rather than the page when the modal is absent: typing into
# whatever the page happens to expose is worse than not answering at all.
_DEEP_QUERY_JS = """
    const deepQuery = (sel) => {
        const direct = document.querySelector(sel);
        if (direct) return direct;
        const stack = [document];
        while (stack.length) {
            const root = stack.pop();
            for (const el of root.querySelectorAll('*')) {
                if (!el.shadowRoot) continue;
                const hit = el.shadowRoot.querySelector(sel);
                if (hit) return hit;
                stack.push(el.shadowRoot);
            }
        }
        return null;
    };
"""


def _js(body: str) -> str:
    """Inline the shadow-piercing helper wherever a body asks for it."""
    return body.replace("__DEEP_QUERY__", _DEEP_QUERY_JS)


# The modal's footer buttons, matched by ACCESSIBLE name — which is the
# aria-label when one is present, not the visible text. LinkedIn's Next button
# reads <button aria-label="Continue to next step">Next</button>, so the old
# get_by_role(name="Next", exact=True) could never match it: the form filled
# correctly, then stalled on step 1 every time because nothing advanced it.
#
# These stay scoped to the modal (see _modal), which is what stops the
# recommended-jobs carousel's own "Next" from being picked up — the exact=True
# that used to serve that purpose was the thing breaking it.
# A control labelled with a document filename is a resume chooser, never a
# screening question.
_DOC_NAME_RE = re.compile(r"\.(pdf|docx?|rtf)\b", re.I)

NEXT_NAME = re.compile(r"(^next$|continue to next|^continue$)", re.I)
REVIEW_NAME = re.compile(r"^review", re.I)
# Deliberately anchored. This is the irreversible click, and a loose pattern
# that caught some other control would submit an application we never checked.
# Missing it costs a retry; matching the wrong thing cannot be undone.
SUBMIT_NAME = re.compile(r"^submit", re.I)

# Serialize applies process-wide: parallel headed Chrome logins are an instant
# bot signal, and two runs must never submit the same job at once.
_APPLY_LOCK = asyncio.Lock()

# Last manual-action alert per platform (epoch seconds), for throttling.
_LAST_ALERT: dict = {}

# Per-platform config. We do NOT automate login (platform login pages are
# obfuscated and gated by 2FA/CAPTCHA). Instead you log in once by hand via the
# Login button; the persistent browser profile keeps the session, and the apply
# flow just detects whether that session is still valid.
LOGIN = {
    "linkedin": {
        "home_url": "https://www.linkedin.com/feed/",
        "login_url": "https://www.linkedin.com/login",
        "logged_in_sel": "img.global-nav__me-photo, div.global-nav__me, button.global-nav__primary-link-me-menu-trigger",
        "fail_url_tokens": ["/login", "authwall", "checkpoint", "signup", "uas/login"],
        "home_is_private": True,   # feed redirects to /login when signed out
    },
    "naukri": {
        "home_url": "https://www.naukri.com/mnjuser/homepage",
        "login_url": "https://www.naukri.com/nlogin/login",
        "logged_in_sel": "a[href*='mnjuser/profile'], .nI-gNb-drawer__bars, .view-profile-wrapper",
        "fail_url_tokens": ["nlogin", "/login"],
        "home_is_private": True,   # homepage redirects to login when signed out
    },
    "indeed": {
        "home_url": "https://in.indeed.com/",
        "login_url": "https://secure.indeed.com/auth",
        "logged_in_sel": "[data-gnav-element-name='AccountMenu'], #gnav-main-AccountButton",
        "fail_url_tokens": ["account/login", "/auth", "signin"],
    },
    "dice": {
        "home_url": "https://www.dice.com/dashboard/",
        "login_url": "https://www.dice.com/dashboard/login",
        "logged_in_sel": "[data-cy='nav-profile'], a[href*='dashboard']",
        "fail_url_tokens": ["/login", "signin"],
    },
}

# Boards worth offering a Login button for. A board we will never submit to has
# no reason to ask you to fight its sign-in flow, so it drops off Setup > Job
# boards along with the apply path.
SUPPORTED_PLATFORMS = [p for p in LOGIN if apply_supported(p)]


class ApplyAgent:
    def __init__(self):
        first = os.environ.get("USER_FIRST_NAME", "Mukul")
        last = os.environ.get("USER_LAST_NAME", "Mokkapati")
        self.user_name = f"{first} {last}"
        self.user_email = os.environ.get("MY_EMAIL", "mukulmokkapati@gmail.com")
        self.user_phone = os.environ.get("MY_PHONE", "")
        self.answers = None   # AnswerResolver, loaded per apply (needs DB)
        self.current_job = ""  # "Title @ Company" — names the job in a pause notice

        # Headed is required for manual CAPTCHA. Only go headless if explicitly asked.
        self.headless = os.environ.get("APPLY_HEADLESS", "").strip().lower() in ("1", "true", "yes")
        self.human_timeout = int(os.environ.get("APPLY_HUMAN_TIMEOUT", "300"))
        self.login_timeout = int(os.environ.get("LOGIN_TIMEOUT", "420"))
        # How many inconclusive attempts a posting gets before we stop asking.
        # See _finalize — this is what keeps selector drift from permanently
        # burning a queue of perfectly applicable jobs.
        self.max_apply_attempts = int(os.environ.get("APPLY_MAX_ATTEMPTS", "3"))
        # APPLY_DRY_RUN: navigate + fill the form but stop before the final submit
        # (screenshot it) — for safely tuning selectors without applying for real.
        self.dry_run = os.environ.get("APPLY_DRY_RUN", "").strip().lower() in ("1", "true", "yes")

    def _say(self, msg: str):
        """
        One line of narration — to the terminal, and to the live agent log.

        Every step of an apply used to print() and nothing more, which meant the
        only way to see why a posting was deferred was to be watching the
        process's stdout at the moment it happened. These lines are the actual
        account of the run: which question went unanswered, whether the tailored
        resume attached, which modal step it stalled on. They belong somewhere
        you can read them afterwards.
        """
        agent_state.log(msg, job=self.current_job)

    # ── Public entrypoint ────────────────────────────────────────────────────

    async def apply(self, job: dict) -> dict:
        job_id = job.get("job_id", "")
        source = job.get("source", "")
        self.current_job = f"{job.get('title', '')} @ {job.get('company', '')}".strip(" @")
        agent_state.set_job(self.current_job)

        # Re-read here, not in __init__: the dry-run toggle lives in Setup and
        # has to bind at apply time to be worth anything as a safety switch.
        from services.settings_service import get_agent_rules
        self.dry_run = (await get_agent_rules())["dry_run"]
        # Why this job was put back, if it was. Any non-None value means no
        # application was submitted and the job stays retryable.
        self._defer_reason = None
        self._resume_attached = False

        # An application cannot be withdrawn, so a generic resume is not an
        # acceptable degradation — we would be spending the one shot at this
        # employer on a weaker application. Defer instead, before claiming the
        # job or opening a browser, and pick it up when the quota refills.
        if is_rate_limited():
            self._say(f"    deferred before starting: LLM quota exhausted for "
                      f"{cooldown_remaining():.0f}s")
            return {"status": "deferred", "url": job.get("url", ""),
                    "retry_after": round(cooldown_remaining()),
                    "message": f"LLM quota exhausted — deferring this job for "
                               f"{cooldown_remaining():.0f}s rather than applying "
                               f"with an untailored resume."}

        if not apply_supported(source):
            # Known board, deliberately not submitted to. Hand it back before
            # spending a browser launch or a single LLM call on it.
            self._say(f"    skipped: {disabled_reason(source)}")
            return {
                "status": "manual_required",
                "url": job.get("url", ""),
                "message": disabled_reason(source),
            }

        if source not in LOGIN:
            self._say(f"    skipped: auto-apply is not supported for '{source}'")
            return {
                "status": "manual_required",
                "url": job.get("url", ""),
                "message": f"Auto-apply not supported for '{source}'. Open the link and apply manually.",
            }

        # Serialize + dedup. The lock stops concurrent runs; the claim stops
        # re-applying to a job that's already applied or in progress.
        async with _APPLY_LOCK:
            existing = await get_application_by_job_id(job_id)
            if existing and existing.get("status") not in (None, "saved"):
                return {"status": "already_applied", "message": "Already applied to this job."}

            if not await claim_job_for_apply(job_id):
                return {"status": "already_applied",
                        "message": "Job is already applied or an apply is in progress."}

            try:
                result = await self._do_apply(job)
            except Exception as e:
                result = {"status": "error", "message": str(e)}

            await self._finalize(job_id, result)
            return result

    # The in-platform apply control per board — the thing whose presence means
    # "we can submit this ourselves".
    APPLY_CONTROL = {
        # LinkedIn ships per-build obfuscated class names and has moved the
        # control from <button class="jobs-apply-button"> to an <a>, so every
        # class- and tag-based selector we had matched nothing at all — the
        # button was plainly visible in the failure screenshots while all four
        # selectors returned zero. The accessible name is the only stable
        # handle left.
        #
        # Do NOT reach for :has-text("Easy Apply") here: the recommended-jobs
        # cards further down the page carry that text too, so it would resolve
        # to a different posting and apply to the wrong job.
        "linkedin": ('a[aria-label*="Easy Apply" i], button[aria-label*="Easy Apply" i], '
                     '.jobs-apply-button, .jobs-s-apply button'),
        "naukri": 'button#apply-button, a#apply-button, button.apply-button, button[title*="Apply"]',
        "dice": ('apply-button-wc, button[data-cy="apply-button"], '
                 'a[data-cy="apply-button"], button[id*="apply"]'),
    }

    async def _preflight(self, page, job: dict, source: str):
        """
        Decide whether this posting is ours to submit, before any LLM spend.

        Returns None to proceed, or the terminal result to hand back. Records
        apply_type either way so the answer is durable and the candidate query
        stops offering the same dead job on every cycle.
        """
        try:
            await page.goto(job["url"], wait_until="domcontentloaded", timeout=25000)
        except Exception as e:
            return {"status": "error", "message": f"Could not open the posting: {type(e).__name__}"}
        await asyncio.sleep(2)

        job_id = job.get("job_id", "")

        if await self._looks_expired(page):
            await update_job(job_id, {"apply_type": "expired"})
            return {"status": "expired", "url": page.url,
                    "message": "This posting has closed — it is no longer accepting applications."}

        for selector in EXTERNAL_MARKERS.get(source, ()):
            try:
                if await page.query_selector(selector):
                    await update_job(job_id, {"apply_type": "external"})
                    return self._external_apply(job)
            except Exception:
                continue

        control = await self._await_apply_control(page, self.APPLY_CONTROL.get(source, ""))
        if control:
            await update_job(job_id, {"apply_type": "in_platform"})
            return None

        # No control and no marker — ambiguous, and not something to guess at.
        return await self._no_apply_control(page, job, source)

    async def _finalize(self, job_id: str, result: dict):
        status = result.get("status")
        if status == "applied":
            await finish_job_apply(job_id, "applied")
            await record_application(job_id, {
                "status": "applied",
                "applied_at": datetime.utcnow(),
                "tailored_resume_text": result.pop("_tailored_text", None),
                "cover_letter": result.pop("_cover_letter", None),
                "notes": "Auto-applied via ApplyAgent",
            })
        elif status == "expired":
            # Terminal, and not your problem to finish by hand either.
            await finish_job_apply(job_id, "expired")
        elif status == "manual_required":
            # A read answer: the posting itself says it applies elsewhere. Final.
            await finish_job_apply(job_id, "manual_required")
        elif status == "needs_review":
            # An unread answer: the control never appeared, the form stalled, a
            # CAPTCHA went unattended. Selector drift and a slow render are
            # indistinguishable here, and filing this terminally meant one bad
            # run burned the posting forever — 53 live jobs were sitting in
            # manual_required with working apply buttons because of it.
            #
            # Retry a bounded number of times instead. The retry is cheap: the
            # control is checked in _preflight, before any tailoring, so a
            # posting that is genuinely unreadable costs a page load and not an
            # LLM call. After the budget it goes to manual_required and stops
            # asking, so a permanently broken posting cannot spin forever.
            attempts = await bump_apply_attempt(job_id)
            if attempts < self.max_apply_attempts:
                await finish_job_apply(job_id, "new")
                self._say(f"    retryable: attempt {attempts}/{self.max_apply_attempts}, "
                          f"returned to the queue")
            else:
                await finish_job_apply(job_id, "manual_required")
        elif status in ("login_required", "dry_run", "deferred"):
            # Not a real apply — release it so it can be retried later.
            await finish_job_apply(job_id, "new")
        elif status == "question_pending":
            # Waiting on you, not on us. Released so the next cycle retries it,
            # but it still spends the ambiguity budget: reaching the questions
            # costs a tailored resume and a cover letter, so a posting whose
            # questions never get answered must not be re-tailored forever.
            attempts = await bump_apply_attempt(job_id)
            await finish_job_apply(job_id, "new" if attempts < self.max_apply_attempts
                                   else "manual_required")
        elif status == "already_applied":
            pass
        else:  # error
            await finish_job_apply(job_id, "apply_failed")

    async def _do_apply(self, job: dict) -> dict:
        source = job["source"]
        self._captcha_gave_up = False   # per-job: don't carry a giveup forward

        async with async_playwright() as pw:
            async with self._session(pw, source) as (ctx, page):
                login = await self._ensure_login(page, source)
                if not login["ok"]:
                    return login["result"]

                # Establish that this posting is actually submittable BEFORE
                # spending anything on it. Tailoring plus a cover letter is two
                # LLM calls, and roughly 70% of postings hand off to the
                # employer's own site — so discovering that inside the handler,
                # after the documents exist, wastes the scarcest resource on the
                # jobs least able to use it.
                preflight = await self._preflight(page, job, source)
                if preflight is not None:
                    return preflight

                self._say("    tailoring the resume for this posting")
                pdf_path, tailored_text = await self._make_resume(job)
                if self._defer_reason:
                    return {"status": "deferred", "url": job.get("url", ""),
                            "retry_after": round(cooldown_remaining()),
                            "message": f"{self._defer_reason} — deferred before touching "
                                       f"the form. No application was submitted."}

                self._say("    writing the cover letter")
                cover_letter = await self._make_cover_letter(job)
                await self._load_answers()

                self._say(f"    filling the {source} application form")
                handler = getattr(self, f"_apply_{source}")
                result = await handler(page, job, pdf_path, cover_letter)

        if result.get("status") == "applied":
            if tailored_text:
                result["_tailored_text"] = tailored_text
            if cover_letter:
                result["_cover_letter"] = cover_letter
        return result

    # ── Resume ───────────────────────────────────────────────────────────────

    # The retry-with-avoid-list lives in services.resume_service._offending_terms,
    # which is the single path that tailors a resume. A copy here went stale —
    # it only knew the fabricated-skills marker, never the invented-quantities
    # one — and nothing called it.

    async def _make_resume(self, job: dict):
        """
        Tailor + validate the resume. If validation fails (fabricated skills,
        truncation, dropped education), fall back to the ORIGINAL resume so an
        honest document is submitted rather than a corrupted one. Returns
        (pdf_path, text_that_was_used).
        """
        from services.resume_service import build_tailored_resume

        built = await build_tailored_resume(job, user_name=self.user_name,
                                            user_email=self.user_email)
        if built["cached"]:
            self._say("    reusing the tailored resume already approved for this job")

        if built["rate_limited"]:
            # Quota died mid-run. Distinct from a tailoring bug: falling back to
            # the original resume would spend an irreversible application on a
            # weaker document, so the caller defers the whole job instead.
            self._say("    LLM quota exhausted while tailoring — deferring.")
            self._defer_reason = "LLM quota exhausted"
            return None, None

        if not built["ok"]:
            # Submitting the original here would quietly send a generic resume on
            # a one-shot, irreversible application — put the job back instead.
            self._say(f"    Resume rejected {built['issues']} — deferring job.")
            self._defer_reason = (
                f"tailored resume failed validation ({'; '.join(built['issues'])[:120]})")
            return None, None

        final_text = built["text"]
        if built["issues"]:
            self._say(f"    Resume validation warnings: {built['issues']}")
        if not final_text:
            return None, None

        try:
            # The filename reaches the recruiter, so it carries the candidate's
            # name and the company — not tempfile's tmpsjrl69sg.pdf, which reads
            # as machine output before anyone opens it.
            safe = re.sub(r"[^A-Za-z0-9]+", "_", self.user_name or "Resume").strip("_")
            company = re.sub(r"[^A-Za-z0-9]+", "_", job.get("company", "")).strip("_")
            name = f"{safe}_{company}.pdf" if company else f"{safe}.pdf"
            path = os.path.join(tempfile.mkdtemp(prefix="jobhunt_"), name)
            generate_resume_pdf(final_text, path)
            return path, final_text
        except Exception as e:
            self._say(f"ApplyAgent: PDF generation failed: {e}")
            return None, final_text

    async def _load_answers(self):
        """Load the questionnaire profile used to answer screening questions."""
        try:
            from db.mongodb import get_resume
            resume = await get_resume() or {}
            self.answers = await AnswerResolver.load(resume.get("parsed_text", ""))
            p = self.answers.profile or {}
            # Prefer questionnaire contact details over env defaults.
            self.user_phone = p.get("phone") or self.user_phone
            self.user_email = p.get("email") or self.user_email
        except Exception as e:
            self._say(f"    ApplyAgent: could not load apply profile ({e}); using defaults")
            self.answers = AnswerResolver({})

    async def _make_cover_letter(self, job: dict) -> str:
        """Best-effort cover letter — never blocks an apply if it fails."""
        try:
            return clean_resume_text(await CoverLetterAgent().generate(job))
        except Exception as e:
            self._say(f"ApplyAgent: cover letter generation failed: {e}")
            return ""

    async def _fill_cover_letter(self, page, text: str):
        """Paste the cover letter into a message / cover-letter field if present."""
        if not text:
            return
        for sel in ['textarea[name*="coverLetter" i]', 'textarea[id*="coverLetter" i]',
                    'textarea[aria-label*="cover letter" i]', 'textarea[placeholder*="cover letter" i]',
                    'textarea[aria-label*="message" i]', 'textarea[placeholder*="message" i]']:
            try:
                el = await page.query_selector(sel)
                if el and not (await el.input_value()):
                    await el.fill(text[:1900])
                    return
            except Exception:
                continue

    # ── Browser session (headed + persistent) ────────────────────────────────

    @asynccontextmanager
    async def _session(self, pw, platform: str, headed: bool = None):
        user_dir = os.path.join(PROFILE_ROOT, platform)
        os.makedirs(user_dir, exist_ok=True)
        ctx = await self._launch_persistent(pw, user_dir, headed=headed)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            try:
                await page.add_init_script(STEALTH_JS)
            except Exception:
                pass
            yield ctx, page
        finally:
            try:
                await ctx.close()
            except Exception:
                pass

    async def _launch_persistent(self, pw, user_dir: str, headed: bool = None):
        headless = self.headless if headed is None else (not headed)
        # Strip the automation signals (webdriver flag, --enable-automation infobar)
        # so sites' bot detection — and third-party OAuth like Google — are less
        # likely to refuse the session. Native email/password login is still the
        # most reliable path (Google OAuth blocks controlled browsers regardless).
        opts = dict(
            headless=headless, viewport={"width": 1360, "height": 900},
            locale="en-IN", user_agent=UA,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        try:
            return await pw.chromium.launch_persistent_context(user_dir, channel="chrome", **opts)
        except Exception as e:
            self._say(f"    ApplyAgent: real Chrome unavailable ({e}); using bundled Chromium")
            return await pw.chromium.launch_persistent_context(user_dir, **opts)

    # ── Session detection + interactive (manual) login ───────────────────────

    async def _ensure_login(self, page, platform: str) -> dict:
        """
        Check whether the persistent profile still has a valid session. We never
        automate login — if the session isn't warm, return login_required so you
        can sign in via the Login button and the job is retried afterwards.
        """
        cfg = LOGIN[platform]
        try:
            await page.goto(cfg["home_url"], wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
        except Exception:
            pass
        if await self._is_logged_in(page, cfg):
            return {"ok": True}
        return {"ok": False, "result": {
            "status": "login_required",
            "message": (f"Not signed in to {platform.title()}. Use the {platform.title()} "
                        f"Login button, sign in, then run apply again."),
        }}

    async def login_interactive(self, platform: str, timeout: int = None) -> dict:
        """
        Open a visible browser to the platform and wait for YOU to log in by
        hand (including 2FA/CAPTCHA). On success the session is saved in the
        persistent profile and reused by the apply flow. Triggered by the Login
        button / POST /auth/{platform}/login.
        """
        if platform not in LOGIN:
            return {"status": "error", "message": f"Unsupported platform '{platform}'."}
        timeout = timeout or self.login_timeout
        cfg = LOGIN[platform]

        async with _APPLY_LOCK:  # never share a profile dir with a running apply
            async with async_playwright() as pw:
                async with self._session(pw, platform, headed=True) as (ctx, page):
                    try:
                        await page.goto(cfg["home_url"], wait_until="domcontentloaded", timeout=30000)
                        await asyncio.sleep(2)
                    except Exception:
                        pass
                    if await self._is_logged_in(page, cfg):
                        await save_auth_state(platform)
                        return {"status": "already_logged_in", "platform": platform,
                                "message": f"Already signed in to {platform.title()}."}
                    try:
                        await page.goto(cfg["login_url"], wait_until="domcontentloaded", timeout=30000)
                    except Exception:
                        pass
                    ok = await self._wait_for_human(
                        page, platform, f"Sign in to {platform.title()} in this window",
                        done_check=lambda: self._is_logged_in(page, cfg),
                        timeout=timeout, notify=False,
                    )
                    if ok:
                        await save_auth_state(platform)
                        return {"status": "logged_in", "platform": platform,
                                "message": f"Signed in to {platform.title()} — session saved."}
                    return {"status": "timeout", "platform": platform,
                            "message": f"No sign-in detected within {timeout}s."}

    async def check_login(self, platform: str) -> dict:
        """
        Live probe: open the persistent session and verify against the actual
        page whether it's still signed in, then reconcile the stored auth state.
        Runs headed because headless loads are unreliable on these sites (Naukri
        Akamai / LinkedIn auth walls), which would give false negatives.
        """
        if platform not in LOGIN:
            return {"platform": platform, "logged_in": False, "error": "unsupported"}
        cfg = LOGIN[platform]
        logged_in = False
        async with _APPLY_LOCK:  # never share a profile dir with an apply/login
            async with async_playwright() as pw:
                async with self._session(pw, platform, headed=True) as (ctx, page):
                    try:
                        await page.goto(cfg["home_url"], wait_until="domcontentloaded", timeout=30000)
                        await asyncio.sleep(2)
                    except Exception:
                        pass
                    logged_in = await self._is_logged_in(page, cfg)
        if logged_in:
            await save_auth_state(platform)
        else:
            await clear_auth_state(platform)
        return {"platform": platform, "logged_in": logged_in}

    async def _is_logged_in(self, page, cfg: dict) -> bool:
        url = page.url.lower()
        if any(tok in url for tok in cfg["fail_url_tokens"]):
            return False
        sel = cfg.get("logged_in_sel")
        if sel:
            try:
                if await page.query_selector(sel) is not None:
                    return True
            except Exception:
                pass
        # Auth-gated home pages (LinkedIn feed, Naukri homepage) redirect to login
        # when signed out, so reaching one without a fail-token in the URL is proof
        # of a live session even if the nav DOM hasn't rendered yet. Public home
        # pages (Indeed/Dice) load for everyone, so there we require the selector.
        return bool(cfg.get("home_is_private"))

    # ── Shared helpers ───────────────────────────────────────────────────────

    async def _has_captcha(self, page) -> bool:
        """
        Detect a real challenge. Deliberately strict: page HTML often contains
        the word "captcha" inside bundled scripts, and matching that produced
        false positives that stalled every apply. Require either a visible
        challenge widget or challenge wording in the *visible* text.
        """
        try:
            widget = await page.query_selector(
                'iframe[src*="recaptcha"]:not([src*="anchor"]), iframe[src*="hcaptcha"], '
                'iframe[title*="challenge" i], div.g-recaptcha, #captcha'
            )
            if widget:
                try:
                    if await widget.is_visible():
                        return True
                except Exception:
                    return True

            body = (await page.inner_text("body")).lower()
            phrases = ["verify you're human", "verify you are human", "prove you're not a robot",
                       "security challenge", "are you a robot", "complete this security check",
                       "unusual activity from your"]
            return any(p in body for p in phrases)
        except Exception:
            return False

    async def _wait_for_human(self, page, platform: str, reason: str, done_check=None,
                              timeout: int = None, notify: bool = True) -> bool:
        """
        Pause a headed browser and wait for you to clear a CAPTCHA / 2FA / manual
        step in the open window. Polls until resolved (done_check true, or CAPTCHA
        gone) or times out. `notify` pings you (skip it for button-initiated login).
        """
        timeout = timeout or self.human_timeout
        self._say(f"    [PAUSE] MANUAL ACTION [{platform}]: {reason}")
        self._say(f"            Act in the open browser window. Waiting up to {timeout}s...")
        # Throttle: a batch run can pause on many jobs, and one email+SMS per
        # pause is just noise. Alert at most once every ALERT_COOLDOWN_SEC.
        if notify:
            now = datetime.utcnow().timestamp()
            cooldown = int(os.environ.get("ALERT_COOLDOWN_SEC", "900"))
            last = _LAST_ALERT.get(platform, 0)
            if now - last >= cooldown:
                _LAST_ALERT[platform] = now
                try:
                    send_manual_action_alert(platform, reason, page.url)
                except Exception:
                    pass

        # Surface the pause on Today, so a run blocked behind a CAPTCHA shows up
        # as something needing you rather than as a run that has simply stalled.
        agent_state.begin_human_wait(platform, reason, self.current_job)
        try:
            waited, interval = 0, 5
            while waited < timeout:
                await asyncio.sleep(interval)
                waited += interval
                try:
                    if done_check is not None:
                        if await done_check():
                            self._say(f"    [RESUME] [{platform}] resolved after {waited}s — continuing.")
                            return True
                    elif not await self._has_captcha(page):
                        self._say(f"    [RESUME] [{platform}] challenge cleared after {waited}s — continuing.")
                        return True
                except Exception:
                    pass
            self._say(f"    [TIMEOUT] [{platform}] no human action within {timeout}s — giving up.")
            return False
        finally:
            agent_state.end_human_wait()

    async def _guard_captcha(self, page, platform: str) -> bool:
        """
        If a CAPTCHA shows up mid-flow, pause for a human once. Returns True if
        clear to continue.

        This is called on every step of the modal loop, so without the
        _captcha_gave_up latch an unattended challenge would burn a full
        APPLY_HUMAN_TIMEOUT per step (12 steps x 300s = an hour on one job).
        """
        if getattr(self, "_captcha_gave_up", False):
            return False
        if await self._has_captcha(page):
            ok = await self._wait_for_human(page, platform, "CAPTCHA appeared during application")
            if not ok:
                self._captcha_gave_up = True
            return ok
        return True

    async def _verify_submission(self, page) -> dict | None:
        await asyncio.sleep(2)
        try:
            content = (await page.content()).lower()
            success = ["application submitted", "successfully applied", "thank you for applying",
                       "your application has been", "application received", "we've received your",
                       "application complete", "you applied"]
            if any(p in content for p in success):
                return {"status": "applied", "message": "Confirmed: application submitted successfully."}

            if await self._looks_expired(page):
                return {"status": "expired", "url": page.url,
                        "message": "Job is no longer accepting applications."}
        except Exception:
            pass
        return None

    # A posting that has closed loses its apply control, which looks identical
    # to a selector that has drifted. Checking this first keeps a dead job from
    # being filed as a bug in our code.
    # Phrases must stay specific. This runs against the whole page HTML, so a
    # loose fragment like "is expired" also matches bundled script text and an
    # unrelated banner, and a false positive here is terminal: the job is filed
    # apply_type=expired and never looked at again.
    CLOSED_PHRASES = ("no longer accepting", "position has been filled", "job has expired",
                      "no longer available", "posting has been closed",
                      "this job is no longer", "applications are closed",
                      # Naukri does not 404 a dead posting — it redirects to a
                      # search page carrying this line. Nothing above matched it
                      # ("is expired", not "has expired"), so closed Naukri jobs
                      # were reaching _no_apply_control and being filed as our
                      # bug rather than as a dead posting.
                      "job you are looking for is expired")

    async def _looks_expired(self, page) -> bool:
        """
        Read the phrases off the *rendered* page, not the raw HTML.

        page.content() returns the whole document including every bundled
        script and i18n table, and LinkedIn ships strings like "no longer
        accepting applications" inside those regardless of whether this
        posting is open. That made expiry depend on which bundle happened to
        be in the response: a live NTT DATA posting with a working Easy Apply
        button was filed apply_type=expired, which is terminal and excludes it
        from the apply queue for good.

        Visible text can only say a posting is closed when the page actually
        says so. If a banner ever hides somewhere inner_text cannot reach, the
        job falls through to needs_review instead — retryable, which is the
        right direction to fail in.
        """
        try:
            visible = (await page.inner_text("body")).lower()
        except Exception:
            return False
        return any(p in visible for p in self.CLOSED_PHRASES)

    def _external_apply(self, job: dict) -> dict:
        return {"status": "manual_required", "url": job.get("url", ""),
                "message": "This posting applies on the company site — open the link and finish manually."}

    async def _await_apply_control(self, page, selector: str, timeout: int = 15000):
        """
        Wait for the apply control to render instead of sampling the DOM once.
        Both Naukri and LinkedIn hydrate that button client-side, so a bare
        query_selector after a fixed sleep loses the race on a cold profile.

        Poll for the first *visible, enabled* match rather than deferring to
        wait_for_selector. These pages render the control twice — a sticky
        header copy plus the one in the top card — and only one of the pair is
        visible at a time. wait_for_selector resolves the selector to the first
        node in DOM order and then waits for that specific node to become
        visible, so a hidden duplicate sitting ahead of the real control times
        out while a perfectly clickable button is on screen. Naukri serves
        exactly that shape: two #apply-button nodes, one hidden.

        Returning a hidden node is not the safer failure either — clicking it
        throws or silently does nothing, which surfaces as a stalled form
        rather than as the missing control it actually is.
        """
        if not selector:
            return None
        deadline = time.monotonic() + timeout / 1000.0
        while True:
            try:
                for el in await page.query_selector_all(selector):
                    try:
                        if not await el.is_visible():
                            continue
                        if not await el.is_enabled():
                            continue
                        # LinkedIn's anchor carries aria-disabled rather than the
                        # disabled property, which is_enabled() cannot see.
                        if (await el.get_attribute("aria-disabled") or "").lower() == "true":
                            continue
                        return el
                    except Exception:
                        continue
            except Exception:
                pass
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.5)

    async def _no_apply_control(self, page, job: dict, platform: str) -> dict:
        """
        No apply control appeared. Only call that an external posting when the
        page actually says so — either it offers a company-site button, or it
        has already navigated off the platform.

        Guessing "external" here is the expensive mistake: _finalize marks the
        job manual_required, so it is never retried, and the message reads like
        a legitimate business outcome rather than a failure. A render timeout
        and a selector change both disappear into it silently.
        """
        # Closed postings lose their apply control too, and that is not a bug in
        # our selectors — report it as what it is so the job stops being retried.
        if await self._looks_expired(page):
            return {"status": "expired", "url": page.url,
                    "message": "This posting has closed — it is no longer accepting applications."}

        if platform not in (page.url or "").lower():
            return self._external_apply(job)

        for selector in EXTERNAL_MARKERS.get(platform, ()):
            try:
                if await page.query_selector(selector):
                    return self._external_apply(job)
            except Exception:
                continue

        await self._screenshot(page, f"{platform}_no_apply_control")
        return {"status": "needs_review", "url": page.url,
                "message": f"No apply control found on {platform} after waiting. The page may "
                           f"not have finished rendering, or the selector has drifted — this is "
                           f"not a company-site posting. Screenshot in backend/logs/apply."}

    async def _dry_stop(self, page, platform: str, what: str) -> dict:
        await self._screenshot(page, f"dryrun_{platform}")
        self._say(f"    [DRY RUN] {platform}: reached '{what}', not submitting.")
        return {"status": "dry_run", "url": page.url,
                "message": f"DRY RUN — filled the form and reached '{what}' without submitting. Screenshot saved to backend/logs/apply."}

    async def _screenshot(self, page, label: str):
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            path = os.path.join(LOG_DIR, f"{label}_{int(datetime.utcnow().timestamp())}.png")
            await page.screenshot(path=path, full_page=False)
            self._say(f"    Screenshot saved: {path}")
        except Exception:
            pass

    # ── Screening-question answering (structured profile, no guessing) ────────

    async def _answer_form_fields(self, page) -> list:
        """
        Fill screening questions using the answer profile.

        Returns the questions the profile could NOT answer, as
        [{"question": str, "options": list}]. The caller emails them and defers
        the job rather than guessing — the count alone used to be enough when
        the only response was "pause and let the human read the screen", but an
        emailed question has to carry its own text and choices.
        """
        # Convenience fills that are always safe.
        if self.user_phone:
            for sel in ['input[id*="phoneNumber"]', 'input[name*="phone"]', 'input[type="tel"]']:
                el = await page.query_selector(sel)
                if el:
                    if not await el.input_value():
                        await el.fill(self.user_phone)
                    break
        for sel in ['input[id*="email"]', 'input[type="email"]']:
            el = await page.query_selector(sel)
            if el:
                if not await el.input_value():
                    await el.fill(self.user_email)
                break

        unknown = []

        # 1) Labelled text/number/select fields. LinkedIn's containers are
        # obfuscated, but each question's <label for> points at its field id, so
        # we map via that. (ids can contain odd chars, so target by [id="..."].)
        # Walk every field in the dialog and derive its question text. LinkedIn
        # doesn't consistently use label[for], so fall back to aria-label and
        # then the nearest preceding text in the field's ancestry.
        fields = await page.evaluate(_js("""(MODAL_SEL) => {
            __DEEP_QUERY__
            // No modal means no questions to answer. Falling back to the page
            // would offer up the nav and the notification bell as form fields.
            const root = deepQuery(MODAL_SEL);
            if (!root) return [];
            const out = [];
            let n = 0;
            root.querySelectorAll('input, select, textarea').forEach(e => {
                const type = (e.type || '').toLowerCase();
                if (['hidden', 'file', 'submit', 'button'].includes(type)) return;
                if (!e.offsetParent && type !== 'radio') return;   // not visible

                // The resume chooser is not a screening question. _upload_resume
                // owns it, and answering it here re-selects a stored resume on
                // top of the tailored one we just attached.
                const inResumeBlock = e.closest(
                    '[class*="resume"], [id*="resume"], [data-test*="resume"]');
                if (inResumeBlock) return;

                const isChoice = type === 'radio' || type === 'checkbox';

                let q = '';
                // A radio's own label is its OPTION ("30 days", or a filename),
                // never the question — that lives on the group. Read the group
                // first for choice inputs, or every radio question gets recorded
                // under the text of whichever option happened to come first.
                if (isChoice) {
                    const group = e.closest('fieldset, [role="radiogroup"], [role="group"]');
                    if (group) {
                        const legend = group.querySelector('legend');
                        if (legend) q = (legend.innerText || '').trim();
                        if (!q) q = (group.getAttribute('aria-label') || '').trim();
                        if (!q) {
                            const lb = group.getAttribute('aria-labelledby');
                            if (lb) {
                                const el = document.getElementById(lb);
                                if (el) q = (el.innerText || '').trim();
                            }
                        }
                    }
                }

                if (!q && e.id) {
                    let l = null;
                    try { l = root.querySelector('label[for="' + CSS.escape(e.id) + '"]'); } catch (_) {}
                    if (l && !isChoice) q = (l.innerText || '').trim();
                }
                if (!q) q = (e.getAttribute('aria-label') || '').trim();
                if (!q) {
                    // Nearest ancestor carrying the question. Prefer a line that
                    // reads like one — taking line [0] blindly picks up headings
                    // and option labels that happen to sit above the field.
                    let p = e.parentElement;
                    for (let i = 0; i < 5 && p && !q; i++, p = p.parentElement) {
                        const t = (p.innerText || '').trim();
                        if (!t || t.length <= 5 || t.length >= 300) continue;
                        const lines = t.split('\\n').map(s => s.trim()).filter(Boolean);
                        q = lines.find(s => s.endsWith('?')) ||
                            lines.sort((a, b) => b.length - a.length)[0] || '';
                    }
                }
                if (!q || q.length < 4) return;
                // A bare filename is never a question.
                if (/\\.(pdf|docx?|rtf)$/i.test(q)) return;

                if (!e.id) { e.setAttribute('data-jh-id', 'jh' + (n++)); }
                const opts = e.tagName.toLowerCase() === 'select'
                    ? [...e.querySelectorAll('option')].map(o => (o.innerText || '').trim())
                          .filter(t => t && !/^select/i.test(t))
                    : [];
                out.push({q, id: e.id || null, jh: e.getAttribute('data-jh-id'),
                          tag: e.tagName.toLowerCase(), type,
                          hasValue: !!(e.value && e.value.trim()), options: opts});
            });
            return out;
        }"""), LINKEDIN_MODAL_SEL)
        self._say(f"    fields found in modal: {[f['q'][:40] for f in fields]}")
        for f in fields:
            q = f["q"]
            ql = q.lower()
            if any(k in ql for k in ("email", "phone", "country code")):
                continue  # handled by the convenience fills above
            if f["type"] in ("radio", "checkbox"):
                continue  # handled as fieldset groups below
            if f["hasValue"]:
                continue  # already filled (by LinkedIn or a previous pass)
            numeric = f["type"] == "number" or any(
                k in ql for k in ("in lakhs", "in days", "in years", "in months",
                                  "how many years", "number of years", "(in ")
            )
            kind = "number" if numeric else ("select" if f["tag"] == "select" else "text")
            ans = await self.answers.answer(q, options=f.get("options") or None, kind=kind)
            if ans is None:
                self._say(f"    [?] no answer for: {q[:70]}")
                unknown.append({"question": q, "options": f.get("options") or []})
                continue
            if numeric:
                # "38 LPA" fails a field that asks for lakhs as a number.
                ans = self._numeric_only(ans)
                if ans is None:
                    self._say(f"    [?] no numeric value for: {q[:60]}")
                    unknown.append({"question": q, "options": []})
                    continue
            loc = (page.locator(f'[id="{f["id"]}"]') if f["id"]
                   else page.locator(f'[data-jh-id="{f["jh"]}"]'))
            try:
                if f["tag"] == "select":
                    await self._select_option(await loc.element_handle(), str(ans))
                else:
                    await loc.fill(str(ans))
                self._say(f"    [ok] {q[:55]} -> {ans}")
            except Exception as e:
                self._say(f"    [!] could not fill '{q[:40]}': {str(e)[:60]}")

        # 2) Radio groups (yes/no). LinkedIn doesn't always wrap these in a
        # <fieldset><legend>, so group by the radios' shared name and derive the
        # question from the nearest ancestor text that isn't just the options.
        groups = await page.evaluate(_js("""(MODAL_SEL) => {
            __DEEP_QUERY__
            const root = deepQuery(MODAL_SEL);
            if (!root) return [];
            const byName = new Map();
            root.querySelectorAll('input[type=radio]').forEach(r => {
                const key = r.name || ('__' + (r.id || Math.random()));
                if (!byName.has(key)) byName.set(key, []);
                byName.get(key).push(r);
            });

            const out = [];
            let n = 0;
            for (const [, radios] of byName) {
                const first = radios[0];
                const opts = radios.map(r => {
                    let lab = '';
                    if (r.id) {
                        try { const l = root.querySelector('label[for="' + CSS.escape(r.id) + '"]'); if (l) lab = (l.innerText || '').trim(); } catch (_) {}
                    }
                    if (!lab) lab = (r.getAttribute('aria-label') || '').trim();
                    if (!lab) lab = (r.closest('label')?.innerText || '').trim();
                    // These radios often have no label[for], no aria-label and
                    // value="on" — the option text sits in a sibling/ancestor.
                    if (!lab) lab = (r.nextElementSibling?.innerText || '').trim();
                    if (!lab) {
                        let p = r.parentElement;
                        for (let i = 0; i < 3 && p && !lab; i++, p = p.parentElement) {
                            const t = (p.innerText || '').trim();
                            // only accept if it looks like a single option, not the whole group
                            if (t && t.length < 40 && !t.includes('\\n')) lab = t;
                        }
                    }
                    // Tag it: these radios often have no usable id to select by.
                    const mark = 'jhr' + (n++);
                    r.setAttribute('data-jh-radio', mark);
                    return {id: r.id, value: r.value, label: lab, mark};
                });
                const optText = new Set(opts.map(o => (o.label || '').toLowerCase()));

                // Question: nearest ancestor whose first line isn't an option label
                let q = '';
                const fs = first.closest('fieldset');
                const legend = fs && fs.querySelector('legend');
                if (legend) q = (legend.innerText || '').trim();
                if (!q) {
                    let p = first.parentElement;
                    for (let i = 0; i < 6 && p && !q; i++, p = p.parentElement) {
                        const lines = (p.innerText || '').split('\\n')
                            .map(s => s.trim())
                            .filter(s => s.length > 4 && !optText.has(s.toLowerCase()));
                        if (lines.length) q = lines[0];
                    }
                }
                const anyChecked = radios.some(r => r.checked);
                if (q && opts.length) out.push({q, radios: opts, anyChecked});
            }
            return out;
        }"""), LINKEDIN_MODAL_SEL)
        for g in groups:
            # The resume chooser is a radio group whose "question" is a filename.
            # _upload_resume owns that choice and has just attached the tailored
            # PDF; answering it here as if it were a screening question walked
            # straight into "Deselect resume <tailored>.pdf / Select resume
            # <old>.pdf" and would have sent a months-old resume. The labelled
            # -field pass already skips filenames — this pass has to as well.
            if _DOC_NAME_RE.search(g.get("q", "") or ""):
                self._say(f"    [skip] resume chooser, not a question: {g['q'][:50]}")
                continue
            opts = [r["label"] or r["value"] for r in g["radios"] if (r["label"] or r["value"])]
            ans = await self.answers.answer(g["q"], options=opts or None, kind="radio")
            if ans is None:
                if not g["anyChecked"]:
                    self._say(f"    [?] no answer for: {g['q'][:70]}")
                    unknown.append({"question": g["q"], "options": opts})
                continue
            av = str(ans).lower()
            target = next((r for r in g["radios"]
                           if av == (r["value"] or "").lower()
                           or (r["label"] and av == r["label"].lower())), None)
            if target is None:
                target = next((r for r in g["radios"]
                               if r["label"] and av in r["label"].lower()), None)
            if target is None:
                self._say(f"    [?] no option matching {ans!r} for: {g['q'][:50]} "
                          f"| options={[(r['label'], r['value']) for r in g['radios']]}")
                unknown.append({"question": g["q"], "options": opts})
                continue

            sel = f'[data-jh-radio="{target["mark"]}"]'
            loc = page.locator(sel)
            # The modal scrolls: options below the fold are "outside of the
            # viewport" and clicks fail until they're scrolled into view.
            try:
                await loc.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
            label = target["label"] or target["value"]
            try:
                await loc.check()
                self._say(f"    [ok] {g['q'][:55]} -> {label}")
            except Exception:
                # React radios often ignore .check(); dispatch a real click,
                # falling back to the DOM so an overlay can't intercept it.
                try:
                    await loc.click(force=True, timeout=5000)
                    self._say(f"    [ok] {g['q'][:55]} -> {label} (click)")
                except Exception:
                    try:
                        await page.eval_on_selector(sel, "e => e.click()")
                        self._say(f"    [ok] {g['q'][:55]} -> {label} (dom)")
                    except Exception as e:
                        self._say(f"    [!] could not select for '{g['q'][:40]}': {str(e)[:60]}")
        return unknown

    @staticmethod
    def _numeric_only(value: str):
        """'38 LPA' -> '38', '3.5 years' -> '3.5'. None if there's no number."""
        import re as _re
        m = _re.search(r"\d+(?:\.\d+)?", str(value))
        return m.group() if m else None

    async def _select_option(self, select_el, value: str):
        if select_el is None:
            return
        try:
            await select_el.select_option(label=value)
            return
        except Exception:
            pass
        try:
            for opt in await select_el.query_selector_all('option'):
                text = (await opt.inner_text()).strip().lower()
                if text == value.lower() or value.lower() in text:
                    await select_el.select_option(value=await opt.get_attribute('value'))
                    return
        except Exception:
            pass

    # ── LinkedIn Easy Apply ──────────────────────────────────────────────────

    async def _apply_linkedin(self, page, job: dict, pdf_path: str = None, cover_letter: str = None) -> dict:
        await page.goto(job["url"], wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)

        # Wait for the job page to finish rendering — clicking while it still
        # shows placeholders silently fails to open the modal. The selector is
        # the shared one: a second copy here drifted out of sync with
        # APPLY_CONTROL once already, and preflight passing while the apply step
        # finds nothing is the worst way to discover that.
        easy_apply = await self._await_apply_control(page, self.APPLY_CONTROL["linkedin"])
        if not easy_apply:
            return await self._no_apply_control(page, job, "linkedin")

        await easy_apply.click()

        # The dialog element appears within a few seconds of the click.
        try:
            await page.wait_for_selector(LINKEDIN_MODAL_SEL, timeout=20000)
        except Exception:
            await self._screenshot(page, "linkedin_modal_never_opened")
            return {"status": "needs_review", "url": page.url,
                    "message": "Easy Apply clicked but the application modal did not open."}

        await self._wait_for_modal_content(page)
        return await self._fill_linkedin_modal(page, pdf_path, cover_letter, job)

    async def _wait_for_modal_content(self, page, timeout: float = 10.0) -> int:
        """
        Each modal step renders its fields asynchronously (~5s). Reading too early
        sees an empty form and blindly clicks Next, so wait until the field count
        inside the dialog stops changing.
        """
        waited, step, last = 0.0, 0.5, -1
        while waited < timeout:
            count = await page.evaluate(_js("""(MODAL_SEL) => {
                __DEEP_QUERY__
                const d = deepQuery(MODAL_SEL);
                if (!d) return 0;
                return d.querySelectorAll('label[for], fieldset legend, input[type=file]').length;
            }"""), LINKEDIN_MODAL_SEL)
            if count and count == last:
                return count           # stable => rendered
            last = count
            await asyncio.sleep(step)
            waited += step
        return max(last, 0)

    async def _upload_resume(self, page, pdf_path: str) -> bool:
        """
        Attach the tailored PDF on LinkedIn's resume step.

        LinkedIn presents the resumes already on your profile as radio choices
        and only renders the file input once you ask to upload a new one. Left
        alone, the step arrives with one pre-selected and _answer_form_fields
        happily confirms it — so the application goes out with whatever you last
        uploaded, months old, and nothing anywhere reports a failure. That is
        the whole tailoring pipeline quietly amounting to nothing, so this is
        worth the extra click.
        """
        # Scope page-wide, not to dialog[open]: LinkedIn renders this input in a
        # portal outside the dialog on some variants, and set_input_files works
        # on hidden inputs, so there is nothing to gain from the tighter scope.
        async def _file_input():
            return await page.query_selector('input[type="file"]')

        upload = await _file_input()
        if upload:
            # Input already in the DOM — setting files directly never opens the
            # OS dialog at all.
            try:
                await upload.set_input_files(pdf_path)
                await asyncio.sleep(2.5)
                self._say(f"    [ok] uploaded tailored resume ({os.path.basename(pdf_path)})")
                return True
            except Exception as e:
                self._say(f"    direct upload failed: {str(e)[:90]}")

        # Otherwise the input only exists after asking to upload — and that click
        # opens Chrome's native file chooser, which is an OS window Playwright
        # cannot reach afterwards: set_input_files does not dismiss it, so it
        # sits there blocking the browser. expect_file_chooser intercepts the
        # request so the dialog is never actually shown.
        modal = self._modal(page)
        for name in ("Upload resume", "Upload new resume", "Choose file"):
            try:
                btn = modal.get_by_role("button", name=name, exact=False)
                if await btn.count() == 0:
                    continue
                self._say(f"    revealing file input via '{name}'")
                async with page.expect_file_chooser(timeout=10000) as fc_info:
                    await btn.first.click()
                chooser = await fc_info.value
                await chooser.set_files(pdf_path)
                await asyncio.sleep(2.5)
                self._say(f"    [ok] uploaded tailored resume ({os.path.basename(pdf_path)})")
                return True
            except Exception as e:
                self._say(f"    upload via '{name}' failed: {str(e)[:90]}")
                continue

        upload = await _file_input()

        if not upload:
            # Say so loudly — a silent miss here is what sent months-old resumes.
            labels = await page.evaluate(_js("""(MODAL_SEL) => {
                __DEEP_QUERY__
                const d = deepQuery(MODAL_SEL);
                if (!d) return 'no modal';
                return [...d.querySelectorAll('button,label,h3')]
                    .map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 12).join(' / ');
            }"""), LINKEDIN_MODAL_SEL)
            self._say(f"    no file input on this step. controls: {str(labels)[:220]}")
            return False
        try:
            await upload.set_input_files(pdf_path)
            await asyncio.sleep(2.5)   # LinkedIn parses the file before advancing
            self._say(f"    [ok] uploaded tailored resume ({os.path.basename(pdf_path)})")
            return True
        except Exception as e:
            self._say(f"    resume upload failed: {str(e)[:100]}")
            return False

    async def _fill_linkedin_modal(self, page, pdf_path: str = None, cover_letter: str = None,
                                   job: dict = None) -> dict:
        last_sig, stalled = None, 0
        for step in range(12):
            await asyncio.sleep(1.5)
            await self._wait_for_modal_content(page)
            self._say(f"    -- modal step {step + 1}")

            # If the same step renders twice running, Next/Review isn't advancing
            # (usually a validation error we can't satisfy) — stop rather than
            # spinning through all 12 iterations.
            sig = await page.evaluate(_js("""(MODAL_SEL) => {
                __DEEP_QUERY__
                const d = deepQuery(MODAL_SEL);
                if (!d) return 'closed';
                return [...d.querySelectorAll('label')].map(l => (l.innerText || '').trim()).join('|').slice(0, 400);
            }"""), LINKEDIN_MODAL_SEL)
            if sig == last_sig:
                stalled += 1
                if stalled >= 2:
                    await self._screenshot(page, "linkedin_stalled")
                    return {"status": "needs_review", "url": page.url,
                            "message": ("Form stopped advancing — a field was rejected "
                                        "(check the screenshot in backend/logs/apply).")}
            else:
                stalled = 0
            last_sig = sig
            if not await self._guard_captcha(page, "linkedin"):
                return {"status": "needs_review", "url": page.url,
                        "message": "CAPTCHA not cleared during LinkedIn apply."}

            # Footer buttons carry only text, so match by accessible name — scoped
            # to the dialog so the page's carousel "Next" can't be picked up.
            modal = self._modal(page)
            submit_btn = modal.get_by_role("button", name=SUBMIT_NAME)
            if await submit_btn.count() > 0:
                # Last gate before the irreversible click. If we built a tailored
                # resume and never managed to attach it, this application would
                # carry whatever LinkedIn had on file — the exact silent
                # downgrade the defer policy exists to prevent.
                if pdf_path and not self._resume_attached:
                    await self._screenshot(page, "linkedin_resume_not_attached")
                    return {"status": "needs_review", "url": page.url,
                            "message": ("Reached Submit but the tailored resume was never "
                                        "attached — LinkedIn would have sent the copy already "
                                        "on your profile. Not submitting. Screenshot in "
                                        "backend/logs/apply.")}
                if self.dry_run:
                    return await self._dry_stop(page, "linkedin", "Submit application")
                await submit_btn.first.click()
                confirmed = await self._verify_submission(page)
                if confirmed:
                    return confirmed
                return {"status": "applied", "message": "Application submitted via LinkedIn Easy Apply."}

            if pdf_path and not self._resume_attached:
                self._resume_attached = await self._upload_resume(page, pdf_path)

            # Paste the cover letter if this step has a message field.
            await self._fill_cover_letter(page, cover_letter)

            # Answer screening questions from the profile. Anything the profile
            # can't answer safely is handed to you — the bot never guesses.
            unknown = await self._answer_form_fields(page)
            if unknown:
                return await self._defer_for_questions(page, unknown, job)

            # Review (final step) before Next.
            review_btn = modal.get_by_role("button", name=REVIEW_NAME)
            next_btn = modal.get_by_role("button", name=NEXT_NAME)
            if await review_btn.count() > 0:
                await review_btn.first.click()
            elif await next_btn.count() > 0:
                await next_btn.first.click()
            else:
                break

        await self._screenshot(page, "linkedin_stuck")
        return {"status": "needs_review", "url": page.url,
                "message": "Partially filled — could not reach final submit. Some questions need manual answers."}

    async def _defer_for_questions(self, page, unknown: list, job: dict = None) -> dict:
        """
        Email the questions we couldn't answer and release the job.

        The run does not hold the browser open waiting for you. A screening
        question can take hours to come back — blocking on it stalled the whole
        cycle behind one posting, and the pause was useless unless you happened
        to be at the desk. Deferring instead means the rest of the batch keeps
        moving and this posting is retried once the answer is learned, from the
        app or from an email reply.

        Each distinct question is emailed once. record_pending_question already
        ran inside AnswerResolver, so the question is in Setup > Saved answers
        whether or not the email goes out.
        """
        job = job or {}
        asked = []
        for item in unknown:
            question = (item.get("question") or "").strip()
            if not question:
                continue
            try:
                if await mark_question_emailed(question):
                    await asyncio.to_thread(
                        send_question_email,
                        question, item.get("options") or [],
                        job.get("title", ""), job.get("company", ""), job.get("url", ""),
                    )
                    asked.append(question)
            except Exception as e:
                self._say(f"    could not email question: {type(e).__name__}: {str(e)[:80]}")

        n = len(unknown)
        detail = f"; emailed {len(asked)}" if asked else " (already asked)"
        self._say(f"    [DEFER] {n} unanswered screening question(s){detail}")
        return {"status": "question_pending", "url": page.url,
                "message": (f"{n} screening question(s) I can't answer from your profile or "
                            f"resume. Emailed to you{'' if asked else ' previously'} — reply to "
                            f"the email or answer in Setup > Saved answers, and this posting "
                            f"is retried automatically. Nothing was submitted.")}

    async def _linkedin_modal_closed(self, page) -> bool:
        # The modal container disappears from the DOM when it closes, so its
        # absence is the signal. (Matching footer button text page-wide
        # false-positives on the "Next" button of the recommended-jobs carousel.)
        try:
            return await page.locator(LINKEDIN_MODAL_SEL).count() == 0
        except Exception:
            return True

    def _modal(self, page):
        """
        Locator scoped to the Easy Apply modal.

        .first because the selector lists several equivalent anchors and a
        single container can match more than one of them — get_by_role chained
        off a multi-element locator would then be ambiguous.
        """
        return page.locator(LINKEDIN_MODAL_SEL).first

    # ── Naukri Apply ─────────────────────────────────────────────────────────

    async def _apply_naukri(self, page, job: dict, pdf_path: str = None, cover_letter: str = None) -> dict:
        await page.goto(job["url"], wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)
        if not await self._guard_captcha(page, "naukri"):
            return {"status": "needs_review", "url": page.url, "message": "CAPTCHA not cleared on Naukri."}

        # Shared constant, not a second copy: the LinkedIn selector was
        # duplicated exactly like this and the two halves drifted apart, so
        # preflight found the control and the apply step then could not.
        apply_btn = await self._await_apply_control(page, self.APPLY_CONTROL["naukri"])
        if not apply_btn:
            return await self._no_apply_control(page, job, "naukri")

        if self.dry_run:
            return await self._dry_stop(page, "naukri", "Apply button")

        await apply_btn.click()
        await asyncio.sleep(3)

        # Naukri may redirect to the company site for many roles.
        if "naukri.com" not in page.url.lower():
            return self._external_apply(job)

        applied = await page.query_selector('[class*="applied"], button[disabled][title*="Applied"]')
        if applied:
            return {"status": "applied", "message": "Successfully applied on Naukri."}

        confirmed = await self._verify_submission(page)
        if confirmed:
            return confirmed
        return {"status": "needs_review", "url": page.url,
                "message": "Clicked Apply on Naukri — may need profile completion or extra steps."}

    # ── Indeed Apply ─────────────────────────────────────────────────────────

    async def _apply_indeed(self, page, job: dict, pdf_path: str = None, cover_letter: str = None) -> dict:
        await page.goto(job["url"], wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)

        apply_btn = await self._await_apply_control(
            page,
            'button[data-testid="indeedApplyButton"], button[id="indeedApplyButton"], '
            'a[data-testid="apply-button"], span.indeed-apply-button',
        )
        if not apply_btn:
            return await self._no_apply_control(page, job, "indeed")

        await apply_btn.click()
        await asyncio.sleep(3)
        if not await self._guard_captcha(page, "indeed"):
            return {"status": "needs_review", "url": page.url, "message": "CAPTCHA not cleared on Indeed."}

        if pdf_path:
            upload = await page.query_selector('input[type="file"][accept*="pdf"], input[type="file"]')
            if upload:
                try:
                    await upload.set_input_files(pdf_path)
                    await asyncio.sleep(1)
                except Exception:
                    pass

        if self.user_phone:
            for sel in ['input[name*="phone"]', 'input[type="tel"]']:
                el = await page.query_selector(sel)
                if el and not await el.input_value():
                    await el.fill(self.user_phone)
                    break

        await self._fill_cover_letter(page, cover_letter)

        submit = await page.query_selector(
            'button[data-testid*="submit"], button[aria-label*="Submit"], button[type="submit"]'
        )
        if submit:
            if self.dry_run:
                return await self._dry_stop(page, "indeed", "Submit")
            await submit.click()
            confirmed = await self._verify_submission(page)
            if confirmed:
                return confirmed
            return {"status": "applied", "message": "Application submitted via Indeed Easy Apply."}

        return {"status": "needs_review", "url": page.url,
                "message": "Clicked Apply on Indeed — may need additional steps."}

    # ── Dice Apply ───────────────────────────────────────────────────────────

    async def _apply_dice(self, page, job: dict, pdf_path: str = None, cover_letter: str = None) -> dict:
        await page.goto(job["url"], wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)

        apply_btn = await self._await_apply_control(
            page,
            'apply-button-wc, button[data-cy="apply-button"], '
            'a[data-cy="apply-button"], button[id*="apply"]',
        )
        if not apply_btn:
            return await self._no_apply_control(page, job, "dice")

        await apply_btn.click()
        await asyncio.sleep(3)
        if not await self._guard_captcha(page, "dice"):
            return {"status": "needs_review", "url": page.url, "message": "CAPTCHA not cleared on Dice."}

        if pdf_path:
            upload = await page.query_selector('input[type="file"]')
            if upload:
                try:
                    await upload.set_input_files(pdf_path)
                    await asyncio.sleep(1)
                except Exception:
                    pass

        if self.user_phone:
            for sel in ['input[name*="phone"]', 'input[type="tel"]']:
                el = await page.query_selector(sel)
                if el and not await el.input_value():
                    await el.fill(self.user_phone)
                    break

        await self._fill_cover_letter(page, cover_letter)

        submit = await page.query_selector('button[data-cy="submit-application"], button[type="submit"]')
        if submit:
            if self.dry_run:
                return await self._dry_stop(page, "dice", "Submit")
            await submit.click()
            confirmed = await self._verify_submission(page)
            if confirmed:
                return confirmed
            return {"status": "applied", "message": "Application submitted via Dice Easy Apply."}

        return {"status": "needs_review", "url": page.url,
                "message": "Clicked Apply on Dice — may need additional steps."}
