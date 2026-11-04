"""Pipeline runner tests: real sample PDFs, offline parser, no network."""
import json

import pytest
from sqlalchemy import select

from db.models import Finding, Run, StageEvent, Vendor, make_session_factory
from db.reviews import add_review
from db.seed import seed_vendors
from logic.cache import cached_extractor
from logic.extract import ExtractionError, extract_document
from logic.message import draft_message
from pipeline.runner import PipelineError, run_pipeline
from scripts.make_sample_pdfs import build_all


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    out = tmp_path_factory.mktemp("pipeline_samples")
    build_all(out)
    return out


@pytest.fixture
def session(tmp_path):
    with make_session_factory(f"sqlite:///{tmp_path / 'p.db'}")() as s:
        seed_vendors(s)
        yield s


def submission(samples, doc="w9_valid.pdf", **overrides):
    base = {"company_name": "Orion Components LLC", "country": "United States", "tax_id": "12-3456789",
            "bank_account_name": "Orion Components LLC", "bank_account_num": "000555666777",
            "documents": [str(samples / doc)]}
    return {**base, **overrides}


def go(session, sub, **kwargs):
    return run_pipeline(session, sub, use_llm=False, **kwargs)


class FakeClient:
    def __init__(self, intro="Thanks for your submission, we just need a little more.", error=None):
        self.intro, self.error, self.writes = intro, error, []

    def write(self, system, prompt):
        self.writes.append(prompt)
        if self.error:
            raise self.error
        return self.intro

    def generate(self, parts):
        raise ExtractionError("fake extraction unavailable")


# --- happy path and persistence ---------------------------------------------------------

def test_happy_path_runs_all_stages_and_onboards_the_vendor(session, samples):
    result = go(session, submission(samples))
    assert result["status"] == "APPROVED" and result["error"] is None
    stages = [(e["stage"], e["status"]) for e in result["events"]]
    assert stages[0] == ("S1", "started") and stages[-1] == ("S8", "completed")
    assert {s for s, _ in stages} == {"S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"}
    assert len(result["findings"]) == 12  # eleven checks plus the grounding finding
    run = session.get(Run, result["run_id"])
    assert run.status == "APPROVED" and run.vendor_id and "verified everything" in run.vendor_message
    assert json.loads(run.extracted_json)["tax_id"] == "12-3456789"
    assert len(session.scalars(select(Finding).where(Finding.run_id == run.id)).all()) == 12
    assert len(session.scalars(select(StageEvent).where(StageEvent.run_id == run.id)).all()) == len(result["events"])
    assert session.get(Vendor, run.vendor_id).legal_name == "Orion Components LLC"


def test_events_are_saved_as_they_happen(session, samples):
    seen = []

    def on_event(event):
        rows = session.scalars(select(StageEvent).where(StageEvent.run_id == event["run_id"])).all()
        seen.append((event["stage"], event["status"], len(rows)))  # row already committed when callback fires

    go(session, submission(samples), on_event=on_event)
    assert all(count == i + 1 for i, (_, _, count) in enumerate(seen))
    order = [s for s, _, _ in seen]
    assert order.index("S3") < order.index("S4") < order.index("S5") < order.index("S6") < order.index("S8")


# --- the four demo scenarios -------------------------------------------------------------

def test_dba_mismatch_then_resubmission_links_runs_and_keeps_the_original(session, samples):
    first = go(session, submission(samples, bank_account_name="Orion Components Holdings LLC"))
    assert first["status"] == "PENDING"
    assert any("DBA" in r for r in first["decision"]["vendor_requests"])
    assert "What we need from you:" in first["vendor_message"]
    before = [(f.check_id, f.result) for f in session.scalars(select(Finding).where(Finding.run_id == first["run_id"]))]

    second = go(session, {"supporting_documents": [str(samples / "parent_entity_proof.pdf")]},
                parent_run_id=first["run_id"])
    assert second["status"] == "APPROVED" and second["parent_run_id"] == first["run_id"]
    assert session.get(Run, second["run_id"]).parent_run_id == first["run_id"]
    # the original run is untouched
    assert session.get(Run, first["run_id"]).status == "PENDING"
    after = [(f.check_id, f.result) for f in session.scalars(select(Finding).where(Finding.run_id == first["run_id"]))]
    assert before == after


def test_right_ein_wrong_company(session, samples):
    result = go(session, submission(samples, company_name="Zenith Logistics Group",
                                    bank_account_name="Zenith Logistics Group"))
    flagged = {f["id"] for f in result["findings"] if f["result"] != "pass"}
    assert flagged == {"check_form_document_name"} and result["status"] == "PENDING"


def test_bank_account_used_by_another_vendor_does_not_leak(session, samples):
    result = go(session, submission(samples, bank_account_num="000111222333"))
    assert result["status"] == "PENDING" and result["decision"]["requires_human_review"]
    assert "Northwind" not in result["vendor_message"]


def test_wrong_attachment_reports_every_problem(session, samples):
    result = go(session, submission(samples, doc="utility_bill.pdf"))
    assert result["status"] == "PENDING"
    assert "check_document_type" in result["decision"]["unresolved"]
    assert "signed IRS Form W-9" in result["vendor_message"]


# --- failure handling -------------------------------------------------------------------

def test_scan_without_vision_is_pending_with_a_technical_error(session, samples):
    result = go(session, submission(samples, doc="w9_scanned.pdf"))
    assert result["status"] == "PENDING" and result["decision"]["technical_errors"]
    assert any(e["stage"] == "S3" and e["status"] == "failed" for e in result["events"])


def test_missing_document_is_one_clear_request_not_a_technical_error(session, samples):
    result = go(session, submission(samples, documents=[]))
    assert result["status"] == "PENDING" and not result["decision"]["technical_errors"]
    assert any("tax registration document" in r for r in result["decision"]["vendor_requests"])
    assert any(e["stage"] == "S3" and "skipped" in (e["detail"] or "") for e in result["events"])


def test_missing_file_on_disk_is_a_technical_error(session, samples):
    result = go(session, submission(samples, documents=[str(samples / "nope.pdf")]))
    assert result["status"] == "PENDING" and result["decision"]["technical_errors"]


def test_unexpected_crash_is_recorded_not_raised(session, samples, monkeypatch):
    def boom(findings):
        raise RuntimeError("decision engine exploded")

    monkeypatch.setattr("pipeline.runner.decide", boom)
    result = go(session, submission(samples))
    assert result["status"] == "PENDING" and "exploded" in result["error"]
    assert result["events"][-1]["status"] == "failed" and result["events"][-1]["stage"] == "S6"
    saved = session.scalars(select(Finding).where(Finding.run_id == result["run_id"], Finding.check_id == "pipeline")).one()
    assert saved.result == "error"
    assert session.get(Run, result["run_id"]).vendor_id is None


# --- resubmission rules and duplicates after onboarding ------------------------------------

def test_only_pending_runs_can_be_resubmitted(session, samples):
    approved = go(session, submission(samples))
    with pytest.raises(PipelineError, match="PENDING"):
        go(session, {}, parent_run_id=approved["run_id"])
    with pytest.raises(PipelineError, match="Unknown parent"):
        go(session, {}, parent_run_id="run-nope")
    assert len(session.scalars(select(Run)).all()) == 1  # failed resubmissions create no run


def test_rerunning_an_approved_vendor_trips_the_duplicate_checks(session, samples):
    go(session, submission(samples))
    again = go(session, submission(samples))
    assert again["status"] == "PENDING"
    assert {"check_duplicate_tax_id", "check_duplicate_bank_account"} & set(again["decision"]["unresolved"])


def test_reviewer_approval_onboards_the_vendor(session, samples):
    pending = go(session, submission(samples, bank_account_name="Zenith Logistics Group"))
    assert pending["status"] == "PENDING"
    add_review(session, pending["run_id"], "Priya", "approve", "Bank letter confirmed by phone.")
    assert session.get(Run, pending["run_id"]).vendor_id is not None
    again = go(session, submission(samples))
    assert "check_duplicate_tax_id" in again["decision"]["unresolved"]


# --- message drafting ----------------------------------------------------------------------

def test_model_writes_only_the_opening_and_requests_stay_verbatim():
    client = FakeClient()
    requests = ["Please upload your signed W-9.", "Confirm your bank account holder name."]
    message, source = draft_message("PENDING", requests, "Acme LLC", client=client)
    assert source == "model" and client.writes == ["Write the opening paragraph."]
    assert client.intro in message and all(r in message for r in requests)


@pytest.mark.parametrize("intro", ["", "x" * 400, "Visit http://evil.example now", "Your tax ID 12-3456789 failed",
                                   "Here you go:\n- Provide a W-9"])
def test_unsafe_model_intro_falls_back_to_template(intro):
    message, source = draft_message("PENDING", ["Please upload a W-9."], "Acme LLC", client=FakeClient(intro=intro))
    assert source == "template" and "Please upload a W-9." in message
    assert not intro.strip() or intro.strip() not in message


def test_model_failure_falls_back_to_template():
    _, source = draft_message("PENDING", ["x"], "Acme", client=FakeClient(error=RuntimeError("503")))
    assert source == "template"


def test_approved_and_rejected_messages_never_call_the_model():
    client = FakeClient()
    assert "no further action" in draft_message("APPROVED", [], "Acme", client=client)[0].lower()
    assert "unable to proceed" in draft_message("REJECTED", ["ignored"], "Acme", client=client)[0]
    assert "ignored" not in draft_message("REJECTED", ["ignored"], "Acme", client=client)[0]
    assert client.writes == []


def test_pending_without_requests_still_tells_the_vendor_what_happens_next():
    message, _ = draft_message("PENDING", [], "Acme")
    assert "reviewing your submission" in message


def test_runner_message_uses_model_opening_when_available(session, samples):
    result = run_pipeline(session, submission(samples, bank_account_name="Orion Components Holdings LLC"),
                          client=FakeClient(), use_llm=True)
    assert result["message_source"] == "model" and "little more" in result["vendor_message"]


# --- extraction cache -----------------------------------------------------------------------

def test_cache_stores_only_successful_model_results(samples, tmp_path):
    calls = []

    def fake_extractor(path, client=None, use_llm=True):
        calls.append(path)
        return {"status": "ok", "source": "gemini-text", "detail": "Extracted by model.", "document_type": "w9",
                "legal_name": "X", "tax_id": "1", "signed": True, "signed_date": "d", "entity_names": []}

    cache = tmp_path / "cache.json"
    run = cached_extractor(cache, fake_extractor)
    first, second = run(samples / "w9_valid.pdf"), run(samples / "w9_valid.pdf")
    assert len(calls) == 1 and second["detail"].endswith("(cached)") and first["tax_id"] == second["tax_id"]
    run(samples / "w9_unsigned.pdf")
    assert len(calls) == 2  # different content is a different key


def test_cache_ignores_offline_and_error_results(samples, tmp_path):
    cache = tmp_path / "cache.json"
    run = cached_extractor(cache, extract_document)
    run(samples / "w9_valid.pdf", None, False)  # offline parser
    run(samples / "w9_scanned.pdf", None, False)  # error
    assert not cache.exists()
