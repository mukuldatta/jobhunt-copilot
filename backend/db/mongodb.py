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


async def get_jobs(skip: int = 0, limit: int = 50, min_score: int = None, status: str = None) -> list:
    db = get_db()
    query = {}
    if min_score is not None:
        query["match_score"] = {"$gte": min_score}
    if status:
        query["status"] = status
    cursor = db.jobs.find(query).sort("scraped_at", -1).skip(skip).limit(limit)
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


# --- H1B Cache ---

async def get_cached_sponsorship(company: str) -> dict:
    db = get_db()
    return await db.h1b_cache.find_one({"company": company}, {"_id": 0})


async def cache_sponsorship(company: str, status: str):
    db = get_db()
    from datetime import datetime
    await db.h1b_cache.replace_one(
        {"company": company},
        {"company": company, "status": status, "cached_at": datetime.utcnow()},
        upsert=True
    )


# --- Login Failure Tracking ---

async def increment_login_failure(platform: str) -> int:
    db = get_db()
    from datetime import datetime
    result = await db.login_failures.find_one_and_update(
        {"platform": platform},
        {"$inc": {"count": 1}, "$set": {"last_failed_at": datetime.utcnow()}},
        upsert=True,
        return_document=True,
    )
    return result.get("count", 1) if result else 1


async def reset_login_failures(platform: str):
    db = get_db()
    await db.login_failures.update_one(
        {"platform": platform},
        {"$set": {"count": 0}},
        upsert=True,
    )


async def get_login_failures(platform: str) -> int:
    db = get_db()
    doc = await db.login_failures.find_one({"platform": platform})
    return doc.get("count", 0) if doc else 0


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
