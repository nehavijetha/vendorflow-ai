"""Generate synthetic test documents into samples/."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "samples"


def _w9_lines(name, ein, address, signed, date):
    return [
        ("Form W-9  Request for Taxpayer Identification Number and Certification", 14, "Helvetica-Bold"),
        ("(Rev. March 2024)  Department of the Treasury, Internal Revenue Service", 9, "Helvetica"),
        ("", 10, "Helvetica"),
        (f"1. Name: {name}", 11, "Helvetica"),
        (f"2. Business name / disregarded entity name: {name}", 11, "Helvetica"),
        ("3. Federal tax classification: Limited liability company", 11, "Helvetica"),
        (f"5. Address: {address}", 11, "Helvetica"),
        (f"Employer identification number: {ein}", 11, "Helvetica-Bold"),
        ("", 10, "Helvetica"),
        ("Under penalties of perjury, I certify that the number shown on this form is my correct", 9, "Helvetica"),
        ("taxpayer identification number and that I am a U.S. person.", 9, "Helvetica"),
        ("", 10, "Helvetica"),
        (f"Signature of U.S. person: {name + ' (signed)' if signed else '________________________'}", 11, "Helvetica"),
        (f"Date: {date if date else '____________'}", 11, "Helvetica"),
    ]


def _write(path, lines):
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 740
    for text, size, font in lines:
        c.setFont(font, size)
        c.drawString(60, y, text)
        y -= size + 8
    c.save()


def w9(path, name="Orion Components LLC", ein="12-3456789", signed=True, date="2026-10-28"):
    _write(path, _w9_lines(name, ein, "400 Market Street, Austin, TX 78701", signed, date))


def utility_bill(path):
    _write(path, [
        ("Evergreen Power & Light", 16, "Helvetica-Bold"),
        ("Monthly Statement - October 2026", 11, "Helvetica"),
        ("", 10, "Helvetica"),
        ("Account holder: Orion Components LLC", 11, "Helvetica"),
        ("Service address: 400 Market Street, Austin, TX 78701", 11, "Helvetica"),
        ("Account number: 5521-8890-12", 11, "Helvetica"),
        ("Usage: 1,284 kWh      Amount due: $312.48      Due date: 2026-11-15", 11, "Helvetica"),
    ])


def parent_entity_proof(path):
    _write(path, [
        ("Certificate of Ownership", 16, "Helvetica-Bold"),
        ("State of Texas - Secretary of State", 10, "Helvetica"),
        ("", 10, "Helvetica"),
        ("This certifies that Orion Components LLC is a wholly owned subsidiary of", 11, "Helvetica"),
        ("Orion Components Holdings LLC, which holds banking relationships on its behalf.", 11, "Helvetica"),
        ("", 10, "Helvetica"),
        ("Issued: 2026-09-02", 11, "Helvetica"),
    ])


def scanned_w9(path):
    """Image-only PDF: no text layer, so plain text extraction returns nothing."""
    image = Image.new("RGB", (1275, 1650), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=26)
    y = 120
    for text, _, _ in _w9_lines("Orion Components LLC", "12-3456789", "400 Market Street, Austin, TX 78701", True, "2026-10-28"):
        draw.text((100, y), text, fill=(40, 40, 40), font=font)
        y += 44
    c = canvas.Canvas(str(path), pagesize=letter)
    from reportlab.lib.utils import ImageReader
    c.drawImage(ImageReader(image), 0, 0, width=letter[0], height=letter[1])
    c.save()


def build_all(out_dir=DEFAULT_OUT):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    w9(out / "w9_valid.pdf")
    w9(out / "w9_unsigned.pdf", signed=False, date=None)
    w9(out / "w9_tax_mismatch.pdf", ein="12-3456780")
    w9(out / "w9_blocked.pdf", name="Shadow Supplies LLC", ein="55-5555555")
    scanned_w9(out / "w9_scanned.pdf")
    utility_bill(out / "utility_bill.pdf")
    parent_entity_proof(out / "parent_entity_proof.pdf")
    return sorted(p.name for p in out.glob("*.pdf"))


if __name__ == "__main__":
    for name in build_all(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT):
        print(name)
