import { useEffect, useState } from 'react'
import { getApplications, updateApplicationStatus } from '../api'

const STATUSES = ['saved', 'applied', 'recruiter_screen', 'technical', 'final_round', 'offer', 'rejected']

const STATUS_COLOR = {
  saved: 'text-textSecondary',
  applied: 'text-accent',
  recruiter_screen: 'text-warning',
  technical: 'text-warning',
  final_round: 'text-success',
  offer: 'text-success',
  rejected: 'text-danger',
}

export default function Applications() {
  const [apps, setApps] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getApplications()
      .then(r => setApps(r.data.applications))
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  async function handleStatusChange(id, status) {
    try {
      await updateApplicationStatus(id, { status })
      setApps(prev => prev.map(a => a.id === id ? { ...a, status } : a))
    } catch (e) {
      console.error(e)
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-textPrimary mb-6">Applications</h1>

      {loading ? (
        <div className="text-textSecondary text-center py-16">Loading...</div>
      ) : apps.length === 0 ? (
        <div className="text-textSecondary text-center py-16">
          <p className="text-lg">No applications yet</p>
          <p className="text-sm mt-2">Mark jobs as Applied from the Jobs page.</p>
        </div>
      ) : (
        <div className="bg-card border border-border rounded-lg overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-border text-left">
                <th className="px-5 py-3 text-textSecondary text-xs font-medium">Job</th>
                <th className="px-5 py-3 text-textSecondary text-xs font-medium">Applied</th>
                <th className="px-5 py-3 text-textSecondary text-xs font-medium">Status</th>
                <th className="px-5 py-3 text-textSecondary text-xs font-medium">Update</th>
              </tr>
            </thead>
            <tbody>
              {apps.map(app => (
                <tr key={app.id} className="border-b border-border last:border-0 hover:bg-border/20 transition-colors">
                  <td className="px-5 py-3 text-textPrimary text-sm">{app.job_id}</td>
                  <td className="px-5 py-3 text-textSecondary text-sm">
                    {new Date(app.applied_at).toLocaleDateString()}
                  </td>
                  <td className="px-5 py-3">
                    <span className={`text-sm font-medium ${STATUS_COLOR[app.status] || 'text-textSecondary'}`}>
                      {app.status.replace('_', ' ')}
                    </span>
                  </td>
                  <td className="px-5 py-3">
                    <select
                      value={app.status}
                      onChange={e => handleStatusChange(app.id, e.target.value)}
                      className="bg-bg border border-border text-textSecondary text-xs rounded px-2 py-1 focus:outline-none focus:border-accent"
                    >
                      {STATUSES.map(s => (
                        <option key={s} value={s}>{s.replace('_', ' ')}</option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
