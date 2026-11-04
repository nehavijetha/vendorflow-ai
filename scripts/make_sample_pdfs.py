"""Generate the synthetic test documents into samples/.

Genuine W-9s are the real IRS form: page 1 of reference/fw9.pdf (downloaded unmodified from irs.gov)
with values written onto it, so layout, ruled boxes and every printed word are the IRS's own. The
fakes are what a careless or dishonest vendor might send instead:

  spoof_fake_w9           one line saying "Form W-9" and typed fields: no form at all
  spoof_lookalike_w9      the form's wording retyped in full on a plain page: right words, wrong layout
  spoof_letter_mentions   a letter that only mentions a W-9
  w9_blank_template       the genuine form with nothing filled in
"""
import io
import random
import sys
import textwrap
from pathlib import Path

import pymupdf
from PIL import Image, ImageFilter
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
REFERENCE_PDF = ROOT / "reference" / "fw9.pdf"
DEFAULT_OUT = Path(ROOT / "samples")
PAGE = (611.976, 791.968)  # the IRS page size, so overlays line up exactly

# One vendor identity per scenario that ends APPROVED, so approving one never collides with another.
VENDORS = {
    "orion": dict(name="Orion Components LLC", kind="llc", address="400 Market Street", city="Austin, TX 78701", ein="12-3456789"),
    "meridian": dict(name="Meridian Tools LLC", kind="llc", address="88 Harbor Way", city="Portland, OR 97204", ein="20-4417283"),
    "cobalt": dict(name="Cobalt Freight Inc", kind="c_corp", address="1200 Rail Yard Rd", city="Omaha, NE 68102", ein="84-2201937"),
    "shadow": dict(name="Shadow Supplies LLC", kind="llc", address="9 Dock Lane", city="Newark, NJ 07102", ein="55-5555555"),
}

# Positions on page 1 of the official form, read from its fillable fields (PDF points, origin bottom-left).
NAME_Y, BUSINESS_Y, ADDRESS_Y, CITY_Y = 664, 640, 495, 471
BOX_LLC = (73.0, 590.5)
BOX_C_CORP = (180.0, 603.7)
LLC_CODE_AT = (432.0, 591.5)
EIN_FIRST = (417.6, 14.4, 2)   # x of first cell, cell width, cells
EIN_SECOND = (460.8, 14.4, 7)
EIN_BASELINE = 355
SIGNATURE_AREA = (130, 196, 360, 214)
DATE_AT = (408, 198)


def _overlay(vendor, ein=None, signed=True, filled=True):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=PAGE)
    if filled:
        c.setFont("Helvetica", 10)
        c.drawString(62, NAME_Y, vendor["name"])
        c.drawString(62, ADDRESS_Y, vendor["address"])
        c.drawString(62, CITY_Y, vendor["city"])
        x, y = BOX_LLC if vendor["kind"] == "llc" else BOX_C_CORP
        c.setLineWidth(1.4)
        c.line(x + 1, y + 1, x + 7, y + 7)
        c.line(x + 1, y + 7, x + 7, y + 1)
        if vendor["kind"] == "llc":
            c.drawCentredString(*LLC_CODE_AT, "C")
        digits = (ein or vendor["ein"]).replace("-", "")
        cells = [(EIN_FIRST[0] + EIN_FIRST[1] * (i + 0.5)) for i in range(EIN_FIRST[2])]
        cells += [(EIN_SECOND[0] + EIN_SECOND[1] * (i + 0.5)) for i in range(EIN_SECOND[2])]
        c.setFont("Helvetica", 11)
        for digit, cx in zip(digits, cells):
            c.drawCentredString(cx, EIN_BASELINE, digit)
        if signed:
            x0, y0, x1, y1 = SIGNATURE_AREA
            c.setStrokeColorRGB(0.08, 0.1, 0.42)
            c.setLineWidth(1.3)
            p = c.beginPath()
            p.moveTo(x0, y0 + 6)
            for i in range(6):  # a loose, cursive-looking scribble
                step = (x1 - x0) / 6
                p.curveTo(x0 + step * i + 8, y1, x0 + step * i + 16, y0 - 2, x0 + step * (i + 1), y0 + 8 + (i % 2) * 5)
            c.drawPath(p, stroke=1, fill=0)
            c.line(x0 + 10, y0 + 2, x1 - 20, y0 + 5)
            c.setFillColorRGB(0, 0, 0)
            c.setFont("Helvetica", 10)
            c.drawString(DATE_AT[0], DATE_AT[1], "10/28/2026")
    c.showPage()  # a page must exist even when nothing is drawn (the blank template)
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]


def genuine_w9(path, vendor=None, ein=None, signed=True, filled=True):
    """The real IRS page 1 with values written on it. Fillable fields are removed, as in a printed form."""
    writer = PdfWriter()
    writer.add_page(PdfReader(str(REFERENCE_PDF)).pages[0])
    page = writer.pages[0]
    if "/Annots" in page:
        del page["/Annots"]
    page.merge_page(_overlay(vendor or VENDORS["orion"], ein, signed, filled))
    if hasattr(path, "write"):
        writer.write(path)
    else:
        with open(path, "wb") as handle:
            writer.write(handle)


def scanned_w9(path, vendor, ein=None, signed=True):
    """A photocopy of the genuine form: rasterised, slightly skewed, noisy, no text layer."""
    buf = io.BytesIO()
    genuine_w9(buf, vendor, ein, signed)
    pix = pymupdf.open(stream=buf.getvalue(), filetype="pdf")[0].get_pixmap(dpi=150)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    image = image.rotate(0.8, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
    random.seed(11)
    noise = Image.effect_noise(image.size, 14).convert("RGB")
    image = Image.blend(image, noise, 0.05).filter(ImageFilter.GaussianBlur(0.6))
    jpeg = io.BytesIO()
    image.save(jpeg, "JPEG", quality=62)
    jpeg.seek(0)
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawImage(ImageReader(jpeg), 0, 0, width=letter[0], height=letter[1])
    c.save()


def _plain(path, lines, size=11, leading=18, left=60, top=730):
    c = canvas.Canvas(str(path), pagesize=letter)
    y = top
    for text in lines:
        c.setFont("Helvetica", size)
        c.drawString(left, y, text)
        y -= leading
    c.save()


def fake_w9(path):
    """No form at all: a title line and typed fields, including a typed '(signed)'."""
    _plain(path, ["Form W-9", "Name: Orion Components LLC", "Employer identification number: 12-3456789",
                  "Signature of U.S. person: Orion Components LLC (signed)", "Date: 2026-10-28"])


def lookalike_w9(path):
    """Every word of the official page retyped on a plain page: the text signature passes, the layout does not."""
    text = PdfReader(str(REFERENCE_PDF)).pages[0].extract_text()
    lines = []
    for raw in text.splitlines():
        safe = raw.encode("latin-1", "replace").decode("latin-1").replace("?", "'") if raw.strip() else ""
        lines += textwrap.wrap(safe, 125) or [""]
    typed = ["", "Name of entity/individual: Orion Components LLC", "Employer identification number: 12-3456789",
             "Signature of U.S. person: Orion Components LLC (signed)   Date: 10/28/2026"]
    _plain(path, [*typed, "", *lines], size=6.4, leading=7.7, left=36, top=770)


def letter_mentions_w9(path):
    _plain(path, ["Dear AP team,", "", "We will send our Form W-9 and a bank letter next week.",
                  "Our EIN is 12-3456789.", "", "Thanks, Orion Components LLC"])


def utility_bill(path):
    _plain(path, ["Evergreen Power & Light", "Monthly Statement - October 2026", "",
                  "Account holder: Orion Components LLC", "Service address: 400 Market Street, Austin, TX 78701",
                  "Account number: 5521-8890-12",
                  "Usage: 1,284 kWh      Amount due: $312.48      Due date: 2026-11-15"])


def parent_entity_proof(path):
    _plain(path, ["Certificate of Ownership", "State of Texas - Secretary of State", "",
                  "This certifies that Orion Components LLC is a wholly owned subsidiary of",
                  "Orion Components Holdings LLC, which holds banking relationships on its behalf.", "",
                  "Issued: 2026-09-02"])


def build_all(out_dir=DEFAULT_OUT):
    if not REFERENCE_PDF.exists():
        raise FileNotFoundError(f"The official IRS form is missing: {REFERENCE_PDF}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    genuine_w9(out / "w9_valid.pdf", VENDORS["orion"])
    genuine_w9(out / "w9_unsigned.pdf", VENDORS["orion"], signed=False)
    genuine_w9(out / "w9_tax_mismatch.pdf", VENDORS["orion"], ein="12-3456780")
    genuine_w9(out / "w9_meridian.pdf", VENDORS["meridian"])
    genuine_w9(out / "w9_blocked.pdf", VENDORS["shadow"])
    genuine_w9(out / "w9_blank_template.pdf", filled=False)
    scanned_w9(out / "w9_scanned.pdf", VENDORS["cobalt"])
    fake_w9(out / "spoof_fake_w9.pdf")
    lookalike_w9(out / "spoof_lookalike_w9.pdf")
    letter_mentions_w9(out / "spoof_letter_mentions_w9.pdf")
    utility_bill(out / "utility_bill.pdf")
    parent_entity_proof(out / "parent_entity_proof.pdf")
    return sorted(p.name for p in out.glob("*.pdf"))


if __name__ == "__main__":
    for name in build_all(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT):
        print(name)
