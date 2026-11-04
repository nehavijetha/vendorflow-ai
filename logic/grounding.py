"""S4 grounding: is what the model read really in the document?

A hallucination only does harm if it produces a wrong PASS, so grounding uses two independent
sources depending on the PDF:

  text-layer PDF   the value must literally appear in the PDF's own text, whatever the form says
                   (a form-vs-document difference is the verification checks' job, not this one)
                   -> found: PASS   |   not found: PENDING (unverified extraction)
  scan (no text)   there is no text to look in, so the vendor's form is the independent source:
                   the value must equal what the vendor typed (normalised)
                   -> equal: PASS with a note   |   different: PENDING + manual review

The second rule is only valid because the extraction request never contains the form values
(tests/test_extract_independence.py): the model cannot copy what it never saw.

Only names and tax IDs are grounded. Dates and the signature flag are model judgements with no
literal text to match, so they are covered by the verification checks instead.
"""
import unicodedata

from .checks import make_finding
from .extract import MIN_TEXT_CHARS, read_text
from .names import normalize_name, normalize_tax_id

PRIMARY_ID = "check_extraction_grounding"
SUPPORTING_ID = "check_supporting_grounding"

UNVERIFIED_REQUEST = (
    "We could not verify the details read from your document against the document itself. "
    "Please upload a clear, text-based copy."
)
SCAN_MISMATCH_REQUEST = (
    "The details read from your scanned document do not match your form. Please check the form "
    "and upload a clear copy of the document."
)
SCAN_NOTE = "read from scan, confirmed against form"


def squash(text):
    """Lowercase ASCII letters and digits only, so spacing, hyphens and accents cannot hide a match."""
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return "".join(ch for ch in ascii_text.lower() if ch.isalnum())


def _read(path, reader):
    try:
        return reader(path)
    except Exception:
        return None


def _field_checks(extracted, form, country):
    """(field, extracted value, form value, form-equality test) for the values that were read."""
    pairs = (
        ("tax_id", extracted.get("tax_id"), form.get("tax_id"),
         lambda a, b: bool(a) and normalize_tax_id(a) == normalize_tax_id(b)),
        ("legal_name", extracted.get("legal_name"), form.get("company_name"),
         lambda a, b: bool(a) and normalize_name(a, country) == normalize_name(b, country)),
    )
    return [p for p in pairs if p[1]]


def _judge(cid, subject, items, text, text_layer):
    """items: (label, extracted value, equal-to-form test). Returns one finding."""
    if text_layer:
        missing = [label for label, value, _ in items if squash(value) not in squash(text)]
        if not missing:
            return make_finding(cid, "pass", f"{subject}: " + ", ".join(label for label, _, _ in items)
                                + " found in the document text.")
        return make_finding(
            cid, "fail", f"{subject}: {', '.join(missing)} read by the model not found in the document text "
            "(unverified extraction).", UNVERIFIED_REQUEST,
        )
    differing = [label for label, value, equal in items if not equal()]
    if not differing:
        return make_finding(cid, "pass", f"{subject}: {SCAN_NOTE}.")
    return make_finding(
        cid, "fail", f"{subject}: {', '.join(differing)} read from the scan differs from the form. Cannot tell "
        "whether the scan was misread or the form is wrong; a person should look.",
        SCAN_MISMATCH_REQUEST, True,
    )


def ground_extraction(data, extracted, reader=read_text):
    """Return the grounding findings for the primary document and any supporting documents."""
    country = data.get("country")
    form = data
    findings = []

    status = (extracted or {}).get("status")
    documents = data.get("documents") or []
    if status != "ok" or not documents:
        findings.append(make_finding(PRIMARY_ID, "skipped", "Skipped: no readable extraction to ground."))
    else:
        text = _read(documents[0], reader)
        if text is None:
            findings.append(make_finding(PRIMARY_ID, "skipped", "Skipped: the document text could not be read."))
        else:
            items = [(field, value, (lambda v=value, f=form_value, t=test: t(v, f)))
                     for field, value, form_value, test in _field_checks(extracted, form, country)]
            layer = len(text.strip()) >= MIN_TEXT_CHARS
            if not items:
                findings.append(make_finding(PRIMARY_ID, "pass", "No name or tax ID was read, so nothing to ground."))
            else:
                findings.append(_judge(PRIMARY_ID, "Tax document", items, text, layer))

    support = [d for d in (extracted or {}).get("supporting_documents", []) if d.get("path")]
    for doc in support:
        text = _read(doc["path"], reader)
        label = f"Supporting document {doc['path'].replace(chr(92), '/').rsplit('/', 1)[-1]}"
        if text is None:
            findings.append(make_finding(SUPPORTING_ID, "skipped", f"Skipped: {label} text could not be read."))
            continue
        form_names = [normalize_name(form.get("company_name"), country), normalize_name(form.get("bank_account_name"), country)]
        items = [(name, name, (lambda n=name: normalize_name(n, country) in form_names)) for name in doc["entity_names"]]
        if items:
            findings.append(_judge(SUPPORTING_ID, label, items, text, len(text.strip()) >= MIN_TEXT_CHARS))
    return _merge_supporting(findings)


def _merge_supporting(findings):
    """One finding for all supporting documents: the worst result, with every document's evidence."""
    primary = [f for f in findings if f["id"] == PRIMARY_ID]
    support = [f for f in findings if f["id"] == SUPPORTING_ID]
    if len(support) <= 1:
        return primary + support
    order = {"pass": 0, "skipped": 1, "warn": 2, "fail": 3, "error": 4, "block": 5}
    worst = max(support, key=lambda f: order[f["result"]])
    merged = dict(worst)
    merged["evidence"] = " | ".join(f["evidence"] for f in support)
    merged["human_review"] = any(f["human_review"] for f in support)
    return primary + [merged]
