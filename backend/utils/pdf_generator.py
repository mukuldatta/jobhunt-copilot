from fpdf import FPDF
import re


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

        # Sanitize to latin-1 (FPDF default encoding)
        line = line.encode("latin-1", "replace").decode("latin-1")

        # Section headers: ALL CAPS short lines
        if re.match(r"^[A-Z\s\-|]{4,40}$", line) and len(line) < 45:
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 12)
            pdf.cell(0, 7, line, ln=True)
            pdf.set_draw_color(79, 195, 247)
            pdf.set_line_width(0.5)
            pdf.line(20, pdf.get_y(), 190, pdf.get_y())
            pdf.ln(1)
        # Bullet points
        elif line.startswith(("•", "-", "*")):
            pdf.set_font("Helvetica", "", 10)
            pdf.set_x(25)
            clean = line.lstrip("•-* ").strip()
            pdf.multi_cell(165, 5, "  " + clean)
        # Bold-looking lines (job titles / company names) — contain | or are short
        elif "|" in line and len(line) < 80:
            pdf.set_font("Helvetica", "B", 10)
            pdf.multi_cell(0, 5, line)
        # Normal text
        else:
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 5, line)

    pdf.output(output_path)
