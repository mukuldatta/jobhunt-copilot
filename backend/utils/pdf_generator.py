from fpdf import FPDF
import re

# FPDF core fonts are latin-1 only, so characters outside it (the smart
# punctuation LLMs love) would render as "?". Transliterate the common ones to
# ASCII first; latin-1 then covers the rest (including accented letters).
_UNICODE_TO_ASCII = {
    "–": "-", "—": "-", "−": "-",           # en/em dash, minus
    "‘": "'", "’": "'", "‚": ",", "′": "'",  # single quotes
    "“": '"', "”": '"', "„": '"', "″": '"',  # double quotes
    "•": "-", "●": "-", "▪": "-", "‣": "-",   # bullets
    "…": "...", " ": " ", "​": "",           # ellipsis, nbsp, zwsp
    "™": "(TM)", "®": "(R)", "©": "(C)",
    "→": "->", "←": "<-", "≤": "<=", "≥": ">=",
}
_UNICODE_TABLE = {ord(k): v for k, v in _UNICODE_TO_ASCII.items()}


def _to_latin1(text: str) -> str:
    return text.translate(_UNICODE_TABLE).encode("latin-1", "replace").decode("latin-1")


def _break_long_tokens(text: str, maxlen: int = 45) -> str:
    """Insert break points into unbreakable runs (long URLs, IDs) so multi_cell
    can wrap them — otherwise fpdf2 raises 'Not enough horizontal space'."""
    def brk(w):
        return w if len(w) <= maxlen else " ".join(w[i:i + maxlen] for i in range(0, len(w), maxlen))
    return " ".join(brk(w) for w in text.split(" "))


def _safe_multi_cell(pdf, w, h, txt):
    """multi_cell that never crashes the whole document on a pathological line."""
    for candidate in (txt, _break_long_tokens(txt, 30), _break_long_tokens(txt, 15), txt[:120]):
        try:
            pdf.multi_cell(w, h, candidate)
            return
        except Exception:
            continue
    pdf.ln(h)  # give up on this line, keep the document


def generate_resume_pdf(text: str, output_path: str):
    pdf = FPDF()
    pdf.set_margins(20, 20, 20)
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    for raw_line in text.split("\n"):
        line = raw_line.strip()

        if not line:
            pdf.ln(3)
            continue

        # Transliterate smart punctuation, sanitize to latin-1, break long tokens
        line = _break_long_tokens(_to_latin1(line))

        try:
            # Section headers: ALL CAPS short lines
            if re.match(r"^[A-Z\s\-|]{4,40}$", line) and len(line) < 45:
                pdf.ln(2)
                pdf.set_font("Helvetica", "B", 12)
                _safe_multi_cell(pdf, 0, 7, line)
                pdf.set_draw_color(79, 195, 247)
                pdf.set_line_width(0.5)
                pdf.line(20, pdf.get_y(), 190, pdf.get_y())
                pdf.ln(1)
            # Bullet points
            elif line.startswith(("•", "-", "*")):
                pdf.set_font("Helvetica", "", 10)
                pdf.set_x(25)
                clean = line.lstrip("•-* ").strip()
                _safe_multi_cell(pdf, 165, 5, "  " + clean)
            # Bold-looking lines (job titles / company names) — contain | or are short
            elif "|" in line and len(line) < 80:
                pdf.set_font("Helvetica", "B", 10)
                _safe_multi_cell(pdf, 0, 5, line)
            # Normal text
            else:
                pdf.set_font("Helvetica", "", 10)
                _safe_multi_cell(pdf, 0, 5, line)
        except Exception:
            continue  # never let one line break the whole resume

    pdf.output(output_path)
