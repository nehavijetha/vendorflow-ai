"""What makes a document the official IRS Form W-9, rather than a page that merely says so.

Two independent signals, and neither is the model's opinion of the document's type:

  text signature   Eighteen distinctive phrases of the official form (title, "Give form to the
                   requester", Part I and Part II wording, "Cat. No. 10231X", the footer, ...) must
                   be present in the PDF's text layer. Plain code, free, and out of the model's reach.
  visual layout    The model is shown the official page next to the submitted pages and reports
                   observations (is there a Line 1 box? are there SSN and EIN cells? a Sign Here
                   block?). It is never asked "is this genuine?". Code applies the rule below.

A text-only fake fails the first. A fake that retypes all of the wording passes the first and fails
the second. Neither proves the vendor is real or that the number is theirs; they prove the
document uses the official form.
"""
from pathlib import Path

from .names import squash

REFERENCE_DIR = Path(__file__).resolve().parent.parent / "reference"
REFERENCE_PDF = REFERENCE_DIR / "fw9.pdf"
REFERENCE_IMAGE = REFERENCE_DIR / "w9_page1.png"
REVISION = "Rev. March 2024"

# Distinctive text of page 1 of the official form (Rev. March 2024). All 18 are present in the
# IRS file; the threshold leaves room for a print-to-PDF that loses a little text.
PHRASES = {
    "form title": "Request for Taxpayer Identification Number and Certification",
    "give form to the requester": "Give form to the requester",
    "Department of the Treasury": "Department of the Treasury",
    "Internal Revenue Service": "Internal Revenue Service",
    "irs.gov/FormW9 address": "Go to www.irs.gov/FormW9",
    "Line 1 label": "Name of entity/individual",
    "Line 2 label": "Business name/disregarded entity name",
    "Line 3a wording": "federal tax classification",
    "Part I heading": "Taxpayer Identification Number (TIN)",
    "EIN box label": "Employer identification number",
    "Part II perjury wording": "Under penalties of perjury",
    "signature label": "Signature of U.S. person",
    "Cat. No.": "Cat. No. 10231X",
    "footer revision": "Form W-9 (Rev. 3-2024)",
    "header revision": "(Rev. March 2024)",
    "exempt payee code": "Exempt payee code",
    "backup withholding wording": "backup withholding",
    "FATCA wording": "Foreign Account Tax Compliance Act",
}
MIN_PHRASES = 15

# Layout features the model reports on, with the wording used in its instructions.
FEATURES = {
    "header_block": "The top header block: 'Form W-9', 'Request for Taxpayer Identification Number and Certification', and a 'Give form to the requester' box at the top right.",
    "line1_name_box": "Numbered Line 1 for the entity or individual name, with a ruled entry line.",
    "line2_business_box": "Numbered Line 2 for the business or disregarded entity name, with a ruled entry line.",
    "tax_classification_boxes": "Line 3a: a row of check boxes (Individual/sole proprietor, C corporation, S corporation, Partnership, Trust/estate) and an LLC box.",
    "exemptions_box": "Line 4: the exemptions box at the right with the exempt payee and FATCA code entries.",
    "address_lines": "Lines 5 and 6: address and city, state, ZIP entry lines.",
    "tin_boxes": "Part I: a Social security number box (cells grouped 3-2-4) and an Employer identification number box (cells grouped 2-7).",
    "certification_text": "Part II Certification with the numbered statements 1 to 4 and 'Under penalties of perjury'.",
    "sign_here_block": "A 'Sign Here' block with 'Signature of U.S. person' and 'Date'.",
    "form_footer": "The footer with 'Cat. No. 10231X' and 'Form W-9 (Rev. 3-2024)'.",
    "same_ruled_layout": "The page as a whole has the same ruled, boxed form layout as the reference image, and is not plain typed text or a differently designed page.",
}
# The holistic feature must hold, and at most one other feature may be missing (a clipped scan).
HOLISTIC = "same_ruled_layout"
MAX_MISSING = 1

REQUEST = (
    "The uploaded document does not match the official IRS Form W-9 (Rev. March 2024). Please upload the "
    "official form, downloaded from irs.gov/FormW9, filled in and signed."
)


def text_signature(text):
    """Which distinctive phrases of the official form appear in the document's text."""
    squashed = squash(text)
    missing = [label for label, phrase in PHRASES.items() if squash(phrase) not in squashed]
    return {"matched": len(PHRASES) - len(missing), "total": len(PHRASES), "missing": missing,
            "layer": bool((text or "").strip())}


def reference_image_png():
    return REFERENCE_IMAGE.read_bytes()


def judge(text_sig, structure):
    """Apply the fixed rule. Returns (result, evidence, human_review) with result pass, fail or error."""
    layer = bool(text_sig and text_sig.get("layer"))
    if layer and text_sig["matched"] < MIN_PHRASES:
        missing = ", ".join(text_sig["missing"][:4]) + ("..." if len(text_sig["missing"]) > 4 else "")
        return ("fail", f"Only {text_sig['matched']} of {text_sig['total']} distinctive phrases of the official IRS W-9 "
                        f"are in the document text (needs {MIN_PHRASES}); missing: {missing}.", True)
    if not structure:
        return ("error", "The page layout could not be checked against the official form because the model "
                         "was unavailable. A text-only match is not enough.", True)
    absent = [name for name in FEATURES if not structure.get(name)]
    if not structure.get(HOLISTIC) or len([a for a in absent if a != HOLISTIC]) > MAX_MISSING:
        return ("fail", f"The page layout does not match the official IRS W-9: {len(FEATURES) - len(absent)} of "
                        f"{len(FEATURES)} layout features seen; missing: {', '.join(absent)}.", True)
    seen = f"{len(FEATURES) - len(absent)} of {len(FEATURES)} layout features match the reference"
    if layer:
        return ("pass", f"Official IRS W-9 text found ({text_sig['matched']} of {text_sig['total']} phrases) and {seen}.", False)
    return ("pass", f"No text layer (scan); {seen}.", False)
