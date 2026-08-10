import { useState, useEffect } from 'react'
import { uploadResume, getAuthStatus, platformLogin, platformCheck, runAutoApply } from '../api'

const PLATFORM_LABELS = { naukri: 'Naukri', linkedin: 'LinkedIn', indeed: 'Indeed', dice: 'Dice' }

export default function Settings() {
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  // --- Session login ---
  const [platforms, setPlatforms] = useState([])
  const [loginMsg, setLoginMsg] = useState(null)
  const [checking, setChecking] = useState(null)

  // --- Auto-apply ---
  const [applyBusy, setApplyBusy] = useState(false)
  const [applyResult, setApplyResult] = useState(null)

  async function refreshAuth() {
    try {
      const res = await getAuthStatus()
      setPlatforms(res.data.platforms || [])
    } catch {
      /* backend may be offline; leave as-is */
    }
  }

  useEffect(() => { refreshAuth() }, [])

  async function handleUpload() {
    if (!file) return
    setUploading(true); setResult(null); setError(null)
    try {
      const res = await uploadResume(file)
      setResult(res.data)
    } catch (e) {
      setError(e.response?.data?.detail || 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  async function handleCheck(platform) {
    setChecking(platform); setLoginMsg(null)
    try {
      const res = await platformCheck(platform)
      setPlatforms(prev => prev.map(p =>
        p.platform === platform ? { ...p, logged_in: res.data.logged_in } : p))
      setLoginMsg(`${PLATFORM_LABELS[platform] || platform}: ${res.data.logged_in ? 'signed in' : 'not signed in'}`)
    } catch (e) {
      setLoginMsg(e.response?.data?.detail || `Could not check ${platform}`)
    } finally {
      setChecking(null)
    }
  }

  async function handleLogin(platform) {
    setLoginMsg(null)
    try {
      const res = await platformLogin(platform)
      setLoginMsg(res.data.message)
      // Session persists a few seconds after you finish; re-check a couple times.
      setTimeout(refreshAuth, 8000)
      setTimeout(refreshAuth, 30000)
    } catch (e) {
      setLoginMsg(e.response?.data?.detail || `Could not start ${platform} login`)
    }
  }

  async function handleApply(dryRun) {
    setApplyBusy(true); setApplyResult(null)
    try {
      const res = await runAutoApply(dryRun ? { dry_run: true } : { force: true })
      setApplyResult(res.data)
    } catch (e) {
      setApplyResult({ status: 'error', message: e.response?.data?.detail || 'Request failed' })
    } finally {
      setApplyBusy(false)
    }
  }

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-bold text-textPrimary mb-8">Settings</h1>

      {/* Resume */}
      <div className="bg-card border border-border rounded-lg p-6 mb-6">
        <h2 className="text-textPrimary font-semibold mb-1">Resume</h2>
        <p className="text-textSecondary text-sm mb-4">
          Upload your PDF resume. It will be parsed and used for job matching and tailoring.
        </p>
        <div className="border-2 border-dashed border-border rounded-lg p-6 text-center mb-4 hover:border-accent/40 transition-colors">
          <input type="file" accept=".pdf" onChange={e => setFile(e.target.files[0])} className="hidden" id="resume-upload" />
          <label htmlFor="resume-upload" className="cursor-pointer">
            <p className="text-textSecondary text-sm">{file ? file.name : 'Click to select PDF resume'}</p>
          </label>
        </div>
        <button onClick={handleUpload} disabled={!file || uploading}
          className="px-4 py-2 bg-accent text-bg text-sm font-medium rounded hover:bg-accent/90 disabled:opacity-50 transition-colors">
          {uploading ? 'Uploading...' : 'Upload Resume'}
        </button>
        {result && <div className="mt-4 p-3 bg-success/10 border border-success/20 rounded text-success text-sm">Resume uploaded. Found {result.skills_found} skills.</div>}
        {error && <div className="mt-4 p-3 bg-danger/10 border border-danger/20 rounded text-danger text-sm">{error}</div>}
      </div>

      {/* Session login */}
      <div className="bg-card border border-border rounded-lg p-6 mb-6">
        <h2 className="text-textPrimary font-semibold mb-1">Job-board Sessions</h2>
        <p className="text-textSecondary text-sm mb-4">
          Sign in once per session. A browser window opens on this machine — complete the login
          (including any 2FA / CAPTCHA) and the session is saved and reused when applying.
        </p>

        <div className="space-y-2 mb-4">
          {platforms.length === 0 && <p className="text-textSecondary text-sm">No status yet — is the backend running?</p>}
          {platforms.map(p => (
            <div key={p.platform} className="flex items-center justify-between bg-bg border border-border rounded px-3 py-2">
              <div className="flex items-center gap-2">
                <span className={`inline-block w-2 h-2 rounded-full ${p.logged_in ? 'bg-success' : 'bg-textSecondary'}`} />
                <span className="text-textPrimary text-sm">{PLATFORM_LABELS[p.platform] || p.platform}</span>
                <span className="text-textSecondary text-xs">{p.logged_in ? 'signed in' : 'not signed in'}</span>
              </div>
              <div className="flex items-center gap-2">
                <button onClick={() => handleCheck(p.platform)} disabled={checking === p.platform}
                  className="px-3 py-1 text-xs font-medium rounded border border-border text-textSecondary hover:text-textPrimary hover:border-textSecondary disabled:opacity-50 transition-colors">
                  {checking === p.platform ? 'Checking...' : 'Check'}
                </button>
                <button onClick={() => handleLogin(p.platform)}
                  className="px-3 py-1 text-xs font-medium rounded border border-accent text-accent hover:bg-accent hover:text-bg transition-colors">
                  {p.logged_in ? 'Re-login' : 'Login'}
                </button>
              </div>
            </div>
          ))}
        </div>

        <button onClick={refreshAuth} className="text-accent text-xs hover:underline">Refresh status</button>
        {loginMsg && <div className="mt-3 p-3 bg-accent/10 border border-accent/20 rounded text-accent text-sm">{loginMsg}</div>}
      </div>

      {/* Auto-apply */}
      <div className="bg-card border border-border rounded-lg p-6 mb-6">
        <h2 className="text-textPrimary font-semibold mb-1">Check &amp; Submit Applications</h2>
        <p className="text-textSecondary text-sm mb-4">
          Applies to your new high-match jobs using the signed-in sessions above. Preview first
          with a dry run; then submit. A browser window may open and pause for any CAPTCHA.
        </p>
        <div className="flex gap-3">
          <button onClick={() => handleApply(true)} disabled={applyBusy}
            className="px-4 py-2 text-sm font-medium rounded border border-accent text-accent hover:bg-accent hover:text-bg disabled:opacity-50 transition-colors">
            {applyBusy ? 'Working...' : 'Dry run'}
          </button>
          <button onClick={() => handleApply(false)} disabled={applyBusy}
            className="px-4 py-2 bg-accent text-bg text-sm font-medium rounded hover:bg-accent/90 disabled:opacity-50 transition-colors">
            {applyBusy ? 'Working...' : 'Submit applications'}
          </button>
        </div>

        {applyResult && (
          <div className="mt-4 p-3 bg-bg border border-border rounded text-sm">
            <div className="text-textPrimary font-medium mb-1">Status: {applyResult.status}</div>
            {applyResult.message && <div className="text-textSecondary">{applyResult.message}</div>}
            {applyResult.status === 'dry_run' && (
              <ul className="mt-2 space-y-1">
                {(applyResult.jobs || []).map((j, i) => (
                  <li key={i} className="text-textSecondary">• {j.score}% — {j.title} @ {j.company} <span className="text-xs">({j.source})</span></li>
                ))}
                {(applyResult.jobs || []).length === 0 && <li className="text-textSecondary">No eligible jobs right now.</li>}
              </ul>
            )}
            {applyResult.results && (
              <div className="text-textSecondary mt-1">
                {Object.entries(applyResult.results).map(([k, v]) => `${k}: ${v}`).join('  ·  ')}
              </div>
            )}
          </div>
        )}
      </div>

      {/* About */}
      <div className="bg-card border border-border rounded-lg p-6">
        <h2 className="text-textPrimary font-semibold mb-1">About</h2>
        <p className="text-textSecondary text-sm">JobHunt Copilot v1.0.0</p>
        <p className="text-textSecondary text-sm mt-1">Built by Venkata Naga Santosh Mukul Mokkapati · mukulmokkapati@gmail.com</p>
      </div>
    </div>
  )
}
