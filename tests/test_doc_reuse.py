"""Document reuse: the same file (by content) under a different tax ID, outside the resubmission chain."""
import shutil

import pytest
from sqlalchemy import select

from db.documents import chain_run_ids
from db.models import Document, Run, make_session_factory
from db.seed import seed_vendors
from helpers import clean_history
from logic.checks import check_document_reuse
from logic.docs import file_sha256
from pipeline.runner import run_pipeline
from scripts.make_sample_pdfs import build_all
from ui.data import reset_demo


def prior(run_id, sha, tax_id="98-7654321"):
    return {"run_id": run_id, "sha256": sha, "filename": "x.pdf", "tax_id": tax_id}


def reuse(submission, history):
    return check_document_reuse(submission, None, [], history)


# --- the check on its own -------------------------------------------------------------------------------

def test_same_file_for_a_different_tax_id_is_flagged_for_a_person(submission):
    history = clean_history(submission, prior=[prior("run-a", "hash-of-w9_valid.pdf")])
    finding = reuse(submission, history)
    assert finding["result"] == "fail" and finding["human_review"]
    assert "run-a" in finding["evidence"] and "different tax ID" in finding["evidence"]


def test_the_vendor_message_reveals_nothing_about_the_other_run(submission):
    finding = reuse(submission, clean_history(submission, prior=[prior("run-a", "hash-of-w9_valid.pdf")]))
    assert "run-a" not in finding["vendor_request"] and "98-7654321" not in finding["vendor_request"]


def test_same_file_for_the_same_tax_id_is_not_flagged(submission):
    history = clean_history(submission, prior=[prior("run-a", "hash-of-w9_valid.pdf", tax_id="123456789")])
    finding = reuse(submission, history)
    assert finding["result"] == "pass" and "same tax ID" in finding["evidence"]


def test_tax_id_formatting_does_not_hide_the_same_vendor(submission):
    submission["tax_id"] = "12-3456789"
    history = clean_history(submission, prior=[prior("run-a", "hash-of-w9_valid.pdf", tax_id="123456789")])
    assert reuse(submission, history)["result"] == "pass"


def test_runs_in_the_same_resubmission_chain_are_excluded(submission):
    history = clean_history(submission, prior=[prior("run-parent", "hash-of-w9_valid.pdf")], chain={"run-parent"})
    assert reuse(submission, history)["result"] == "pass"


def test_a_different_file_is_clean(submission):
    assert reuse(submission, clean_history(submission, prior=[prior("run-a", "some-other-hash")]))["result"] == "pass"


def test_only_one_of_several_files_needs_to_clash(submission):
    submission["documents"] = ["a.pdf", "b.pdf"]
    history = clean_history(submission, prior=[prior("run-a", "hash-of-b.pdf")])
    assert reuse(submission, history)["result"] == "fail"


@pytest.mark.parametrize("history_factory,reason", [
    (lambda s: None, "no run history"),
    (lambda s: {**clean_history(s), "current_documents": []}, "no document"),
])
def test_missing_inputs_skip_the_check(submission, history_factory, reason):
    finding = reuse(submission, history_factory(submission))
    assert finding["result"] == "skipped" and reason in finding["evidence"]


def test_no_tax_id_means_vendors_cannot_be_told_apart_so_the_check_skips(submission):
    submission["tax_id"] = ""
    assert reuse(submission, clean_history(submission))["result"] == "skipped"


# --- through the pipeline -------------------------------------------------------------------------------

@pytest.fixture
def session(tmp_path):
    with make_session_factory(f"sqlite:///{tmp_path / 'r.db'}")() as s:
        seed_vendors(s)
        yield s


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    out = tmp_path_factory.mktemp("reuse_samples")
    build_all(out)
    return out


def sub(samples, doc="w9_valid.pdf", **overrides):
    base = {"company_name": "Orion Components LLC", "country": "United States", "tax_id": "12-3456789",
            "bank_account_name": "Orion Components LLC", "bank_account_num": "000555666777",
            "documents": [str(samples / doc)]}
    return {**base, **overrides}


def test_every_run_stores_the_hash_of_its_files(session, samples):
    result = run_pipeline(session, sub(samples) | {"supporting_documents": [str(samples / "parent_entity_proof.pdf")]},
                          use_llm=False)
    rows = session.scalars(select(Document).where(Document.run_id == result["run_id"])).all()
    assert {(r.role, r.filename) for r in rows} == {("tax", "w9_valid.pdf"), ("supporting", "parent_entity_proof.pdf")}
    assert {r.sha256 for r in rows} >= {file_sha256(samples / "w9_valid.pdf")}


def test_a_copied_document_submitted_for_another_vendor_goes_to_manual_review(session, samples):
    run_pipeline(session, sub(samples), use_llm=False)
    copy = samples / "copied_under_a_new_name.pdf"
    shutil.copy(samples / "w9_valid.pdf", copy)
    result = run_pipeline(session, sub(samples, tax_id="98-7654322", company_name="Other Vendor LLC",
                                       bank_account_name="Other Vendor LLC", bank_account_num="000222333444")
                          | {"documents": [str(copy)]}, use_llm=False)
    assert result["status"] == "PENDING" and "check_document_reuse" in result["decision"]["unresolved"]
    assert result["decision"]["requires_human_review"]
    assert "run-" not in result["vendor_message"]


def test_the_same_vendor_submitting_again_is_not_a_reuse_flag(session, samples):
    run_pipeline(session, sub(samples, bank_account_name="Orion Components Holdings LLC"), use_llm=False)
    again = run_pipeline(session, sub(samples, bank_account_name="Orion Components Holdings LLC"), use_llm=False)
    finding = {f["id"]: f for f in again["findings"]}["check_document_reuse"]
    assert finding["result"] == "pass"


def test_a_corrected_resubmission_never_flags_itself(session, samples):
    """The correction changes the tax ID but reuses the same file: same chain, so no flag."""
    first = run_pipeline(session, sub(samples, tax_id="12-3456780"), use_llm=False)
    fixed = run_pipeline(session, {"tax_id": "12-3456789"}, parent_run_id=first["run_id"], use_llm=False)
    finding = {f["id"]: f for f in fixed["findings"]}["check_document_reuse"]
    assert finding["result"] == "pass"
    sibling = run_pipeline(session, {"tax_id": "12-3456788"}, parent_run_id=first["run_id"], use_llm=False)
    assert {f["id"]: f for f in sibling["findings"]}["check_document_reuse"]["result"] == "pass"


def test_chain_ids_cover_the_root_and_every_descendant(session, samples):
    first = run_pipeline(session, sub(samples, bank_account_name="Orion Components Holdings LLC"), use_llm=False)
    second = run_pipeline(session, {"tax_id": "12-3456789"}, parent_run_id=first["run_id"], use_llm=False)
    third = run_pipeline(session, {"tax_id": "12-3456789"}, parent_run_id=second["run_id"], use_llm=False)
    expected = {first["run_id"], second["run_id"], third["run_id"]}
    for member in expected:
        assert chain_run_ids(session, member) == expected
    unrelated = run_pipeline(session, sub(samples, doc="w9_unsigned.pdf"), use_llm=False)
    assert unrelated["run_id"] not in chain_run_ids(session, first["run_id"])
    assert chain_run_ids(session, None) == set()


def test_a_run_with_no_document_skips_the_check_without_a_technical_error(session, samples):
    result = run_pipeline(session, sub(samples) | {"documents": []}, use_llm=False)
    assert {f["id"]: f for f in result["findings"]}["check_document_reuse"]["result"] == "skipped"
    assert not result["decision"]["technical_errors"]


def test_reset_demo_clears_stored_documents(session, samples):
    run_pipeline(session, sub(samples), use_llm=False)
    assert session.scalars(select(Document)).all()
    reset_demo(session)
    assert session.scalars(select(Document)).all() == [] and session.scalars(select(Run)).all() == []


def test_a_missing_file_is_not_hashed_and_does_not_crash(session, samples):
    result = run_pipeline(session, sub(samples) | {"documents": [str(samples / "nope.pdf")]}, use_llm=False)
    assert result["error"] is None and session.scalars(select(Document).where(Document.run_id == result["run_id"])).all() == []


def test_file_hash_is_by_content_not_name(samples, tmp_path):
    copy = tmp_path / "renamed.pdf"
    shutil.copy(samples / "w9_valid.pdf", copy)
    assert file_sha256(copy) == file_sha256(samples / "w9_valid.pdf") != file_sha256(samples / "w9_unsigned.pdf")
    assert file_sha256(tmp_path / "missing.pdf") is None
