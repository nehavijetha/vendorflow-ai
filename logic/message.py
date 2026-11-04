"""S6 vendor message.

The model writes one short opening paragraph for PENDING runs. Everything that carries
facts (what the vendor must provide) is inserted verbatim by code from the findings'
`vendor_request` lines, so the model cannot invent or leak a reason. Approved and rejected
messages are fixed templates. Internal evidence is never given to the model.
"""
import re

INTRO_SYSTEM = (
    "You write the opening paragraph of an email from a company's accounts payable team to a vendor "
    "whose onboarding needs more information. Write 1 or 2 friendly, professional sentences, at most "
    "50 words. Do not list requirements, mention reasons, numbers, or other vendors, and do not add a "
    "greeting or sign-off. The list of requirements is added separately."
)

TEMPLATE_INTRO = "Thank you for submitting your onboarding details. We need a little more information before we can finish."
APPROVED = (
    "Thank you for submitting your onboarding details. We have verified everything and your company "
    "is now set up as a vendor. No further action is needed."
)
REJECTED = (
    "Thank you for submitting your onboarding details. After review we are unable to proceed with "
    "onboarding your company at this time."
)
REVIEW_NOTE = "Our team is also reviewing your submission and may contact you."
CLOSING = "Once we receive this, we will continue the review."

_MAX_INTRO_CHARS = 350
_MAX_NAME_CHARS = 80
# The opening must not claim an outcome, quote numbers, add links, or start a list.
_UNSAFE_INTRO = re.compile(
    r"https?://|\d|\n\s*[-*•]|approv|reject|accept|welcome|congratulat|onboard|declin", re.IGNORECASE
)


def _clean_name(name):
    """Vendor-supplied text is data: one line, bounded length."""
    return " ".join(str(name or "").split())[:_MAX_NAME_CHARS] or "Vendor"


def _intro(client):
    if client is not None:
        try:
            # No vendor-supplied text goes to the model: the opening does not need it.
            text = (client.write(INTRO_SYSTEM, "Write the opening paragraph.") or "").strip()
            if text and len(text) <= _MAX_INTRO_CHARS and not _UNSAFE_INTRO.search(text):
                return text, "model"
        except Exception:  # wording is optional; fall back to the template
            pass
    return TEMPLATE_INTRO, "template"


def draft_message(status, vendor_requests, company_name, requires_human_review=False, client=None):
    """Return (message, source) where source says whether the model wrote the opening."""
    company_name = _clean_name(company_name)
    if status == "APPROVED":
        return f"Dear {company_name},\n\n{APPROVED}", "template"
    if status == "REJECTED":
        return f"Dear {company_name},\n\n{REJECTED}", "template"
    intro, source = _intro(client)
    lines = [f"Dear {company_name},", "", intro, ""]
    if vendor_requests:
        lines.append("What we need from you:")
        lines += [f"  - {request}" for request in vendor_requests]
        lines.append("")
    if requires_human_review or not vendor_requests:
        lines += [REVIEW_NOTE, ""]
    lines.append(CLOSING)
    return "\n".join(lines), source
