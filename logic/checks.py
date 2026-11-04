"""S4 verification: independent checks, each returns one finding.

Finding result values:
  pass     check succeeded
  warn     additional evidence needed          -> PENDING
  fail     verification unresolved or wrong    -> PENDING
  block    confirmed disqualifying condition   -> REJECTED
  error    technical failure, not a verdict    -> PENDING (never counts as a pass)
  skipped  inputs missing, check could not run -> PENDING (never counts as a pass)

`evidence` is internal (may name other vendors); `vendor_request` is safe to send.
"""
import re

from . import config, taxid, w9_reference
from .models import Finding
from .names import (
    entity_type_conflict,
    name_similarity,
    normalize_account,
    normalize_country,
    normalize_name,
    normalize_tax_id,
)

DUPLICATE_REQUEST = (
    "Before we can continue, our team needs to confirm some of your registration "
    "details. We will contact you if anything further is needed."
)


def _finding(check_id, result, evidence, vendor_request=None, human_review=False):
    """Every finding goes through the Finding model, so an invented result value cannot get through."""
    return Finding(id=check_id, result=result, evidence=evidence, vendor_request=vendor_request,
                   human_review=human_review).model_dump()


make_finding = _finding  # public name for the other modules that build findings


def _not_assessed(check_id, reason):
    """A check whose inputs are missing is SKIPPED: it is not a pass and cannot lead to approval,
    but it is not a new problem either; the check that explains the gap carries the vendor request."""
    return _finding(check_id, "skipped", f"Skipped: {reason}.")


def _doc_problem(check_id, extracted, vendor_request=None):
    """Finding for a document that could not be read, or None if it is usable."""
    status = (extracted or {}).get("status", "error")
    if status == "ok":
        return None
    if status == "missing":  # no document was uploaded; check_required_fields already asks for it
        return _not_assessed(check_id, "no document was supplied")
    if status == "unreadable":
        if vendor_request is None:  # another check owns the request to the vendor
            return _not_assessed(check_id, "the document could not be read")
        return _finding(
            check_id, "fail", "Document could not be read (illegible or image-only without usable OCR).",
            vendor_request, False,
        )
    return _finding(check_id, "error", "Document extraction did not complete.", None, True)


def check_required_fields(submission, extracted, vendors, history=None):
    cid = "check_required_fields"
    missing = [f for f in config.REQUIRED_FIELDS if not str(submission.get(f) or "").strip()]
    if not submission.get("documents"):
        missing.append("tax_document")
    if not missing:
        return _finding(cid, "pass", "All required fields and a tax document were provided.")
    labels = ", ".join(config.FIELD_LABELS[m] for m in missing)
    return _finding(cid, "fail", f"Missing: {', '.join(missing)}.", f"Please provide the missing information: {labels}.")


def check_tax_id_format(submission, extracted, vendors, history=None):
    cid = "check_tax_id_format"
    tax_id = (submission.get("tax_id") or "").strip()
    if not tax_id:
        return _not_assessed(cid, "no tax ID provided")
    country = submission.get("country")
    cc = normalize_country(country)
    if cc is None:
        return _finding(
            cid, "fail", f"Country '{country}' is not supported for automated checks.",
            "We do not yet support automated onboarding for your country. Our team will review your submission manually.",
            True,
        )
    pattern, label = config.TAX_ID_RULES[cc]
    # ASCII-only digits (\d would accept other scripts); letters are case-insensitive for input.
    if re.fullmatch(pattern, tax_id.upper(), re.ASCII):
        return _finding(cid, "pass", f"'{tax_id}' matches the {config.COUNTRY_NAMES[cc]} format.")
    return _finding(
        cid, "fail", f"'{tax_id}' does not match the {config.COUNTRY_NAMES[cc]} format {pattern}.",
        f"The tax ID provided is not a valid {label}. Please check it and resubmit.",
    )


def check_document_reuse(submission, extracted, vendors, history=None):
    """The same file (by content) submitted for a different tax ID in an unrelated run is a strong
    fraud signal: a copied or stolen document. Runs in the same resubmission chain are the same
    submission and are excluded, so a correction never flags itself."""
    cid = "check_document_reuse"
    if history is None:
        return _not_assessed(cid, "no run history was supplied")
    current = history.get("current_documents") or []
    tax_id = normalize_tax_id(submission.get("tax_id"))
    if not current:
        return _not_assessed(cid, "no document was supplied")
    if not tax_id:
        return _not_assessed(cid, "no tax ID to tell vendors apart")
    chain = set(history.get("chain_run_ids") or ())
    clashes, same_vendor = [], []
    for doc in current:
        for prior in history.get("prior_documents") or []:
            if prior["sha256"] != doc["sha256"] or prior["run_id"] in chain:
                continue
            (same_vendor if prior["tax_id"] == tax_id else clashes).append((doc, prior))
    if clashes:
        doc, prior = clashes[0]
        return _finding(
            cid, "fail",
            f"File {doc['filename']} was already submitted in run {prior['run_id']} for a different tax ID.",
            DUPLICATE_REQUEST, True,
        )
    if same_vendor:
        return _finding(cid, "pass", f"{len(same_vendor)} earlier use(s) of this file were for the same tax ID.")
    return _finding(cid, "pass", "No other vendor's submission used these files.")


_CHECKSUM_REQUEST = (
    "The tax ID provided does not pass its validity check ({why}). Please check it for typos and resubmit."
)


def check_tax_id_checksum(submission, extracted, vendors, history=None):
    """Maths on the tax ID itself. A failure is most likely a typo, so it is unresolved, never a rejection."""
    cid = "check_tax_id_checksum"
    tax_id = (submission.get("tax_id") or "").strip().upper()
    cc = normalize_country(submission.get("country"))
    if not tax_id or cc is None:
        return _not_assessed(cid, "no tax ID or an unsupported country")
    pattern, _ = config.TAX_ID_RULES[cc]
    if not re.fullmatch(pattern, tax_id, re.ASCII):
        return _not_assessed(cid, "the tax ID format check failed")  # that check owns the request
    if cc == "US":
        if taxid.ein_prefix_valid(tax_id):
            return _finding(cid, "pass", f"EIN prefix {tax_id[:2]} is one the IRS assigns.")
        return _finding(cid, "fail", f"EIN prefix {tax_id[:2]} is not on the IRS list of valid prefixes.",
                        _CHECKSUM_REQUEST.format(why="its first two digits are not a prefix the IRS assigns"))
    if cc == "UK":
        variant = taxid.uk_vat_variant(tax_id)
        if variant:
            return _finding(cid, "pass", f"UK VAT check digits pass ({variant}).")
        return _finding(cid, "fail", "UK VAT check digits fail both the mod 97 and the 9755 checks.",
                        _CHECKSUM_REQUEST.format(why="its check digits do not add up"))
    if taxid.ca_bn_valid(tax_id):
        return _finding(cid, "pass", "Business Number passes the Luhn check.")
    return _finding(cid, "fail", "Business Number fails the Luhn check on its first nine digits.",
                    _CHECKSUM_REQUEST.format(why="its check digit is wrong"))


def _required_doc_type(submission):
    return config.REQUIRED_DOCUMENT.get(normalize_country(submission.get("country")))


def check_document_type(submission, extracted, vendors, history=None):
    cid = "check_document_type"
    required = _required_doc_type(submission)
    if required is None:
        return _not_assessed(cid, "required document unknown for this country")
    label = config.DOCUMENT_LABELS[required]
    problem = _doc_problem(cid, extracted, f"We could not read your uploaded document. Please upload a clear copy of your {label}.")
    if problem:
        return problem
    received = extracted.get("document_type")
    if received == required:
        return _finding(cid, "pass", f"Document is a {required}.")
    return _finding(
        cid, "fail", f"Received document type '{received}', expected '{required}'.",
        f"The uploaded document is not the required {label}. Please upload the correct document.",
    )


def check_document_authenticity(submission, extracted, vendors, history=None):
    """Is the document the official IRS form, or only a page that says it is? Decided from the
    document's own text and the model's layout observations by a fixed rule (logic/w9_reference.py).
    Not applicable where no reference layout exists (the UK and Canadian documents): returns None."""
    cid = "check_document_authenticity"
    if _required_doc_type(submission) != "w9":
        return None
    problem = _doc_problem(cid, extracted)
    if problem:
        return problem
    if extracted.get("document_type") != "w9":
        return _not_assessed(cid, "the document is not a W-9")  # the document type check owns that request
    result, evidence, review = w9_reference.judge(extracted.get("text_signature"), extracted.get("structure"))
    if result == "pass":
        return _finding(cid, "pass", evidence)
    return _finding(cid, result, evidence, w9_reference.REQUEST if result == "fail" else None, review)


def check_signature_and_date(submission, extracted, vendors, history=None):
    cid = "check_signature_and_date"
    problem = _doc_problem(cid, extracted)
    if problem:
        return problem
    doc_type = extracted.get("document_type")
    if doc_type != _required_doc_type(submission):
        return _not_assessed(cid, f"document type '{doc_type}' is not the required document")
    missing = []
    if doc_type in config.SIGNATURE_REQUIRED and not extracted.get("signed"):
        missing.append("signature")
    if not extracted.get("signed_date"):
        missing.append("date")
    if not missing:
        return _finding(cid, "pass", "Document is dated" + (" and signed." if doc_type in config.SIGNATURE_REQUIRED else "."))
    return _finding(
        cid, "fail", f"Document is missing: {', '.join(missing)}.",
        f"The uploaded document is missing its {' and '.join(missing)}. Please upload a complete, "
        f"{'signed and ' if 'signature' in missing else ''}dated copy.",
    )


def check_form_document_tax_id(submission, extracted, vendors, history=None):
    cid = "check_form_document_tax_id"
    problem = _doc_problem(cid, extracted)
    if problem:
        return problem
    form_id = normalize_tax_id(submission.get("tax_id"))
    doc_id = normalize_tax_id(extracted.get("tax_id"))
    if not form_id:
        return _not_assessed(cid, "no tax ID provided on the form")
    if not doc_id:
        return _finding(
            cid, "fail", "No tax ID could be read from the document.",
            "We could not find a tax ID on your document. Please upload a copy where it is clearly visible.",
        )
    if form_id == doc_id:
        return _finding(cid, "pass", "Tax ID on the form matches the document.")
    return _finding(
        cid, "fail", f"Form tax ID '{submission.get('tax_id')}' differs from document tax ID '{extracted.get('tax_id')}'.",
        "The tax ID on your form does not match the tax ID on your document. Please confirm the correct "
        "number and correct the form or upload the right document.",
        True,
    )


def _relationship_proven(extracted, name_a, name_b, country):
    """True if a supporting document names both entities."""
    for doc in (extracted or {}).get("supporting_documents") or []:
        if doc.get("document_type") != config.RELATIONSHIP_PROOF_TYPE:
            continue
        names = doc.get("entity_names") or []
        has_a = any(name_similarity(n, name_a, country) >= config.NAME_PASS for n in names)
        has_b = any(name_similarity(n, name_b, country) >= config.NAME_PASS for n in names)
        if has_a and has_b:
            return True
    return False


def check_company_bank_name(submission, extracted, vendors, history=None):
    cid = "check_company_bank_name"
    company = (submission.get("company_name") or "").strip()
    bank = (submission.get("bank_account_name") or "").strip()
    if not company or not bank:
        return _not_assessed(cid, "company name or bank account name missing")
    return _compare_names(
        cid, submission, extracted, company, bank,
        "The company name on your submission does not match the bank account holder name. Please upload a "
        "DBA registration or a document showing the relationship between the two entities.",
    )


def _compare_names(cid, submission, extracted, name_a, name_b, request):
    """Shared name comparison: core-name similarity, a separate entity-type check, and
    relationship proof (DBA / parent entity) as the way to resolve either."""
    country = submission.get("country")
    score = name_similarity(name_a, name_b, country)
    conflict = entity_type_conflict(name_a, name_b, country)
    detail = (
        f"'{name_a}' vs '{name_b}' (normalised '{normalize_name(name_a, country)}' vs "
        f"'{normalize_name(name_b, country)}'): core-name similarity {score:.0f}%."
    )
    if score >= config.NAME_PASS and not conflict:
        return _finding(cid, "pass", detail)
    if _relationship_proven(extracted, name_a, name_b, country):
        return _finding(cid, "pass", detail + " Difference explained by submitted DBA / parent-entity proof.")
    if score >= config.NAME_PASS:
        return _finding(
            cid, "warn", detail + " Names match but the legal entity types differ.",
            "The legal entity type (for example LLC versus Inc) differs between the two names. Please confirm "
            "your exact registered legal name or upload your registration document.",
        )
    if score >= config.NAME_REVIEW:
        return _finding(cid, "warn", detail + " Partial match; relationship evidence required.", request)
    return _finding(cid, "fail", detail + " Low similarity; manual review required.", request, True)


def check_form_document_name(submission, extracted, vendors, history=None):
    cid = "check_form_document_name"
    problem = _doc_problem(cid, extracted)
    if problem:
        return problem
    doc_type = extracted.get("document_type")
    if doc_type != _required_doc_type(submission):
        return _not_assessed(cid, f"document type '{doc_type}' is not the required document")
    company = (submission.get("company_name") or "").strip()
    doc_name = (extracted.get("legal_name") or "").strip()
    if not company:
        return _not_assessed(cid, "no company name provided on the form")
    if not doc_name:
        return _finding(
            cid, "fail", "No legal name could be read from the document.",
            "We could not find the registered name on your document. Please upload a copy where it is clearly visible.",
        )
    return _compare_names(
        cid, submission, extracted, company, doc_name,
        "The company name on your form does not match the name on your tax document. Please upload a DBA "
        "registration or a document showing the relationship between the two names.",
    )


def check_duplicate_tax_id(submission, extracted, vendors, history=None):
    cid = "check_duplicate_tax_id"
    tax_id = normalize_tax_id(submission.get("tax_id"))
    if not tax_id:
        return _not_assessed(cid, "no tax ID provided")
    matches = [v for v in vendors if normalize_tax_id(v.get("tax_id")) == tax_id]
    if not matches:
        return _finding(cid, "pass", "No existing vendor uses this tax ID.")
    blocked = [v for v in matches if v.get("status") == "blocked"]
    if blocked:
        return _finding(
            cid, "block", f"Tax ID belongs to blocked vendor id {blocked[0]['id']}.",
            "We are unable to proceed with onboarding this vendor.", True,
        )
    country = submission.get("country")
    company = submission.get("company_name")
    different = [v for v in matches if name_similarity(v.get("legal_name"), company, country) < config.NAME_PASS]
    if different:
        return _finding(
            cid, "fail", f"Tax ID already used by vendor id {different[0]['id']} under a different company name.",
            DUPLICATE_REQUEST, True,
        )
    return _finding(
        cid, "warn", f"Tax ID matches existing vendor id {matches[0]['id']} with the same company name; possible duplicate onboarding.",
        DUPLICATE_REQUEST, True,
    )


def check_duplicate_bank_account(submission, extracted, vendors, history=None):
    cid = "check_duplicate_bank_account"
    account = normalize_account(submission.get("bank_account_num"))
    if not account:
        return _not_assessed(cid, "no bank account provided")
    matches = [v for v in vendors if normalize_account(v.get("bank_account")) == account]
    if not matches:
        return _finding(cid, "pass", "No existing vendor uses this bank account.")
    blocked = [v for v in matches if v.get("status") == "blocked"]
    if blocked:
        return _finding(
            cid, "block", f"Bank account belongs to blocked vendor id {blocked[0]['id']}.",
            "We are unable to proceed with onboarding this vendor.", True,
        )
    tax_id = normalize_tax_id(submission.get("tax_id"))
    foreign = [v for v in matches if normalize_tax_id(v.get("tax_id")) != tax_id]
    if foreign:
        return _finding(
            cid, "fail", f"Bank account already linked to vendor id {foreign[0]['id']} with a different tax ID; verify ownership.",
            DUPLICATE_REQUEST, True,
        )
    return _finding(cid, "pass", f"Bank account already belongs to the same vendor (id {matches[0]['id']}).")


CHECKS = [
    check_required_fields,
    check_tax_id_format,
    check_tax_id_checksum,
    check_document_type,
    check_document_authenticity,
    check_signature_and_date,
    check_form_document_tax_id,
    check_form_document_name,
    check_company_bank_name,
    check_duplicate_tax_id,
    check_duplicate_bank_account,
    check_document_reuse,
]


def evaluate_submission(submission, extracted, vendors, checks=None, on_finding=None, history=None):
    """Run every check. A crashing check becomes an 'error' finding; the rest still run."""
    findings = []
    for check in checks or CHECKS:
        try:
            finding = check(submission, extracted, vendors, history)
        except Exception as exc:  # technical error, not a verification verdict
            finding = _finding(check.__name__, "error", f"Check could not complete: {exc!r}", None, True)
        if finding is None:  # the check does not apply to this document
            continue
        findings.append(finding)
        if on_finding:
            on_finding(finding)
    return findings
