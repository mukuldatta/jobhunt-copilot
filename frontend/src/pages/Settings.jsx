import { useState } from 'react'
import { uploadResume } from '../api'

export default function Settings() {
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  async function handleUpload() {
    if (!file) return
    setUploading(true)
    setResult(null)
    setError(null)
    try {
      const res = await uploadResume(file)
      setResult(res.data)
    } catch (e) {
      setError(e.response?.data?.detail || 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-bold text-textPrimary mb-8">Settings</h1>

      <div className="bg-card border border-border rounded-lg p-6 mb-6">
        <h2 className="text-textPrimary font-semibold mb-1">Resume</h2>
        <p className="text-textSecondary text-sm mb-4">
          Upload your PDF resume. It will be parsed and used for job matching and tailoring.
        </p>

        <div className="border-2 border-dashed border-border rounded-lg p-6 text-center mb-4 hover:border-accent/40 transition-colors">
          <input
            type="file"
            accept=".pdf"
            onChange={e => setFile(e.target.files[0])}
            className="hidden"
            id="resume-upload"
          />
          <label htmlFor="resume-upload" className="cursor-pointer">
            <p className="text-textSecondary text-sm">
              {file ? file.name : 'Click to select PDF resume'}
            </p>
          </label>
        </div>

        <button
          onClick={handleUpload}
          disabled={!file || uploading}
          className="px-4 py-2 bg-accent text-bg text-sm font-medium rounded hover:bg-accent/90 disabled:opacity-50 transition-colors"
        >
          {uploading ? 'Uploading...' : 'Upload Resume'}
        </button>

        {result && (
          <div className="mt-4 p-3 bg-success/10 border border-success/20 rounded text-success text-sm">
            Resume uploaded. Found {result.skills_found} skills.
          </div>
        )}
        {error && (
          <div className="mt-4 p-3 bg-danger/10 border border-danger/20 rounded text-danger text-sm">
            {error}
          </div>
        )}
      </div>

      <div className="bg-card border border-border rounded-lg p-6">
        <h2 className="text-textPrimary font-semibold mb-1">About</h2>
        <p className="text-textSecondary text-sm">JobHunt Copilot v1.0.0</p>
        <p className="text-textSecondary text-sm mt-1">Built by Venkata Naga Santosh Mukul Mokkapati · mukulmokkapati@gmail.com</p>
      </div>
    </div>
  )
}
