"""Runs one submission through S1-S7.

Every stage writes a StageEvent as it happens (and findings are saved one by one), so the
live view and the audit trail exist even if a run fails part-way. A resubmission is a new
run linked by parent_run_id; the parent and its findings are never modified.
"""
import json
import uuid
from datetime import datetime, timezone

from pydantic import ValidationError

from db.models import Finding, Run, StageEvent
from db.documents import build_history, store_documents
from db.reviews import latest_review
from db.seed import vendor_dicts
from db.vendors import onboard_vendor
from logic import checks as check_lib
from logic.cache import cached_extractor
from logic.decision import decide
from logic.extract import build_extracted, default_client
from logic.grounding import ground_extraction
from logic.message import draft_message
from logic.models import Extraction, Submission

STAGES = {
    "S1": "Intake",
    "S2": "Completeness",
    "S3": "Document extraction",
    "S4": "Grounding",
    "S5": "Verification",
    "S6": "Decision",
    "S7": "Communication",
    "S8": "Persistence",
}


# Human review happens after the pipeline but writes its own event, so a run's status history can be
# rebuilt from its events even though the runs row itself is updated in place.
STAGE_NAMES = {**STAGES, "S9": "Human review"}


class PipelineError(ValueError):
    pass


def _validated_update(submission):
    """Validate what the caller handed in. Only the keys it actually provided are kept, so a
    resubmission that sends one field does not blank the others."""
    try:
        model = submission if isinstance(submission, Submission) else Submission.model_validate(submission)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise PipelineError(f"Invalid submission: {problems}.") from exc
    return model.model_dump(exclude_unset=True)


def _merge(submission, parent_input):
    """A resubmission starts from the parent's data; supporting documents accumulate."""
    merged = {**(parent_input or {}), **{k: v for k, v in submission.items() if k != "supporting_documents"}}
    support = list((parent_input or {}).get("supporting_documents") or [])
    for path in submission.get("supporting_documents") or []:
        if str(path) not in support:
            support.append(str(path))
    merged["supporting_documents"] = support
    merged["documents"] = [str(p) for p in merged.get("documents") or []]
    return merged


class _Recorder:
    def __init__(self, session, run_id, on_event):
        self.session, self.run_id, self.on_event, self.events = session, run_id, on_event, []

    def __call__(self, stage, status, detail=None):
        now = datetime.now(timezone.utc)
        self.session.add(StageEvent(run_id=self.run_id, stage=stage, status=status, detail=detail, created_at=now))
        self.session.commit()
        event = {"run_id": self.run_id, "stage": stage, "name": STAGES[stage], "status": status,
                 "detail": detail, "at": now}
        self.events.append(event)
        if self.on_event:
            self.on_event(event)


def _save_finding(session, run_id, finding):
    session.add(Finding(run_id=run_id, check_id=finding["id"], result=finding["result"],
                        evidence=finding["evidence"], vendor_request=finding.get("vendor_request"),
                        human_review=bool(finding.get("human_review"))))
    session.commit()


def _summary(findings):
    results = [f["result"] for f in findings]
    attention = sum(r not in ("pass", "skipped") for r in results)
    text = f"{results.count('pass')} passed, {attention} need attention"
    return text + (f", {results.count('skipped')} skipped" if "skipped" in results else "")


def run_pipeline(session, submission, parent_run_id=None, client=None, use_llm=True,
                 cache_path=None, on_event=None):
    update = _validated_update(submission)  # fail before anything is created
    parent_input = None
    if parent_run_id is not None:
        parent = session.get(Run, parent_run_id)
        if parent is None:
            raise PipelineError(f"Unknown parent run '{parent_run_id}'.")
        if parent.status != "PENDING":
            raise PipelineError(f"Only a PENDING run can be resubmitted (run is {parent.status}).")
        review = latest_review(session, parent_run_id)
        if review and review["decision"] in ("approve", "reject"):
            raise PipelineError(f"A reviewer already closed this run ({review['decision']}).")
        parent_input = json.loads(parent.input_json)
    data = Submission.model_validate(_merge(update, parent_input)).model_dump()

    if use_llm and client is None:
        client = default_client()
    extractor = cached_extractor(cache_path) if cache_path else None

    run = Run(id=f"run-{uuid.uuid4().hex[:8]}", parent_run_id=parent_run_id,
              input_json=json.dumps(data, default=str))
    session.add(run)
    session.commit()
    record = _Recorder(session, run.id, on_event)
    findings, extracted, decision, message, message_source = [], None, None, None, None
    stage, error, current_documents = "S1", None, []

    try:
        record("S1", "started")
        current_documents = store_documents(session, run.id, data)
        record("S1", "completed", f"Run {run.id} created" + (f" as a resubmission of {parent_run_id}" if parent_run_id else "")
               + f"; {len(current_documents)} file(s) hashed")

        stage = "S2"
        record("S2", "started")
        for finding in check_lib.evaluate_submission(data, None, [], checks=[check_lib.check_required_fields]):
            findings.append(finding)
            _save_finding(session, run.id, finding)
        record("S2", "completed", findings[0]["evidence"])

        stage = "S3"
        record("S3", "started")
        if data["documents"]:
            extracted = build_extracted(data["documents"][0], data["supporting_documents"],
                                        client=client, use_llm=False, extractor=extractor)
        else:
            extracted = Extraction(status="missing").model_dump()
        run.extracted_json = json.dumps(extracted, default=str)
        session.commit()
        if extracted["status"] == "ok":
            record("S3", "completed", "; ".join(extracted["sources"]))
        elif extracted["status"] == "missing":
            record("S3", "completed", "No document supplied; extraction skipped")
        else:
            record("S3", "failed", f"{extracted['status']}: {extracted.get('detail', '')}")

        stage = "S4"
        record("S4", "started")
        grounding = ground_extraction(data, extracted)
        for finding in grounding:
            findings.append(finding)
            _save_finding(session, run.id, finding)
            record("S4", "progress", f"{finding['id']}: {finding['result']}")
        record("S4", "completed", _summary(grounding))

        stage = "S5"
        record("S5", "started")
        remaining = [c for c in check_lib.CHECKS if c is not check_lib.check_required_fields]

        def on_finding(finding):
            findings.append(finding)
            _save_finding(session, run.id, finding)
            record("S5", "progress", f"{finding['id']}: {finding['result']}")

        history = build_history(session, run.id, parent_run_id, current_documents)
        check_lib.evaluate_submission(data, extracted, vendor_dicts(session), checks=remaining,
                                      on_finding=on_finding, history=history)
        record("S5", "completed", _summary(findings))

        stage = "S6"
        record("S6", "started")
        decision = decide(findings)
        run.status = decision["status"]
        session.commit()
        record("S6", "completed", f"{decision['status']}"
               + (f"; unresolved: {', '.join(decision['unresolved'])}" if decision["unresolved"] else ""))

        stage = "S7"
        record("S7", "started")
        company = data.get("company_name") or "Vendor"
        args = (decision["status"], decision["vendor_requests"], company, decision["requires_human_review"])
        message, message_source = draft_message(*args, client=client)
        record("S7", "completed", f"Opening written by {message_source}")

        stage = "S8"
        record("S8", "started")
        run.vendor_message = message
        if decision["status"] == "APPROVED":
            onboard_vendor(session, run)
        session.commit()
        record("S8", "completed", "Run saved" + ("; vendor onboarded" if run.vendor_id else ""))
    except Exception as exc:  # no decision was reached: leave the run PENDING and say why
        error = f"{exc.__class__.__name__}: {exc}"
        session.rollback()
        run = session.get(Run, run.id)
        run.status = "PENDING"
        session.commit()
        finding = {"id": "pipeline", "result": "error", "evidence": f"Pipeline stopped at {stage}: {error}",
                   "vendor_request": None, "human_review": True}
        findings.append(finding)
        _save_finding(session, run.id, finding)
        record(stage, "failed", error)

    return {
        "run_id": run.id, "parent_run_id": parent_run_id, "status": run.status, "decision": decision,
        "findings": findings, "extracted": extracted, "vendor_message": message,
        "message_source": message_source, "events": record.events, "error": error,
    }
