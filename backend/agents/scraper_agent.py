import os
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

        linkedin_india = await self._scrape_linkedin_guest(region="india")
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
        async with async_playwright() as pw:
            browser = await self._launch_headed(pw)
            if browser is None:
                return []
            context = await browser.new_context(
                locale="en-IN",
                viewport={"width": 1360, "height": 900},
            )
            page = await context.new_page()
            try:
                for query in INDIA_QUERIES:
                    if len(jobs) >= self.max_jobs_per_source:
                        break
                    for city in INDIA_LOCATIONS:
                        if len(jobs) >= self.max_jobs_per_source:
                            break
                        try:
                            rows = await self._load_naukri_page(page, query, city)
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
                            print(f"    Naukri error '{query}' {city}: {e}")
            finally:
                await context.close()
                await browser.close()
        return jobs[:self.max_jobs_per_source]

    async def _launch_headed(self, pw):
        """Headed real Chrome beats Akamai; fall back to headed Chromium."""
        try:
            return await pw.chromium.launch(headless=False, channel="chrome")
        except Exception as e:
            print(f"    Naukri: real Chrome unavailable ({e}); trying headed Chromium")
        try:
            return await pw.chromium.launch(headless=False)
        except Exception as e:
            print(f"    Naukri: headed browser unavailable ({e}); skipping Naukri")
            return None

    async def _load_naukri_page(self, page, query: str, city: str) -> list:
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
            title = await page.title()
            if "access denied" in title.lower():
                print(f"    Naukri BLOCKED on '{query}' {city} (Access Denied) "
                      f"— need a residential IP + headed browser")
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

    # ── INDEED INDIA (httpx) ────────────────────────────────────────────────
    # in.indeed.com is far less aggressive than Naukri about blocking servers.

    async def _scrape_indeed_india(self) -> list:
        jobs = []
        headers = {
            "User-Agent": HEADERS["User-Agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9",
            "Referer": "https://in.indeed.com/",
        }
        async with httpx.AsyncClient(headers=headers, timeout=20, follow_redirects=True) as client:
            for query in INDIA_QUERIES[:6]:
                if len(jobs) >= self.max_jobs_per_source:
                    break
                for city in INDIA_LOCATIONS:
                    if len(jobs) >= self.max_jobs_per_source:
                        break
                    try:
                        resp = await client.get(
                            "https://in.indeed.com/jobs",
                            params={"q": query, "l": city, "fromage": "3", "sort": "date"},
                        )
                        print(f"    Indeed India '{query}' {city}: HTTP {resp.status_code}")
                        if resp.status_code != 200:
                            await asyncio.sleep(random.uniform(2, 3))
                            continue
                        new_jobs = self._parse_indeed_india_html(resp.text, city)
                        print(f"    Indeed India '{query}' {city}: {len(new_jobs)} jobs")
                        jobs.extend(new_jobs)
                        await asyncio.sleep(random.uniform(2, 3))
                    except Exception as e:
                        print(f"    Indeed India error '{query}' {city}: {e}")
        return jobs[:self.max_jobs_per_source]

    def _parse_indeed_india_html(self, html: str, city: str) -> list:
        soup = BeautifulSoup(html, "lxml")
        jobs = []
        # Indeed job cards — try multiple selector strategies
        cards = (soup.select("div.job_seen_beacon")
                 or soup.select("div[data-jk]")
                 or soup.select("li.css-1ac2h1w")
                 or soup.select("div.tapItem"))
        for card in cards[:20]:
            try:
                title_el = (card.select_one("h2.jobTitle a span[title]")
                            or card.select_one("h2.jobTitle a")
                            or card.select_one("a[data-jk] span"))
                company_el = (card.select_one("span.companyName")
                              or card.select_one("[data-testid='company-name']")
                              or card.select_one(".companyName"))
                loc_el = (card.select_one("div.companyLocation")
                          or card.select_one("[data-testid='text-location']")
                          or card.select_one(".companyLocation"))
                link_el = card.select_one("h2.jobTitle a, a[data-jk]")

                title = (title_el.get("title") or title_el.get_text(strip=True)) if title_el else ""
                company = company_el.get_text(strip=True) if company_el else "Unknown"
                location = loc_el.get_text(strip=True) if loc_el else f"{city}, India"
                href = link_el.get("href", "") if link_el else ""
                url = f"https://in.indeed.com{href}" if href.startswith("/") else href

                if not title or not is_relevant_job(title):
                    continue

                jobs.append({
                    "job_id": generate_job_id(url, title, company),
                    "title": title,
                    "company": company,
                    "location": location if location else f"{city}, India",
                    "description": "",
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
                })
            except Exception:
                continue
        return jobs

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
