# JobHunt Copilot

An AI-powered job hunting assistant that scrapes job postings, scores them against your resume, sends alerts for high matches, and can auto-apply on Naukri, LinkedIn, and Indeed. Currently focused on the **India** market and built to run **locally** (headed browser on a residential IP).

---

## Features

- **Scrapes** Naukri, LinkedIn, and Indeed (India) for AI/ML/Data Engineering roles
- **Scores** each job 0–100% against your uploaded resume using Groq LLaMA 3.3 70B
- **Alerts** via email (SendGrid) and SMS (Twilio) when a job scores 70%+
- **Auto-applies** via Playwright with a manual sign-in you do once per session
- **Tailors** your resume (validated — no fabrication) and generates a cover letter per job
- **Tracks** all applications with status (applied → recruiter screen → offer)
- **Filters** jobs by score, status, source, and sorts by date or score

---

## Stack

| Layer | Tech |
|---|---|
| Frontend | React + Vite + Tailwind CSS |
| Backend | FastAPI + Python |
| Database | MongoDB Atlas (Motor async driver) |
| Scraping | Playwright (Chromium) |
| LLM | Groq LLaMA 3.3 70B (primary) · Gemini Flash (fallback) |
| Alerts | SendGrid (email) · Twilio (SMS) |
| Scheduler | APScheduler (every 30 min) |
| Deploy | Railway (backend) · Vercel (frontend) |

---

## Project Structure

```
jobhunt-copilot/
├── backend/
│   ├── main.py                   # FastAPI app — all routes
│   ├── llm_provider.py           # LLM abstraction (Groq / Gemini / Anthropic)
│   ├── agents/
│   │   ├── scraper_agent.py      # Playwright scraper (LinkedIn, Naukri)
│   │   ├── scorer_agent.py       # Resume match scorer
│   │   ├── tailor_agent.py       # Resume tailor
│   │   ├── cover_letter_agent.py # Cover letter generator
│   │   ├── outreach_agent.py     # LinkedIn outreach message
│   │   └── apply_agent.py        # Auto-apply via Playwright
│   ├── services/
│   │   ├── alert_service.py      # Email + SMS alerts
│   │   ├── orchestrator.py       # Autonomous apply cycle (score-gated + caps)
│   │   └── scheduler.py          # APScheduler tasks
│   ├── config/
│   │   └── apply_profile.py      # Structured answers to screening questions
│   ├── db/
│   │   └── mongodb.py            # MongoDB CRUD
│   └── utils/
│       ├── resume_parser.py      # PDF resume parser
│       ├── job_parser.py         # Job description cleaner
│       └── pdf_generator.py      # Tailored resume → PDF (fpdf2)
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── Dashboard.jsx     # Stats + trigger scrape/score
│       │   ├── Jobs.jsx          # Scraped jobs with filters
│       │   ├── Applications.jsx  # Application tracker
│       │   └── Settings.jsx      # Resume upload + preferences
│       └── components/
│           ├── JobCard.jsx       # Job card with auto-apply
│           ├── ResumeModal.jsx   # Tailored resume + PDF download
│           └── OutreachModal.jsx # Outreach message preview
├── Dockerfile
├── docker-compose.yml
├── railway.toml
├── requirements.txt
└── .env.example
```

---

## Setup

### 1. Clone

```bash
git clone https://github.com/mukuldatta/jobhunt-copilot.git
cd jobhunt-copilot
```

### 2. Environment variables

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `MONGODB_URI` | Yes | `mongodb://localhost:27017/jobhunt` (local Docker) or an Atlas string |
| `GROQ_API_KEY` | Yes | From console.groq.com (free) |
| `LLM_PROVIDER` | Yes | `groq` (default), `gemini`, or `anthropic` |
| `SENDGRID_API_KEY` | For alerts | From app.sendgrid.com |
| `SENDGRID_FROM_EMAIL` | For alerts | Verified sender email |
| `MY_EMAIL` | For alerts | Where to receive alerts |
| `MY_PHONE` | For SMS | E.164 format e.g. `+919xxxxxxxxx` |
| `TWILIO_ACCOUNT_SID` | For SMS | From console.twilio.com |
| `TWILIO_AUTH_TOKEN` | For SMS | From console.twilio.com |
| `TWILIO_PHONE_NUMBER` | For SMS | Your Twilio number |
| `USER_FIRST_NAME` / `USER_LAST_NAME` | For apply | Used on application forms |

> **No platform passwords needed.** Auto-apply uses a **manual login you do once
> per session** (the browser opens, you sign in, the session is saved) — the app
> never stores LinkedIn/Naukri credentials.

**Auto-apply tuning (optional):**

| Variable | Default | Description |
|---|---|---|
| `AUTO_APPLY_ENABLED` | unset | Set to enable the scheduled auto-apply cycle |
| `AUTO_APPLY_MIN_SCORE` | `70` | Only apply to jobs scoring at/above this |
| `AUTO_APPLY_DAILY_CAP` | `20` | Max applications per day |
| `AUTO_APPLY_PER_RUN` | `5` | Max applications per cycle |
| `APPLY_HUMAN_TIMEOUT` | `300` | Seconds to wait for you to clear a CAPTCHA |
| `APPLY_REQUIRES_SPONSORSHIP` | `no` | Answer to sponsorship screening questions |

### 3. Run locally

> **Important:** scraping Naukri/Indeed and auto-applying use a **headed real
> Chrome** browser on a residential IP (these sites block headless/cloud), so the
> backend must run **locally on your own machine**, not on a headless server.
> Requires **Google Chrome** installed and **Python 3.11+**.

**Database (local MongoDB via Docker)**
```bash
docker compose up -d mongo      # persistent volume; data survives restarts
```
Point `.env` at it: `MONGODB_URI=mongodb://localhost:27017/jobhunt`
(or use your own MongoDB Atlas cluster).

**Backend**
```bash
cd backend
pip install -r requirements.txt
playwright install chromium        # fallback browser; real Chrome is preferred
uvicorn main:app --reload --port 8000
```

**Frontend**
```bash
cd frontend
npm install
npm run dev
```

A Chrome window will open during scraping/apply — that's expected. Any CAPTCHA
pauses and waits for you to solve it in that window.

---

## Deployment

### Backend → Railway

1. Create a new Railway project and connect this repo
2. Railway auto-detects the `Dockerfile` and `railway.toml`
3. Add all environment variables in Railway → Variables
4. Set MongoDB Atlas Network Access to allow `0.0.0.0/0`

### Frontend → Vercel

1. Import this repo in Vercel
2. Set **Root Directory** to `frontend`
3. Add environment variable: `VITE_API_URL=https://your-app.up.railway.app`
4. Deploy

---

## How It Works

### Scoring (0–100%)

| Category | Weight |
|---|---|
| Skills match | 40 pts |
| Experience match | 30 pts |
| Domain relevance | 20 pts |
| Location preference | 10 pts |

### Alert thresholds

| Score | Action |
|---|---|
| 70%+ | Immediate email + SMS |
| 50–69% | Daily digest email |
| < 50% | Stored in DB, no alert |

### Auto-apply

1. Sign in once per session via **Settings → Login** (a browser opens; you sign
   in, the session is saved to a persistent profile and reused)
2. AI tailors your resume + generates a cover letter for the specific job, and
   the resume is validated (no fabricated skills) before submission
3. Playwright opens the job in your signed-in session and fills the form —
   screening questions are answered from a structured profile, and anything it
   can't answer safely (or a CAPTCHA) pauses for you to handle in the window
4. Submits and records the application

Run it for a batch from **Settings → Check & Submit Applications** (dry-run
preview, then submit), gated by a daily cap and min-score.

---

## API Routes

```
GET  /health
GET  /resume
POST /resume/upload
GET  /jobs                     ?min_score, status, source, sponsorship, sort_by, search
GET  /jobs/{job_id}
POST /jobs/{job_id}/tailor
GET  /jobs/{job_id}/tailor-pdf
POST /jobs/{job_id}/cover-letter
POST /jobs/{job_id}/outreach
POST /jobs/{job_id}/apply
POST /jobs/{job_id}/auto-apply
GET  /applications
PATCH /applications/{id}/status
GET  /stats
POST /scrape/trigger
POST /score/trigger
GET  /auth/status                 # per-platform sign-in state
POST /auth/{platform}/login       # open browser to sign in (session saved)
POST /auth/{platform}/check       # live-probe whether a session is still valid
POST /auto-apply/run              # {dry_run|force, max_apply} — batch apply cycle
```

---

## License

MIT
