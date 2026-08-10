import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

_client: AsyncIOMotorClient = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        uri = os.environ.get("MONGODB_URI")
        if not uri:
            raise RuntimeError("MONGODB_URI is not set in your .env file")
        _client = AsyncIOMotorClient(uri)
    return _client


def get_db():
    return get_client()["jobhunt"]


# --- Jobs ---

async def insert_job(job: dict) -> bool:
    db = get_db()
    existing = await db.jobs.find_one({"job_id": job["job_id"]})
    if existing:
        return False
    await db.jobs.insert_one(job)
    return True


_INDIA_CITIES = "India|Hyderabad|Bangalore|Bengaluru|Pune|Chennai|Mumbai|Delhi|Noida|Gurugram|Gurgaon"


async def get_jobs(
    skip: int = 0,
    limit: int = 50,
    min_score: int = None,
    status: str = None,
    source: str = None,
    sponsorship: str = None,
    sort_by: str = "date_desc",
    search: str = None,
    region: str = None,
) -> list:
    db = get_db()
    query = {}
    if min_score is not None:
        query["match_score"] = {"$gte": min_score}
    if status:
        query["status"] = status
    if source:
        query["source"] = source
    if sponsorship:
        query["sponsorship_status"] = sponsorship
    if search:
        query["$or"] = [
            {"title": {"$regex": search, "$options": "i"}},
            {"company": {"$regex": search, "$options": "i"}},
        ]
    if region == "india":
        query["$and"] = query.pop("$and", []) + [{"$or": [
            {"region": "india"},
            {"location": {"$regex": _INDIA_CITIES, "$options": "i"}},
        ]}]
    elif region == "us":
        query["$and"] = query.pop("$and", []) + [{"$or": [
            {"region": "us"},
            {"region": {"$exists": False}, "location": {"$not": {"$regex": _INDIA_CITIES, "$options": "i"}}},
        ]}]
    sort_map = {
        "date_desc": ("scraped_at", -1),
        "date_asc": ("scraped_at", 1),
        "score_desc": ("match_score", -1),
        "score_asc": ("match_score", 1),
    }
    sort_field, sort_dir = sort_map.get(sort_by, ("scraped_at", -1))
    cursor = db.jobs.find(query).sort(sort_field, sort_dir).skip(skip).limit(limit)
    jobs = []
    async for job in cursor:
        job["id"] = str(job.pop("_id"))
        jobs.append(job)
    return jobs


async def get_job(job_id: str) -> dict:
    db = get_db()
    job = await db.jobs.find_one({"job_id": job_id})
    if job:
        job["id"] = str(job.pop("_id"))
    return job


async def update_job(job_id: str, updates: dict) -> bool:
    db = get_db()
    result = await db.jobs.update_one({"job_id": job_id}, {"$set": updates})
    return result.modified_count > 0


async def get_unscored_jobs() -> list:
    db = get_db()
    cursor = db.jobs.find({"match_score": None})
    jobs = []
    async for job in cursor:
        job["id"] = str(job.pop("_id"))
        jobs.append(job)
    return jobs


async def get_high_match_jobs(threshold: int = 70) -> list:
    db = get_db()
    from datetime import datetime, timedelta
    two_hours_ago = datetime.utcnow() - timedelta(hours=2)
    cursor = db.jobs.find({
        "match_score": {"$gte": threshold},
        "scraped_at": {"$gte": two_hours_ago},
        "status": "new"
    })
    jobs = []
    async for job in cursor:
        job["id"] = str(job.pop("_id"))
        jobs.append(job)
    return jobs


async def get_apply_candidates(min_score: int = 70, region: str = "india", limit: int = 5) -> list:
    """
    Jobs eligible for auto-apply: still 'new', scored at/above the threshold, in
    the target region. Excludes anything already applied/applying/manual/failed
    (those are no longer status 'new'). Highest score first.
    """
    db = get_db()
    query = {"status": "new", "match_score": {"$gte": min_score}}
    if region == "india":
        query["$or"] = [
            {"region": "india"},
            {"location": {"$regex": _INDIA_CITIES, "$options": "i"}},
        ]
    cursor = db.jobs.find(query).sort("match_score", -1).limit(limit)
    jobs = []
    async for job in cursor:
        job["id"] = str(job.pop("_id"))
        jobs.append(job)
    return jobs


async def count_applications_today() -> int:
    from datetime import datetime
    db = get_db()
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return await db.applications.count_documents({
        "applied_at": {"$gte": start},
        "status": {"$ne": "saved"},
    })


async def delete_old_jobs(days: int = 7) -> int:
    db = get_db()
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = await db.jobs.delete_many({"scraped_at": {"$lt": cutoff}})
    return result.deleted_count


# --- Applications ---

async def insert_application(application: dict) -> str:
    db = get_db()
    result = await db.applications.insert_one(application)
    return str(result.inserted_id)


async def get_applications(skip: int = 0, limit: int = 50) -> list:
    db = get_db()
    cursor = db.applications.find().sort("applied_at", -1).skip(skip).limit(limit)
    apps = []
    async for app in cursor:
        app["id"] = str(app.pop("_id"))
        apps.append(app)
    return apps


async def get_application_by_job_id(job_id: str) -> dict:
    db = get_db()
    return await db.applications.find_one({"job_id": job_id})


async def claim_job_for_apply(job_id: str) -> bool:
    """
    Atomically transition a job into the 'applying' state. Returns True only if
    this call won the claim; returns False if the job is already applied or an
    apply is already in progress. This is the dedup guard that stops an
    autonomous loop from applying to the same job twice.
    """
    from datetime import datetime
    db = get_db()
    res = await db.jobs.find_one_and_update(
        {"job_id": job_id, "status": {"$nin": ["applied", "applying"]}},
        {"$set": {"status": "applying", "apply_started_at": datetime.utcnow()}},
    )
    return res is not None


async def finish_job_apply(job_id: str, status: str) -> bool:
    """Set the terminal job status after an apply attempt."""
    from datetime import datetime
    db = get_db()
    result = await db.jobs.update_one(
        {"job_id": job_id},
        {"$set": {"status": status, "apply_finished_at": datetime.utcnow()}},
    )
    return result.modified_count > 0


async def record_application(job_id: str, doc: dict) -> str:
    """Idempotent upsert of an application, keyed by job_id."""
    db = get_db()
    payload = dict(doc)
    payload["job_id"] = job_id
    result = await db.applications.update_one(
        {"job_id": job_id}, {"$set": payload}, upsert=True
    )
    if result.upserted_id:
        return str(result.upserted_id)
    existing = await db.applications.find_one({"job_id": job_id}, {"_id": 1})
    return str(existing["_id"]) if existing else ""


async def get_application(application_id: str) -> dict:
    from bson import ObjectId
    db = get_db()
    app = await db.applications.find_one({"_id": ObjectId(application_id)})
    if app:
        app["id"] = str(app.pop("_id"))
    return app


async def update_application(application_id: str, updates: dict) -> bool:
    from bson import ObjectId
    db = get_db()
    result = await db.applications.update_one(
        {"_id": ObjectId(application_id)},
        {"$set": updates}
    )
    return result.modified_count > 0


# --- Resume ---

async def save_resume(resume: dict):
    db = get_db()
    await db.resume.replace_one({}, resume, upsert=True)


async def get_resume() -> dict:
    db = get_db()
    return await db.resume.find_one({}, {"_id": 0})


# --- Auth session state (manual login persisted in browser profiles) ---

async def save_auth_state(platform: str):
    from datetime import datetime
    db = get_db()
    await db.auth_state.replace_one(
        {"platform": platform},
        {"platform": platform, "logged_in_at": datetime.utcnow()},
        upsert=True,
    )


async def get_auth_states() -> dict:
    db = get_db()
    out = {}
    async for doc in db.auth_state.find({}, {"_id": 0}):
        out[doc["platform"]] = doc.get("logged_in_at")
    return out


async def clear_auth_state(platform: str):
    db = get_db()
    await db.auth_state.delete_one({"platform": platform})


# --- Stats ---

async def get_stats() -> dict:
    try:
        db = get_db()
    except RuntimeError:
        return {"total_jobs": 0, "high_match": 0, "medium_match": 0, "low_match": 0, "applied": 0, "interviews": 0, "last_scraped": None}
    total = await db.jobs.count_documents({})
    high = await db.jobs.count_documents({"match_score": {"$gte": 70}})
    medium = await db.jobs.count_documents({"match_score": {"$gte": 50, "$lt": 70}})
    low = await db.jobs.count_documents({"match_score": {"$lt": 50, "$ne": None}})
    applied = await db.applications.count_documents({"status": {"$ne": "saved"}})
    interviews = await db.applications.count_documents({
        "status": {"$in": ["recruiter_screen", "technical", "final_round"]}
    })
    last_job = await db.jobs.find_one({}, sort=[("scraped_at", -1)])
    return {
        "total_jobs": total,
        "high_match": high,
        "medium_match": medium,
        "low_match": low,
        "applied": applied,
        "interviews": interviews,
        "last_scraped": last_job["scraped_at"] if last_job else None,
    }
