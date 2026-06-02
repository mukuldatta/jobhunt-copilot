import os
import asyncio
import tempfile
from playwright.async_api import async_playwright
from agents.tailor_agent import TailorAgent
from utils.pdf_generator import generate_resume_pdf
from services.alert_service import send_login_failure_alert
from db.mongodb import increment_login_failure, reset_login_failures
from dotenv import load_dotenv

load_dotenv()

STEALTH_JS = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
    window.chrome = {runtime: {}};
"""


class ApplyAgent:
    def __init__(self):
        self.linkedin_email = os.environ.get("LINKEDIN_EMAIL", "")
        self.linkedin_password = os.environ.get("LINKEDIN_PASSWORD", "")
        self.indeed_email = os.environ.get("INDEED_EMAIL", "")
        self.indeed_password = os.environ.get("INDEED_PASSWORD", "")
        self.dice_email = os.environ.get("DICE_EMAIL", "")
        self.dice_password = os.environ.get("DICE_PASSWORD", "")
        first = os.environ.get("USER_FIRST_NAME", "Mukul")
        last = os.environ.get("USER_LAST_NAME", "Mokkapati")
        self.user_name = f"{first} {last}"
        self.user_email = os.environ.get("MY_EMAIL", "mukulmokkapati@gmail.com")
        self.user_phone = os.environ.get("MY_PHONE", "")

    async def apply(self, job: dict) -> dict:
        # Step 1: Generate tailored resume PDF
        pdf_path = None
        try:
            tailor = TailorAgent()
            tailored_text = await tailor.tailor(job)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            tmp.close()
            generate_resume_pdf(tailored_text, tmp.name)
            pdf_path = tmp.name
        except Exception as e:
            print(f"ApplyAgent: resume tailoring failed: {e}")

        source = job.get("source", "")

        if source == "linkedin":
            return await self._apply_linkedin(job, pdf_path)
        elif source == "naukri":
            return await self._apply_naukri(job, pdf_path)
        elif source == "indeed":
            return await self._apply_indeed(job, pdf_path)
        elif source == "dice":
            return await self._apply_dice(job, pdf_path)
        else:
            return {
                "status": "manual_required",
                "url": job.get("url", ""),
                "message": f"Auto-apply not yet supported for '{source}'. Open the link and apply manually.",
            }

    # ── LinkedIn Easy Apply ──────────────────────────────────────────────────

    async def _apply_linkedin(self, job: dict, pdf_path: str = None) -> dict:
        if not self.linkedin_email or not self.linkedin_password:
            return {
                "status": "credentials_missing",
                "message": "Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD in Railway environment variables.",
            }

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900},
            )
            page = await context.new_page()
            await page.add_init_script(STEALTH_JS)

            try:
                # Login
                await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
                await page.fill("#username", self.linkedin_email)
                await page.fill("#password", self.linkedin_password)
                await page.click('button[type="submit"]')
                await page.wait_for_load_state("networkidle", timeout=15000)

                if "checkpoint" in page.url or "login" in page.url:
                    count = await increment_login_failure("linkedin")
                    if count >= 5:
                        send_login_failure_alert("linkedin", count)
                    return {
                        "status": "login_failed",
                        "message": f"LinkedIn login failed (attempt {count}). Check credentials or disable 2FA temporarily.",
                    }

                await reset_login_failures("linkedin")

                # Navigate to job
                await page.goto(job["url"], wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(2)

                # Find Easy Apply button
                easy_apply = await page.query_selector(
                    'button.jobs-apply-button, '
                    'button[aria-label*="Easy Apply"], '
                    '.jobs-s-apply button'
                )

                if not easy_apply:
                    return {
                        "status": "manual_required",
                        "url": job["url"],
                        "message": "No Easy Apply button found. This job requires applying on the company site.",
                    }

                await easy_apply.click()
                await asyncio.sleep(2)

                result = await self._fill_linkedin_modal(page, pdf_path)
                return result

            except Exception as e:
                return {"status": "error", "message": str(e)}
            finally:
                await context.close()
                await browser.close()

    async def _fill_linkedin_modal(self, page, pdf_path: str = None) -> dict:
        for step in range(12):
            await asyncio.sleep(1.5)

            # Final submit button
            submit_btn = await page.query_selector('button[aria-label="Submit application"]')
            if submit_btn:
                await submit_btn.click()
                await asyncio.sleep(2)
                return {"status": "applied", "message": "Application submitted via LinkedIn Easy Apply."}

            # Upload resume
            if pdf_path:
                upload_input = await page.query_selector('input[type="file"][accept*="pdf"]')
                if upload_input:
                    try:
                        await upload_input.set_input_files(pdf_path)
                        await asyncio.sleep(1)
                    except Exception:
                        pass

            # Fill phone if empty
            if self.user_phone:
                for selector in ['input[id*="phoneNumber"]', 'input[name*="phone"]', 'input[type="tel"]']:
                    phone_el = await page.query_selector(selector)
                    if phone_el:
                        val = await phone_el.input_value()
                        if not val:
                            await phone_el.fill(self.user_phone)
                        break

            # Fill email if empty
            for selector in ['input[id*="email"]', 'input[type="email"]']:
                email_el = await page.query_selector(selector)
                if email_el:
                    val = await email_el.input_value()
                    if not val:
                        await email_el.fill(self.user_email)
                    break

            # Auto-select "Yes" for work authorization / sponsorship questions
            yes_radios = await page.query_selector_all('input[type="radio"]')
            for radio in yes_radios:
                label = await radio.get_attribute("aria-label") or ""
                value = await radio.get_attribute("value") or ""
                if "yes" in label.lower() or value.lower() == "yes":
                    await radio.check()

            # Fill years of experience text inputs with "3"
            number_inputs = await page.query_selector_all('input[type="number"], input[class*="numeric"]')
            for inp in number_inputs:
                val = await inp.input_value()
                if not val:
                    await inp.fill("3")

            # Advance to next step
            review_btn = await page.query_selector('button[aria-label="Review your application"]')
            next_btn = await page.query_selector('button[aria-label="Continue to next step"]')

            if review_btn:
                await review_btn.click()
            elif next_btn:
                await next_btn.click()
            else:
                break

        return {
            "status": "needs_review",
            "url": page.url,
            "message": "Partially filled. Could not reach final submit step — some questions may need manual answers.",
        }

    # ── Naukri Apply ────────────────────────────────────────────────────────

    async def _apply_naukri(self, job: dict, pdf_path: str = None) -> dict:
        naukri_email = os.environ.get("NAUKRI_EMAIL", self.linkedin_email)
        naukri_password = os.environ.get("NAUKRI_PASSWORD", self.linkedin_password)

        if not naukri_email or not naukri_password:
            return {
                "status": "credentials_missing",
                "message": "Set NAUKRI_EMAIL and NAUKRI_PASSWORD in Railway environment variables.",
            }

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900},
            )
            page = await context.new_page()
            await page.add_init_script(STEALTH_JS)

            try:
                # Login to Naukri
                await page.goto("https://www.naukri.com/nlogin/login", wait_until="domcontentloaded")
                await asyncio.sleep(2)
                await page.fill('input[placeholder*="Email"]', naukri_email)
                await page.fill('input[placeholder*="Password"]', naukri_password)
                await page.click('button[type="submit"]')
                await page.wait_for_load_state("networkidle", timeout=15000)

                if "nlogin" in page.url or "login" in page.url:
                    count = await increment_login_failure("naukri")
                    if count >= 5:
                        send_login_failure_alert("naukri", count)
                    return {
                        "status": "login_failed",
                        "message": f"Naukri login failed (attempt {count}). Check NAUKRI_EMAIL and NAUKRI_PASSWORD.",
                    }

                await reset_login_failures("naukri")

                # Navigate to job
                await page.goto(job["url"], wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(2)

                # Click Apply button
                apply_btn = await page.query_selector('button#apply-button, a#apply-button, button[title*="Apply"]')
                if not apply_btn:
                    return {
                        "status": "manual_required",
                        "url": job["url"],
                        "message": "Could not find Apply button on Naukri job page.",
                    }

                await apply_btn.click()
                await asyncio.sleep(3)

                # Check if applied (Naukri shows "Applied" state)
                applied_indicator = await page.query_selector('[class*="applied"], button[disabled][title*="Applied"]')
                if applied_indicator:
                    return {"status": "applied", "message": "Successfully applied on Naukri."}

                return {
                    "status": "needs_review",
                    "url": page.url,
                    "message": "Clicked Apply on Naukri — may need profile completion or additional steps.",
                }

            except Exception as e:
                return {"status": "error", "message": str(e)}
            finally:
                await context.close()
                await browser.close()

    # ── Indeed Apply ─────────────────────────────────────────────────────────

    async def _apply_indeed(self, job: dict, pdf_path: str = None) -> dict:
        if not self.indeed_email or not self.indeed_password:
            return {
                "status": "credentials_missing",
                "message": "Set INDEED_EMAIL and INDEED_PASSWORD in Railway environment variables.",
            }

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900},
            )
            page = await context.new_page()
            await page.add_init_script(STEALTH_JS)

            try:
                # Login
                await page.goto("https://in.indeed.com/account/login", wait_until="domcontentloaded")
                await asyncio.sleep(2)
                await page.fill('input[name="__email"], input[type="email"]', self.indeed_email)
                await page.click('button[type="submit"], button[data-testid="login-button"]')
                await asyncio.sleep(2)
                pw_field = await page.query_selector('input[name="__password"], input[type="password"]')
                if pw_field:
                    await pw_field.fill(self.indeed_password)
                    await page.click('button[type="submit"]')
                await page.wait_for_load_state("networkidle", timeout=15000)

                if "login" in page.url or "signin" in page.url:
                    count = await increment_login_failure("indeed")
                    if count >= 5:
                        send_login_failure_alert("indeed", count)
                    return {
                        "status": "login_failed",
                        "message": f"Indeed login failed (attempt {count}). Check INDEED_EMAIL and INDEED_PASSWORD.",
                    }

                await reset_login_failures("indeed")

                # Navigate to job
                await page.goto(job["url"], wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(2)

                # Indeed "Easily apply" button
                apply_btn = await page.query_selector(
                    'button[data-testid="indeedApplyButton"], '
                    'button[id="indeedApplyButton"], '
                    'a[data-testid="apply-button"], '
                    'span.indeed-apply-button'
                )
                if not apply_btn:
                    return {
                        "status": "manual_required",
                        "url": job["url"],
                        "message": "No Indeed Easy Apply button found. Apply directly on the company site.",
                    }

                await apply_btn.click()
                await asyncio.sleep(3)

                # Upload resume
                if pdf_path:
                    upload = await page.query_selector('input[type="file"][accept*="pdf"], input[type="file"]')
                    if upload:
                        try:
                            await upload.set_input_files(pdf_path)
                            await asyncio.sleep(1)
                        except Exception:
                            pass

                # Fill phone if empty
                if self.user_phone:
                    for sel in ['input[name*="phone"]', 'input[type="tel"]']:
                        el = await page.query_selector(sel)
                        if el and not await el.input_value():
                            await el.fill(self.user_phone)
                            break

                # Submit
                submit = await page.query_selector(
                    'button[data-testid*="submit"], '
                    'button[aria-label*="Submit"], '
                    'button[type="submit"]'
                )
                if submit:
                    await submit.click()
                    await asyncio.sleep(2)
                    return {"status": "applied", "message": "Application submitted via Indeed Easy Apply."}

                return {
                    "status": "needs_review",
                    "url": page.url,
                    "message": "Clicked Apply on Indeed — may need additional steps.",
                }

            except Exception as e:
                return {"status": "error", "message": str(e)}
            finally:
                await context.close()
                await browser.close()

    # ── Dice Apply ───────────────────────────────────────────────────────────

    async def _apply_dice(self, job: dict, pdf_path: str = None) -> dict:
        if not self.dice_email or not self.dice_password:
            return {
                "status": "credentials_missing",
                "message": "Set DICE_EMAIL and DICE_PASSWORD in Railway environment variables.",
            }

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900},
            )
            page = await context.new_page()
            await page.add_init_script(STEALTH_JS)

            try:
                # Login
                await page.goto("https://www.dice.com/dashboard/login", wait_until="domcontentloaded")
                await asyncio.sleep(2)
                await page.fill('input[name="email"], input[type="email"]', self.dice_email)
                await page.click('button[type="submit"]')
                await asyncio.sleep(1)
                pw_field = await page.query_selector('input[name="password"], input[type="password"]')
                if pw_field:
                    await pw_field.fill(self.dice_password)
                    await page.click('button[type="submit"]')
                await page.wait_for_load_state("networkidle", timeout=15000)

                if "login" in page.url or "signin" in page.url:
                    count = await increment_login_failure("dice")
                    if count >= 5:
                        send_login_failure_alert("dice", count)
                    return {
                        "status": "login_failed",
                        "message": f"Dice login failed (attempt {count}). Check DICE_EMAIL and DICE_PASSWORD.",
                    }

                await reset_login_failures("dice")

                # Navigate to job
                await page.goto(job["url"], wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(2)

                # Dice Easy Apply button
                apply_btn = await page.query_selector(
                    'apply-button-wc, '
                    'button[data-cy="apply-button"], '
                    'a[data-cy="apply-button"], '
                    'button[id*="apply"]'
                )
                if not apply_btn:
                    return {
                        "status": "manual_required",
                        "url": job["url"],
                        "message": "No Dice Easy Apply button found. Apply directly on the company site.",
                    }

                await apply_btn.click()
                await asyncio.sleep(3)

                # Upload resume if prompted
                if pdf_path:
                    upload = await page.query_selector('input[type="file"]')
                    if upload:
                        try:
                            await upload.set_input_files(pdf_path)
                            await asyncio.sleep(1)
                        except Exception:
                            pass

                # Fill phone if empty
                if self.user_phone:
                    for sel in ['input[name*="phone"]', 'input[type="tel"]']:
                        el = await page.query_selector(sel)
                        if el and not await el.input_value():
                            await el.fill(self.user_phone)
                            break

                # Submit
                submit = await page.query_selector(
                    'button[data-cy="submit-application"], '
                    'button[type="submit"]'
                )
                if submit:
                    await submit.click()
                    await asyncio.sleep(2)
                    return {"status": "applied", "message": "Application submitted via Dice Easy Apply."}

                return {
                    "status": "needs_review",
                    "url": page.url,
                    "message": "Clicked Apply on Dice — may need additional steps.",
                }

            except Exception as e:
                return {"status": "error", "message": str(e)}
            finally:
                await context.close()
                await browser.close()
