import httpx
import os
from db.mongodb import get_cached_sponsorship, cache_sponsorship
from datetime import datetime, timedelta


H1B_STRONG_SPONSORS = {
    "google", "microsoft", "amazon", "meta", "apple", "netflix", "uber",
    "lyft", "airbnb", "stripe", "palantir", "databricks", "snowflake",
    "salesforce", "oracle", "ibm", "intel", "nvidia", "amd", "qualcomm",
    "deloitte", "accenture", "infosys", "tcs", "wipro", "cognizant",
    "capgemini", "hcl", "tech mahindra", "jpmorgan", "goldman sachs",
    "morgan stanley", "citigroup", "bank of america", "wells fargo",
    "bloomberg", "mastercard", "visa", "paypal", "square", "twilio",
    "datadog", "splunk", "pagerduty", "elastic", "mongodb", "redis",
    "confluent", "hashicorp", "vmware", "citrix", "juniper", "cisco",
    "adobe", "autodesk", "intuit", "workday", "servicenow", "okta",
    "zendesk", "hubspot", "atlassian", "github", "gitlab", "bitbucket",
}

CONTRACT_KEYWORDS = [
    "staffing", "consulting", "solutions", "technologies", "systems",
    "it services", "talent", "resource", "contract",
]


async def check_h1b_sponsorship(company: str) -> str:
    company_lower = company.lower().strip()

    cached = await get_cached_sponsorship(company_lower)
    if cached:
        cached_at = cached.get("cached_at", datetime.min)
        if isinstance(cached_at, datetime) and datetime.utcnow() - cached_at < timedelta(days=30):
            return cached["status"]

    status = _quick_classify(company_lower)

    if status == "unknown":
        status = await _query_h1b_data(company)

    await cache_sponsorship(company_lower, status)
    return status


def _quick_classify(company_lower: str) -> str:
    for known in H1B_STRONG_SPONSORS:
        if known in company_lower:
            return "strong"

    for kw in CONTRACT_KEYWORDS:
        if kw in company_lower:
            return "contract"

    return "unknown"


async def _query_h1b_data(company: str) -> str:
    try:
        url = f"https://h1bdata.info/index.php?em={company.replace(' ', '+')}&job=&city=&year=2023"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if response.status_code == 200 and company.lower() in response.text.lower():
                return "moderate"
    except Exception:
        pass
    return "none"
