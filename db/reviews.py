from sqlalchemy import select

from logic.review import ReviewError, effective_outcome, validate_review

from .models import Finding, Review, Run, StageEvent
from .vendors import onboard_vendor


def add_review(session, run_id, reviewer, decision, note):
    run = session.get(Run, run_id)
    if run is None:
        raise ReviewError(f"Unknown run '{run_id}'.")
    errors = session.scalars(
        select(Finding.check_id).where(Finding.run_id == run_id, Finding.result == "error")
    ).all()
    validate_review(run.status, decision, reviewer, note, errors)
    review = Review(run_id=run_id, reviewer=reviewer.strip(), decision=decision, note=note.strip())
    session.add(review)
    onboarded = decision == "approve" and run.vendor_id is None
    if decision == "approve":
        onboard_vendor(session, run)  # so later duplicate checks see this vendor
    # The runs row changes in place (vendor_id), and a review changes the effective status, so the
    # event keeps the history rebuildable from events alone.
    session.add(StageEvent(run_id=run_id, stage="S9", status="completed",
                           detail=f"decision={decision}; reviewer={review.reviewer}"
                                  + ("; vendor onboarded" if onboarded else "")))
    session.commit()
    return review


def latest_review(session, run_id):
    review = session.scalars(
        select(Review).where(Review.run_id == run_id).order_by(Review.id.desc()).limit(1)
    ).first()
    if review is None:
        return None
    return {"reviewer": review.reviewer, "decision": review.decision, "note": review.note,
            "created_at": review.created_at}


def run_outcome(session, run_id):
    run = session.get(Run, run_id)
    return effective_outcome(run.status, latest_review(session, run_id))
