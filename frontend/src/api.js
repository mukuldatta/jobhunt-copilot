import axios from 'axios'

// Development: VITE_API_URL is unset → Vite proxies /api → localhost:8000
// Production:  VITE_API_URL=https://your-app.railway.app (set in Vercel dashboard)
const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api',
  timeout: 60000,
})

export const getJobs = (params) => api.get('/jobs', { params })
export const getJob = (jobId) => api.get(`/jobs/${jobId}`)
export const tailorResume = (jobId) => api.post(`/jobs/${jobId}/tailor`)
export const generateCoverLetter = (jobId) => api.post(`/jobs/${jobId}/cover-letter`)
export const generateOutreach = (jobId) => api.post(`/jobs/${jobId}/outreach`)
export const markApplied = (jobId) => api.post(`/jobs/${jobId}/apply`)

export const getApplications = (params) => api.get('/applications', { params })
export const updateApplicationStatus = (id, data) => api.patch(`/applications/${id}/status`, data)

export const getResume = () => api.get('/resume')
export const uploadResume = (file) => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/resume/upload', form, { headers: { 'Content-Type': 'multipart/form-data' } })
}

export const downloadTailoredPdf = (jobId) => {
  const base = import.meta.env.VITE_API_URL || '/api'
  window.open(`${base}/jobs/${jobId}/tailor-pdf`, '_blank')
}
export const autoApply = (jobId) => api.post(`/jobs/${jobId}/auto-apply`)

export const getStats = () => api.get('/stats')
export const triggerScrape = (data) => api.post('/scrape/trigger', data)
export const triggerScoring = () => api.post('/score/trigger')
