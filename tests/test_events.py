"""The runs row is updated in place, so every status change must also leave an event: the status
history has to be rebuildable from events alone."""
from stubs import StubModel
import pytest
from sqlalchemy import select

from db.models import Run, StageEvent, make_session_factory
from db.reviews import add_review
from db.seed import seed_vendors
from pipeline.runner import STAGE_NAMES, STAGES, run_pipeline
from scripts.make_sample_pdfs import build_all
from ui.data import run_detail, runs_dataframe, status_from_events


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    out = tmp_path_factory.mktemp("event_samples")
    build_all(out)
    return out


@pytest.fixture
def session(tmp_path):
    with make_session_factory(f"sqlite:///{tmp_path / 'e.db'}")() as s:
        seed_vendors(s)
        yield s


def sub(samples, doc="w9_valid.pdf", **overrides):
    base = {"company_name": "Orion Components LLC", "country": "United States", "tax_id": "12-3456789",
            "bank_account_name": "Orion Components LLC", "bank_account_num": "000555666777",
            "documents": [str(samples / doc)]}
    return {**base, **overrides}


def events_of(session, run_id):
    return [{"stage": e.stage, "status": e.status, "detail": e.detail}
            for e in session.scalars(select(StageEvent).where(StageEvent.run_id == run_id).order_by(StageEvent.id))]


def assert_rebuilds(session, run_id):
    rebuilt = status_from_events(events_of(session, run_id))
    row = runs_dataframe(session)
    row = row[row["Run"] == run_id].iloc[0]
    assert rebuilt["system"] == session.get(Run, run_id).status == row["System status"]
    assert rebuilt["status"] == row["Final status"] and rebuilt["source"] == row["Decided by"]
    return rebuilt


def test_each_outcome_can_be_rebuilt_from_its_events(session, samples):
    approved = run_pipeline(session, sub(samples), client=StubModel(), use_llm=True)
    pending = run_pipeline(session, sub(samples, bank_account_num="000111222333"), client=StubModel(), use_llm=True)
    rejected = run_pipeline(session, sub(samples, doc="w9_blocked.pdf", company_name="Shadow Supplies LLC",
                                         tax_id="55-5555555", bank_account_name="Shadow Supplies LLC",
                                         bank_account_num="999000111222"), client=StubModel(), use_llm=True)
    assert [assert_rebuilds(session, r["run_id"])["status"] for r in (approved, pending, rejected)] == [
        "APPROVED", "PENDING", "REJECTED"]


def test_a_pipeline_crash_is_rebuilt_as_pending(session, samples, monkeypatch):
    monkeypatch.setattr("pipeline.runner.decide", lambda findings: 1 / 0)
    result = run_pipeline(session, sub(samples), client=StubModel(), use_llm=True)
    assert assert_rebuilds(session, result["run_id"])["status"] == "PENDING"


def test_a_technical_extraction_failure_does_not_pretend_the_pipeline_stopped(session, samples):
    result = run_pipeline(session, sub(samples, doc="w9_scanned.pdf"), use_llm=False)  # a scan needs a model
    names = [(e["stage"], e["status"]) for e in events_of(session, result["run_id"])]
    assert ("S3", "failed") in names and ("S6", "completed") in names
    assert assert_rebuilds(session, result["run_id"])["status"] == "PENDING"


def test_a_reviewer_approval_writes_an_event_and_is_rebuilt_as_reviewer_approved(session, samples):
    pending = run_pipeline(session, sub(samples, bank_account_name="Zenith Logistics Group"), client=StubModel(), use_llm=True)
    add_review(session, pending["run_id"], "Priya", "approve", "Confirmed by phone.")
    last = events_of(session, pending["run_id"])[-1]
    assert last["stage"] == "S9" and last["status"] == "completed"
    assert "decision=approve" in last["detail"] and "reviewer=Priya" in last["detail"] and "vendor onboarded" in last["detail"]
    rebuilt = assert_rebuilds(session, pending["run_id"])
    assert (rebuilt["system"], rebuilt["status"], rebuilt["source"]) == ("PENDING", "APPROVED", "reviewer")
    assert session.get(Run, pending["run_id"]).status == "PENDING"  # the system result itself is untouched


def test_the_latest_review_wins_when_rebuilding(session, samples):
    pending = run_pipeline(session, sub(samples, bank_account_name="Zenith Logistics Group"), client=StubModel(), use_llm=True)
    add_review(session, pending["run_id"], "Priya", "request_info", "Asked for a bank letter.")
    assert assert_rebuilds(session, pending["run_id"])["status"] == "PENDING"
    add_review(session, pending["run_id"], "Sam", "reject", "Letter did not arrive.")
    assert assert_rebuilds(session, pending["run_id"])["status"] == "REJECTED"


def test_a_second_approval_does_not_claim_to_onboard_the_vendor_again(session, samples):
    pending = run_pipeline(session, sub(samples, bank_account_name="Zenith Logistics Group"), client=StubModel(), use_llm=True)
    add_review(session, pending["run_id"], "Priya", "approve", "First.")
    add_review(session, pending["run_id"], "Sam", "approve", "Second.")
    details = [e["detail"] for e in events_of(session, pending["run_id"]) if e["stage"] == "S9"]
    assert "vendor onboarded" in details[0] and "vendor onboarded" not in details[1]


def test_resubmissions_rebuild_independently_of_their_parent(session, samples):
    first = run_pipeline(session, sub(samples, bank_account_name="Orion Components Holdings LLC"), client=StubModel(), use_llm=True)
    second = run_pipeline(session, {"supporting_documents": [str(samples / "parent_entity_proof.pdf")]},
                          parent_run_id=first["run_id"], client=StubModel(), use_llm=True)
    assert assert_rebuilds(session, first["run_id"])["status"] == "PENDING"
    assert assert_rebuilds(session, second["run_id"])["status"] == "APPROVED"


def test_stage_names_cover_every_stage_that_can_appear_in_a_timeline(session, samples):
    pending = run_pipeline(session, sub(samples, bank_account_name="Zenith Logistics Group"), client=StubModel(), use_llm=True)
    add_review(session, pending["run_id"], "Priya", "reject", "No.")
    seen = {e["stage"] for e in run_detail(session, pending["run_id"])["events"]}
    assert seen <= set(STAGE_NAMES) and set(STAGES) < set(STAGE_NAMES) and STAGE_NAMES["S9"] == "Human review"


def test_a_run_with_no_events_rebuilds_to_nothing():
    assert status_from_events([]) == {"system": None, "status": None, "source": "system"}
