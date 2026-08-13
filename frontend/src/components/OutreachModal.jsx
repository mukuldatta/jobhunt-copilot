import { useState, useEffect } from 'react'
import { X } from '@phosphor-icons/react'
import { generateOutreach, errorMessage } from '../api'
import { useModal, backdropProps } from '../hooks/useModal'

export default function OutreachModal({ job, onClose }) {
  const [message, setMessage] = useState('')
  const [loading, setLoading] = useState(true)
  const [copied, setCopied] = useState(false)

  const dialogRef = useModal(onClose)

  useEffect(() => {
    let cancelled = false
    generateOutreach(job.job_id)
      .then((res) => !cancelled && setMessage(res.data.outreach_message))
      .catch((e) => !cancelled && setMessage(errorMessage(e, 'Could not generate an outreach message.')))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [job.job_id])

  function handleCopy() {
    navigator.clipboard.writeText(message)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const overLimit = message.length > 300

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      {...backdropProps(onClose)}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="outreach-modal-title"
        tabIndex={-1}
        className="w-full max-w-lg rounded border border-line bg-bg animate-viewIn focus:outline-none"
      >
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <h2 id="outreach-modal-title" className="text-lg">LinkedIn outreach</h2>
          <button onClick={onClose} aria-label="Close" className="btn btn-neutral btn-icon">
            <X size={14} />
          </button>
        </div>

        <div className="p-6">
          <p className="mb-3 text-xs+ text-neutral-600">
            {job.company} · {job.title}
          </p>
          {loading ? (
            <div className="flex flex-col gap-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="skeleton h-3" style={{ width: `${70 + ((i * 11) % 30)}%` }} />
              ))}
            </div>
          ) : (
            <div className="rounded border border-line p-4">
              <p className="text-base leading-relaxed text-text">{message}</p>
              <p className={`mt-2 text-xs ${overLimit ? 'text-accent-400' : 'text-neutral-600'}`}>
                {message.length} / 300 characters
              </p>
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-line px-6 py-4">
          <button onClick={handleCopy} className="btn btn-accent">
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
      </div>
    </div>
  )
}
