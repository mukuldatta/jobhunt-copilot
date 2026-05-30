import asyncio
import random
import httpx
from datetime import datetime
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from db.mongodb import insert_job
from utils.job_parser import (
    clean_description, generate_job_id, extract_contract_type, is_relevant_job
)
from services.h1b_checker import check_h1b_sponsorship
from dotenv import load_dotenv

load_dotenv()

INDIA_QUERIES = [
    "AI Engineer", "Machine Learning Engineer", "Data Engineer",
    "Software Engineer", "Python Developer", "Full Stack Developer",
    "Backend Developer", "MLOps Engineer", "GenAI Engineer",
]
US_QUERIES = [
    "AI Engineer", "Machine Learning Engineer", "Data Engineer",
    "Software Engineer", "Backend Engineer", "Python Developer",
    "GenAI Engineer", "MLOps Engineer",
]
INDIA_LOCATIONS = ["Hyderabad", "Bangalore", "Pune"]

# Hides headless browser signals from anti-bot detectors
STEALTH_JS = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
    window.chrome = {runtime: {}};
"""

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

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)

            naukri_jobs = await self._scrape_naukri(browser)
            all_jobs.extend(naukri_jobs)
            print(f"  Naukri: {len(naukri_jobs)} jobs")

            await browser.close()

        # LinkedIn guest API works without a browser
        linkedin_india = await self._scrape_linkedin_guest(region="india")
        all_jobs.extend(linkedin_india)
        print(f"  LinkedIn India: {len(linkedin_india)} jobs")

        linkedin_us = await self._scrape_linkedin_guest(region="us")
        all_jobs.extend(linkedin_us)
        print(f"  LinkedIn US: {len(linkedin_us)} jobs")

        saved = 0
        for job in all_jobs:
            if await insert_job(job):
                saved += 1

        print(f"ScraperAgent: scraped {len(all_jobs)} total, {saved} new saved")
        return all_jobs

    # ── Shared: new stealth browser page ────────────────────────────────────

    async def _new_page(self, browser):
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = await context.new_page()
        await page.add_init_script(STEALTH_JS)
        return page, context

    # ── NAUKRI (India Primary) ───────────────────────────────────────────────
    # Strategy 1: direct httpx call to Naukri's internal search API (fast).
    # Strategy 2: Playwright XHR interception fallback if httpx is blocked.

    async def _scrape_naukri(self, browser) -> list:
        jobs = []

        # Try direct API first — much faster than Playwright
        try:
            jobs = await self._scrape_naukri_api()
            print(f"  Naukri API: {len(jobs)} jobs")
            if jobs:
                return jobs[:self.max_jobs_per_source]
        except Exception as e:
            print(f"  Naukri API failed ({e}), falling back to Playwright")

        # Playwright XHR interception fallback
        jobs = await self._scrape_naukri_playwright(browser)
        return jobs[:self.max_jobs_per_source]

    async def _scrape_naukri_api(self) -> list:
        """Call Naukri's internal search API directly via httpx."""
        naukri_headers = {
            "appid": "109",
            "systemid": "Naukri",
            "Content-Type": "application/json",
            "User-Agent": HEADERS["User-Agent"],
            "Accept": "application/json",
            "Referer": "https://www.naukri.com/",
        }
        jobs = []
        async with httpx.AsyncClient(headers=naukri_headers, timeout=20, follow_redirects=True) as client:
            for query in INDIA_QUERIES:
                if len(jobs) >= self.max_jobs_per_source:
                    break
                for city in INDIA_LOCATIONS:
                    if len(jobs) >= self.max_jobs_per_source:
                        break
                    try:
                        params = {
                            "noOfResults": "20",
                            "urlType": "search_by_keyword",
                            "searchType": "adv",
                            "keyword": query,
                            "location": city,
                            "experience": "3",
                            "k": query,
                            "l": city,
                        }
                        resp = await client.get(
                            "https://www.naukri.com/jobapi/v3/search", params=params
                        )
                        print(f"    Naukri API '{query}' {city}: HTTP {resp.status_code}")
                        if resp.status_code != 200:
                            await asyncio.sleep(random.uniform(2, 3))
                            continue
                        data = resp.json()
                        items = data.get("jobDetails", [])
                        print(f"    Naukri API '{query}' {city}: {len(items)} items")
                        for item in items[:20]:
                            try:
                                job = self._parse_naukri_item(item, city)
                                if job and is_relevant_job(job["title"]):
                                    jobs.append(job)
                            except Exception:
                                continue
                        await asyncio.sleep(random.uniform(2, 3))
                    except Exception as e:
                        print(f"    Naukri API error '{query}' {city}: {e}")
        return jobs

    async def _scrape_naukri_playwright(self, browser) -> list:
        """Fallback: load real Naukri page and extract jobs via XHR + page state + HTML."""
        jobs = []
        for query in INDIA_QUERIES:
            if len(jobs) >= self.max_jobs_per_source:
                break
            for city in INDIA_LOCATIONS:
                if len(jobs) >= self.max_jobs_per_source:
                    break

                captured = []
                page, context = await self._new_page(browser)

                async def on_response(response, _c=captured):
                    # Catch any successful JSON response from naukri.com
                    if response.status == 200 and "naukri.com" in response.url:
                        ct = response.headers.get("content-type", "")
                        if "json" in ct:
                            try:
                                data = await response.json()
                                # Handle both top-level and nested jobDetails
                                items = (data.get("jobDetails")
                                         or data.get("data", {}).get("jobDetails")
                                         or [])
                                if items:
                                    print(f"      XHR hit: {len(items)} items from {response.url[:80]}")
                                    _c.extend(items)
                            except Exception:
                                pass

                page.on("response", on_response)

                try:
                    slug = query.lower().replace(" ", "-")
                    city_slug = city.lower()
                    url = f"https://www.naukri.com/{slug}-jobs-in-{city_slug}"
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass
                    await asyncio.sleep(random.uniform(2, 4))

                    # If XHR interception got nothing, try page-embedded state
                    if not captured:
                        captured = await self._extract_naukri_page_state(page)

                    # If still nothing, parse rendered HTML
                    if not captured:
                        html = await page.content()
                        html_jobs = self._parse_naukri_html(html, city)
                        print(f"    Naukri HTML '{query}' {city}: {len(html_jobs)} jobs")
                        jobs.extend(html_jobs[:20])
                    else:
                        print(f"    Naukri Playwright '{query}' {city}: {len(captured)} captured")
                        for item in captured[:20]:
                            try:
                                job = self._parse_naukri_item(item, city)
                                if job and is_relevant_job(job["title"]):
                                    jobs.append(job)
                            except Exception:
                                continue
                except Exception as e:
                    print(f"    Naukri Playwright error '{query}' {city}: {e}")
                finally:
                    await context.close()

        return jobs

    async def _extract_naukri_page_state(self, page) -> list:
        """Try to pull jobDetails from Naukri's embedded page state."""
        try:
            items = await page.evaluate("""() => {
                // Next.js embedded data
                const nd = window.__NEXT_DATA__;
                if (nd) {
                    const jobs = nd?.props?.pageProps?.jobDetails
                        || nd?.props?.pageProps?.initialState?.jobSearch?.jobDetails
                        || nd?.props?.pageProps?.dehydratedState?.jobDetails;
                    if (jobs && jobs.length) return jobs;
                }
                // Legacy Naukri global
                if (window.initialState?.jobDetails) return window.initialState.jobDetails;
                if (window.__INITIAL_STATE__?.jobDetails) return window.__INITIAL_STATE__.jobDetails;
                return [];
            }""")
            return items or []
        except Exception:
            return []

    def _parse_naukri_html(self, html: str, city: str) -> list:
        """Parse job cards from Naukri's rendered HTML."""
        soup = BeautifulSoup(html, "lxml")
        jobs = []
        # Naukri job cards: article tags or divs with data-job-id
        cards = (soup.select("article[data-job-id]")
                 or soup.select("div[data-job-id]")
                 or soup.select(".jobTuple")
                 or soup.select(".cust-job-tuple"))
        for card in cards[:25]:
            try:
                title_el = card.select_one("a.title, h2.title a, .title a, [class*='title'] a")
                company_el = card.select_one("a.subTitle, .subTitle, [class*='companyInfo'] a, [class*='company']")
                loc_el = card.select_one(".locWdth, [class*='location'], [class*='loc']")

                title = title_el.get_text(strip=True) if title_el else ""
                company = company_el.get_text(strip=True) if company_el else "Unknown"
                location = loc_el.get_text(strip=True) if loc_el else city
                url = title_el.get("href", "") if title_el else ""
                if url and not url.startswith("http"):
                    url = "https://www.naukri.com" + url

                if not title or not is_relevant_job(title):
                    continue

                jobs.append({
                    "job_id": generate_job_id(url, title, company),
                    "title": title,
                    "company": company,
                    "location": f"{location}, India" if "india" not in location.lower() else location,
                    "description": "",
                    "url": url,
                    "posted_at": datetime.utcnow(),
                    "scraped_at": datetime.utcnow(),
                    "source": "naukri",
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

    def _parse_naukri_item(self, item: dict, city: str) -> dict:
        title = item.get("title", "").strip()
        company = item.get("companyName", "Unknown").strip()
        placeholders = item.get("placeholders", [])
        location = next((p.get("label", city) for p in placeholders if p.get("type") == "location"), city)
        exp_text = next((p.get("label", "") for p in placeholders if p.get("type") == "experience"), "")

        url = item.get("jdURL", "") or ""
        if url and not url.startswith("http"):
            url = "https://www.naukri.com" + url

        description = clean_description(item.get("jobDescription", "") or "")
        if exp_text:
            description = f"Experience: {exp_text}. {description}"

        return {
            "job_id": generate_job_id(url, title, company),
            "title": title,
            "company": company,
            "location": f"{location}, India",
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

    # ── LINKEDIN Guest API (India + US) ──────────────────────────────────────
    # LinkedIn exposes a public guest jobs endpoint used for AJAX pagination.
    # It returns HTML fragments without requiring login.

    async def _scrape_linkedin_guest(self, region: str) -> list:
        jobs = []
        queries = INDIA_QUERIES[:6] if region == "india" else US_QUERIES[:5]
        locations = INDIA_LOCATIONS if region == "india" else ["United States"]

        async with httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=True) as client:
            for query in queries:
                if len(jobs) >= self.max_jobs_per_source:
                    break
                for loc in locations:
                    if len(jobs) >= self.max_jobs_per_source:
                        break
                    try:
                        location_str = f"{loc}, India" if region == "india" else loc
                        url = (
                            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
                            f"?keywords={query.replace(' ', '%20')}"
                            f"&location={location_str.replace(' ', '%20').replace(',', '%2C')}"
                            f"&f_TPR=r259200"   # posted in last 3 days
                            f"&f_E=3%2C4"       # mid-senior + associate level
                            f"&start=0"
                        )
                        if region == "us":
                            url += "&f_WT=2"    # remote only

                        resp = await client.get(url)
                        print(f"    LinkedIn {region} '{query}' {loc}: HTTP {resp.status_code}")
                        if resp.status_code != 200:
                            await asyncio.sleep(random.uniform(2, 4))
                            continue

                        new_jobs = self._parse_linkedin_guest_html(resp.text, region)
                        print(f"    LinkedIn {region} '{query}' {loc}: {len(new_jobs)} jobs")
                        jobs.extend(new_jobs)
                        await asyncio.sleep(random.uniform(2, 4))
                    except Exception as e:
                        print(f"    LinkedIn {region} error '{query}' {loc}: {e}")

        # For US jobs, look up H1B sponsorship
        if region == "us":
            for job in jobs:
                try:
                    job["sponsorship_status"] = await check_h1b_sponsorship(job["company"])
                except Exception:
                    job["sponsorship_status"] = "unknown"

        return jobs[:self.max_jobs_per_source]

    def _parse_linkedin_guest_html(self, html: str, region: str) -> list:
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
                location = location_el.get_text(strip=True) if location_el else (
                    "India" if region == "india" else "Remote, US"
                )
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
                    "region": region,
                    "sponsorship_status": "contract" if region == "india" else "unknown",
                    "contract_type": extract_contract_type(title, ""),
                    "match_score": None,
                    "score_breakdown": None,
                    "gap_analysis": [],
                    "status": "new",
                })
            except Exception:
                continue

        return jobs
