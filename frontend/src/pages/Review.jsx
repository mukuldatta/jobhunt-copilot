import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { MagnifyingGlass } from '@phosphor-icons/react'
import FilterMenu from '../components/FilterMenu'
import JobDetail from '../components/JobDetail'
import { getJobs, errorMessage } from '../api'
import { useToast } from '../components/Toast'
import { useAgent } from '../hooks/useAgent'
import { useReducedMotion, useReveal, stagger } from '../hooks/useMotion'
import { age, scoreText, shortLocation } from '../lib/format'

const PAGE = 25

const SCORE = [
  { label: 'Any score', value: '' },
  { label: '70%+', value: '70' },
  { label: '50%+', value: '50' },
]
// The statuses the apply state machine actually writes. "Reviewed" and
// "Skipped" sat here offering nothing — the alerting rewrite stopped writing
// "reviewed" — while manual_required, the largest actionable bucket and the
// only one that is a to-do list, could be reached by URL but not by dropdown.
const STATUS = [
  { label: 'All statuses', value: '' },
  { label: 'Needs manual apply', value: 'manual_required' },
  { label: 'New', value: 'new' },
  { label: 'Applying', value: 'applying' },
  { label: 'Applied', value: 'applied' },
  { label: 'Apply failed', value: 'apply_failed' },
  { label: 'Sign-in needed', value: 'login_required' },
  { label: 'Expired', value: 'expired' },
  { label: 'Skipped', value: 'skipped' },
]
const SOURCE = [
  { label: 'All sources', value: '' },
  { label: 'LinkedIn', value: 'linkedin' },
  { label: 'Naukri', value: 'naukri' },
  { label: 'Indeed', value: 'indeed' },
]
const REGION = [
  { label: 'All regions', value: '' },
  { label: 'India', value: 'india' },
  { label: 'United States', value: 'us' },
]
const SPONSORSHIP = [
  { label: 'Any sponsorship', value: '' },
  { label: 'Strong', value: 'strong' },
  { label: 'Moderate', value: 'moderate' },
  { label: 'Contract', value: 'contract' },
  { label: 'None', value: 'none' },
]
const SORT = [
  { label: 'Highest score', value: 'score_desc' },
  { label: 'Lowest score', value: 'score_asc' },
  { label: 'Newest first', value: 'date_desc' },
  { label: 'Oldest first', value: 'date_asc' },
]

const DEFAULTS = {
  min_score: '',
  status: '',
  source: '',
  region: '',
  sponsorship: '',
  sort_by: 'score_desc',
  search: '',
}

const labelFor = (options, value, fallback) =>
  options.find((o) => o.value === value)?.label ?? fallback

function SkeletonRow() {
  return (
    <div className="flex gap-3 border-t border-line-soft px-[18px] py-[11px]">
      <div className="skeleton h-3 w-[26px] flex-none" />
      <div className="min-w-0 flex-1">
        <div className="skeleton h-3 w-2/3" />
        <div className="skeleton mt-2 h-2.5 w-1/3" />
      </div>
    </div>
  )
}

export default function Review() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const reduced = useReducedMotion()
  const revealRef = useReveal(reduced)

  const [jobs, setJobs] = useState([])
  const [total, setTotal] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState(null)
  const [pageError, setPageError] = useState(null)
  const [selected, setSelected] = useState(null)
  const [searchInput, setSearchInput] = useState(params.get('search') || '')
  const [overlay, setOverlay] = useState(false)
  const [showKeys, setShowKeys] = useState(false)

  const notify = useToast()
  const { refreshStats } = useAgent()

  const searchRef = useRef(null)
  const listRef = useRef(null)
  const sentinel = useRef(null)

  // Filters live in the URL, so a filtered view is a link you can send yourself.
  const filters = useMemo(() => {
    const f = { ...DEFAULTS }
    Object.keys(DEFAULTS).forEach((k) => {
      const v = params.get(k)
      if (v != null) f[k] = v
    })
    return f
  }, [params])

  const setFilter = useCallback(
    (key, value) => {
      const next = new URLSearchParams(params)
      if (!value || value === DEFAULTS[key]) next.delete(key)
      else next.set(key, value)
      setParams(next, { replace: true })
    },
    [params, setParams]
  )

  const clearFilters = () => setParams(new URLSearchParams(), { replace: true })

  const query = useCallback(
    (skip) => ({
      skip,
      limit: PAGE,
      min_score: filters.min_score || undefined,
      status: filters.status || undefined,
      source: filters.source || undefined,
      region: filters.region || undefined,
      sponsorship: filters.sponsorship || undefined,
      sort_by: filters.sort_by,
      search: filters.search || undefined,
    }),
    [filters]
  )

  // Fresh page whenever the filters change.
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getJobs(query(0))
      .then((r) => {
        if (cancelled) return
        const rows = r.data.jobs || []
        setJobs(rows)
        // The real size of the filtered set, from the server. This was the
        // length of the first page, so a filter matching 400 jobs read "25+".
        setTotal(r.data.total ?? rows.length)
        setHasMore(rows.length === PAGE)
        listRef.current?.scrollTo({ top: 0 })
      })
      .catch((e) => !cancelled && setError(errorMessage(e, 'Could not load jobs.')))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [query])

  // Pagination is replaced by infinite scroll on this pane.
  const loadMore = useCallback(async () => {
    if (loadingMore || !hasMore) return
    setLoadingMore(true)
    setPageError(null)
    try {
      const r = await getJobs(query(jobs.length))
      const rows = r.data.jobs || []
      setJobs((prev) => [...prev, ...rows])
      if (r.data.total != null) setTotal(r.data.total)
      setHasMore(rows.length === PAGE)
    } catch (e) {
      // A dropped request is not the end of the list. Swallowing it here set
      // hasMore false, so one blip permanently ended scrolling with no notice
      // and no way back short of reloading the page.
      setPageError(errorMessage(e, 'Could not load more jobs.'))
    } finally {
      setLoadingMore(false)
    }
  }, [hasMore, jobs.length, loadingMore, query])

  useEffect(() => {
    const node = sentinel.current
    if (!node) return undefined
    const io = new IntersectionObserver((entries) => entries[0].isIntersecting && loadMore(), {
      root: listRef.current,
      rootMargin: '240px',
    })
    io.observe(node)
    return () => io.disconnect()
  }, [loadMore])

  // Below 1100px the detail pane covers the list rather than squeezing it.
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 1100px)')
    const sync = () => setOverlay(mq.matches)
    sync()
    mq.addEventListener('change', sync)
    return () => mq.removeEventListener('change', sync)
  }, [])

  const selectedJob = useMemo(
    () => selected ?? jobs.find((j) => j.job_id === jobId) ?? null,
    [jobs, jobId, selected]
  )

  // Keep a selection in step with the route, and open the first row by default
  // on a wide screen — the queue should be workable without a first click.
  useEffect(() => {
    if (jobId) {
      const match = jobs.find((j) => j.job_id === jobId)
      if (match) setSelected(match)
      return
    }
    setSelected(null)
    if (!overlay && jobs.length > 0) navigate(`/review/${jobs[0].job_id}${window.location.search}`, { replace: true })
  }, [jobId, jobs, navigate, overlay])

  const open = useCallback(
    (job) => {
      setSelected(job)
      navigate(`/review/${job.job_id}${window.location.search}`)
    },
    [navigate]
  )

  const move = useCallback(
    (delta) => {
      if (jobs.length === 0) return
      const at = jobs.findIndex((j) => j.job_id === jobId)
      const next = jobs[Math.min(jobs.length - 1, Math.max(0, at + delta))]
      if (next) {
        open(next)
        document.getElementById(`row-${next.job_id}`)?.scrollIntoView({ block: 'nearest' })
      }
    },
    [jobId, jobs, open]
  )

  // This is a queue, so it should be workable without the mouse.
  useEffect(() => {
    const onKey = (e) => {
      const typing = ['INPUT', 'TEXTAREA'].includes(e.target.tagName)
      if (e.key === '/' && !typing) {
        e.preventDefault()
        searchRef.current?.focus()
        return
      }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === 'j' || e.key === 'ArrowDown') {
        e.preventDefault()
        move(1)
      } else if (e.key === 'k' || e.key === 'ArrowUp') {
        e.preventDefault()
        move(-1)
      } else if (e.key === 'Enter' && selectedJob) {
        // Enter submits a real application, which cannot be undone, so the
        // keyboard path asks first — the mouse path has a button you had to
        // aim at, and a stray Enter while skimming the queue did not.
        const applyBtn = document.querySelector('[data-apply-button]')
        if (!applyBtn) {
          // External postings render a link instead, so this used to do
          // nothing at all with no explanation.
          notify.ok('This posting is applied to on the company site — use Open posting.')
          return
        }
        if (window.confirm(`Apply to ${selectedJob.title} at ${selectedJob.company}?`)) {
          applyBtn.click()
        }
      } else if (e.key === 'e' && selectedJob) {
        document.querySelector('[data-skip-button]')?.click()
      } else if (e.key === '?') {
        setShowKeys((v) => !v)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [move, selectedJob, notify])

  function submitSearch(e) {
    e.preventDefault()
    setFilter('search', searchInput.trim())
  }

  function onJobStatusChange(id, status) {
    setJobs((prev) => prev.map((j) => (j.job_id === id ? { ...j, status } : j)))
    setSelected((prev) => (prev && prev.job_id === id ? { ...prev, status } : prev))
    // The sidebar counts derive from these statuses; without this they lagged
    // by up to a minute behind an action taken right next to them.
    refreshStats()
  }

  const dirty = Object.keys(DEFAULTS).some((k) => filters[k] !== DEFAULTS[k])

  return (
    <div className="flex h-full">
      <div
        className={`flex w-full flex-none flex-col border-r border-line wide:w-list ${
          overlay && jobId ? 'hidden' : ''
        }`}
      >
        <div className="border-b border-line px-[18px] pb-3 pt-[18px]">
          <form
            onSubmit={submitSearch}
            className="mb-2.5 flex items-center gap-2 rounded border border-line px-2.5 py-[7px]
                       transition-colors duration-180 focus-within:border-accent hover:border-text/40"
          >
            <MagnifyingGlass size={14} className="flex-none text-neutral-600" />
            <input
              ref={searchRef}
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search title or company"
              className="min-w-0 flex-1 bg-transparent text-base text-text outline-none
                         placeholder:text-neutral-600"
            />
            <span className="flex-none rounded-sm border border-line px-[5px] py-px text-xs text-neutral-700">
              /
            </span>
          </form>

          <div className="flex flex-wrap gap-1.5">
            <FilterMenu
              label={labelFor(SCORE, filters.min_score, 'Any score')}
              options={SCORE}
              value={filters.min_score}
              active={!!filters.min_score}
              onChange={(v) => setFilter('min_score', v)}
            />
            <FilterMenu
              label={labelFor(STATUS, filters.status, 'All statuses')}
              options={STATUS}
              value={filters.status}
              active={!!filters.status}
              onChange={(v) => setFilter('status', v)}
            />
            <FilterMenu
              label={labelFor(SOURCE, filters.source, 'All sources')}
              options={SOURCE}
              value={filters.source}
              active={!!filters.source}
              onChange={(v) => setFilter('source', v)}
            />
            <FilterMenu
              label={labelFor(REGION, filters.region, 'All regions')}
              options={REGION}
              value={filters.region}
              active={!!filters.region}
              onChange={(v) => setFilter('region', v)}
            />
            <FilterMenu
              label={filters.sponsorship ? labelFor(SPONSORSHIP, filters.sponsorship) : 'More'}
              icon={filters.sponsorship ? 'caret' : 'plus'}
              options={SPONSORSHIP}
              value={filters.sponsorship}
              active={!!filters.sponsorship}
              onChange={(v) => setFilter('sponsorship', v)}
            />
            <FilterMenu
              label={labelFor(SORT, filters.sort_by, 'Highest score')}
              options={SORT}
              value={filters.sort_by}
              active={filters.sort_by !== DEFAULTS.sort_by}
              onChange={(v) => setFilter('sort_by', v)}
            />
          </div>

          <div className="mt-2.5 flex items-center justify-between text-xs+ text-neutral-600">
            <span>
              {/* total is the server's count for this filter, so no "+". */}
              {loading ? 'Loading' : `${total ?? 0} job${total === 1 ? '' : 's'}`} · sorted by{' '}
              {labelFor(SORT, filters.sort_by, '').toLowerCase()}
            </span>
            <span className="flex items-center gap-3">
              <button
                onClick={() => setShowKeys((v) => !v)}
                className="text-neutral-700 hover:text-text"
                title="Keyboard shortcuts"
              >
                ?
              </button>
              {dirty && (
                <button onClick={clearFilters} className="text-accent hover:text-accent-300">
                  Clear
                </button>
              )}
            </span>
          </div>
        </div>

        {showKeys && (
          <div className="border-b border-line px-[18px] py-2 text-xs+ text-neutral-600">
            <span className="text-neutral-500">j / k</span> move ·{' '}
            <span className="text-neutral-500">/</span> search ·{' '}
            <span className="text-neutral-500">Enter</span> apply (confirms) ·{' '}
            <span className="text-neutral-500">e</span> skip ·{' '}
            <span className="text-neutral-500">?</span> this list
          </div>
        )}

        {error && (
          <div className="flex items-center justify-between border-b border-line px-[18px] py-2 text-xs+ text-accent-400">
            {error}
            <button onClick={() => setError(null)} className="text-neutral-600 hover:text-text">
              Dismiss
            </button>
          </div>
        )}

        <div ref={listRef} className="flex-1 overflow-y-auto">
          {loading ? (
            Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} />)
          ) : jobs.length === 0 ? (
            <div className="px-[18px] py-16 text-center text-neutral-600">
              <p className="text-lg text-text">No jobs found</p>
              <p className="mt-2 text-base">
                {dirty
                  ? 'Try adjusting your filters.'
                  : 'Trigger a scrape from Today to fetch new jobs.'}
              </p>
            </div>
          ) : (
            <>
              {jobs.map((job, i) => {
                const active = job.job_id === jobId
                return (
                  <button
                    key={job.job_id}
                    id={`row-${job.job_id}`}
                    ref={revealRef}
                    style={stagger(i, { reduced })}
                    onClick={() => open(job)}
                    className={`flex w-full gap-3 border-t border-line-soft border-l-2 px-[18px] py-[11px]
                                text-left transition-colors duration-180 ${
                                  reduced ? '' : 'animate-rowIn'
                                } ${
                                  active
                                    ? 'border-l-accent bg-accent/[0.10]'
                                    : 'border-l-transparent hover:bg-text/[0.04]'
                                } ${job.status === 'applied' ? 'opacity-55' : ''}`}
                  >
                    <div
                      className={`w-[26px] flex-none text-base font-medium ${scoreText(
                        job.match_score ?? 0
                      )}`}
                    >
                      {job.match_score ?? '—'}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-base">
                        {job.title}
                        {job.status === 'applied' && (
                          <span className="ml-1 rounded-sm border border-line px-1 text-2xs text-neutral-600">
                            applied
                          </span>
                        )}
                      </div>
                      <div className="text-xs+ text-neutral-600">
                        {[job.company, shortLocation(job.location)].filter(Boolean).join(' · ')}
                      </div>
                    </div>
                    <span className="self-center text-xs text-neutral-600">
                      {age(job.posted_at || job.scraped_at)}
                    </span>
                  </button>
                )
              })}
              {/* Parked while a page failed, so the observer does not
                  immediately retry the request that just errored. */}
              {!pageError && <div ref={sentinel} className="h-px" />}
              {loadingMore && <SkeletonRow />}
              {pageError && (
                <div className="flex items-center justify-between px-[18px] py-3 text-xs+ text-accent-400">
                  {pageError}
                  <button
                    onClick={() => {
                      setPageError(null)
                      loadMore()
                    }}
                    className="text-accent hover:text-accent-300"
                  >
                    Retry
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {selectedJob ? (
        <JobDetail
          job={selectedJob}
          overlay={overlay}
          onStatusChange={onJobStatusChange}
          onClose={() => navigate(`/review${window.location.search}`)}
        />
      ) : (
        !overlay && (
          <div className="hidden flex-1 items-center justify-center text-base text-neutral-600 wide:flex">
            Pick a job to see why it scored what it did.
          </div>
        )
      )}
    </div>
  )
}
