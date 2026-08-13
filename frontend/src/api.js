import axios from 'axios'

// Development: VITE_API_URL is unset → Vite proxies /api → localhost:8000
// Production:  VITE_API_URL=https://your-app.railway.app (set in Vercel dashboard)
const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api',
  timeout: 60000,
})

const RETRIES = 2
const RETRY_STATUS = [502, 503, 504]

/**
 * Retry a GET that failed for reasons unrelated to what was asked.
 *
 * Only GETs, and only on a network error or a gateway status — a POST here
 * tailors a resume or submits an application, and neither is safe to repeat
 * on a timeout you cannot prove was a timeout.
 *
 * Also attaches `userMessage`, so call sites stop rewriting
 * `e.response?.data?.detail || '...'` — it appeared fourteen times.
 */
api.interceptors.response.use(
  (r) => r,
  async (error) => {
    const config = error.config || {}
    const status = error.response?.status
    const retriable =
      config.method === 'get' &&
      (error.code === 'ECONNABORTED' || !error.response || RETRY_STATUS.includes(status))

    if (retriable) {
      config._retries = (config._retries || 0) + 1
      if (config._retries <= RETRIES) {
        await new Promise((r) => setTimeout(r, 400 * 2 ** (config._retries - 1)))
        return api(config)
      }
    }

    error.userMessage =
      error.response?.data?.detail ||
      (error.response
        ? `Request failed (${status}).`
        : 'Cannot reach the backend — is it running?')
    return Promise.reject(error)
  }
)

/** The message to show a user for any rejected request from this client. */
export const errorMessage = (e, fallback = 'Something went wrong.') =>
  e?.userMessage || e?.response?.data?.detail || e?.message || fallback

export const getJobs = (params) => api.get('/jobs', { params })
export const getJob = (jobId) => api.get(`/jobs/${jobId}`)
export const tailorResume = (jobId) => api.post(`/jobs/${jobId}/tailor`)
export const generateCoverLetter = (jobId) => api.post(`/jobs/${jobId}/cover-letter`)
export const generateOutreach = (jobId) => api.post(`/jobs/${jobId}/outreach`)
export const markApplied = (jobId) => api.post(`/jobs/${jobId}/apply`)
export const setJobStatus = (jobId, status) => api.patch(`/jobs/${jobId}/status`, { status })

export const getApplications = (params) => api.get('/applications', { params })
export const updateApplicationStatus = (id, data) => api.patch(`/applications/${id}/status`, data)

export const getResume = () => api.get('/resume')
export const uploadResume = (file) => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/resume/upload', form, { headers: { 'Content-Type': 'multipart/form-data' } })
}

export const downloadTailoredPdf = (jobId) => {
  // Read the client's own baseURL rather than re-deriving it — two copies of
  // this expression is two things to keep in step.
  window.open(`${api.defaults.baseURL}/jobs/${jobId}/tailor-pdf`, '_blank')
}
export const autoApply = (jobId) => api.post(`/jobs/${jobId}/auto-apply`)

export const getStats = () => api.get('/stats')
export const triggerScrape = (data) => api.post('/scrape/trigger', data)
export const triggerScoring = () => api.post('/score/trigger')

export const getProfile = () => api.get('/profile')
export const saveProfile = (data) => api.put('/profile', data)
export const getPendingQuestions = () => api.get('/profile/questions')
export const answerQuestion = (question, answer) => api.post('/profile/questions', { question, answer })
export const dismissQuestion = (question) => api.delete('/profile/questions', { params: { question } })

export const getAuthStatus = () => api.get('/auth/status')
export const platformLogin = (platform) => api.post(`/auth/${platform}/login`)
export const platformCheck = (platform) => api.post(`/auth/${platform}/check`, null, { timeout: 90000 })
export const runAutoApply = (data) => api.post('/auto-apply/run', data)

export const getAgentState = () => api.get('/agent/state')
export const getPlatforms = () => api.get('/platforms')
export const getSettings = () => api.get('/settings')
export const saveSettings = (data) => api.put('/settings', data)
