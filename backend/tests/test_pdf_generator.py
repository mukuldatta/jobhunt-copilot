"""
The tailored resume is what an employer receives, so a line that does not
reach the page is not a cosmetic bug — an entire job and a degree once fell
out of it. multi_cell leaves the cursor at the cell's right edge, and the next
full-width line then had zero width to draw in, so every other line of a
section was dropped without an error anywhere.
"""

import pdfplumber
import pytest

from utils.pdf_generator import generate_resume_pdf


RESUME = """VENKATA NAGA SANTOSH MUKUL MOKKAPATI
(+91) 9949144498 | mokkapatimukul@gmail.com | LinkedIn | GitHub
TECHNICAL SKILLS
GenAI & Agentic: LangGraph, CrewAI, LangChain, LlamaIndex, RAG Pipelines, MCP
ML & AI: PyTorch, Scikit-learn, Transformers, Fine-tuning, NLP
Backend & Cloud: Python, FastAPI, Node.js, AWS, Docker, Kubernetes
Databases & Analytics: PostgreSQL, MongoDB, Pinecone, Redis, Pandas
PROFESSIONAL EXPERIENCE
AI Software Engineer \u2014 Incrivelsoft LLC Oct 2024 \u2013 May 2026
\u25cf Designed and deployed full-stack AI applications with RESTful APIs and
microservices, leveraging Python, FastAPI, and React across several teams.
Software Engineer \u2014 TCS (Tata Consultancy Services) Jun 2021 \u2013 Jul 2022
\u25cf Developed ETL pipelines and designed data lakes for insurance records.
EDUCATION
Master of Professional Studies in Data Science \u2014 UMBC Aug 2022 \u2013 May 2024
Bachelor of Technology in Computer Science & Engineering \u2014 JNTU Jun 2016
"""


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    out = tmp_path_factory.mktemp("pdf") / "resume.pdf"
    generate_resume_pdf(RESUME, str(out))
    with pdfplumber.open(str(out)) as pdf:
        return " ".join((p.extract_text() or "") for p in pdf.pages)


@pytest.mark.parametrize("phrase", [
    "ML & AI: PyTorch",                     # 2nd line of a section
    "Databases & Analytics: PostgreSQL",    # 4th line of a section
    "TCS (Tata Consultancy Services)",      # a whole job, after a bullet
    "Bachelor of Technology",               # a degree, after another degree
])
def test_every_line_reaches_the_page(rendered, phrase):
    assert phrase in " ".join(rendered.split())


LINKS = {"linkedin": "https://www.linkedin.com/in/mukul/",
         "github": "https://github.com/mukuldatta"}


def _render(tmp_path, links=None, name="resume.pdf"):
    out = tmp_path / name
    generate_resume_pdf(RESUME, str(out), links)
    return str(out)


def test_contact_links_become_real_annotations(tmp_path):
    """The words "LinkedIn" and "Github" carry URLs in the original as PDF link
    annotations. Printing the word without the link hands a recruiter a dead
    reference, so the tailored copy has to re-attach them."""
    with pdfplumber.open(_render(tmp_path, LINKS)) as pdf:
        uris = {a["uri"] for page in pdf.pages for a in (page.hyperlinks or [])}
    assert uris == set(LINKS.values())


def test_page_matches_the_original_letter_geometry(tmp_path):
    with pdfplumber.open(_render(tmp_path, LINKS)) as pdf:
        page = pdf.pages[0]
        assert (round(page.width), round(page.height)) == (612, 792)
        # section rules span the text column, edge to edge
        rules = [r for r in page.lines if r["width"] > 400]
        assert rules and all(round(r["x0"]) == 49 for r in rules)


def test_renders_without_a_truetype_face(tmp_path, monkeypatch):
    """No Arial on the box (a Linux container) must degrade to the core font,
    not crash — and the non-latin-1 characters must survive as ASCII."""
    import utils.pdf_generator as gen
    monkeypatch.setattr(gen, "_ARIAL", {k: ["/nonexistent.ttf"] for k in gen._ARIAL})
    path = _render(tmp_path, LINKS, name="fallback.pdf")
    with pdfplumber.open(path) as pdf:
        text = " ".join(" ".join((p.extract_text() or "").split()) for p in pdf.pages)
    assert "TCS (Tata Consultancy Services)" in text
    assert "Bachelor of Technology" in text
