"""Human-in-the-loop rules. The system result is never edited; a review sits beside it."""

DECISIONS = ("approve", "reject", "request_info")


class ReviewError(ValueError):
    pass


def validate_review(system_status, decision, reviewer, note, technical_errors=()):
    if decision not in DECISIONS:
        raise ReviewError(f"Unknown decision '{decision}'.")
    if not (reviewer or "").strip():
        raise ReviewError("A reviewer name is required.")
    if not (note or "").strip():
        raise ReviewError("A note explaining the decision is required.")
    if system_status == "APPROVED":
        raise ReviewError("This run was already approved; there is nothing to review.")
    if system_status == "REJECTED":
        raise ReviewError("A rejection from a confirmed disqualifying finding cannot be overridden.")
    if decision == "approve" and technical_errors:
        raise ReviewError("Cannot approve over a technical error; re-run the submission instead.")


def effective_outcome(system_status, latest_review=None):
    """Status shown to operators, with where it came from."""
    if system_status == "PENDING" and latest_review:
        if latest_review["decision"] == "approve":
            return {"status": "APPROVED", "source": "reviewer"}
        if latest_review["decision"] == "reject":
            return {"status": "REJECTED", "source": "reviewer"}
    return {"status": system_status, "source": "system"}
