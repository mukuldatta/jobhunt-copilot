import { useEffect, useState } from 'react'
import { getJobs } from '../api'
import JobCard from '../components/JobCard'

const SCORE_FILTERS = [
  { label: 'All', value: null },
  { label: 'High (70%+)', value: 70 },
  { label: 'Medium (50%+)', value: 50 },
]

const STATUS_FILTERS = [
  { label: 'All', value: null },
  { label: 'New', value: 'new' },
  { label: 'Applied', value: 'applied' },
  { label: 'Skipped', value: 'skipped' },
]

const SOURCE_FILTERS = [
  { label: 'All Sources', value: null },
  { label: 'LinkedIn', value: 'linkedin' },
  { label: 'Naukri', value: 'naukri' },
  { label: 'Indeed', value: 'indeed' },
  { label: 'Dice', value: 'dice' },
]

const SPONSORSHIP_FILTERS = [
  { label: 'All', value: null },
  { label: 'Strong', value: 'strong' },
  { label: 'Moderate', value: 'moderate' },
  { label: 'Contract', value: 'contract' },
  { label: 'None', value: 'none' },
]

const SORT_OPTIONS = [
  { label: 'Newest First', value: 'date_desc' },
  { label: 'Oldest First', value: 'date_asc' },
  { label: 'Highest Score', value: 'score_desc' },
  { label: 'Lowest Score', value: 'score_asc' },
]

function FilterGroup({ label, options, value, onChange }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-textSecondary uppercase tracking-wide">{label}</span>
      <div className="flex gap-1 flex-wrap">
        {options.map(f => (
          <button key={String(f.value)} onClick={() => onChange(f.value)}
            className={`px-3 py-1.5 text-xs rounded transition-colors ${
              value === f.value
                ? 'bg-accent/10 text-accent'
                : 'bg-border text-textSecondary hover:text-textPrimary'
            }`}>
            {f.label}
          </button>
        ))}
      </div>
    </div>
  )
}

export default function Jobs() {
  const [jobs, setJobs] = useState([])
  const [loading, setLoading] = useState(true)
  const [minScore, setMinScore] = useState(null)
  const [status, setStatus] = useState(null)
  const [source, setSource] = useState(null)
  const [sponsorship, setSponsorship] = useState(null)
  const [sortBy, setSortBy] = useState('date_desc')
  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [page, setPage] = useState(0)
  const limit = 20

  useEffect(() => {
    setLoading(true)
    getJobs({
      skip: page * limit,
      limit,
      min_score: minScore,
      status,
      source,
      sponsorship,
      sort_by: sortBy,
      search: search || undefined,
    })
      .then(r => setJobs(r.data.jobs))
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [minScore, status, source, sponsorship, sortBy, search, page])

  function handleStatusChange(jobId, newStatus) {
    setJobs(prev => prev.map(j => j.job_id === jobId ? { ...j, status: newStatus } : j))
  }

  function handleSearchSubmit(e) {
    e.preventDefault()
    setSearch(searchInput.trim())
    setPage(0)
  }

  function resetFilters() {
    setMinScore(null)
    setStatus(null)
    setSource(null)
    setSponsorship(null)
    setSortBy('date_desc')
    setSearch('')
    setSearchInput('')
    setPage(0)
  }

  const hasActiveFilters = minScore !== null || status !== null || source !== null ||
    sponsorship !== null || sortBy !== 'date_desc' || search !== ''

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-bold text-textPrimary">Jobs</h1>
        <div className="flex items-center gap-3">
          {hasActiveFilters && (
            <button onClick={resetFilters}
              className="text-xs text-textSecondary hover:text-danger transition-colors">
              Clear filters
            </button>
          )}
          <p className="text-textSecondary text-sm">{jobs.length} results</p>
        </div>
      </div>

      {/* Search */}
      <form onSubmit={handleSearchSubmit} className="flex gap-2 mb-4">
        <input
          type="text"
          value={searchInput}
          onChange={e => setSearchInput(e.target.value)}
          placeholder="Search by title or company..."
          className="flex-1 bg-card border border-border rounded px-3 py-2 text-sm text-textPrimary placeholder-textSecondary focus:outline-none focus:border-accent"
        />
        <button type="submit"
          className="px-4 py-2 text-sm bg-accent/10 text-accent rounded hover:bg-accent/20 transition-colors">
          Search
        </button>
        {search && (
          <button type="button" onClick={() => { setSearch(''); setSearchInput(''); setPage(0) }}
            className="px-3 py-2 text-sm bg-border text-textSecondary rounded hover:text-textPrimary transition-colors">
            &times;
          </button>
        )}
      </form>

      {/* Filters */}
      <div className="bg-card border border-border rounded-lg p-4 mb-6 flex flex-col gap-4">
        <div className="flex gap-6 flex-wrap">
          <FilterGroup label="Score" options={SCORE_FILTERS} value={minScore}
            onChange={v => { setMinScore(v); setPage(0) }} />
          <FilterGroup label="Status" options={STATUS_FILTERS} value={status}
            onChange={v => { setStatus(v); setPage(0) }} />
          <FilterGroup label="Sponsorship" options={SPONSORSHIP_FILTERS} value={sponsorship}
            onChange={v => { setSponsorship(v); setPage(0) }} />
        </div>
        <div className="flex gap-6 flex-wrap items-end">
          <FilterGroup label="Source" options={SOURCE_FILTERS} value={source}
            onChange={v => { setSource(v); setPage(0) }} />
          <div className="flex flex-col gap-1">
            <span className="text-xs text-textSecondary uppercase tracking-wide">Sort</span>
            <select value={sortBy} onChange={e => { setSortBy(e.target.value); setPage(0) }}
              className="bg-border text-textPrimary text-xs rounded px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-accent">
              {SORT_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="text-textSecondary text-center py-16">Loading jobs...</div>
      ) : jobs.length === 0 ? (
        <div className="text-textSecondary text-center py-16">
          <p className="text-lg">No jobs found</p>
          <p className="text-sm mt-2">
            {hasActiveFilters ? 'Try adjusting your filters.' : 'Trigger a scrape from the Dashboard to fetch new jobs.'}
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {jobs.map(job => (
            <JobCard key={job.job_id} job={job} onStatusChange={handleStatusChange} />
          ))}
        </div>
      )}

      {(jobs.length === limit || page > 0) && (
        <div className="flex justify-center mt-6 gap-2">
          <button onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}
            className="px-4 py-2 text-sm bg-border text-textSecondary rounded disabled:opacity-40 hover:text-textPrimary">
            Previous
          </button>
          <span className="px-4 py-2 text-sm text-textSecondary">Page {page + 1}</span>
          <button onClick={() => setPage(p => p + 1)} disabled={jobs.length < limit}
            className="px-4 py-2 text-sm bg-border text-textSecondary rounded disabled:opacity-40 hover:text-textPrimary">
            Next
          </button>
        </div>
      )}
    </div>
  )
}
