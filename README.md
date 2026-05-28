# JobHunt Copilot

An AI-powered job hunting assistant that scrapes job postings, scores them against your resume, sends alerts for high matches, and can auto-apply on LinkedIn and Naukri.

**Live:** React frontend on Vercel + FastAPI backend on Railway + MongoDB Atlas

---

## Features

- **Scrapes** LinkedIn and Naukri every 30 minutes for AI/ML/Data Engineering roles
- **Scores** each job 0–100% against your uploaded resume using Groq LLaMA 3.3 70B
- **Alerts** via email (SendGrid) and SMS (Twilio) when a job scores 70%+
- **Auto-applies** via Playwright (LinkedIn Easy Apply + Naukri one-click apply)
- **Tailors** your resume and generates a cover letter per job using AI
- **Tracks** all applications with status (applied → recruiter screen → offer)
- **Filters** jobs by score, status, source, sponsorship, and sorts by date or score

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
│   │   ├── h1b_checker.py        # H1B sponsorship lookup
│   │   └── scheduler.py          # APScheduler tasks
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
| `MONGODB_URI` | Yes | MongoDB Atlas connection string |
| `GROQ_API_KEY` | Yes | From console.groq.com (free) |
| `LLM_PROVIDER` | Yes | `groq` (default), `gemini`, or `anthropic` |
| `SENDGRID_API_KEY` | For alerts | From app.sendgrid.com |
| `SENDGRID_FROM_EMAIL` | For alerts | Verified sender email |
| `MY_EMAIL` | For alerts | Where to receive alerts |
| `MY_PHONE` | For SMS | E.164 format e.g. `+14155551234` |
| `TWILIO_ACCOUNT_SID` | For SMS | From console.twilio.com |
| `TWILIO_AUTH_TOKEN` | For SMS | From console.twilio.com |
| `TWILIO_PHONE_NUMBER` | For SMS | Your Twilio number |
| `LINKEDIN_EMAIL` | For auto-apply | LinkedIn login |
| `LINKEDIN_PASSWORD` | For auto-apply | LinkedIn password |
| `NAUKRI_EMAIL` | For auto-apply | Naukri login |
| `NAUKRI_PASSWORD` | For auto-apply | Naukri password |
| `USER_FIRST_NAME` | For auto-apply | Your first name |
| `USER_LAST_NAME` | For auto-apply | Your last name |

### 3. Run locally

**Backend**
```bash
cd backend
pip install -r requirements.txt
playwright install chromium
uvicorn main:app --reload --port 8000
```

**Frontend**
```bash
cd frontend
npm install
npm run dev
```

**Full stack with Docker**
```bash
docker-compose up --build
```

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

When you click **Auto Apply** on a job card:
1. AI tailors your resume for the specific job
2. Playwright logs into LinkedIn / Naukri
3. Fills the application form (resume upload, phone, work authorization)
4. Submits and marks the job as applied in the dashboard

If login fails 5+ consecutive times, you receive an email alert.

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
```

---

## License

MIT
