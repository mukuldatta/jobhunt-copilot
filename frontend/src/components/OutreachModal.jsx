import { useState, useEffect } from 'react'
import { generateOutreach } from '../api'

export default function OutreachModal({ job, onClose }) {
  const [message, setMessage] = useState('')
  const [loading, setLoading] = useState(true)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    generateOutreach(job.job_id)
      .then(res => setMessage(res.data.outreach_message))
      .catch(() => setMessage('Error generating outreach message.'))
      .finally(() => setLoading(false))
  }, [job.job_id])

  function handleCopy() {
    navigator.clipboard.writeText(message)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-card border border-border rounded-xl w-full max-w-lg">
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <h2 className="text-textPrimary font-semibold">LinkedIn Outreach</h2>
          <button onClick={onClose} className="text-textSecondary hover:text-textPrimary text-xl">&times;</button>
        </div>

        <div className="p-6">
          <p className="text-textSecondary text-xs mb-3">{job.company} · {job.title}</p>
          {loading ? (
            <div className="text-textSecondary text-center py-6">Generating message...</div>
          ) : (
            <div className="bg-bg rounded-lg p-4 border border-border">
              <p className="text-textPrimary text-sm leading-relaxed">{message}</p>
              <p className={`text-xs mt-2 ${message.length > 300 ? 'text-danger' : 'text-textSecondary'}`}>
                {message.length} / 300 chars
              </p>
            </div>
          )}
        </div>

        <div className="px-6 py-4 border-t border-border flex justify-end gap-2">
          <button onClick={handleCopy}
            className="px-4 py-2 text-sm bg-accent/10 text-accent rounded hover:bg-accent/20 transition-colors">
            {copied ? 'Copied!' : 'Copy'}
          </button>
          <button onClick={onClose}
            className="px-4 py-2 text-sm bg-border text-textSecondary rounded hover:text-textPrimary transition-colors">
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
