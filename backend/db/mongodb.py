import os
import re
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from utils.question_key import question_key
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

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
    from utils.job_parser import dedup_key
    db = get_db()
    if await db.jobs.find_one({"job_id": job["job_id"]}):
        return False

    # Same role re-listed under a different URL — skip it, but let a listing
    # with a real description replace one scraped without it (better scoring).
    key = dedup_key(job.get("title", ""), job.get("company", ""))
    job["dedup_key"] = key
    twin = await db.jobs.find_one({"dedup_key": key})
    if twin:
        if job.get("description") and not (twin.get("description") or "").strip():
            await db.jobs.update_one(
                {"_id": twin["_id"]},
                {"$set": {"description": job["description"], "url": job.get("url", twin.get("url")),
                          "match_score": None, "score_breakdown": None, "gap_analysis": []}},
            )
        return False

    await db.jobs.insert_one(job)
    return True


def _india_regex() -> str:
    from utils.job_parser import india_location_regex
    return india_location_regex()


_INDIA_CITIES = _india_regex()


def _jobs_query(
    min_score: int = None,
    status: str = None,
    source: str = None,
    sponsorship: str = None,
    search: str = None,
    region: str = None,
) -> dict:
    """The filter shared by get_jobs and count_jobs, so a page and its total
    can never be counted against different criteria."""
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
        # Escaped: this is a user-typed string from the Review search box, not a
        # pattern. Unescaped, a stray "(" is a 500 and a nested quantifier is a
        # ReDoS aimed at the database.
        term = re.escape(search)
        query["$or"] = [
            {"title": {"$regex": term, "$options": "i"}},
            {"company": {"$regex": term, "$options": "i"}},
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
    return query


_SORTS = {
    "date_desc": ("scraped_at", -1),
    "date_asc": ("scraped_at", 1),
    "score_desc": ("match_score", -1),
    "score_asc": ("match_score", 1),
}


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
    query = _jobs_query(min_score, status, source, sponsorship, search, region)
    sort_field, sort_dir = _SORTS.get(sort_by, ("scraped_at", -1))
    cursor = db.jobs.find(query).sort(sort_field, sort_dir).skip(skip).limit(limit)
    jobs = []
    async for job in cursor:
        job["id"] = str(job.pop("_id"))
        jobs.append(job)
    return jobs


async def count_jobs(
    min_score: int = None,
    status: str = None,
    source: str = None,
    sponsorship: str = None,
    search: str = None,
    region: str = None,
) -> int:
    """How many jobs match the filter, ignoring the page window. Review showed
    the page size instead, so a filter matching 400 jobs read as "25+ jobs"."""
    db = get_db()
    return await db.jobs.count_documents(
        _jobs_query(min_score, status, source, sponsorship, search, region)
    )


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


async def get_jobs_needing_score() -> list:
    """
    Never-scored jobs, plus jobs whose score came from a superseded scorer.

    get_apply_candidates requires the current scorer_version, so selecting only
    match_score None made a version bump permanently drain the apply queue: an
    old job was neither eligible to apply nor eligible to be re-scored, and
    nothing else would ever look at it again. Bumping SCORER_VERSION is the
    migration; this selector is what carries it out.

    Never-scored jobs sort first so a backfill of the archive cannot starve
    today's scrape behind it.
    """
    from utils.score_rules import SCORER_VERSION

    db = get_db()
    cursor = db.jobs.find({"$or": [
        {"match_score": None},
        {"scorer_version": {"$ne": SCORER_VERSION}},
    ]})
    jobs = []
    async for job in cursor:
        job["id"] = str(job.pop("_id"))
        jobs.append(job)
    jobs.sort(key=lambda j: j.get("match_score") is not None)
    return jobs


async def get_high_match_jobs(threshold: int = 70) -> list:
    """
    High matches you have not been alerted about yet.

    Alerting used to key off status: it selected status "new" and then wrote
    "reviewed", which quietly deleted the job from the auto-apply pool, because
    get_apply_candidates requires "new". Two unrelated facts — "has the user
    been told?" and "is this eligible to apply?" — were sharing one field, and
    alerting won. alerted_at now carries the first, and status is left alone.
    """
    db = get_db()
    from datetime import datetime, timedelta
    two_hours_ago = datetime.utcnow() - timedelta(hours=2)
    cursor = db.jobs.find({
        "match_score": {"$gte": threshold},
        "scraped_at": {"$gte": two_hours_ago},
        "alerted_at": {"$exists": False},
    })
    jobs = []
    async for job in cursor:
        job["id"] = str(job.pop("_id"))
        jobs.append(job)
    return jobs


async def _max_apply_years():
    """
    The most years a posting may demand before auto-apply refuses it.

    The candidate's own years plus a stretch, because postings routinely ask
    for more than the job needs and a year or two over is worth a shot. Set
    APPLY_YEARS_STRETCH to widen or narrow it; None when experience has never
    been filled in, which disables the gate rather than guessing.
    """
    profile = await get_apply_profile() or {}
    raw = profile.get("total_years_experience") or os.getenv("APPLY_YEARS_EXPERIENCE")
    try:
        mine = float(raw)
    except (TypeError, ValueError):
        return None
    try:
        stretch = float(os.getenv("APPLY_YEARS_STRETCH", "2"))
    except ValueError:
        stretch = 2.0
    return mine + stretch


async def get_apply_candidates(min_score: int = 70, region: str = "india", limit: int = 5,
                               exclude_sources: list = None) -> list:
    """
    Jobs eligible for auto-apply: still 'new', scored at/above the threshold, in
    the target region. Excludes anything already applied/applying/manual/failed
    (those are no longer status 'new'). Highest score first.

    exclude_sources drops boards we never submit to, so they cannot take a slot
    in a run that is capped at a handful of applications. Known-external
    postings are dropped for the same reason once apply_type has been recorded.
    """
    db = get_db()
    # Never offer a posting a previous run proved we cannot submit. $nin also
    # matches documents with no apply_type at all, which is what we want: an
    # unclassified job is a candidate, and the preflight settles it cheaply.
    from utils.score_rules import SCORER_VERSION

    # Requiring the current scorer version is the guardrail that makes an armed
    # batch safe on its own. A score from a superseded scorer is not a weaker
    # signal, it is an untrusted one — and this queue spends irreversible
    # applications strictly in score order. Stale jobs re-enter once re-scored.
    # Only postings we can actually submit to: LinkedIn Easy Apply and Naukri's
    # own apply. A hand-off to the employer's careers site is not a weaker
    # candidate, it is a different task — one for a human — and offering it here
    # spends a browser launch and a slot in a capped batch to rediscover
    # something already recorded. Two of a 20-job batch went that way.
    #
    # "Submittable" means either a run confirmed it, or the scrape saw the
    # board's own marker. The hint has agreed with the confirmed answer on every
    # job where both exist — 34 external, 20 in-platform, no disagreements — so
    # it is trusted to exclude, not merely to reorder. A job with neither is not
    # offered; services.classify_service settles those without applying.
    query = {"status": "new", "match_score": {"$gte": min_score},
             "scorer_version": SCORER_VERSION,
             "apply_type": {"$nin": ["external", "expired"]}}
    # In $and, not $or: the region filter below assigns query["$or"] outright
    # and would silently drop this one.
    query["$and"] = query.get("$and", []) + [{"$or": [
        {"apply_type": "in_platform"},
        {"apply_type": None, "apply_type_hint": "in_platform"},
        {"apply_type": {"$exists": False}, "apply_type_hint": "in_platform"},
    ]}]

    # A role years beyond the candidate's experience is not a near miss to be
    # settled by score — it is somebody else's job, and an application spent on
    # it is wasted. The score already penalises the gap; this stops a very
    # strong skills overlap from carrying a 10-year role over the threshold
    # anyway. Jobs whose postings never state a requirement are still eligible.
    max_years = await _max_apply_years()
    if max_years is not None:
        query["$and"] = query.get("$and", []) + [{"$or": [
            {"required_years": None},
            {"required_years": {"$exists": False}},
            {"required_years": {"$lte": max_years}},
        ]}]
    if exclude_sources:
        query["source"] = {"$nin": list(exclude_sources)}

    # Employers you have excluded. Filtered here rather than only at apply time
    # so they never take a slot in a capped batch either.
    from platforms import excluded_company_pattern
    blocked = excluded_company_pattern()
    if blocked:
        query["company"] = {"$not": {"$regex": blocked, "$options": "i"}}
    if region == "india":
        query["$or"] = [
            {"region": "india"},
            {"location": {"$regex": _INDIA_CITIES, "$options": "i"}},
        ]
    # Take a wider slice than needed, then order it. apply_type_hint is a scrape
    # -time guess (~4/5 accurate), so it may reorder the queue but must never
    # exclude: a wrong "external" hint would otherwise bury a good job forever,
    # and nothing would ever correct it. Confirmed apply_type, set by an actual
    # run, is what the query above filters on.
    # Jobs a previous run proved submittable are fetched in their own right,
    # not hoped for inside a score-ranked window. The ranking below prefers
    # them over unclassified jobs regardless of score — but it can only order
    # what it was given, and the window is filled by score alone. With a small
    # per_run the window is small, so a confirmed 91% job lost its place to
    # twenty unclassified jobs scoring 94-98% and never reached the ranking at
    # all. A batch of two then spent both slots re-discovering hand-offs while
    # the jobs we already knew we could submit to sat behind them.
    rows, seen = [], set()
    async def _take(cur):
        async for job in cur:
            if job["job_id"] in seen:
                continue
            seen.add(job["job_id"])
            job["id"] = str(job.pop("_id"))
            rows.append(job)

    await _take(db.jobs.find({**query, "apply_type": "in_platform"})
                .sort("match_score", -1).limit(max(limit, 10)))
    await _take(db.jobs.find(query).sort("match_score", -1).limit(max(limit * 4, 20)))

    def rank(job):
        hint = job.get("apply_type_hint")
        confirmed = job.get("apply_type")
        # 0 = known good, 1 = unknown, 2 = probably a hand-off
        tier = 0 if confirmed == "in_platform" else (2 if hint == "external" else
                                                     0 if hint == "in_platform" else 1)
        # A posting that has already been tried goes behind one that has not,
        # whatever it scores. Three high-scoring jobs that could not succeed —
        # one deferred on a question, two refused by the resume guard — kept
        # returning to the queue, sorted straight back to the top on score, and
        # occupied two entire cycles while 25 untried candidates waited behind
        # them. The retry still happens; it just stops being first in line.
        attempts = job.get("apply_attempts") or 0
        return (tier, attempts, -(job.get("match_score") or 0))

    rows.sort(key=rank)
    return rows[:limit]


async def count_applications_today() -> int:
    """
    Applications since *local* midnight.

    This backs the daily cap and the "applied today" readout in the sidebar.
    Anchored on UTC midnight it rolled over at 05:30 IST, so a morning's
    applications counted against the previous day's budget.
    """
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    db = get_db()
    tz = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Kolkata"))
    start_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    # applied_at is stored naive-UTC, so compare against a naive-UTC instant.
    start = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    return await db.applications.count_documents({
        "applied_at": {"$gte": start},
        "status": {"$ne": "saved"},
    })


async def delete_old_jobs(days: int = 7) -> int:
    """
    Drop stale postings — but never one an application points at.

    The pipeline board keeps no copy of a job's title or company: it joins back
    to this collection (see get_applications). Deleting a job you applied to
    therefore erased the identity of the application too, and a week after
    applying the board showed a bare job_id. Age alone is not grounds for
    removal; being spoken for outranks being old.
    """
    db = get_db()
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)
    spoken_for = await db.applications.distinct("job_id")
    result = await db.jobs.delete_many({
        "scraped_at": {"$lt": cutoff},
        "status": {"$nin": ["applied", "applying"]},
        "job_id": {"$nin": spoken_for},
    })
    return result.deleted_count


# --- Applications ---

async def get_applications(skip: int = 0, limit: int = 50) -> list:
    db = get_db()
    cursor = db.applications.find().sort("applied_at", -1).skip(skip).limit(limit)
    apps = []
    async for app in cursor:
        app["id"] = str(app.pop("_id"))
        apps.append(app)

    # The pipeline board shows real job titles, not job_ids like
    # "linkedin_4021887733" — so resolve them here in one extra query rather
    # than making the client fetch the whole jobs collection to join it.
    job_ids = [a["job_id"] for a in apps if a.get("job_id")]
    if job_ids:
        projection = {"job_id": 1, "title": 1, "company": 1, "location": 1,
                      "match_score": 1, "source": 1, "url": 1, "_id": 0}
        jobs = {}
        async for job in db.jobs.find({"job_id": {"$in": job_ids}}, projection):
            jobs[job["job_id"]] = job
        for app in apps:
            job = jobs.get(app.get("job_id"))
            if job:
                # Never let the job document clobber the application's own
                # fields (status, applied_at, id) — only fill in what's missing.
                for key, value in job.items():
                    app.setdefault(key, value)
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


async def release_stale_apply_claims(older_than_minutes: int = 60) -> int:
    """
    Return jobs abandoned mid-apply to the queue. Returns how many were freed.

    claim_job_for_apply moves a job to 'applying' and finish_job_apply moves it
    out. Nothing moved it if the process died in between — a crash, or simply
    restarting the backend during a run — and the job then sits in 'applying'
    forever: excluded from the apply queue, which wants 'new', and from every
    other view that reasons about real states. Three jobs were in exactly that
    position, invisible to everything.

    Two guards, because releasing a claim is what makes a double application
    possible:

    - The age. An apply that is genuinely still running can take a while — a
      CAPTCHA wait plus a sign-in wait is twelve minutes before any tailoring —
      so the default is an hour, far outside any legitimate single job.
    - The application record. If one exists the job was submitted and the crash
      happened after the irreversible part, so it is completed rather than
      released. Only a job with no record goes back in the queue.
    """
    from datetime import datetime, timedelta
    db = get_db()
    cutoff = datetime.utcnow() - timedelta(minutes=older_than_minutes)
    freed = 0
    query = {"status": "applying",
             "$or": [{"apply_started_at": {"$lt": cutoff}},
                     # Claimed before apply_started_at was stamped at all.
                     {"apply_started_at": {"$exists": False}}]}
    async for job in db.jobs.find(query, {"job_id": 1, "title": 1, "_id": 0}):
        job_id = job.get("job_id")
        if not job_id:
            continue
        applied = await db.applications.find_one({"job_id": job_id}, {"_id": 1})
        await db.jobs.update_one(
            {"job_id": job_id, "status": "applying"},
            {"$set": {"status": "applied" if applied else "new",
                      "apply_finished_at": datetime.utcnow()}},
        )
        freed += 1
    return freed


async def finish_job_apply(job_id: str, status: str) -> bool:
    """Set the terminal job status after an apply attempt."""
    from datetime import datetime
    db = get_db()
    result = await db.jobs.update_one(
        {"job_id": job_id},
        {"$set": {"status": status, "apply_finished_at": datetime.utcnow()}},
    )
    return result.modified_count > 0


async def bump_apply_attempt(job_id: str) -> int:
    """
    Count one ambiguous apply attempt and return the new total.

    Only inconclusive outcomes call this — a posting we could not read, not one
    we read and rejected. It is what lets an unreadable job be retried a bounded
    number of times instead of either being burned on the first bad render or
    retried forever.
    """
    db = get_db()
    doc = await db.jobs.find_one_and_update(
        {"job_id": job_id},
        {"$inc": {"apply_attempts": 1}},
        projection={"apply_attempts": 1, "_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    return (doc or {}).get("apply_attempts", 1)


async def record_application(job_id: str, doc: dict) -> str:
    """Idempotent upsert of an application, keyed by job_id."""
    db = get_db()
    payload = dict(doc)
    payload["job_id"] = job_id

    # Snapshot what you applied to, at the moment you applied. get_applications
    # otherwise reads these off the live job document, which makes the record of
    # an application depend on the posting still existing — and postings do go
    # away (cleanup, or a re-scrape that folds a twin into another row). The
    # join still runs and still fills gaps; this just means it has nothing left
    # to fill.
    if not payload.get("title"):
        job = await db.jobs.find_one(
            {"job_id": job_id},
            {"title": 1, "company": 1, "location": 1, "url": 1,
             "match_score": 1, "source": 1, "_id": 0},
        )
        if job:
            payload.update({k: v for k, v in job.items() if v is not None})
    result = await db.applications.update_one(
        {"job_id": job_id}, {"$set": payload}, upsert=True
    )
    if result.upserted_id:
        return str(result.upserted_id)
    existing = await db.applications.find_one({"job_id": job_id}, {"_id": 1})
    return str(existing["_id"]) if existing else ""


async def update_application(application_id: str, updates: dict) -> bool:
    """
    True when the application exists, whether or not the write changed it.

    modified_count is 0 when the new status equals the old one, which made
    re-selecting the current status in the Pipeline dropdown return a 404 for
    a row plainly on screen. Existence is the question the caller is asking.
    """
    from bson import ObjectId
    from bson.errors import InvalidId

    db = get_db()
    try:
        oid = ObjectId(application_id)
    except (InvalidId, TypeError):
        return False
    result = await db.applications.update_one({"_id": oid}, {"$set": updates})
    return result.matched_count > 0


# --- Resume ---

async def save_resume(resume: dict):
    from datetime import datetime, timezone
    db = get_db()
    # Stamped here rather than by the caller, because this is the single write
    # and a caller that forgets costs more than a missing field: it is what
    # `resume_service._resume_version` keys the tailored-resume cache on, and
    # without it that key falls back to len(parsed_text). Two resumes of the
    # same length would then share a key, and every tailored resume already
    # stored would be served against the new document without re-tailoring.
    await db.resume.replace_one(
        {}, {**resume, "uploaded_at": datetime.now(timezone.utc)}, upsert=True)


async def get_resume() -> dict:
    db = get_db()
    return await db.resume.find_one({}, {"_id": 0})


# --- Apply-run history ---

async def record_run(summary: dict) -> str:
    """
    Persist the outcome of an auto-apply cycle. The orchestrator builds a full
    per-job log and, until now, printed it to stdout and dropped it — which for
    a system whose premise is "it works while you are away" is the wrong end of
    the telescope. Today reads the latest of these.
    """
    from datetime import datetime
    db = get_db()
    doc = dict(summary)
    doc["finished_at"] = datetime.utcnow()
    result = await db.runs.insert_one(doc)
    return str(result.inserted_id)


async def get_last_run() -> dict:
    db = get_db()
    doc = await db.runs.find_one({}, sort=[("finished_at", -1)])
    if doc:
        doc.pop("_id", None)
    return doc or {}


async def _ensure_index(coll, keys, name, **opts):
    """
    Create one index, reconciling with whatever is already on the collection.

    Matching is on the *key spec*, not the name: an index built by an earlier
    version (or by hand) carries Mongo's auto-generated name, so asking for the
    same keys under our name raises IndexOptionsConflict, and dropping by our
    name finds nothing to drop. Three cases:

      - same keys, same uniqueness  -> nothing to do
      - same keys, different options -> drop the one that exists, rebuild
      - a unique index the data cannot satisfy -> fall back to non-unique

    None of these is worth refusing to boot over, so a failure is reported and
    the app continues with a slower query rather than no app at all.
    """
    from pymongo.errors import OperationFailure

    want_unique = bool(opts.get("unique"))
    existing = await coll.index_information()

    for got_name, info in existing.items():
        if [tuple(k) for k in info.get("key", [])] != [tuple(k) for k in keys]:
            continue
        if bool(info.get("unique")) == want_unique:
            return                                    # already exactly right
        logger.info("Index %s.%s: rebuilding as %s", coll.name, got_name,
                    "unique" if want_unique else "non-unique")
        await coll.drop_index(got_name)
        break

    try:
        await coll.create_index(keys, name=name, **opts)
    except OperationFailure as e:
        if not (want_unique and e.code in (11000, 85, 86)):
            raise
        # Duplicates already in the collection. The index is still worth having
        # for speed; uniqueness needs the duplicates cleaned up first.
        logger.warning(
            "Index %s.%s: duplicates present, creating non-unique instead (%s)",
            coll.name, name, e,
        )
        await coll.create_index(keys, name=name)


async def ensure_indexes():
    """
    Indexes for the queries that run on every cycle. get_apply_candidates
    filters on status + score + apply_type and sorts by score, which is a
    collection scan and an in-memory sort without this.

    jobs.job_id is the hottest lookup in the application — insert_job, get_job,
    update_job, claim_job_for_apply, finish_job_apply and the get_applications
    join all key on it — and it is unique by construction, so the index both
    speeds those up and closes the read-then-write race in insert_job.
    """
    db = get_db()
    await _ensure_index(db.jobs, [("job_id", 1)], "job_id", unique=True)
    await _ensure_index(db.jobs, [("dedup_key", 1)], "dedup_key")
    await _ensure_index(db.jobs, [("scraped_at", -1)], "scraped_at")
    await _ensure_index(db.jobs, [("match_score", -1)], "match_score")
    await _ensure_index(db.jobs, [("status", 1), ("match_score", -1)], "apply_candidates")
    await _ensure_index(db.jobs, [("apply_type", 1)], "apply_type")
    await _ensure_index(db.jobs, [("source", 1), ("match_score", -1)], "source_score")
    await _ensure_index(db.applications, [("job_id", 1)], "job_id", unique=True)
    await _ensure_index(db.applications, [("applied_at", -1)], "applied_at")
    await _ensure_index(db.applications, [("status", 1)], "status")
    await _ensure_index(db.pending_questions, [("question_norm", 1)], "question_norm")
    await _ensure_index(db.runs, [("finished_at", -1)], "recent_runs")


# --- Agent rules (Setup > Agent rules) ---

async def get_settings() -> dict:
    db = get_db()
    return await db.settings.find_one({}, {"_id": 0}) or {}


async def save_settings(values: dict):
    from datetime import datetime
    db = get_db()
    payload = dict(values)
    payload["updated_at"] = datetime.utcnow()
    await db.settings.update_one({}, {"$set": payload}, upsert=True)


# --- Apply profile (questionnaire) + learned answers ---

async def get_apply_profile() -> dict:
    db = get_db()
    return await db.apply_profile.find_one({}, {"_id": 0}) or {}


async def save_apply_profile(profile: dict):
    from datetime import datetime
    payload = dict(profile)
    payload["updated_at"] = datetime.utcnow()
    db = get_db()
    await db.apply_profile.replace_one({}, payload, upsert=True)


async def upsert_learned_answer(question: str, answer: str):
    """Store an answer for a specific form question, so it's reused next time."""
    from datetime import datetime
    db = get_db()
    profile = await db.apply_profile.find_one({}) or {}
    qa = profile.get("qa", [])
    q_norm = question_key(question)
    for entry in qa:
        if question_key(entry.get("question", "")) == q_norm:
            entry["answer"] = answer
            break
    else:
        qa.append({"question": question, "answer": answer})
    await db.apply_profile.update_one(
        {}, {"$set": {"qa": qa, "updated_at": datetime.utcnow()}}, upsert=True
    )
    # Answering it clears it from the pending list.
    await db.pending_questions.delete_one({"question_norm": q_norm})


async def record_pending_question(question: str, source: str = "", job_title: str = ""):
    """Log a question the system could not answer, for you to fill in later."""
    from datetime import datetime
    db = get_db()
    q_norm = question_key(question)
    await db.pending_questions.update_one(
        {"question_norm": q_norm},
        {"$set": {"question": question, "question_norm": q_norm, "last_seen_at": datetime.utcnow(),
                  "source": source, "job_title": job_title},
         "$inc": {"times_seen": 1}},
        upsert=True,
    )


async def mark_question_emailed(question: str, reask_after_days: int = 7) -> bool:
    """
    Claim the right to email this question, returning False if it was already
    sent recently.

    The claim is the same update that records it, so two runs hitting the same
    question at once cannot both email it: only one update matches. Without
    that, every cycle that re-encounters an unanswered question would send
    another copy, and the questions that recur most are exactly the ones that go
    unanswered longest.

    After reask_after_days it becomes claimable again — a question you never got
    round to should resurface eventually, just not daily.
    """
    from datetime import datetime, timedelta
    db = get_db()
    q_norm = question_key(question)
    cutoff = datetime.utcnow() - timedelta(days=reask_after_days)
    res = await db.pending_questions.update_one(
        {"question_norm": q_norm,
         "$or": [{"emailed_at": {"$exists": False}},
                 {"emailed_at": None},
                 {"emailed_at": {"$lt": cutoff}}]},
        {"$set": {"emailed_at": datetime.utcnow()}},
    )
    if res.matched_count == 0:
        # No such pending question — AnswerResolver's own record failed. Create
        # it here rather than dropping the question on the floor.
        exists = await db.pending_questions.find_one({"question_norm": q_norm}, {"_id": 1})
        if not exists:
            await db.pending_questions.update_one(
                {"question_norm": q_norm},
                {"$set": {"question": question, "question_norm": q_norm,
                          "emailed_at": datetime.utcnow(),
                          "last_seen_at": datetime.utcnow()},
                 "$inc": {"times_seen": 1}},
                upsert=True,
            )
            return True
    return res.modified_count > 0


async def get_pending_questions() -> list:
    db = get_db()
    out = []
    async for doc in db.pending_questions.find({}, {"_id": 0}).sort("times_seen", -1):
        out.append(doc)
    return out


async def delete_pending_question(question: str):
    db = get_db()
    await db.pending_questions.delete_one({"question_norm": " ".join(question.lower().split())})


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

    # "Worth reviewing" has to mean the same thing here as it does to the agent.
    # These bands were fixed at 70/50 while min_score became a setting, so
    # lowering the threshold to 60 left the dashboard still counting from 70 —
    # the number on Today disagreed with the queue it linked to.
    from services.settings_service import get_agent_rules
    threshold = (await get_agent_rules())["min_score"]
    medium_floor = max(0, threshold - 20)

    total = await db.jobs.count_documents({})
    high = await db.jobs.count_documents({"match_score": {"$gte": threshold}})
    medium = await db.jobs.count_documents({"match_score": {"$gte": medium_floor, "$lt": threshold}})
    low = await db.jobs.count_documents({"match_score": {"$lt": medium_floor, "$ne": None}})
    applied = await db.applications.count_documents({"status": {"$ne": "saved"}})
    interviews = await db.applications.count_documents({
        "status": {"$in": ["recruiter_screen", "technical", "final_round"]}
    })
    # Today's header counted this by fetching 200 job documents and filtering
    # them in the browser. It is one indexed count.
    from datetime import datetime, timedelta
    since = datetime.utcnow() - timedelta(hours=24)
    new_last_24h = await db.jobs.count_documents({"scraped_at": {"$gte": since}})

    last_job = await db.jobs.find_one({}, sort=[("scraped_at", -1)])
    return {
        "total_jobs": total,
        "high_match": high,
        "medium_match": medium,
        "low_match": low,
        "applied": applied,
        "interviews": interviews,
        "new_last_24h": new_last_24h,
        "last_scraped": last_job["scraped_at"] if last_job else None,
    }
