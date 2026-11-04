"""Demo scenarios for the run view, built on the generated sample PDFs."""
from pathlib import Path

from scripts.make_sample_pdfs import build_all

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = ROOT / "samples"
UPLOAD_CHOICE = "Upload a PDF..."
NO_SUPPORT = "None"
PROOF_SAMPLE = "parent_entity_proof.pdf"

COUNTRIES = ["United States", "United Kingdom", "Canada", "Germany"]

_ORION = {"company_name": "Orion Components LLC", "country": "United States", "tax_id": "12-3456789",
          "bank_account_name": "Orion Components LLC", "bank_account_num": "000555666777",
          "document": "w9_valid.pdf"}

PRESETS = {
    "happy": {
        "label": "Happy path: clean W-9",
        "expect": "APPROVED",
        "about": "Everything matches. Every check passes and the vendor is onboarded.",
        "fields": _ORION,
    },
    "dba": {
        "label": "Edge case 1: parent / DBA name mismatch",
        "expect": "PENDING, then APPROVED after resubmitting proof",
        "about": "The bank account is held by 'Orion Components Holdings LLC'. Run it, then use "
                 "'Resubmit with more evidence' to add the parent-entity certificate.",
        "fields": {**_ORION, "bank_account_name": "Orion Components Holdings LLC"},
    },
    "wrongco": {
        "label": "Edge case 2: right EIN, wrong company",
        "expect": "PENDING (document name check fails)",
        "about": "Form and W-9 agree on the tax ID, but the W-9 names Orion while the form says Zenith.",
        "fields": {**_ORION, "company_name": "Zenith Logistics Group", "bank_account_name": "Zenith Logistics Group"},
    },
    "dupbank": {
        "label": "Edge case 3: bank account used by another vendor",
        "expect": "PENDING, human review (other vendor not named to the vendor)",
        "about": "The bank account number already belongs to an existing vendor with a different tax ID.",
        "fields": {**_ORION, "bank_account_num": "000111222333"},
    },
    "wrongdoc": {
        "label": "Edge case 4: wrong attachment (utility bill)",
        "expect": "PENDING (asks for a signed W-9)",
        "about": "A utility bill was uploaded instead of a W-9.",
        "fields": {**_ORION, "document": "utility_bill.pdf"},
    },
    "unsigned": {
        "label": "Unsigned W-9",
        "expect": "PENDING (signature and date)",
        "about": "The W-9 has no signature and no date.",
        "fields": {**_ORION, "document": "w9_unsigned.pdf"},
    },
    "taxmismatch": {
        "label": "Tax ID on form differs from W-9",
        "expect": "PENDING, human review",
        "about": "The form says 12-3456789 but the W-9 says 12-3456780.",
        "fields": {**_ORION, "document": "w9_tax_mismatch.pdf"},
    },
    "scanned": {
        "label": "Scanned W-9 (image only)",
        "expect": "APPROVED with AI on; PENDING with a technical error with AI off",
        "about": "The PDF has no text layer, so only the vision model can read it.",
        "fields": {**_ORION, "document": "w9_scanned.pdf"},
    },
    "blocked": {
        "label": "Blocked vendor",
        "expect": "REJECTED",
        "about": "The tax ID and bank account belong to a vendor on the blocked list.",
        "fields": {"company_name": "Shadow Supplies LLC", "country": "United States", "tax_id": "55-5555555",
                   "bank_account_name": "Shadow Supplies LLC", "bank_account_num": "999000111222",
                   "document": "w9_blocked.pdf"},
    },
    "blank": {
        "label": "Blank form",
        "expect": "PENDING (lists everything missing)",
        "about": "Start from an empty form.",
        "fields": {"company_name": "", "country": "United States", "tax_id": "", "bank_account_name": "",
                   "bank_account_num": "", "document": UPLOAD_CHOICE},
    },
}


def ensure_samples():
    """Generate the sample PDFs once if any are missing; returns their file names."""
    needed = {p["fields"]["document"] for p in PRESETS.values()} | {PROOF_SAMPLE}
    needed.discard(UPLOAD_CHOICE)
    if not all((SAMPLES_DIR / name).exists() for name in needed):
        build_all(SAMPLES_DIR)
    return sorted(p.name for p in SAMPLES_DIR.glob("*.pdf"))
