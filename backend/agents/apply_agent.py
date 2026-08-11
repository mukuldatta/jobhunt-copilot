import os
import asyncio
import tempfile
from datetime import datetime
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright
from agents.tailor_agent import TailorAgent
from agents.cover_letter_agent import CoverLetterAgent
from services.answer_service import AnswerResolver
from utils.pdf_generator import generate_resume_pdf
from utils.resume_validator import validate_tailored_resume, clean_resume_text
from services.alert_service import send_manual_action_alert
from db.mongodb import (
    get_application_by_job_id, claim_job_for_apply, finish_job_apply, record_application,
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

SUPPORTED_PLATFORMS = list(LOGIN.keys())


class ApplyAgent:
    def __init__(self):
        first = os.environ.get("USER_FIRST_NAME", "Mukul")
        last = os.environ.get("USER_LAST_NAME", "Mokkapati")
        self.user_name = f"{first} {last}"
        self.user_email = os.environ.get("MY_EMAIL", "mukulmokkapati@gmail.com")
        self.user_phone = os.environ.get("MY_PHONE", "")
        self.answers = None   # AnswerResolver, loaded per apply (needs DB)

        # Headed is required for manual CAPTCHA. Only go headless if explicitly asked.
        self.headless = os.environ.get("APPLY_HEADLESS", "").strip().lower() in ("1", "true", "yes")
        self.human_timeout = int(os.environ.get("APPLY_HUMAN_TIMEOUT", "300"))
        self.login_timeout = int(os.environ.get("LOGIN_TIMEOUT", "420"))
        # APPLY_DRY_RUN: navigate + fill the form but stop before the final submit
        # (screenshot it) — for safely tuning selectors without applying for real.
        self.dry_run = os.environ.get("APPLY_DRY_RUN", "").strip().lower() in ("1", "true", "yes")

    # ── Public entrypoint ────────────────────────────────────────────────────

    async def apply(self, job: dict) -> dict:
        job_id = job.get("job_id", "")
        source = job.get("source", "")

        if source not in LOGIN:
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
        elif status in ("manual_required", "needs_review"):
            await finish_job_apply(job_id, "manual_required")
        elif status in ("login_required", "dry_run"):
            # Not a real apply — release it so it can be retried later.
            await finish_job_apply(job_id, "new")
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

                # Generate documents only after login is confirmed, so a
                # login_required job never burns LLM calls.
                pdf_path, tailored_text = await self._make_resume(job)
                cover_letter = await self._make_cover_letter(job)
                await self._load_answers()

                handler = getattr(self, f"_apply_{source}")
                result = await handler(page, job, pdf_path, cover_letter)

        if result.get("status") == "applied":
            if tailored_text:
                result["_tailored_text"] = tailored_text
            if cover_letter:
                result["_cover_letter"] = cover_letter
        return result

    # ── Resume ───────────────────────────────────────────────────────────────

    async def _make_resume(self, job: dict):
        """
        Tailor + validate the resume. If validation fails (fabricated skills,
        truncation, dropped education), fall back to the ORIGINAL resume so an
        honest document is submitted rather than a corrupted one. Returns
        (pdf_path, text_that_was_used).
        """
        try:
            from db.mongodb import get_resume
            resume = await get_resume()
        except Exception:
            resume = None

        try:
            tailored_text = await TailorAgent().tailor(job)
        except Exception as e:
            print(f"ApplyAgent: tailoring failed: {e}")
            tailored_text = None

        final_text = None
        if tailored_text and resume:
            v = validate_tailored_resume(tailored_text, resume,
                                         user_name=self.user_name, user_email=self.user_email)
            if v["ok"]:
                final_text = v["text"]
                if v["issues"]:
                    print(f"    Resume validation warnings: {v['issues']}")
            else:
                print(f"    Resume validation FAILED {v['issues']} — using original resume instead.")
                final_text = resume.get("parsed_text")
        elif tailored_text:
            final_text = tailored_text  # no stored resume to validate against
        elif resume:
            final_text = resume.get("parsed_text")

        if not final_text:
            return None, None

        try:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            tmp.close()
            generate_resume_pdf(final_text, tmp.name)
            return tmp.name, final_text
        except Exception as e:
            print(f"ApplyAgent: PDF generation failed: {e}")
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
            print(f"    ApplyAgent: could not load apply profile ({e}); using defaults")
            self.answers = AnswerResolver({})

    async def _make_cover_letter(self, job: dict) -> str:
        """Best-effort cover letter — never blocks an apply if it fails."""
        try:
            return clean_resume_text(await CoverLetterAgent().generate(job))
        except Exception as e:
            print(f"ApplyAgent: cover letter generation failed: {e}")
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
            print(f"    ApplyAgent: real Chrome unavailable ({e}); using bundled Chromium")
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
        print(f"    [PAUSE] MANUAL ACTION [{platform}]: {reason}")
        print(f"            Act in the open browser window. Waiting up to {timeout}s...")
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

        waited, interval = 0, 5
        while waited < timeout:
            await asyncio.sleep(interval)
            waited += interval
            try:
                if done_check is not None:
                    if await done_check():
                        print(f"    [RESUME] [{platform}] resolved after {waited}s — continuing.")
                        return True
                elif not await self._has_captcha(page):
                    print(f"    [RESUME] [{platform}] challenge cleared after {waited}s — continuing.")
                    return True
            except Exception:
                pass
        print(f"    [TIMEOUT] [{platform}] no human action within {timeout}s — giving up.")
        return False

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

            closed = ["no longer accepting", "position has been filled", "job has expired",
                      "no longer available", "posting has been closed"]
            if any(p in content for p in closed):
                return {"status": "manual_required", "url": page.url,
                        "message": "Job is no longer accepting applications."}
        except Exception:
            pass
        return None

    def _external_apply(self, job: dict) -> dict:
        return {"status": "manual_required", "url": job.get("url", ""),
                "message": "This posting applies on the company site — open the link and finish manually."}

    async def _dry_stop(self, page, platform: str, what: str) -> dict:
        await self._screenshot(page, f"dryrun_{platform}")
        print(f"    [DRY RUN] {platform}: reached '{what}', not submitting.")
        return {"status": "dry_run", "url": page.url,
                "message": f"DRY RUN — filled the form and reached '{what}' without submitting. Screenshot saved to backend/logs/apply."}

    async def _screenshot(self, page, label: str):
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            path = os.path.join(LOG_DIR, f"{label}_{int(datetime.utcnow().timestamp())}.png")
            await page.screenshot(path=path, full_page=False)
            print(f"    Screenshot saved: {path}")
        except Exception:
            pass

    # ── Screening-question answering (structured profile, no guessing) ────────

    async def _answer_form_fields(self, page) -> int:
        """
        Fill screening questions using the answer profile. Returns the number of
        required questions the profile could NOT answer — the caller pauses for a
        human on those instead of guessing.
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

        unknown = 0

        # 1) Labelled text/number/select fields. LinkedIn's containers are
        # obfuscated, but each question's <label for> points at its field id, so
        # we map via that. (ids can contain odd chars, so target by [id="..."].)
        # Walk every field in the dialog and derive its question text. LinkedIn
        # doesn't consistently use label[for], so fall back to aria-label and
        # then the nearest preceding text in the field's ancestry.
        fields = await page.evaluate("""() => {
            const root = document.querySelector('dialog[open]') || document;
            const out = [];
            let n = 0;
            root.querySelectorAll('input, select, textarea').forEach(e => {
                const type = (e.type || '').toLowerCase();
                if (['hidden', 'file', 'submit', 'button'].includes(type)) return;
                if (!e.offsetParent && type !== 'radio') return;   // not visible

                let q = '';
                if (e.id) {
                    let l = null;
                    try { l = root.querySelector('label[for="' + CSS.escape(e.id) + '"]'); } catch (_) {}
                    if (l) q = (l.innerText || '').trim();
                }
                if (!q) q = (e.getAttribute('aria-label') || '').trim();
                if (!q) {
                    // nearest ancestor whose text is mostly the question
                    let p = e.parentElement;
                    for (let i = 0; i < 5 && p && !q; i++, p = p.parentElement) {
                        const t = (p.innerText || '').trim();
                        if (t && t.length > 5 && t.length < 300) q = t.split('\\n')[0].trim();
                    }
                }
                if (!q || q.length < 4) return;

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
        }""")
        print(f"    fields found in modal: {[f['q'][:40] for f in fields]}")
        for f in fields:
            q = f["q"]
            ql = q.lower()
            if any(k in ql for k in ("email", "phone", "country code")):
                continue  # handled by the convenience fills above
            if f["type"] in ("radio", "checkbox"):
                continue  # handled as fieldset groups below
            if f["hasValue"]:
                continue  # already filled (by LinkedIn or a previous pass)
            kind = "number" if f["type"] == "number" else ("select" if f["tag"] == "select" else "text")
            ans = await self.answers.answer(q, options=f.get("options") or None, kind=kind)
            if ans is None:
                print(f"    [?] no answer for: {q[:70]}")
                unknown += 1
                continue
            loc = (page.locator(f'[id="{f["id"]}"]') if f["id"]
                   else page.locator(f'[data-jh-id="{f["jh"]}"]'))
            try:
                if f["tag"] == "select":
                    await self._select_option(await loc.element_handle(), str(ans))
                else:
                    await loc.fill(str(ans))
                print(f"    [ok] {q[:55]} -> {ans}")
            except Exception as e:
                print(f"    [!] could not fill '{q[:40]}': {str(e)[:60]}")

        # 2) Radio groups (yes/no): a <fieldset> with a <legend> question and
        # radios whose <label for> gives the option text.
        groups = await page.evaluate("""() => {
            const out = [];
            const root = document.querySelector('dialog[open]') || document;
            root.querySelectorAll('fieldset').forEach(fs => {
                const legend = fs.querySelector('legend');
                const q = legend ? (legend.innerText || '').trim() : '';
                if (!q) return;
                const radios = [...fs.querySelectorAll('input[type=radio]')].map(r => {
                    let lab = '';
                    if (r.id) { const l = fs.querySelector('label[for="' + CSS.escape(r.id) + '"]'); if (l) lab = (l.innerText || '').trim(); }
                    return {id: r.id, value: r.value, label: lab};
                });
                const anyChecked = [...fs.querySelectorAll('input[type=radio]')].some(r => r.checked);
                if (radios.length) out.push({q, radios, anyChecked});
            });
            return out;
        }""")
        for g in groups:
            opts = [r["label"] or r["value"] for r in g["radios"] if (r["label"] or r["value"])]
            ans = await self.answers.answer(g["q"], options=opts or None, kind="radio")
            if ans is None:
                if not g["anyChecked"]:
                    print(f"    [?] no answer for: {g['q'][:70]}")
                    unknown += 1
                continue
            av = str(ans).lower()
            target = next((r for r in g["radios"]
                           if av == r["value"].lower() or (r["label"] and av in r["label"].lower())), None)
            if target and target["id"]:
                try:
                    await page.locator(f'[id="{target["id"]}"]').check()
                except Exception:
                    try:
                        await page.locator(f'label[for="{target["id"]}"]').click()
                    except Exception:
                        pass
        return unknown

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
        # shows placeholders silently fails to open the modal.
        sel = ('button.jobs-apply-button, button[aria-label*="Easy Apply"], '
               'button:has-text("Easy Apply"), .jobs-s-apply button')
        try:
            await page.wait_for_selector(sel, timeout=15000)
        except Exception:
            return self._external_apply(job)

        easy_apply = await page.query_selector(sel)
        if not easy_apply:
            return self._external_apply(job)

        await easy_apply.click()

        # The dialog element appears within a few seconds of the click.
        try:
            await page.wait_for_selector("dialog[open]", timeout=20000)
        except Exception:
            await self._screenshot(page, "linkedin_modal_never_opened")
            return {"status": "needs_review", "url": page.url,
                    "message": "Easy Apply clicked but the application modal did not open."}

        await self._wait_for_modal_content(page)
        return await self._fill_linkedin_modal(page, pdf_path, cover_letter)

    async def _wait_for_modal_content(self, page, timeout: float = 10.0) -> int:
        """
        Each modal step renders its fields asynchronously (~5s). Reading too early
        sees an empty form and blindly clicks Next, so wait until the field count
        inside the dialog stops changing.
        """
        waited, step, last = 0.0, 0.5, -1
        while waited < timeout:
            count = await page.evaluate("""() => {
                const d = document.querySelector('dialog[open]');
                if (!d) return 0;
                return d.querySelectorAll('label[for], fieldset legend, input[type=file]').length;
            }""")
            if count and count == last:
                return count           # stable => rendered
            last = count
            await asyncio.sleep(step)
            waited += step
        return max(last, 0)

    async def _fill_linkedin_modal(self, page, pdf_path: str = None, cover_letter: str = None) -> dict:
        for step in range(12):
            await asyncio.sleep(1.5)
            await self._wait_for_modal_content(page)
            print(f"    -- modal step {step + 1}")
            if not await self._guard_captcha(page, "linkedin"):
                return {"status": "needs_review", "url": page.url,
                        "message": "CAPTCHA not cleared during LinkedIn apply."}

            # Footer buttons carry only text, so match by accessible name — scoped
            # to the dialog so the page's carousel "Next" can't be picked up.
            modal = self._modal(page)
            submit_btn = modal.get_by_role("button", name="Submit application")
            if await submit_btn.count() > 0:
                if self.dry_run:
                    return await self._dry_stop(page, "linkedin", "Submit application")
                await submit_btn.first.click()
                confirmed = await self._verify_submission(page)
                if confirmed:
                    return confirmed
                return {"status": "applied", "message": "Application submitted via LinkedIn Easy Apply."}

            if pdf_path:
                upload = await page.query_selector('dialog[open] input[type="file"]')
                if upload:
                    try:
                        await upload.set_input_files(pdf_path)
                        await asyncio.sleep(1)
                        print("    [ok] uploaded tailored resume")
                    except Exception:
                        pass

            # Paste the cover letter if this step has a message field.
            await self._fill_cover_letter(page, cover_letter)

            # Answer screening questions from the profile. Anything the profile
            # can't answer safely is handed to you — the bot never guesses.
            unknown = await self._answer_form_fields(page)
            if unknown:
                await self._wait_for_human(
                    page, "linkedin",
                    f"{unknown} screening question(s) I can't answer safely — please finish "
                    f"and submit this application in the window",
                    done_check=lambda: self._linkedin_modal_closed(page),
                )
                confirmed = await self._verify_submission(page)
                if confirmed:
                    return confirmed
                return {"status": "needs_review", "url": page.url,
                        "message": "Handed to you for screening questions — verify it submitted."}

            # Review (final step) before Next. "Review" matches both "Review" and
            # "Review your application"; Next is exact to avoid "Next steps".
            review_btn = modal.get_by_role("button", name="Review")
            next_btn = modal.get_by_role("button", name="Next", exact=True)
            if await review_btn.count() > 0:
                await review_btn.first.click()
            elif await next_btn.count() > 0:
                await next_btn.first.click()
            else:
                break

        await self._screenshot(page, "linkedin_stuck")
        return {"status": "needs_review", "url": page.url,
                "message": "Partially filled — could not reach final submit. Some questions need manual answers."}

    async def _linkedin_modal_closed(self, page) -> bool:
        # LinkedIn's Easy Apply modal is a native <dialog open> element — the one
        # reliable signal. (Matching footer button text page-wide false-positives
        # on the "Next" button of the recommended-jobs carousel.)
        try:
            return await page.locator("dialog[open]").count() == 0
        except Exception:
            return True

    def _modal(self, page):
        """Locator scoped to the Easy Apply dialog."""
        return page.locator("dialog[open]")

    # ── Naukri Apply ─────────────────────────────────────────────────────────

    async def _apply_naukri(self, page, job: dict, pdf_path: str = None, cover_letter: str = None) -> dict:
        await page.goto(job["url"], wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)
        if not await self._guard_captcha(page, "naukri"):
            return {"status": "needs_review", "url": page.url, "message": "CAPTCHA not cleared on Naukri."}

        apply_btn = await page.query_selector(
            'button#apply-button, a#apply-button, button.apply-button, button[title*="Apply"]'
        )
        if not apply_btn:
            return self._external_apply(job)

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

        apply_btn = await page.query_selector(
            'button[data-testid="indeedApplyButton"], button[id="indeedApplyButton"], '
            'a[data-testid="apply-button"], span.indeed-apply-button'
        )
        if not apply_btn:
            return self._external_apply(job)

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

        apply_btn = await page.query_selector(
            'apply-button-wc, button[data-cy="apply-button"], '
            'a[data-cy="apply-button"], button[id*="apply"]'
        )
        if not apply_btn:
            return self._external_apply(job)

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
