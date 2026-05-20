# JobHunt Copilot — CLAUDE.md

This file gives Claude Code full context about the JobHunt Copilot project.
Read this entirely before making any changes or suggestions.

---

## 🎯 Project Overview

JobHunt Copilot is an AI-powered job hunting assistant that:
- Scrapes job postings from LinkedIn, Indeed, Dice.com every 30 minutes
- Filters for H1B-sponsoring companies and contract roles
- Scores each job against the user's resume (0-100%)
- Sends instant alerts for high-match jobs
- Tailors resume and generates cover letters per job
- Tracks all applications in a dashboard

This is both a **portfolio project** and a **real tool** the developer
is actively using for their own job search.

---

## 👤 Developer Context

- **Name:** Venkata Naga Santosh Mokkapati (Mukul)
- **Role:** AI Software Engineer
- **Visa:** F-1 OPT — needs H1B sponsorship for full time roles
- **Open to:** Full time (H1B sponsors) + Contract + Contract-to-hire
- **Target roles:** AI Engineer, ML Engineer, GenAI Engineer, Data Engineer
- **Target locations:** Remote + Hyderabad India + US (any state)
- **Email:** mukulmokkapati@gmail.com
- **GitHub:** github.com/mukuldatta

---

## 🏗️ Architecture Overview

```
Job Sources (LinkedIn, Indeed, Dice, Wellfound)
          ↓
    Scraper Agent (Playwright)
          ↓
    H1B + Contract Filter
          ↓
    Resume Match Scorer (Groq LLaMA 3.3 70B)
          ↓
    MongoDB Atlas (store jobs + scores)
          ↓
    Alert System (SendGrid + Twilio)
          ↓
    React Dashboard (view + act on jobs)
          ↓
    Tailor / Cover Letter / Outreach Agents
```

---

## 📁 Project Structure

```
jobhunt-copilot/
├── backend/
│   ├── main.py                   # FastAPI app — all routes
│   ├── llm_provider.py           # LLM abstraction layer
│   ├── agents/
│   │   ├── scraper_agent.py      # Playwright job scraper
│   │   ├── scorer_agent.py       # Resume match scorer
│   │   ├── tailor_agent.py       # Resume tailor
│   │   ├── cover_letter_agent.py # Cover letter generator
│   │   └── outreach_agent.py     # LinkedIn outreach message
│   ├── services/
│   │   ├── h1b_checker.py        # H1B sponsorship lookup
│   │   ├── alert_service.py      # Email + SMS alerts
│   │   └── scheduler.py          # APScheduler — runs every 30 mins
│   ├── models/
│   │   └── schemas.py            # All Pydantic models
│   ├── db/
│   │   └── mongodb.py            # MongoDB connection + CRUD
│   └── utils/
│       ├── resume_parser.py      # PDF resume parser
│       └── job_parser.py         # Job description cleaner
├── frontend/
│   ├── src/
│   │   ├── App.jsx               # Root component + routing
│   │   ├── pages/
│   │   │   ├── Dashboard.jsx     # Stats overview
│   │   │   ├── Jobs.jsx          # Scraped jobs + scores
│   │   │   ├── Applications.jsx  # Application tracker
│   │   │   └── Settings.jsx      # User preferences
│   │   └── components/
│   │       ├── JobCard.jsx       # Job card with match score
│   │       ├── ScoreGauge.jsx    # Circular score visual
│   │       ├── ResumeModal.jsx   # Tailored resume preview
│   │       └── OutreachModal.jsx # Outreach message preview
│   └── package.json
├── resume/
│   └── resume.pdf                # Developer resume — DO NOT MODIFY
├── .env                          # Environment variables — never commit
├── .env.example                  # Template for env vars
├── Dockerfile                    # Backend container
├── docker-compose.yml            # Full stack orchestration
├── requirements.txt              # Python dependencies
├── CLAUDE.md                     # This file
└── README.md                     # Project documentation
```

---

## 🔑 Critical Rules — Read Before Every Change

### 1. LLM Provider
**ALWAYS use LLMProvider from llm_provider.py**
Never import Groq, Anthropic, or Gemini directly in agents.
Every agent must call `self.llm.complete(prompt)` only.

```python
# CORRECT
from llm_provider import LLMProvider
self.llm = LLMProvider(provider=os.getenv("LLM_PROVIDER", "groq"))
result = self.llm.complete(prompt)

# WRONG — never do this
from groq import Groq
client = Groq(api_key=...)
```

### 2. Resume Safety
**NEVER fabricate or add skills/experience that don't exist.**
The tailor agent must only reframe existing experience.
This is both an ethical rule and a legal protection for the user.

### 3. Resume File
`resume/resume.pdf` is the developer's actual resume.
**Never modify, overwrite, or delete this file.**
Only read from it.

### 4. Environment Variables
**Never hardcode API keys or credentials.**
Always use `os.environ.get("KEY_NAME")`.
Never commit .env file.

### 5. MongoDB
Always use async motor client for MongoDB operations.
Never use synchronous pymongo in FastAPI routes.

```python
# CORRECT
from motor.motor_asyncio import AsyncIOMotorClient

# WRONG in FastAPI
from pymongo import MongoClient
```

### 6. Scraping Ethics
- Always add random delays between requests (2-5 seconds)
- Respect robots.txt
- Never scrape more than 100 jobs per run per source
- Always set realistic browser user agents in Playwright

---

## 🤖 LLM Provider Details

Default: **Groq** (free, fast, LLaMA 3.3 70B)
Fallback: **Gemini Flash** (free)
Optional: **Anthropic Claude** (paid, highest quality)

Switching providers: change `LLM_PROVIDER` in `.env` — no code changes needed.

```python
# llm_provider.py behavior
provider = os.getenv("LLM_PROVIDER", "groq")  # default groq
llm = LLMProvider(provider=provider)
result = llm.complete(prompt)
```

**Groq model:** `llama-3.3-70b-versatile`
**Gemini model:** `gemini-1.5-flash`
**Anthropic model:** `claude-sonnet-4-20250514`

---

## 📊 MongoDB Collections

### jobs
```json
{
  "job_id": "unique hash of url+title+company",
  "title": "AI Engineer",
  "company": "Microsoft",
  "location": "Remote",
  "description": "full job description text",
  "url": "https://...",
  "posted_at": "datetime",
  "scraped_at": "datetime",
  "source": "linkedin | indeed | dice | wellfound",
  "sponsorship_status": "strong | moderate | none | contract",
  "contract_type": "fulltime | w2_contract | c2c | contract_to_hire",
  "match_score": 87,
  "score_breakdown": {
    "skills_score": 35,
    "experience_score": 28,
    "domain_score": 18,
    "location_score": 6
  },
  "gap_analysis": ["missing: LangGraph", "missing: Kubernetes"],
  "status": "new | reviewed | applied | skipped"
}
```

### applications
```json
{
  "job_id": "ref to jobs collection",
  "applied_at": "datetime",
  "status": "saved | applied | recruiter_screen | technical | final_round | offer | rejected",
  "tailored_resume_text": "full tailored resume text",
  "cover_letter": "full cover letter text",
  "outreach_message": "LinkedIn message text",
  "notes": "free text notes",
  "follow_up_date": "datetime",
  "response_received": false
}
```

### resume
```json
{
  "version": 1,
  "uploaded_at": "datetime",
  "parsed_text": "full resume text",
  "skills": ["Python", "CrewAI", "FastAPI", "..."],
  "experience": ["AI Software Engineer at Incrivelsoft", "..."],
  "education": ["M.S. Data Science UMBC", "B.Tech IT JNTU"]
}
```

---

## 🔍 Job Match Scoring Logic

Score breakdown (must always add to 100):
- **Skills match:** 40 points — how many required skills match resume
- **Experience match:** 30 points — years and level alignment
- **Domain match:** 20 points — industry/domain relevance
- **Location match:** 10 points — remote/hybrid/onsite preference

Alert thresholds:
- **>= 70** → High match → immediate email + SMS alert
- **50-69** → Medium match → daily digest email only
- **< 50** → Low match → stored in DB, no alert

---

## 🚨 Alert System

### Email (SendGrid)
Triggered when: score >= 70 AND posted < 2 hours ago

Email content:
- Job title + company + match score
- Top 3 matching skills
- Top 3 skill gaps
- Direct apply URL
- Action links: Tailor Resume / Cover Letter / Outreach

### SMS (Twilio)
Same trigger as email.
Format: `"🔥 {score}% match: {title} @ {company}. Apply: {url}"`
Max 160 characters.

---

## ⚙️ Scheduler

APScheduler runs these jobs:
- **Every 30 minutes:** scrape_jobs() — scrape all sources
- **Every 30 minutes:** score_new_jobs() — score unscored jobs
- **Every 30 minutes:** send_alerts() — alert on high matches
- **Every 24 hours:** cleanup_old_jobs() — remove jobs older than 7 days

---

## 🎨 Frontend Design System

```
Background:     #0F1117
Card background:#1A1D2E
Primary accent: #4FC3F7
Success green:  #4CAF50
Warning yellow: #FFC107
Danger red:     #FF5370
Text primary:   #E0E0E0
Text secondary: #9E9E9E
Border:         #2A2D3E
Font:           Inter
```

Score gauge colors:
- 0-49%  → #FF5370 (red — low match)
- 50-69% → #FFC107 (yellow — medium match)
- 70-100%→ #4CAF50 (green — high match)

---

## 🌐 API Routes

```
GET  /health                    # Health check
GET  /resume                    # Get parsed resume
POST /resume/upload             # Upload new resume PDF
GET  /jobs                      # Get all scraped jobs (paginated)
GET  /jobs/{job_id}             # Get single job details
POST /jobs/{job_id}/tailor      # Tailor resume for job
POST /jobs/{job_id}/cover-letter# Generate cover letter
POST /jobs/{job_id}/outreach    # Generate outreach message
POST /jobs/{job_id}/apply       # Mark as applied
GET  /applications              # Get all applications
PATCH /applications/{id}/status # Update application status
GET  /stats                     # Dashboard statistics
POST /scrape/trigger            # Manually trigger scrape
```

---

## 📦 Dependencies

### Python (requirements.txt)
```
fastapi==0.111.0
uvicorn==0.30.0
motor==3.4.0                # async MongoDB
pymongo==4.7.2
pydantic==2.7.0
python-dotenv==1.0.1
groq==0.9.0                 # primary LLM
anthropic==0.28.0           # optional LLM
google-generativeai==0.7.0  # fallback LLM
crewai==0.28.0
playwright==1.44.0
beautifulsoup4==4.12.3
apscheduler==3.10.4
sendgrid==6.11.0
twilio==9.1.0
pdfplumber==0.11.0
pypdf2==3.0.1
python-multipart==0.0.9
httpx==0.27.0
```

### Node (package.json key deps)
```
react + vite
tailwindcss
recharts
axios
react-router-dom
```

---

## 🔐 Environment Variables

```bash
# LLM Provider
LLM_PROVIDER=groq

# Groq (Primary — Free)
GROQ_API_KEY=

# Anthropic (Optional — Paid)
ANTHROPIC_API_KEY=

# Gemini (Optional — Free fallback)
GEMINI_API_KEY=

# MongoDB
MONGODB_URI=

# SendGrid
SENDGRID_API_KEY=
SENDGRID_FROM_EMAIL=mukulmokkapati@gmail.com

# Twilio
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=

# Personal
MY_EMAIL=mukulmokkapati@gmail.com
MY_PHONE=

# LinkedIn (for scraping)
LINKEDIN_EMAIL=
LINKEDIN_PASSWORD=

# App
FRONTEND_URL=http://localhost:3000
BACKEND_URL=http://localhost:8000
```

---

## 🚀 Running Locally

### Backend
```bash
cd backend
pip install -r requirements.txt
playwright install chromium
uvicorn main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

### Full Stack with Docker
```bash
docker-compose up --build
```

---

## 🧪 Testing

When testing scrapers:
- Use a small batch (max 10 jobs) to avoid rate limiting
- Always check MongoDB for stored results after scraping
- Verify deduplication by running scraper twice — no duplicates should appear

When testing scoring:
- Use sample job descriptions from LinkedIn
- Expected score for strong AI Engineer role match: 70-90%
- Expected score for unrelated role: < 30%

When testing alerts:
- Use SendGrid sandbox mode for email testing
- Use Twilio test credentials for SMS testing
- Never send real alerts during development

---

## 📝 Teaching Approach

This project is being built as a learning exercise.
When making changes or explaining code:
- Always explain WHY before HOW
- Ask the developer questions before giving answers
- Give hints before full solutions
- Relate everything back to the job search problem
- Summarize learnings after each major component

The developer understands:
- Python, FastAPI, React basics
- MongoDB, Pinecone, Docker
- CrewAI and multi-agent concepts
- ML fundamentals (built fraud detection system)

The developer is learning:
- Playwright web scraping
- APScheduler
- SendGrid + Twilio integrations
- Production deployment on Railway + Vercel

---

## ⚠️ Known Limitations + Workarounds

**LinkedIn scraping:**
LinkedIn actively blocks scrapers. Use these workarounds:
- Random delays 3-7 seconds between requests
- Rotate user agents
- Use LinkedIn's official Job Search URL params
- If blocked, fall back to Indeed + Dice which are more scraper-friendly

**H1B data:**
h1bdata.info may be slow or rate limit.
Cache sponsorship results in MongoDB for 30 days.
Don't re-query if company was checked recently.

**Groq rate limits:**
Free tier: 14,400 requests/day, 500k tokens/minute.
If hit, add exponential backoff and retry logic.
Fallback to Gemini Flash automatically if Groq fails.
