"""
The apply queue offers only postings the agent can actually submit to.

A posting that hands off to the employer's careers site is not a weaker
candidate — it is a different task, one for a human. Offering it here spends a
browser launch and a slot in a capped batch to rediscover something already
recorded: two of a twenty-job batch went exactly that way, and the run reported
"done" having submitted nothing.

No database here. The query is built, captured, and inspected — which is the
part that decides what the agent is allowed to see.
"""

import asyncio

import pytest

from db import mongodb


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def __aiter__(self):
        async def gen():
            for r in self.rows:
                yield r
        return gen()


class FakeJobs:
    def __init__(self, sink):
        self.sink = sink

    def find(self, query, *a, **k):
        self.sink.append(query)
        return FakeCursor([])


class FakeDB:
    def __init__(self, sink):
        self.jobs = FakeJobs(sink)


@pytest.fixture
def queries(monkeypatch):
    """Every query get_apply_candidates issues, in order."""
    sink = []
    monkeypatch.setattr(mongodb, "get_db", lambda: FakeDB(sink))

    async def no_limit():
        return None

    monkeypatch.setattr(mongodb, "_max_apply_years", no_limit)
    asyncio.run(mongodb.get_apply_candidates(min_score=60, region="india", limit=5))
    return sink


def submittable_clause(query):
    """The 'only what we can submit to' constraint, wherever it ended up."""
    for clause in query.get("$and", []):
        opts = clause.get("$or", [])
        if any("apply_type_hint" in o or o.get("apply_type") == "in_platform" for o in opts):
            return opts
    return None


class TestOnlySubmittableJobsAreOffered:
    def test_the_constraint_is_present(self, queries):
        assert any(submittable_clause(q) for q in queries), \
            "nothing restricts the queue to jobs we can submit to"

    def test_a_confirmed_in_platform_job_qualifies(self, queries):
        opts = next(submittable_clause(q) for q in queries if submittable_clause(q))
        assert {"apply_type": "in_platform"} in opts

    def test_a_hinted_in_platform_job_qualifies(self, queries):
        # Otherwise the queue starves: most postings are only ever hinted, and
        # the confirmed answer is a by-product of applying.
        opts = next(submittable_clause(q) for q in queries if submittable_clause(q))
        assert any(o.get("apply_type_hint") == "in_platform" for o in opts)

    def test_nothing_qualifies_on_an_external_hint(self, queries):
        for q in queries:
            for o in submittable_clause(q) or []:
                assert o.get("apply_type_hint") != "external"

    def test_confirmed_hand_offs_and_dead_postings_are_excluded(self, queries):
        for q in queries:
            excluded = q.get("apply_type", {})
            if isinstance(excluded, dict) and "$nin" in excluded:
                assert "external" in excluded["$nin"]
                assert "expired" in excluded["$nin"]
                return
        pytest.fail("nothing excludes confirmed external/expired postings")


class TestTheRegionFilterStillApplies:
    def test_region_did_not_overwrite_the_submittable_clause(self, queries):
        # Both are $or-shaped. Assigning query["$or"] for the region would drop
        # the other one silently, and the queue would quietly widen again.
        for q in queries:
            if submittable_clause(q):
                assert q.get("$or"), "the region filter is missing"
                assert any("region" in o or "location" in o for o in q["$or"])
                return
        pytest.fail("no query carried both filters")


class TestTheGuardsThatWereAlreadyThere:
    def test_only_new_jobs(self, queries):
        assert all(q.get("status") == "new" for q in queries)

    def test_the_score_threshold_is_applied(self, queries):
        assert all(q.get("match_score", {}).get("$gte") == 60 for q in queries)

    def test_the_current_scorer_version_is_required(self, queries):
        from utils.score_rules import SCORER_VERSION
        assert all(q.get("scorer_version") == SCORER_VERSION for q in queries)
