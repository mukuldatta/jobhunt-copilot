from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


class SponsorshipStatus(str, Enum):
    strong = "strong"
    moderate = "moderate"
    none = "none"
    contract = "contract"


class ContractType(str, Enum):
    fulltime = "fulltime"
    w2_contract = "w2_contract"
    c2c = "c2c"
    contract_to_hire = "contract_to_hire"


class JobStatus(str, Enum):
    new = "new"
    reviewed = "reviewed"
    applied = "applied"
    skipped = "skipped"


class JobSource(str, Enum):
    naukri = "naukri"
    linkedin = "linkedin"
    indeed = "indeed"
    dice = "dice"
    wellfound = "wellfound"


class ApplicationStatus(str, Enum):
    saved = "saved"
    applied = "applied"
    recruiter_screen = "recruiter_screen"
    technical = "technical"
    final_round = "final_round"
    offer = "offer"
    rejected = "rejected"


class ScoreBreakdown(BaseModel):
    skills_score: int = 0
    experience_score: int = 0
    domain_score: int = 0
    location_score: int = 0


class Job(BaseModel):
    job_id: str
    title: str
    company: str
    location: str
    description: str
    url: str
    posted_at: Optional[datetime] = None
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    source: JobSource
    sponsorship_status: Optional[SponsorshipStatus] = None
    contract_type: Optional[ContractType] = None
    match_score: Optional[int] = None
    score_breakdown: Optional[ScoreBreakdown] = None
    gap_analysis: Optional[List[str]] = []
    status: JobStatus = JobStatus.new


class JobResponse(Job):
    id: Optional[str] = None

    class Config:
        from_attributes = True


class Application(BaseModel):
    job_id: str
    applied_at: datetime = Field(default_factory=datetime.utcnow)
    status: ApplicationStatus = ApplicationStatus.saved
    tailored_resume_text: Optional[str] = None
    cover_letter: Optional[str] = None
    outreach_message: Optional[str] = None
    notes: Optional[str] = None
    follow_up_date: Optional[datetime] = None
    response_received: bool = False


class ApplicationResponse(Application):
    id: Optional[str] = None

    class Config:
        from_attributes = True


class ApplicationStatusUpdate(BaseModel):
    status: ApplicationStatus
    notes: Optional[str] = None


class Resume(BaseModel):
    version: int = 1
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)
    parsed_text: str
    skills: List[str] = []
    experience: List[str] = []
    education: List[str] = []


class Stats(BaseModel):
    total_jobs: int = 0
    high_match: int = 0
    medium_match: int = 0
    low_match: int = 0
    applied: int = 0
    interviews: int = 0
    last_scraped: Optional[datetime] = None


class ScrapeRequest(BaseModel):
    sources: Optional[List[str]] = None
    max_jobs: int = 50


class TailorRequest(BaseModel):
    job_id: str


class CoverLetterRequest(BaseModel):
    job_id: str


class OutreachRequest(BaseModel):
    job_id: str
