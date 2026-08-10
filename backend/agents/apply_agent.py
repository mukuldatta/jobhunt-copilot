import os
import asyncio
import tempfile
from datetime import datetime
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright
from agents.tailor_agent import TailorAgent
from config.apply_profile import AnswerProfile
from utils.pdf_generator import generate_resume_pdf
from services.alert_service import send_login_failure_alert, send_manual_action_alert
from db.mongodb import (
    increment_login_failure, reset_login_failures,
    get_application_by_job_id, claim_job_for_apply, finish_job_apply, record_application,
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

# Per-platform login config. The apply flow is otherwise generic.
LOGIN = {
    "linkedin": {
        "home_url": "https://www.linkedin.com/feed/",
        "login_url": "https://www.linkedin.com/login",
        "email_sel": "#username",
        "pass_sel": "#password",
        "submit_sel": "button[type='submit']",
        "logged_in_sel": "img.global-nav__me-photo, div.global-nav__me, button.global-nav__primary-link-me-menu-trigger",
        "fail_url_tokens": ["/login", "authwall", "checkpoint", "signup"],
    },
    "naukri": {
        "home_url": "https://www.naukri.com/mnjuser/homepage",
        "login_url": "https://www.naukri.com/nlogin/login",
        "email_sel": "input[placeholder*='Email'], #usernameField",
        "pass_sel": "input[placeholder*='Password'], #passwordField",
        "submit_sel": "button[type='submit']",
        "logged_in_sel": "a[href*='mnjuser/profile'], .nI-gNb-drawer__bars, .view-profile-wrapper",
        "fail_url_tokens": ["nlogin", "/login"],
    },
    "indeed": {
        "home_url": "https://in.indeed.com/",
        "login_url": "https://in.indeed.com/account/login",
        "email_sel": "input[name='__email'], input[type='email'], #ifl-InputFormField-3",
        "pass_sel": "input[name='__password'], input[type='password']",
        "submit_sel": "button[type='submit'], button[data-testid='login-button']",
        "logged_in_sel": "[data-gnav-element-name='AccountMenu'], #gnav-main-AccountButton",
        "fail_url_tokens": ["account/login", "/login", "signin"],
    },
    "dice": {
        "home_url": "https://www.dice.com/dashboard/",
        "login_url": "https://www.dice.com/dashboard/login",
        "email_sel": "input[name='email'], input[type='email']",
        "pass_sel": "input[name='password'], input[type='password']",
        "submit_sel": "button[type='submit']",
        "logged_in_sel": "[data-cy='nav-profile'], a[href*='dashboard']",
        "fail_url_tokens": ["/login", "signin"],
    },
}


class ApplyAgent:
    def __init__(self):
        self.creds = {
            "linkedin": (os.environ.get("LINKEDIN_EMAIL", ""), os.environ.get("LINKEDIN_PASSWORD", "")),
            "naukri": (os.environ.get("NAUKRI_EMAIL", os.environ.get("LINKEDIN_EMAIL", "")),
                       os.environ.get("NAUKRI_PASSWORD", os.environ.get("LINKEDIN_PASSWORD", ""))),
            "indeed": (os.environ.get("INDEED_EMAIL", ""), os.environ.get("INDEED_PASSWORD", "")),
            "dice": (os.environ.get("DICE_EMAIL", ""), os.environ.get("DICE_PASSWORD", "")),
        }
        first = os.environ.get("USER_FIRST_NAME", "Mukul")
        last = os.environ.get("USER_LAST_NAME", "Mokkapati")
        self.user_name = f"{first} {last}"
        self.user_email = os.environ.get("MY_EMAIL", "mukulmokkapati@gmail.com")
        self.user_phone = os.environ.get("MY_PHONE", "")
        self.answers = AnswerProfile()

        # Headed is required for manual CAPTCHA. Only go headless if explicitly asked.
        self.headless = os.environ.get("APPLY_HEADLESS", "").strip().lower() in ("1", "true", "yes")
        self.human_timeout = int(os.environ.get("APPLY_HUMAN_TIMEOUT", "300"))

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
                "notes": "Auto-applied via ApplyAgent",
            })
        elif status in ("manual_required", "needs_review"):
            await finish_job_apply(job_id, "manual_required")
        elif status == "already_applied":
            pass
        else:  # login_failed, credentials_missing, error
            await finish_job_apply(job_id, "apply_failed")

    async def _do_apply(self, job: dict) -> dict:
        source = job["source"]
        pdf_path, tailored_text = await self._make_resume(job)

        async with async_playwright() as pw:
            async with self._session(pw, source) as (ctx, page):
                login = await self._ensure_login(page, source)
                if not login["ok"]:
                    return login["result"]

                handler = getattr(self, f"_apply_{source}")
                result = await handler(page, job, pdf_path)

        if tailored_text and result.get("status") == "applied":
            result["_tailored_text"] = tailored_text
        return result

    # ── Resume ───────────────────────────────────────────────────────────────

    async def _make_resume(self, job: dict):
        try:
            tailored_text = await TailorAgent().tailor(job)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            tmp.close()
            generate_resume_pdf(tailored_text, tmp.name)
            return tmp.name, tailored_text
        except Exception as e:
            print(f"ApplyAgent: resume tailoring failed: {e}")
            return None, None

    # ── Browser session (headed + persistent) ────────────────────────────────

    @asynccontextmanager
    async def _session(self, pw, platform: str):
        user_dir = os.path.join(PROFILE_ROOT, platform)
        os.makedirs(user_dir, exist_ok=True)
        ctx = await self._launch_persistent(pw, user_dir)
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

    async def _launch_persistent(self, pw, user_dir: str):
        opts = dict(headless=self.headless, viewport={"width": 1360, "height": 900},
                    locale="en-IN", user_agent=UA)
        try:
            return await pw.chromium.launch_persistent_context(user_dir, channel="chrome", **opts)
        except Exception as e:
            print(f"    ApplyAgent: real Chrome unavailable ({e}); using bundled Chromium")
            return await pw.chromium.launch_persistent_context(user_dir, **opts)

    # ── Login with human-in-the-loop fallback ────────────────────────────────

    async def _ensure_login(self, page, platform: str) -> dict:
        cfg = LOGIN[platform]
        email, password = self.creds[platform]

        # 1. Warm persistent session? Then we're done.
        try:
            await page.goto(cfg["home_url"], wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
        except Exception:
            pass
        if await self._is_logged_in(page, cfg):
            return {"ok": True}

        # 2. Best-effort automated credential login.
        if email and password:
            try:
                await page.goto(cfg["login_url"], wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2)
                await page.fill(cfg["email_sel"], email)
                pass_el = await page.query_selector(cfg["pass_sel"])
                if pass_el:
                    await page.fill(cfg["pass_sel"], password)
                await page.click(cfg["submit_sel"])
                await asyncio.sleep(3)
                # Email-first flows (Indeed): password appears on the next page.
                if not pass_el:
                    pass_el2 = await page.query_selector(cfg["pass_sel"])
                    if pass_el2:
                        await pass_el2.fill(password)
                        await page.click(cfg["submit_sel"])
                        await asyncio.sleep(3)
            except Exception as e:
                print(f"    {platform} auto-login error (will fall back to manual): {e}")

        if await self._is_logged_in(page, cfg):
            await reset_login_failures(platform)
            return {"ok": True}

        # 3. Human fallback — only possible with a visible window.
        if self.headless:
            count = await increment_login_failure(platform)
            if count >= 5:
                send_login_failure_alert(platform, count)
            return {"ok": False, "result": {
                "status": "login_failed",
                "message": (f"{platform} not logged in and running headless. Run with a visible "
                            f"browser (APPLY_HEADLESS unset) and log in once."),
            }}

        ok = await self._wait_for_human(
            page, platform,
            f"log in to {platform.title()} (solve 2FA / CAPTCHA / manual login) in the browser window",
            done_check=lambda: self._is_logged_in(page, cfg),
        )
        if ok:
            await reset_login_failures(platform)
            return {"ok": True}

        count = await increment_login_failure(platform)
        if count >= 5:
            send_login_failure_alert(platform, count)
        return {"ok": False, "result": {
            "status": "login_failed",
            "message": f"{platform} login not completed within {self.human_timeout}s.",
        }}

    async def _is_logged_in(self, page, cfg: dict) -> bool:
        url = page.url.lower()
        if any(tok in url for tok in cfg["fail_url_tokens"]):
            return False
        sel = cfg.get("logged_in_sel")
        if not sel:
            return True
        try:
            return await page.query_selector(sel) is not None
        except Exception:
            return False

    # ── Shared helpers ───────────────────────────────────────────────────────

    async def _has_captcha(self, page) -> bool:
        try:
            content = (await page.content()).lower()
            signals = ["captcha", "verify you're human", "prove you're not a robot",
                       "security challenge", "bot detection", "are you a robot",
                       "unusual activity", "verify your identity"]
            if any(s in content for s in signals):
                return True
            frame = await page.query_selector(
                'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], '
                'div[class*="captcha"], #captcha'
            )
            return frame is not None
        except Exception:
            return False

    async def _wait_for_human(self, page, platform: str, reason: str, done_check=None) -> bool:
        """
        Pause a headed apply and wait for you to clear a CAPTCHA / 2FA / manual
        step in the open browser window. Notifies you, then polls until the
        challenge is resolved (done_check true, or CAPTCHA gone) or times out.
        """
        print(f"    [PAUSE] MANUAL ACTION [{platform}]: {reason}")
        print(f"            Solve it in the open browser window. Waiting up to {self.human_timeout}s...")
        try:
            send_manual_action_alert(platform, reason, page.url)
        except Exception:
            pass

        waited, interval = 0, 5
        while waited < self.human_timeout:
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
        print(f"    [TIMEOUT] [{platform}] no human action within {self.human_timeout}s — giving up.")
        return False

    async def _guard_captcha(self, page, platform: str) -> bool:
        """If a CAPTCHA shows up mid-flow, pause for a human. Returns True if clear to continue."""
        if await self._has_captcha(page):
            return await self._wait_for_human(page, platform, "CAPTCHA appeared during application")
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
        containers = await page.query_selector_all(
            'div.fb-dash-form-element, div[data-test-form-element], '
            'div[data-test-text-entity-list-form-component]'
        )
        for c in containers:
            try:
                label_el = await c.query_selector(
                    'label, legend, span[data-test-form-element-label]'
                )
                label = (await label_el.inner_text()).strip() if label_el else ""
                if not label:
                    continue

                select_el = await c.query_selector('select')
                radios = await c.query_selector_all('input[type="radio"]')
                text_el = await c.query_selector(
                    'input[type="text"], input[type="number"], input:not([type]), textarea'
                )
                if not (select_el or radios or text_el):
                    continue

                ans = self.answers.answer(label)
                if ans is None:
                    unknown += 1
                    continue

                if select_el:
                    await self._select_option(select_el, str(ans))
                elif radios:
                    await self._check_radio(c, radios, str(ans))
                elif text_el:
                    if not await text_el.input_value():
                        await text_el.fill(str(ans))
            except Exception:
                continue
        return unknown

    async def _select_option(self, select_el, value: str):
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

    async def _check_radio(self, container, radios, value: str):
        v = value.strip().lower()
        for r in radios:
            label = (await r.get_attribute('aria-label') or "").lower()
            rvalue = (await r.get_attribute('value') or "").lower()
            text = ""
            rid = await r.get_attribute('id')
            if rid:
                lab = await container.query_selector(f'label[for="{rid}"]')
                if lab:
                    text = (await lab.inner_text()).strip().lower()
            if v == rvalue or v in label or (text and v in text):
                try:
                    await r.check()
                except Exception:
                    try:
                        await r.click()
                    except Exception:
                        pass
                return

    # ── LinkedIn Easy Apply ──────────────────────────────────────────────────

    async def _apply_linkedin(self, page, job: dict, pdf_path: str = None) -> dict:
        await page.goto(job["url"], wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)

        easy_apply = await page.query_selector(
            'button.jobs-apply-button, button[aria-label*="Easy Apply"], .jobs-s-apply button'
        )
        if not easy_apply:
            return self._external_apply(job)

        await easy_apply.click()
        await asyncio.sleep(2)
        return await self._fill_linkedin_modal(page, pdf_path)

    async def _fill_linkedin_modal(self, page, pdf_path: str = None) -> dict:
        for _ in range(12):
            await asyncio.sleep(1.5)
            if not await self._guard_captcha(page, "linkedin"):
                return {"status": "needs_review", "url": page.url,
                        "message": "CAPTCHA not cleared during LinkedIn apply."}

            submit_btn = await page.query_selector('button[aria-label="Submit application"]')
            if submit_btn:
                await submit_btn.click()
                confirmed = await self._verify_submission(page)
                if confirmed:
                    return confirmed
                return {"status": "applied", "message": "Application submitted via LinkedIn Easy Apply."}

            if pdf_path:
                upload = await page.query_selector('input[type="file"][accept*="pdf"]')
                if upload:
                    try:
                        await upload.set_input_files(pdf_path)
                        await asyncio.sleep(1)
                    except Exception:
                        pass

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

            review_btn = await page.query_selector('button[aria-label="Review your application"]')
            next_btn = await page.query_selector('button[aria-label="Continue to next step"]')
            if review_btn:
                await review_btn.click()
            elif next_btn:
                await next_btn.click()
            else:
                break

        return {"status": "needs_review", "url": page.url,
                "message": "Partially filled — could not reach final submit. Some questions need manual answers."}

    async def _linkedin_modal_closed(self, page) -> bool:
        try:
            modal = await page.query_selector('div.jobs-easy-apply-modal, div[role="dialog"]')
            return modal is None
        except Exception:
            return True

    # ── Naukri Apply ─────────────────────────────────────────────────────────

    async def _apply_naukri(self, page, job: dict, pdf_path: str = None) -> dict:
        await page.goto(job["url"], wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)
        if not await self._guard_captcha(page, "naukri"):
            return {"status": "needs_review", "url": page.url, "message": "CAPTCHA not cleared on Naukri."}

        apply_btn = await page.query_selector(
            'button#apply-button, a#apply-button, button[title*="Apply"]'
        )
        if not apply_btn:
            return self._external_apply(job)

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

    async def _apply_indeed(self, page, job: dict, pdf_path: str = None) -> dict:
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

        submit = await page.query_selector(
            'button[data-testid*="submit"], button[aria-label*="Submit"], button[type="submit"]'
        )
        if submit:
            await submit.click()
            confirmed = await self._verify_submission(page)
            if confirmed:
                return confirmed
            return {"status": "applied", "message": "Application submitted via Indeed Easy Apply."}

        return {"status": "needs_review", "url": page.url,
                "message": "Clicked Apply on Indeed — may need additional steps."}

    # ── Dice Apply ───────────────────────────────────────────────────────────

    async def _apply_dice(self, page, job: dict, pdf_path: str = None) -> dict:
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

        submit = await page.query_selector('button[data-cy="submit-application"], button[type="submit"]')
        if submit:
            await submit.click()
            confirmed = await self._verify_submission(page)
            if confirmed:
                return confirmed
            return {"status": "applied", "message": "Application submitted via Dice Easy Apply."}

        return {"status": "needs_review", "url": page.url,
                "message": "Clicked Apply on Dice — may need additional steps."}
