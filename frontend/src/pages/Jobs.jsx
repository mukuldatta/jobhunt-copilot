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

export default function Jobs() {
  const [jobs, setJobs] = useState([])
  const [loading, setLoading] = useState(true)
  const [minScore, setMinScore] = useState(null)
  const [status, setStatus] = useState(null)
  const [page, setPage] = useState(0)
  const limit = 20

  useEffect(() => {
    setLoading(true)
    getJobs({ skip: page * limit, limit, min_score: minScore, status })
      .then(r => setJobs(r.data.jobs))
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [minScore, status, page])

  function handleStatusChange(jobId, newStatus) {
    setJobs(prev => prev.map(j => j.job_id === jobId ? { ...j, status: newStatus } : j))
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-textPrimary">Jobs</h1>
        <p className="text-textSecondary text-sm">{jobs.length} results</p>
      </div>

      <div className="flex gap-4 mb-6 flex-wrap">
        <div className="flex gap-1">
          {SCORE_FILTERS.map(f => (
            <button key={String(f.value)} onClick={() => { setMinScore(f.value); setPage(0) }}
              className={`px-3 py-1.5 text-xs rounded transition-colors ${
                minScore === f.value ? 'bg-accent/10 text-accent' : 'bg-border text-textSecondary hover:text-textPrimary'
              }`}>
              {f.label}
            </button>
          ))}
        </div>
        <div className="flex gap-1">
          {STATUS_FILTERS.map(f => (
            <button key={String(f.value)} onClick={() => { setStatus(f.value); setPage(0) }}
              className={`px-3 py-1.5 text-xs rounded transition-colors ${
                status === f.value ? 'bg-accent/10 text-accent' : 'bg-border text-textSecondary hover:text-textPrimary'
              }`}>
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="text-textSecondary text-center py-16">Loading jobs...</div>
      ) : jobs.length === 0 ? (
        <div className="text-textSecondary text-center py-16">
          <p className="text-lg">No jobs found</p>
          <p className="text-sm mt-2">Trigger a scrape from the Dashboard to fetch new jobs.</p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {jobs.map(job => (
            <JobCard key={job.job_id} job={job} onStatusChange={handleStatusChange} />
          ))}
        </div>
      )}

      {jobs.length === limit && (
        <div className="flex justify-center mt-6 gap-2">
          <button onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}
            className="px-4 py-2 text-sm bg-border text-textSecondary rounded disabled:opacity-40 hover:text-textPrimary">
            Previous
          </button>
          <button onClick={() => setPage(p => p + 1)}
            className="px-4 py-2 text-sm bg-border text-textSecondary rounded hover:text-textPrimary">
            Next
          </button>
        </div>
      )}
    </div>
  )
}
