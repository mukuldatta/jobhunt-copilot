from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum


class JobStatus(str, Enum):
    new = "new"
    reviewed = "reviewed"
    applied = "applied"
    skipped = "skipped"
    expired = "expired"          # posting closed — terminal, and not yours to finish
    manual_required = "manual_required"


class ApplicationStatus(str, Enum):
    saved = "saved"
    applied = "applied"
    recruiter_screen = "recruiter_screen"
    technical = "technical"
    final_round = "final_round"
    offer = "offer"
    rejected = "rejected"


class ApplicationStatusUpdate(BaseModel):
    status: ApplicationStatus
    notes: Optional[str] = None


class JobStatusUpdate(BaseModel):
    status: JobStatus


class ScrapeRequest(BaseModel):
    sources: Optional[List[str]] = None
    max_jobs: int = 50


class QAEntry(BaseModel):
    question: str
    answer: str


class ApplyProfile(BaseModel):
    """Answers used to fill job-application forms. Filled once via the UI."""
    # Identity / contact
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    current_city: Optional[str] = "Hyderabad"
    # Work eligibility
    authorized_to_work: bool = True
    requires_sponsorship: bool = False
    # Experience / availability
    total_years_experience: Optional[str] = "3"
    notice_period_days: Optional[str] = "30"
    earliest_start: Optional[str] = "Immediately"
    willing_to_relocate: bool = True
    willing_onsite_hybrid: bool = True
    # Compensation (blank => ask a human rather than guess)
    current_ctc: Optional[str] = None
    expected_ctc: Optional[str] = None
    # Education / background
    highest_degree: Optional[str] = None
    has_bachelors: bool = True
    # Free-form: skill -> years, e.g. {"Python": "3", "LangChain": "2"}
    skill_years: dict = {}
    # Language -> proficiency, e.g. {"English": "Native or bilingual proficiency"}
    languages: dict = {}
    # Learned answers to specific questions seen on real forms
    qa: List[QAEntry] = []
    # Anything else the LLM should know when answering
    notes: Optional[str] = None


class AnswerUpsert(BaseModel):
    question: str
    answer: str


class AutoApplyRunRequest(BaseModel):
    max_apply: Optional[int] = None
    dry_run: bool = False
    force: bool = False


class AgentRules(BaseModel):
    """
    The knobs Setup > Agent rules writes. Every field is optional: a saved value
    overrides the matching env var, and anything left unset falls back to it.
    """
    min_score: Optional[int] = Field(default=None, ge=0, le=100)
    daily_cap: Optional[int] = Field(default=None, ge=0, le=500)
    per_run: Optional[int] = Field(default=None, ge=1, le=100)
    interval_minutes: Optional[int] = Field(default=None, ge=5, le=1440)
    region: Optional[str] = None
    auto_apply_enabled: Optional[bool] = None
    dry_run: Optional[bool] = None
    alerts_enabled: Optional[bool] = None
    sms_alerts: Optional[bool] = None
