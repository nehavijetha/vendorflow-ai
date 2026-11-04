"""S6 decision: deterministic severity rules."""

# Results that need someone's attention. "skipped" is deliberately not here: a skipped check is not
# a new problem, but it is also not a pass, so it can never lead to APPROVED.
ATTENTION = ("warn", "fail", "error")


def determine_status(findings):
    results = [f["result"] for f in findings]
    if "block" in results:
        return "REJECTED"
    # Only an explicit pass counts. Skipped, error and unknown values all keep the run PENDING.
    if not results or any(r != "pass" for r in results):
        return "PENDING"
    return "APPROVED"


def decide(findings):
    unresolved = [f for f in findings if f["result"] in ATTENTION]
    skipped = [f for f in findings if f["result"] == "skipped"]
    requests = []
    for f in findings:
        text = f.get("vendor_request")
        if text and text not in requests:
            requests.append(text)
    wants_review = any(f.get("human_review") for f in findings)
    if skipped and not unresolved and not any(f["result"] == "block" for f in findings):
        wants_review = True  # a skip with no explained cause needs a person, not a silent wait
    return {
        "status": determine_status(findings),
        "requires_human_review": wants_review,
        "blocking": [f["id"] for f in findings if f["result"] == "block"],
        "unresolved": [f["id"] for f in unresolved],
        "skipped": [f["id"] for f in skipped],
        "technical_errors": [f["id"] for f in findings if f["result"] == "error"],
        "vendor_requests": requests,
    }
