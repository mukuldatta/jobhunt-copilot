import os
import asyncio
import tempfile
from playwright.async_api import async_playwright
from agents.tailor_agent import TailorAgent
from utils.pdf_generator import generate_resume_pdf
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
        self.user_name = os.environ.get("USER_FULL_NAME", "Venkata Naga Santosh Mukul Mokkapati")
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
                    return {
                        "status": "login_failed",
                        "message": "LinkedIn login failed. Check credentials or disable 2FA temporarily.",
                    }

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
