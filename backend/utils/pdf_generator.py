"""
Render tailored resume text as a PDF that matches the uploaded original.

Every constant here was measured off `resume/resume.pdf` rather than chosen —
Letter page, 50.4pt margins, Arial, the navy (31,78,121) used for the name,
the section headings and their rules, the 9pt grey contact line, the bullet at
59.4pt with its text hanging at 77.4pt, and the italic dates flush right. A
recruiter sees the tailored PDF, not the original, so the two should not look
like documents from different people.

Arial is embedded from the system when it can be found (the original is set in
Arial, and an embedded TrueType face is the only way to get a real bullet or em
dash). Without it we fall back to the core Helvetica face — metrically the same
shapes, but latin-1 only, so the text is transliterated first.
"""

import os
import re
from urllib.parse import urlparse

from fpdf import FPDF
from fpdf.enums import XPos, YPos

# ── Measured from resume/resume.pdf (points, Letter 612 x 792) ───────────────
PAGE = "Letter"
PAGE_W = 612.0
MARGIN = 50.4
TOP_MARGIN = 45.9
RULE_BLEED = 1.4          # section rules run 1.4pt past the text on both sides
BULLET_DOT_X = 59.4
BULLET_TEXT_X = 77.4

NAVY = (31, 78, 121)      # name, section headings, section rules
GREY = (85, 85, 85)       # contact line, company names, dates
BLACK = (0, 0, 0)
LINK_BLUE = (5, 99, 193)

NAME_SIZE, SECTION_SIZE, BODY_SIZE, CONTACT_SIZE = 18, 11, 9, 9
BODY_H, BULLET_H, ENTRY_H, NAME_H, SECTION_H = 10.4, 11.0, 12.5, 22.0, 13.0
SPACE_BEFORE_SECTION, SPACE_AFTER_RULE, SPACE_BEFORE_ENTRY = 8.0, 5.0, 7.0

_ARIAL = {
    "": ["C:/Windows/Fonts/arial.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    "B": ["C:/Windows/Fonts/arialbd.ttf",
          "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
          "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    "I": ["C:/Windows/Fonts/ariali.ttf",
          "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
          "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"],
}

# FPDF core fonts are latin-1 only, so characters outside it (the smart
# punctuation LLMs love) would render as "?". Transliterate the common ones to
# ASCII; latin-1 then covers the rest (including accented letters). Only used
# when no TrueType face was available.
_UNICODE_TO_ASCII = {
    "–": "-", "—": "-", "−": "-",           # en/em dash, minus
    "‘": "'", "’": "'", "‚": ",", "′": "'",  # single quotes
    "“": '"', "”": '"', "„": '"', "″": '"',  # double quotes
    "•": "-", "●": "-", "▪": "-", "‣": "-",   # bullets
    "…": "...", " ": " ", "​": "",           # ellipsis, nbsp, zwsp
    "™": "(TM)", "®": "(R)", "©": "(C)",
    "→": "->", "←": "<-", "≤": "<=", "≥": ">=",
}
_UNICODE_TABLE = {ord(k): v for k, v in _UNICODE_TO_ASCII.items()}

_BULLET_CHARS = ("•", "●", "▪", "‣", "-", "*", "·")
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
_POINT = rf"(?:{_MONTH}\s+)?\d{{4}}"
# "Oct 2024 – May 2026", "Jun 2021 - Jul 2022", "2019 — Present"
_DATE_RANGE = re.compile(rf"\s+({_POINT}\s*[-–—]\s*(?:Present|Current|{_POINT}))\s*$")
# "GenAI & Agentic: LangGraph, ..." — a short label introducing a list
_LABELLED = re.compile(r"^([A-Za-z][\w&/+.\- ]{0,28}):\s+(\S.*)$")
_SEPARATOR = re.compile(r"\s+[—–-]\s+")


def _to_latin1(text: str) -> str:
    return text.translate(_UNICODE_TABLE).encode("latin-1", "replace").decode("latin-1")


def _break_long_tokens(text: str, maxlen: int = 45) -> str:
    """Insert break points into unbreakable runs (long URLs, IDs) so a flowed
    line can wrap them — otherwise fpdf2 raises 'Not enough horizontal space'."""
    def brk(w):
        return w if len(w) <= maxlen else " ".join(w[i:i + maxlen] for i in range(0, len(w), maxlen))
    return " ".join(brk(w) for w in text.split(" "))


def _register_fonts(pdf: FPDF) -> str:
    """Embed Arial (or a metric-compatible stand-in) and return the family name.
    Falls back to the core Helvetica face, which has the same metrics but cannot
    render anything outside latin-1."""
    paths = {}
    for style, candidates in _ARIAL.items():
        for path in candidates:
            if os.path.exists(path):
                paths[style] = path
                break
    if len(paths) < len(_ARIAL):
        return "Helvetica"
    try:
        for style, path in paths.items():
            pdf.add_font("Resume", style, path)
        return "Resume"
    except Exception:
        return "Helvetica"


class _Doc:
    """Thin layout helper over FPDF: colours, styled runs, and flowed text."""

    def __init__(self):
        self.pdf = FPDF(format=PAGE, unit="pt")
        self.pdf.set_margins(MARGIN, TOP_MARGIN, MARGIN)
        self.pdf.set_auto_page_break(auto=True, margin=MARGIN)
        self.family = _register_fonts(self.pdf)
        self.unicode_ok = self.family != "Helvetica"
        self.pdf.add_page()

    def prep(self, line: str) -> str:
        if not self.unicode_ok:
            line = _to_latin1(line)
        return _break_long_tokens(line)

    def style(self, style: str = "", size: float = BODY_SIZE, colour=BLACK):
        self.pdf.set_font(self.family, style, size)
        self.pdf.set_text_color(*colour)

    def flow(self, height: float, runs: list):
        """Write styled runs inline, wrapping at the current margins. Returns
        with the cursor on the line below the last one written."""
        for text, style, colour in runs:
            if not text:
                continue
            self.style(style, BODY_SIZE, colour)
            self.pdf.write(height, text)
        self.pdf.ln(height)

    def rule(self):
        y = self.pdf.get_y()
        self.pdf.set_draw_color(*NAVY)
        self.pdf.set_line_width(0.7)
        self.pdf.line(MARGIN - RULE_BLEED, y, PAGE_W - MARGIN + RULE_BLEED, y)


# ── Line kinds ───────────────────────────────────────────────────────────────

def _is_section(line: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z\s&/|.\-]{3,39}", line)) and len(line) < 45


def _is_contact(line: str) -> bool:
    return "@" in line and "|" in line


def _is_bullet(line: str) -> bool:
    return line.startswith(_BULLET_CHARS)


def _link_for(segment: str, links: dict) -> str:
    """Match a contact segment ("LinkedIn", "Github") to a stored URL."""
    key = re.sub(r"[^a-z]", "", segment.lower())
    return (links or {}).get(key, "")


# ── Writers ──────────────────────────────────────────────────────────────────

def _write_name(doc: _Doc, line: str):
    doc.style("B", NAME_SIZE, NAVY)
    doc.pdf.cell(0, NAME_H, line, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _write_contact(doc: _Doc, line: str, links: dict):
    """Centred, with LinkedIn/GitHub as real underlined hyperlinks — the
    original carries them as PDF link annotations, and a tailored copy that
    prints the word without the link is a dead reference on a recruiter's
    screen."""
    parts = [p.strip() for p in line.split("|") if p.strip()]
    gap = "  |  "
    doc.style("", CONTACT_SIZE, GREY)
    gap_w = doc.pdf.get_string_width(gap)
    widths = []
    for p in parts:
        doc.style("U" if _link_for(p, links) else "", CONTACT_SIZE, GREY)
        widths.append(doc.pdf.get_string_width(p))
    total = sum(widths) + gap_w * (len(parts) - 1)

    y = doc.pdf.get_y()
    doc.pdf.set_xy(max(MARGIN, (PAGE_W - total) / 2), y)
    for i, (part, w) in enumerate(zip(parts, widths)):
        url = _link_for(part, links)
        doc.style("U" if url else "", CONTACT_SIZE, LINK_BLUE if url else GREY)
        doc.pdf.cell(w, BODY_H, part, link=url or "")
        if i < len(parts) - 1:
            doc.style("", CONTACT_SIZE, GREY)
            doc.pdf.cell(gap_w, BODY_H, gap)
    doc.pdf.ln(BODY_H)


def _write_section(doc: _Doc, line: str):
    doc.pdf.ln(SPACE_BEFORE_SECTION)
    doc.style("B", SECTION_SIZE, NAVY)
    doc.pdf.cell(0, SECTION_H, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    doc.rule()
    doc.pdf.ln(SPACE_AFTER_RULE)


def _write_bullet(doc: _Doc, line: str):
    text = line.lstrip("".join(_BULLET_CHARS) + " ").strip()
    marker = "•" if doc.unicode_ok else "-"
    doc.style("", BODY_SIZE, BLACK)
    doc.pdf.set_x(BULLET_DOT_X)
    doc.pdf.cell(BULLET_TEXT_X - BULLET_DOT_X, BULLET_H, marker)
    # Indent through the margin, not just x: a wrapped bullet's continuation
    # lines return to the left margin, so setting x alone gives the first line
    # an indent the rest of the bullet does not share.
    doc.pdf.set_left_margin(BULLET_TEXT_X)
    doc.pdf.set_x(BULLET_TEXT_X)
    doc.flow(BULLET_H, [(text, "", BLACK)])
    doc.pdf.set_left_margin(MARGIN)
    doc.pdf.set_x(MARGIN)


def _is_heading(line: str, next_line: str) -> bool:
    """A project title: a short line carrying no date that introduces bullets.
    The original sets these bold, and the lookahead is what keeps them from
    reading as body text."""
    return (bool(next_line) and _is_bullet(next_line)
            and len(line) < 70 and not line.endswith((".", ",", ";", ":")))


def _write_entry(doc: _Doc, left: str, date: str):
    """A role/degree line: bold title, grey employer, italic dates flush right."""
    doc.pdf.ln(SPACE_BEFORE_ENTRY)
    y = doc.pdf.get_y()

    doc.style("I", BODY_SIZE, GREY)
    date_w = doc.pdf.get_string_width(date) if date else 0.0
    if date:
        doc.pdf.set_xy(PAGE_W - MARGIN - date_w, y)
        doc.pdf.cell(date_w, ENTRY_H, date, align="R")

    # Only a dated line splits into title + employer. A project heading carries
    # no date and is bold end to end in the original, so leave it whole — its
    # dash is part of the title, not a separator.
    title, rest = _split_entry(left) if date else (left, "")
    doc.pdf.set_xy(MARGIN, y)
    if date:
        # Keep the title from running into the dates if it has to wrap.
        doc.pdf.set_right_margin(MARGIN + date_w + 12)
    runs = [(title, "B", BLACK)]
    if rest:
        runs.append(("  —  " if doc.unicode_ok else "  -  ", "", GREY))
        runs.append((rest, "", GREY))
    doc.flow(ENTRY_H, runs)
    doc.pdf.set_right_margin(MARGIN)


def _split_entry(left: str) -> tuple:
    parts = _SEPARATOR.split(left, 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (left, "")


def _write_labelled(doc: _Doc, label: str, rest: str):
    doc.flow(BODY_H, [(f"{label}: ", "B", BLACK), (rest, "", BLACK)])


def _write_body(doc: _Doc, line: str):
    doc.flow(BODY_H, [(line, "", BLACK)])


def generate_resume_pdf(text: str, output_path: str, links: dict = None):
    """Render `text` to `output_path`, styled like the uploaded original.

    `links` maps a contact-line label to a URL ({"linkedin": ..., "github": ...})
    and comes from the original PDF's link annotations — see
    `resume_parser.extract_links`.
    """
    doc = _Doc()
    # Spacing is structural rather than copied from blank source lines, so the
    # blanks go now — and a heading can then look ahead to the line below it.
    lines = [doc.prep(l.strip()) for l in text.split("\n") if l.strip()]

    for i, line in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        try:
            if i == 0:
                _write_name(doc, line)
            elif _is_contact(line):
                _write_contact(doc, line, links)
            elif _is_section(line):
                _write_section(doc, line)
            elif _is_bullet(line):
                _write_bullet(doc, line)
            elif (m := _DATE_RANGE.search(line)):
                _write_entry(doc, line[:m.start()].strip(), m.group(1))
            elif _is_heading(line, nxt):
                _write_entry(doc, line, "")
            elif (m := _LABELLED.match(line)):
                _write_labelled(doc, m.group(1), m.group(2))
            else:
                _write_body(doc, line)
        except Exception:
            # One pathological line must not cost the rest of the document —
            # but it must not silently vanish either, so leave its space and
            # put the cursor back where the next line starts.
            doc.pdf.set_left_margin(MARGIN)
            doc.pdf.set_right_margin(MARGIN)
            doc.pdf.set_x(MARGIN)
            doc.pdf.ln(BODY_H)

    doc.pdf.output(output_path)
