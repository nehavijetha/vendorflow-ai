"""Policy values for the verification engine. Assumptions, not Zamp requirements."""

# Name similarity (0-100) between company name and bank account holder name.
NAME_PASS = 90
NAME_REVIEW = 60

REQUIRED_FIELDS = (
    "company_name",
    "country",
    "tax_id",
    "bank_account_name",
    "bank_account_num",
)

FIELD_LABELS = {
    "company_name": "company name",
    "country": "country",
    "tax_id": "tax ID",
    "bank_account_name": "bank account holder name",
    "bank_account_num": "bank account number",
    "tax_document": "tax registration document",
}

# Supported countries -> (tax ID regex, human-readable label)
TAX_ID_RULES = {
    "US": (r"^\d{2}-\d{7}$", "EIN (NN-NNNNNNN)"),
    "UK": (r"^GB\d{9}$", "VAT number (GB followed by 9 digits)"),
    "CA": (r"^\d{9}(RT\d{4})?$", "Business Number (9 digits, optionally followed by RT and 4 digits)"),
}

COUNTRY_NAMES = {"US": "United States", "UK": "United Kingdom", "CA": "Canada"}

# Accepted tax-registration document per country.
REQUIRED_DOCUMENT = {
    "US": "w9",
    "UK": "vat_certificate",
    "CA": "cra_business_number_letter",
}

DOCUMENT_LABELS = {
    "w9": "signed IRS Form W-9",
    "vat_certificate": "HMRC VAT registration certificate",
    "cra_business_number_letter": "CRA business number confirmation letter",
}

# Document types that must carry a signature.
SIGNATURE_REQUIRED = {"w9"}

# Supporting document that links two differing legal/trading/parent names.
RELATIONSHIP_PROOF_TYPE = "dba_or_parent_proof"
