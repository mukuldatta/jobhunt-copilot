import { useState } from 'react'
import ScoreGauge from './ScoreGauge'
import ResumeModal from './ResumeModal'
import OutreachModal from './OutreachModal'
import { markApplied } from '../api'

export default function JobCard({ job, onStatusChange }) {
  const [resumeOpen, setResumeOpen] = useState(false)
  const [outreachOpen, setOutreachOpen] = useState(false)
  const [applying, setApplying] = useState(false)

  const sponsorColor = {
    strong: 'text-success',
    moderate: 'text-warning',
    contract: 'text-accent',
    none: 'text-danger',
  }

  async function handleApply() {
    setApplying(true)
    try {
      await markApplied(job.job_id)
      onStatusChange?.(job.job_id, 'applied')
    } catch (e) {
      console.error(e)
    } finally {
      setApplying(false)
    }
  }

  return (
    <div className="bg-card border border-border rounded-lg p-5 flex gap-4 hover:border-accent/40 transition-colors">
      <ScoreGauge score={job.match_score ?? 0} />

      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-2">
          <div>
            <a href={job.url} target="_blank" rel="noreferrer"
              className="text-textPrimary font-semibold hover:text-accent transition-colors line-clamp-1">
              {job.title}
            </a>
            <p className="text-textSecondary text-sm mt-0.5">{job.company} · {job.location}</p>
          </div>
          <div className="flex flex-col items-end gap-1 shrink-0">
            <span className={`text-xs font-medium ${sponsorColor[job.sponsorship_status] || 'text-textSecondary'}`}>
              {job.sponsorship_status ?? 'unknown'}
            </span>
            <span className="text-xs text-textSecondary">{job.source}</span>
          </div>
        </div>

        {job.gap_analysis?.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {job.gap_analysis.slice(0, 3).map((gap, i) => (
              <span key={i} className="text-xs bg-danger/10 text-danger px-2 py-0.5 rounded">
                gap: {gap.replace('missing: ', '')}
              </span>
            ))}
          </div>
        )}

        <div className="mt-3 flex gap-2 flex-wrap">
          <button onClick={() => setResumeOpen(true)}
            className="text-xs px-3 py-1.5 bg-accent/10 text-accent rounded hover:bg-accent/20 transition-colors">
            Tailor Resume
          </button>
          <button onClick={() => setOutreachOpen(true)}
            className="text-xs px-3 py-1.5 bg-border text-textSecondary rounded hover:text-textPrimary transition-colors">
            Outreach
          </button>
          <button onClick={handleApply} disabled={applying || job.status === 'applied'}
            className="text-xs px-3 py-1.5 bg-success/10 text-success rounded hover:bg-success/20 transition-colors disabled:opacity-40">
            {job.status === 'applied' ? 'Applied' : applying ? 'Marking...' : 'Mark Applied'}
          </button>
        </div>
      </div>

      {resumeOpen && <ResumeModal job={job} onClose={() => setResumeOpen(false)} />}
      {outreachOpen && <OutreachModal job={job} onClose={() => setOutreachOpen(false)} />}
    </div>
  )
}
