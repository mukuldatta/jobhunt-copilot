import os
import asyncio
import random
import httpx
from datetime import datetime
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from db.mongodb import insert_job
from platforms import NAUKRI_SCRAPE_PROFILE
from utils.job_parser import (
    clean_description, generate_job_id, extract_contract_type, is_relevant_job
)
from dotenv import load_dotenv

load_dotenv()

INDIA_QUERIES = [
    "AI Engineer", "Machine Learning Engineer", "Data Engineer",
    "Software Engineer", "Python Developer", "Full Stack Developer",
    "Backend Developer", "MLOps Engineer", "GenAI Engineer",
]
INDIA_LOCATIONS = ["Hyderabad", "Bangalore", "Pune"]

# Persistent browser profiles keep Cloudflare/Akamai clearance cookies between
# runs, so a bot check solved once isn't re-challenged on every scrape. The
# Naukri one is a *different* profile from the apply agent's, and its name comes
# from platforms so the two sides cannot disagree about which directory it is —
# see the note there.
PROFILE_ROOT = os.environ.get("BROWSER_PROFILE_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), ".browser_profiles"
)


def is_closed_error(e: Exception) -> bool:
    """
    Did this fail because the browser is gone, rather than because the page said
    something we did not like?

    The distinction had no representation in the scrape loops: every exception
    was logged and followed by the next query. So closing the window mid-run
    produced one error per remaining query/city pair — seventeen in a row
    against a browser that no longer existed — and the source ended with 0 jobs
    and seventeen lines that each looked like an unrelated page-load problem.

    Matching on the message rather than the exception type because Playwright
    raises this as a plain Error from several call sites, and the wording is the
    only thing common to all of them.
    """
    msg = str(e).lower()
    return any(s in msg for s in (
        "has been closed",          # "Target page, context or browser has been closed"
        "target closed",
        "browser closed",
        "connection closed",
    ))


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


class ScraperAgent:
    def __init__(self):
        self.max_jobs_per_source = 100

    async def scrape_all(self) -> list:
        all_jobs = []

        # India-only pipeline. Naukri is the primary source but sits behind
        # Akamai + reCAPTCHA: it only loads in a *headed* real-Chrome browser on
        # a residential IP, so this must run locally (not on a cloud host).
        naukri_jobs = await self._scrape_naukri()
        all_jobs.extend(naukri_jobs)
        print(f"  Naukri: {len(naukri_jobs)} jobs")

        indeed_india = await self._scrape_indeed_india()
        all_jobs.extend(indeed_india)
        print(f"  Indeed India: {len(indeed_india)} jobs")

        linkedin_india = await self._scrape_linkedin_guest()
        all_jobs.extend(linkedin_india)
        print(f"  LinkedIn India: {len(linkedin_india)} jobs")

        saved = 0
        for job in all_jobs:
            if await insert_job(job):
                saved += 1

        print(f"ScraperAgent: scraped {len(all_jobs)} total, {saved} new saved")
        return all_jobs

    # ── NAUKRI (India Primary) ──────────────────────────────────
    # Naukri is behind Akamai Bot Manager + reCAPTCHA. The internal search API
    # returns 406 "recaptcha required" for any script (even with browser-TLS
    # impersonation), and headless browsers get a 403 "Access Denied". The ONLY
    # reliable path is a HEADED real-Chrome browser on a residential IP, reading
    # jobs from the rendered DOM. That means this runs locally, not on a cloud
    # host. Set NAUKRI_DISABLED=1 to skip it (e.g. when deployed headless).

    async def _scrape_naukri(self) -> list:
        if os.getenv("NAUKRI_DISABLED", "").strip() in ("1", "true", "yes"):
            print("  Naukri: skipped (NAUKRI_DISABLED set)")
            return []

        jobs = []
        seen = set()
        blocked_streak = 0
        browser_gone = False
        self._challenge_gave_up = False
        async with async_playwright() as pw:
            context = await self._headed_context(pw, NAUKRI_SCRAPE_PROFILE)
            if context is None:
                return []
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                for query in INDIA_QUERIES:
                    if len(jobs) >= self.max_jobs_per_source or blocked_streak >= 2 or browser_gone:
                        break
                    for city in INDIA_LOCATIONS:
                        if len(jobs) >= self.max_jobs_per_source or blocked_streak >= 2 or browser_gone:
                            break
                        try:
                            rows = await self._load_naukri_page(page, query, city)
                            if rows is None:          # blocked — don't grind on
                                blocked_streak += 1
                                continue
                            blocked_streak = 0
                            added = 0
                            for row in rows:
                                title = (row.get("title") or "").strip()
                                url = (row.get("url") or "").strip()
                                if not title or not url or url in seen:
                                    continue
                                if not is_relevant_job(title):
                                    continue
                                seen.add(url)
                                jobs.append(self._naukri_row_to_job(row, city))
                                added += 1
                            print(f"    Naukri '{query}' {city}: {added} jobs")
                            await asyncio.sleep(random.uniform(2, 4))
                        except Exception as e:
                            if is_closed_error(e):
                                print("    Naukri: the browser window is gone — "
                                      "stopping this source.")
                                browser_gone = True
                                break
                            print(f"    Naukri error '{query}' {city}: {e}")
                if browser_gone:
                    print(f"    Naukri: stopped early with {len(jobs)} job(s) — "
                          f"the window closed mid-run.")
                elif blocked_streak >= 2:
                    print("    Naukri: stopping early — blocked by Akamai "
                          "(needs a headed browser on a residential IP)")

                jobs = jobs[:self.max_jobs_per_source]
                # The search card carries a ~100-char teaser, which is not
                # something a resume can be matched against — the scorer would
                # be guessing. The detail page has the real JD and we already
                # hold a warm, unblocked context, so fetch it while we can.
                # Unless there is no context left to hold: the teasers are worth
                # keeping, and the jobs still land with what the cards gave us.
                if not browser_gone:
                    await self._fetch_naukri_descriptions(page, jobs)
            finally:
                await context.close()
        return jobs[:self.max_jobs_per_source]

    # Naukri renders the JD through CSS modules, so the class carries a build
    # hash (styles_JDC__dang-inner-html__h0K4t) that changes on their deploys.
    # Match on the stable stem instead, widest container last.
    NAUKRI_JD_SELECTORS = (
        "[class*='JDC__dang-inner-html']",
        "[class*='job-desc-container']",
        "div.dang-inner-html",
    )

    async def _fetch_naukri_descriptions(self, page, jobs: list):
        """Replace each teaser with the full JD from the posting's own page."""
        filled = fail_streak = 0
        for job in jobs:
            if fail_streak >= 3:
                print("    Naukri descriptions: stopping early (3 consecutive failures)")
                break
            if len(job.get("description") or "") >= 600:
                continue
            try:
                await page.goto(job["url"], wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(1.5, 2.5))
                text = ""
                for sel in self.NAUKRI_JD_SELECTORS:
                    el = await page.query_selector(sel)
                    if el:
                        text = (await el.inner_text()).strip()
                        if len(text) > len(job.get("description") or ""):
                            break
                if len(text) < 200:
                    fail_streak += 1
                    continue
                fail_streak = 0
                # Keep the experience line the card gave us — the JD often omits it.
                exp = (job.get("description") or "").split(".")[0]
                job["description"] = clean_description(
                    f"{exp}. {text}" if exp.lower().startswith("experience") else text
                )
                filled += 1
                await asyncio.sleep(random.uniform(2, 4))
            except Exception as e:
                if is_closed_error(e):
                    # The streak counter would have stopped this after three
                    # tries anyway, but it would have blamed "3 consecutive
                    # failures" — a description-selector problem — for a browser
                    # that simply is not there any more.
                    print(f"    Naukri descriptions: browser gone after {filled} — stopping.")
                    return
                fail_streak += 1
        print(f"    Naukri descriptions: {filled}/{len(jobs)} fetched")

    async def _headed_context(self, pw, name: str):
        """
        Headed browser with a *persistent* profile. Without this the scraper
        started from an empty profile every run, so Cloudflare's clearance cookie
        was never kept and every page load was challenged again. One profile per
        site keeps cookies isolated.
        """
        user_dir = os.path.join(PROFILE_ROOT, name)
        os.makedirs(user_dir, exist_ok=True)
        opts = dict(headless=False, viewport={"width": 1360, "height": 900}, locale="en-IN",
                    args=["--disable-blink-features=AutomationControlled"],
                    ignore_default_args=["--enable-automation"])
        try:
            return await pw.chromium.launch_persistent_context(user_dir, channel="chrome", **opts)
        except Exception as e:
            print(f"    {name}: real Chrome unavailable ({e}); using bundled Chromium")
        try:
            return await pw.chromium.launch_persistent_context(user_dir, **opts)
        except Exception as e:
            print(f"    {name}: headed browser unavailable ({e}); skipping")
            return None

    async def _is_challenged(self, page) -> bool:
        """Cloudflare / bot-check interstitial rather than a real results page."""
        try:
            title = (await page.title() or "").lower()
            if any(s in title for s in ("just a moment", "attention required", "verify", "access denied")):
                return True
            body = (await page.inner_text("body"))[:1500].lower()
            return any(s in body for s in (
                "verify you are human", "verify you're human", "checking your browser",
                "needs to review the security", "additional verification required",
                "complete the security check",
            ))
        except Exception as e:
            # A page we cannot read is not a page without a challenge. Swallowing
            # everything here meant a closed browser answered "no challenge",
            # which _solve_challenge reported as "[RESUME] cleared" — a window
            # you closed read as a bot check you had solved.
            if is_closed_error(e):
                raise
            return False

    async def _solve_challenge(self, page, label: str) -> bool:
        """
        Pause and let you clear the bot check in the visible window. Because the
        profile is persistent, solving it once keeps the clearance cookie for
        subsequent runs instead of re-challenging forever.
        """
        # If nobody cleared the last challenge, nobody is watching — don't burn
        # another full timeout on every subsequent blocked page.
        if getattr(self, "_challenge_gave_up", False):
            return False

        timeout = int(os.getenv("SCRAPE_CHALLENGE_TIMEOUT", "180"))
        print(f"    [PAUSE] {label}: bot check detected — solve it in the open browser window.")
        print(f"            Waiting up to {timeout}s (set SCRAPE_CHALLENGE_TIMEOUT to change)...")
        waited = 0
        while waited < timeout:
            await asyncio.sleep(5)
            waited += 5
            # Closing the window is a perfectly reasonable way to say "not now",
            # and it has to be distinguishable from solving the puzzle. It was
            # not: the read failed, the failure was read as "no challenge", and
            # the scrape carried on against a browser that no longer existed.
            if page.is_closed():
                print(f"    [ABORT] {label}: the window was closed — nothing left to clear.")
                self._challenge_gave_up = True
                return False
            try:
                still_challenged = await self._is_challenged(page)
            except Exception as e:
                if is_closed_error(e):
                    print(f"    [ABORT] {label}: the browser is gone — giving up on this source.")
                    self._challenge_gave_up = True
                    return False
                raise
            if not still_challenged:
                print(f"    [RESUME] {label}: cleared after {waited}s — continuing.")
                return True
        print(f"    [TIMEOUT] {label}: not cleared within {timeout}s.")
        self._challenge_gave_up = True
        return False

    async def _load_naukri_page(self, page, query: str, city: str):
        """Returns a list of rows, or None if Akamai blocked this load."""
        slug = query.lower().replace(" ", "-")
        url = f"https://www.naukri.com/{slug}-jobs-in-{city.lower()}"
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        # Wait for job cards to render; Akamai block pages never show them.
        try:
            await page.wait_for_selector("div.srp-jobtuple-wrapper", timeout=12000)
        except Exception:
            title = (await page.title() or "").lower()
            if "access denied" in title:
                print(f"    Naukri BLOCKED on '{query}' {city} (Access Denied) "
                      f"— need a residential IP + headed browser")
                return None
            if await self._is_challenged(page):
                if not await self._solve_challenge(page, f"Naukri '{query}' {city}"):
                    return None
                try:
                    await page.wait_for_selector("div.srp-jobtuple-wrapper", timeout=15000)
                except Exception:
                    return None
            else:
                return []
        await asyncio.sleep(random.uniform(1.5, 3))
        return await page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('div.srp-jobtuple-wrapper').forEach(c => {
                const g = (s) => { const e = c.querySelector(s); return e ? e.textContent.trim() : ""; };
                const a = c.querySelector('a.title');
                out.push({
                    title: g('a.title'),
                    url: a ? a.href : "",
                    company: g('a.comp-name, span.comp-name'),
                    exp: g('span.expwdth'),
                    loc: g('span.locWdth, span.loc-wrap span, span.location'),
                    desc: g('span.job-desc'),
                });
            });
            return out;
        }""")

    def _naukri_row_to_job(self, row: dict, city: str) -> dict:
        title = (row.get("title") or "").strip()
        company = (row.get("company") or "Unknown").strip() or "Unknown"
        location = (row.get("loc") or city).strip() or city
        if "india" not in location.lower():
            location = f"{location}, India"
        url = (row.get("url") or "").strip()

        description = clean_description(row.get("desc") or "")
        exp_text = (row.get("exp") or "").strip()
        if exp_text:
            description = f"Experience: {exp_text}. {description}".strip()

        return {
            "job_id": generate_job_id(url, title, company),
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "url": url,
            "posted_at": datetime.utcnow(),
            "scraped_at": datetime.utcnow(),
            "source": "naukri",
            "region": "india",
            "sponsorship_status": "contract",
            "contract_type": extract_contract_type(title, description),
            "match_score": None,
            "score_breakdown": None,
            "gap_analysis": [],
            "status": "new",
        }

    # ── INDEED INDIA (headed) ────────────────────────────────────────────────
    # in.indeed.com is Cloudflare-protected: httpx gets 403, but a headed
    # real-Chrome browser on a residential IP loads results fine (like Naukri),
    # so this must run locally. Jobs are read from the rendered DOM.

    async def _scrape_indeed_india(self) -> list:
        jobs = []
        seen = set()
        blocked_streak = 0
        browser_gone = False
        self._challenge_gave_up = False
        async with async_playwright() as pw:
            context = await self._headed_context(pw, "indeed")
            if context is None:
                return []
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                for query in INDIA_QUERIES[:6]:
                    if len(jobs) >= self.max_jobs_per_source or blocked_streak >= 2 or browser_gone:
                        break
                    for city in INDIA_LOCATIONS:
                        if len(jobs) >= self.max_jobs_per_source or blocked_streak >= 2 or browser_gone:
                            break
                        try:
                            rows = await self._load_indeed_page(page, query, city)
                            if rows is None:          # bot check we couldn't clear
                                blocked_streak += 1
                                continue
                            blocked_streak = 0
                            added = 0
                            for row in rows:
                                title = (row.get("title") or "").strip()
                                url = (row.get("url") or "").strip()
                                if not title or not url or url in seen:
                                    continue
                                if not is_relevant_job(title):
                                    continue
                                seen.add(url)
                                jobs.append(self._indeed_row_to_job(row, city))
                                added += 1
                            print(f"    Indeed '{query}' {city}: {added} jobs")
                            # Indeed rate-limits bursts; pace requests generously.
                            await asyncio.sleep(random.uniform(5, 9))
                        except Exception as e:
                            if is_closed_error(e):
                                # Nothing after this point can succeed, and every
                                # attempt would report the same thing in the
                                # vocabulary of a page-load failure.
                                print("    Indeed: the browser window is gone — "
                                      "stopping this source.")
                                browser_gone = True
                                break
                            print(f"    Indeed error '{query}' {city}: {e}")
                if browser_gone:
                    print(f"    Indeed: stopped early with {len(jobs)} job(s) — "
                          f"the window closed mid-run.")
                elif blocked_streak >= 2:
                    print("    Indeed: stopping early — bot check not cleared "
                          "(solve it once in the window and the session is remembered)")
            finally:
                await context.close()
        return jobs[:self.max_jobs_per_source]

    async def _load_indeed_page(self, page, query: str, city: str):
        """Returns a list of rows, or None if a bot check blocked this load."""
        from urllib.parse import quote
        url = f"https://in.indeed.com/jobs?q={quote(query)}&l={quote(city)}&fromage=3&sort=date"
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_selector("div.job_seen_beacon", timeout=12000)
        except Exception:
            if await self._is_challenged(page):
                # Let the user clear it once; the persistent profile keeps the
                # clearance cookie so later loads sail through.
                if not await self._solve_challenge(page, f"Indeed '{query}' {city}"):
                    return None
                try:
                    await page.wait_for_selector("div.job_seen_beacon", timeout=15000)
                except Exception:
                    return None
            else:
                return []
        await asyncio.sleep(random.uniform(1.5, 3))
        rows = await page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('div.job_seen_beacon').forEach(c => {
                const g = (s) => { const e = c.querySelector(s); return e ? e.textContent.trim() : ""; };
                const a = c.querySelector('a[data-jk]');
                const t = c.querySelector('a[data-jk] span[title]');
                out.push({
                    title: (t && t.getAttribute('title')) || (a ? a.textContent.trim() : ""),
                    company: g('[data-testid="company-name"]') || g('span.companyName'),
                    loc: g('[data-testid="text-location"]') || g('div.companyLocation'),
                    href: a ? a.getAttribute('href') : "",
                    // snippet gives the scorer something to work with
                    desc: g('[data-testid="belowJobSnippet"]') || g('div.job-snippet') || g('ul'),
                    meta: g('[data-testid="attribute_snippet_testid"]') || "",
                });
            });
            return out;
        }""")
        for r in rows:
            href = r.get("href") or ""
            r["url"] = f"https://in.indeed.com{href}" if href.startswith("/") else href
        return rows

    def _indeed_row_to_job(self, row: dict, city: str) -> dict:
        title = (row.get("title") or "").strip()
        company = (row.get("company") or "Unknown").strip() or "Unknown"
        location = (row.get("loc") or f"{city}, India").strip() or f"{city}, India"
        if "india" not in location.lower():
            location = f"{location}, India"
        url = (row.get("url") or "").strip()
        description = clean_description(
            " ".join(x for x in [row.get("meta") or "", row.get("desc") or ""] if x)
        )
        return {
            "job_id": generate_job_id(url, title, company),
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "url": url,
            "posted_at": datetime.utcnow(),
            "scraped_at": datetime.utcnow(),
            "source": "indeed",
            "region": "india",
            "sponsorship_status": "contract",
            "contract_type": extract_contract_type(title, ""),
            "match_score": None,
            "score_breakdown": None,
            "gap_analysis": [],
            "status": "new",
        }

    # ── LINKEDIN Guest API (India) ───────────────────────────────────────────
    # LinkedIn exposes a public guest jobs endpoint used for AJAX pagination.
    # It returns HTML fragments without requiring login.

    async def _scrape_linkedin_guest(self) -> list:
        jobs = []
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
            for query in INDIA_QUERIES[:6]:
                if len(jobs) >= self.max_jobs_per_source:
                    break
                for loc in INDIA_LOCATIONS:
                    if len(jobs) >= self.max_jobs_per_source:
                        break
                    try:
                        location_str = f"{loc}, India"
                        url = (
                            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
                            f"?keywords={query.replace(' ', '%20')}"
                            f"&location={location_str.replace(' ', '%20').replace(',', '%2C')}"
                            f"&f_TPR=r259200"   # posted in last 3 days
                            f"&f_E=3%2C4"       # mid-senior + associate level
                            f"&start=0"
                        )
                        resp = await client.get(url)
                        print(f"    LinkedIn '{query}' {loc}: HTTP {resp.status_code}")
                        if resp.status_code != 200:
                            await asyncio.sleep(random.uniform(2, 4))
                            continue

                        new_jobs = self._parse_linkedin_guest_html(resp.text)
                        print(f"    LinkedIn '{query}' {loc}: {len(new_jobs)} jobs")
                        jobs.extend(new_jobs)
                        await asyncio.sleep(random.uniform(2, 4))
                    except Exception as e:
                        print(f"    LinkedIn error '{query}' {loc}: {e}")

        jobs = jobs[:self.max_jobs_per_source]
        await self._fetch_linkedin_descriptions(jobs)
        return jobs

    async def _fetch_linkedin_descriptions(self, jobs: list):
        """
        The guest search endpoint returns cards without descriptions, which makes
        scoring meaningless (every job lands ~10%). LinkedIn's guest jobPosting
        endpoint returns the full JD without login, so fetch it per job.
        """
        import re as _re
        fail_streak = 0
        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
            for job in jobs:
                if fail_streak >= 3:
                    # Rate limited or blocked — stop rather than walking every
                    # remaining job at ~2s each for nothing.
                    print("    LinkedIn descriptions: stopping early (3 consecutive failures)")
                    break
                if job.get("description") and job.get("apply_type_hint"):
                    continue
                m = _re.search(r"/jobs/view/(?:.*-)?(\d+)", job.get("url", ""))
                if not m:
                    continue
                try:
                    resp = await client.get(
                        f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}"
                    )
                    if resp.status_code != 200:
                        fail_streak += 1
                        continue
                    fail_streak = 0

                    # Free while we are already holding the page: LinkedIn marks
                    # an off-site posting with an offsite-apply glyph on the
                    # apply button. Measured 4/5 against hand-classified jobs, so
                    # it is a HINT that reorders the queue — never a verdict.
                    # Only a real apply run writes the authoritative apply_type.
                    job["apply_type_hint"] = (
                        "external" if "offsite-apply" in resp.text else "in_platform"
                    )

                    soup = BeautifulSoup(resp.text, "html.parser")
                    el = (soup.select_one("div.show-more-less-html__markup")
                          or soup.select_one("div.description__text"))
                    if el:
                        job["description"] = clean_description(el.get_text(" ", strip=True))
                    await asyncio.sleep(random.uniform(1.5, 3))
                except Exception:
                    fail_streak += 1
        filled = sum(1 for j in jobs if j.get("description"))
        print(f"    LinkedIn descriptions: {filled}/{len(jobs)} fetched")

    def _parse_linkedin_guest_html(self, html: str) -> list:
        soup = BeautifulSoup(html, "html.parser")
        jobs = []

        cards = soup.select("li, div.base-card")
        for card in cards[:25]:
            try:
                title_el = card.select_one(
                    "h3.base-search-card__title, "
                    ".base-search-card__title, "
                    "span.sr-only"
                )
                company_el = card.select_one(
                    "h4.base-search-card__subtitle a, "
                    ".base-search-card__subtitle, "
                    "h4 a"
                )
                location_el = card.select_one(
                    ".job-search-card__location, "
                    "span.job-search-card__location"
                )
                link_el = card.select_one("a.base-card__full-link, a[href*='linkedin.com/jobs/view']")

                title = title_el.get_text(strip=True) if title_el else ""
                company = company_el.get_text(strip=True) if company_el else "Unknown"
                location = location_el.get_text(strip=True) if location_el else "India"
                url = link_el.get("href", "").split("?")[0] if link_el else ""

                if not title or not is_relevant_job(title):
                    continue

                jobs.append({
                    "job_id": generate_job_id(url, title, company),
                    "title": title,
                    "company": company,
                    "location": location,
                    "description": "",
                    "url": url,
                    "posted_at": datetime.utcnow(),
                    "scraped_at": datetime.utcnow(),
                    "source": "linkedin",
                    "region": "india",
                    "sponsorship_status": "contract",
                    "contract_type": extract_contract_type(title, ""),
                    "match_score": None,
                    "score_breakdown": None,
                    "gap_analysis": [],
                    "status": "new",
                })
            except Exception:
                continue

        return jobs
