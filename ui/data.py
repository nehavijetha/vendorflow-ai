"""Data access for the UI: DataFrames and detail records built from the database.
No Streamlit in here, so it can be tested directly."""
import json
import uuid
from pathlib import Path

import pandas as pd
from sqlalchemy import delete, select

from db.models import Document, Finding, Review, Run, StageEvent, Vendor
from db.seed import seed_vendors
from logic.review import effective_outcome

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

CHECK_LABELS = {
    "check_required_fields": "Required fields and document",
    "check_extraction_grounding": "Extraction grounding",
    "check_supporting_grounding": "Supporting document grounding",
    "check_tax_id_format": "Tax ID format",
    "check_document_type": "Document type",
    "check_document_authenticity": "Genuine IRS form",
    "check_signature_and_date": "Signature and date",
    "check_form_document_tax_id": "Form vs document tax ID",
    "check_form_document_name": "Form vs document name",
    "check_document_reuse": "Document reuse",
    "check_tax_id_checksum": "Tax ID check digits",
    "check_company_bank_name": "Company vs bank holder name",
    "check_duplicate_tax_id": "Duplicate tax ID",
    "check_duplicate_bank_account": "Duplicate bank account",
    "pipeline": "Pipeline",
}


def check_label(check_id):
    return CHECK_LABELS.get(check_id, check_id)


def _latest_reviews(session):
    latest = {}
    for review in session.scalars(select(Review).order_by(Review.id)):
        latest[review.run_id] = {"reviewer": review.reviewer, "decision": review.decision,
                                 "note": review.note, "created_at": review.created_at}
    return latest


def _finding_flags(session):
    """run_id -> (checks needing attention, any finding wants human review, skipped checks)."""
    flags = {}
    for finding in session.scalars(select(Finding)):
        flagged, review, skipped = flags.get(finding.run_id, (0, False, 0))
        flags[finding.run_id] = (flagged + (finding.result not in ("pass", "skipped")),
                                 review or finding.human_review, skipped + (finding.result == "skipped"))
    return flags


def runs_dataframe(session):
    reviews, flags = _latest_reviews(session), _finding_flags(session)
    rows = []
    for run in session.scalars(select(Run).order_by(Run.created_at.desc())):
        data = json.loads(run.input_json)
        outcome = effective_outcome(run.status, reviews.get(run.id))
        flagged, wants_review, skipped = flags.get(run.id, (0, False, 0))
        rows.append({
            "Run": run.id,
            "Resubmission of": run.parent_run_id or "",
            "Company": data.get("company_name") or "",
            "Country": data.get("country") or "",
            "System status": run.status or "",
            "Final status": outcome["status"] or "",
            "Decided by": outcome["source"],
            "Flagged checks": flagged,
            "Skipped checks": skipped,
            "Needs review": bool(run.status == "PENDING" and outcome["source"] == "system"
                                 and reviews.get(run.id, {}).get("decision") != "request_info"
                                 and wants_review),
            "Created": run.created_at,
        })
    columns = ["Run", "Resubmission of", "Company", "Country", "System status", "Final status",
               "Decided by", "Flagged checks", "Skipped checks", "Needs review", "Created"]
    return pd.DataFrame(rows, columns=columns)


def kpis(df):
    final = df["Final status"]
    return {
        "Runs": len(df),
        "Approved": int((final == "APPROVED").sum()),
        "Pending": int((final == "PENDING").sum()),
        "Rejected": int((final == "REJECTED").sum()),
        "Awaiting review": int(df["Needs review"].sum()),
    }


def flagged_by_check(session):
    rows = session.execute(
        select(Finding.check_id, Finding.result).where(Finding.result.notin_(["pass", "skipped"]))
    ).all()
    counts = {}
    for check_id, _ in rows:
        counts[check_label(check_id)] = counts.get(check_label(check_id), 0) + 1
    return pd.DataFrame(sorted(counts.items(), key=lambda kv: -kv[1]), columns=["Check", "Flagged runs"])


def run_detail(session, run_id):
    run = session.get(Run, run_id)
    if run is None:
        return None
    findings = session.scalars(select(Finding).where(Finding.run_id == run_id).order_by(Finding.id)).all()
    events = session.scalars(select(StageEvent).where(StageEvent.run_id == run_id).order_by(StageEvent.id)).all()
    reviews = session.scalars(select(Review).where(Review.run_id == run_id).order_by(Review.id)).all()
    children = session.scalars(select(Run.id).where(Run.parent_run_id == run_id).order_by(Run.created_at)).all()
    latest = None
    if reviews:
        last = reviews[-1]
        latest = {"reviewer": last.reviewer, "decision": last.decision, "note": last.note}
    return {
        "run_id": run.id,
        "parent_run_id": run.parent_run_id,
        "children": list(children),
        "system_status": run.status,
        "outcome": effective_outcome(run.status, latest),
        "input": json.loads(run.input_json),
        "extracted": json.loads(run.extracted_json) if run.extracted_json else None,
        "vendor_message": run.vendor_message,
        "vendor_id": run.vendor_id,
        "created_at": run.created_at,
        "findings": [{"id": f.check_id, "check": check_label(f.check_id), "result": f.result,
                      "evidence": f.evidence, "vendor_request": f.vendor_request,
                      "human_review": f.human_review} for f in findings],
        "events": [{"stage": e.stage, "status": e.status, "detail": e.detail, "at": e.created_at} for e in events],
        "reviews": [{"reviewer": r.reviewer, "decision": r.decision, "note": r.note, "at": r.created_at}
                    for r in reviews],
    }


_STATE = {"pass": 0, "skipped": 1, "warn": 2, "fail": 2, "error": 2, "block": 3}


def _state(finding):
    return _STATE.get(finding["result"], 2) if finding else 0


def compare_findings(parent_findings, child_findings):
    """Match findings of two runs by check id. A skipped check that later passes is resolved."""
    before = {f["id"]: f for f in parent_findings}
    after = {f["id"]: f for f in child_findings}
    diff = {"resolved": [], "still_open": [], "new": [], "unchanged_pass": 0}
    for check_id in [*after, *(c for c in before if c not in after)]:
        old, new = before.get(check_id), after.get(check_id)
        old_state, new_state = _state(old), _state(new)
        row = {"id": check_id, "check": check_label(check_id),
               "before": old["result"] if old else "absent", "after": new["result"] if new else "absent",
               "evidence": (new or old)["evidence"]}
        if new_state == 0:
            if old_state > 0:
                diff["resolved"].append(row)
            elif old is not None and new is not None:
                diff["unchanged_pass"] += 1  # passing in both runs; a check present in only one is not counted
        elif old_state == 0 or (old_state == 1 and new_state >= 2):
            diff["new"].append(row)  # newly flagged, or a skipped check that now found a problem
        else:
            diff["still_open"].append(row)
    return diff


def run_diff(session, run_id):
    """What a resubmission changed compared with the run it resubmits, or None for an original run."""
    child = run_detail(session, run_id)
    if child is None or not child["parent_run_id"]:
        return None
    parent = run_detail(session, child["parent_run_id"])
    if parent is None:
        return None
    diff = compare_findings(parent["findings"], child["findings"])
    diff.update(parent_run_id=parent["run_id"], parent_status=parent["outcome"]["status"],
                status=child["outcome"]["status"])
    return diff


def status_from_events(events):
    """Rebuild a run's status from its events alone. The runs row is updated in place, so this is
    the proof that nothing changed status without leaving a record."""
    system, effective, source = None, None, "system"
    for event in events:
        stage, status, detail = event["stage"], event["status"], event.get("detail") or ""
        if stage == "S6" and status == "completed":
            system = effective = detail.split(";")[0].strip()
            source = "system"
        elif status == "failed" and stage != "S3":  # a pipeline stop leaves the run PENDING
            system = effective = "PENDING"
            source = "system"
        elif stage == "S9" and status == "completed" and system == "PENDING":
            decision = detail.split("decision=")[-1].split(";")[0]
            if decision in ("approve", "reject"):
                effective, source = ("APPROVED" if decision == "approve" else "REJECTED"), "reviewer"
            else:
                effective, source = system, "system"
    return {"system": system, "status": effective, "source": source}


def review_queue(session):
    df = runs_dataframe(session)
    return [run_detail(session, run_id) for run_id in df.loc[df["Needs review"], "Run"]]


def reviews_dataframe(session):
    rows = [{"Run": r.run_id, "Reviewer": r.reviewer, "Decision": r.decision, "Note": r.note, "Time": r.created_at}
            for r in session.scalars(select(Review).order_by(Review.id.desc()))]
    return pd.DataFrame(rows, columns=["Run", "Reviewer", "Decision", "Note", "Time"])


def reset_demo(session):
    """Delete every run, finding, event, review and vendor, then restore the seed vendors."""
    for model in (Review, StageEvent, Finding, Document, Run, Vendor):
        session.execute(delete(model))
    session.commit()
    seed_vendors(session)


def save_upload(data, upload_dir):
    """Validate an uploaded PDF and store it under a generated name. Only paths created here
    are ever handed to the pipeline."""
    if not data.startswith(b"%PDF"):
        raise ValueError("That file is not a PDF.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("PDF is larger than 10 MB.")
    upload_dir = Path(upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / f"{uuid.uuid4().hex}.pdf"
    path.write_bytes(data)
    return str(path)
