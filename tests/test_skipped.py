"""SKIPPED: a check that cannot run is neither a pass nor a new problem."""
import pytest

from helpers import clean_history
from logic.checks import CHECKS, evaluate_submission, make_finding
from logic.decision import decide, determine_status
from pipeline.runner import _summary


def by_id(findings):
    return {f["id"]: f for f in findings}


def run(submission, extracted, vendors):
    findings = evaluate_submission(submission, extracted, vendors, history=clean_history(submission))
    return by_id(findings), decide(findings)


# --- the safety rule ------------------------------------------------------------------------------

def test_a_run_is_never_approved_while_any_check_is_skipped():
    everything_else_passes = [{"id": f"c{i}", "result": "pass"} for i in range(8)]
    assert determine_status(everything_else_passes) == "APPROVED"
    assert determine_status([*everything_else_passes, {"id": "s", "result": "skipped"}]) == "PENDING"


def test_a_skip_with_no_explained_cause_goes_to_a_person():
    findings = [make_finding("a", "pass", "ok"), make_finding("b", "skipped", "Skipped: nothing")]
    decision = decide(findings)
    assert decision["status"] == "PENDING" and decision["requires_human_review"]
    assert decision["unresolved"] == [] and decision["skipped"] == ["b"]


def test_a_skip_next_to_an_explaining_failure_does_not_force_a_review():
    findings = [make_finding("a", "fail", "missing", "Please upload it"), make_finding("b", "skipped", "Skipped")]
    decision = decide(findings)
    assert decision["unresolved"] == ["a"] and decision["skipped"] == ["b"] and not decision["requires_human_review"]


def test_block_still_wins_over_skips():
    findings = [make_finding("a", "block", "blocked"), make_finding("b", "skipped", "Skipped")]
    assert determine_status(findings) == "REJECTED"
    assert not decide(findings)["requires_human_review"]  # a rejection never waits for a review


def test_skipped_is_never_counted_as_attention_or_a_technical_error():
    decision = decide([make_finding("b", "skipped", "Skipped")])
    assert decision["unresolved"] == [] and decision["technical_errors"] == []


# --- where checks skip --------------------------------------------------------------------------------

def test_wrong_document_asks_once_and_skips_the_checks_that_depend_on_it(submission, extracted, vendors):
    extracted["document_type"] = "utility_bill"
    found, decision = run(submission, extracted, vendors)
    assert found["check_document_type"]["result"] == "fail"
    assert found["check_signature_and_date"]["result"] == "skipped"
    assert found["check_form_document_name"]["result"] == "skipped"
    assert decision["unresolved"] == ["check_document_type"]
    assert len(decision["vendor_requests"]) == 1


def test_missing_document_is_one_failure_and_the_rest_skip(submission, vendors):
    submission["documents"] = []
    found, decision = run(submission, {"status": "missing"}, vendors)
    assert decision["unresolved"] == ["check_required_fields"]
    assert set(decision["skipped"]) == {"check_document_type", "check_signature_and_date",
                                       "check_form_document_tax_id", "check_form_document_name",
                                       "check_document_reuse", "check_document_authenticity"}
    assert decision["status"] == "PENDING" and not decision["technical_errors"]


def test_unreadable_document_is_requested_once_by_the_document_type_check(submission, vendors):
    found, decision = run(submission, {"status": "unreadable"}, vendors)
    assert found["check_document_type"]["result"] == "fail" and found["check_document_type"]["vendor_request"]
    for check in ("check_signature_and_date", "check_form_document_tax_id", "check_form_document_name"):
        assert found[check]["result"] == "skipped" and found[check]["vendor_request"] is None
    assert decision["unresolved"] == ["check_document_type"]


def test_a_technical_extraction_error_is_still_an_error_not_a_skip(submission, vendors):
    found, decision = run(submission, {"status": "error"}, vendors)
    assert found["check_document_type"]["result"] == "error"
    assert decision["technical_errors"] and decision["requires_human_review"]


def test_missing_inputs_skip_the_duplicate_checks(submission, extracted, vendors):
    submission.update(tax_id="", bank_account_num="")
    found, decision = run(submission, extracted, vendors)
    assert found["check_duplicate_tax_id"]["result"] == "skipped"
    assert found["check_duplicate_bank_account"]["result"] == "skipped"
    assert "check_required_fields" in decision["unresolved"]


def test_every_check_can_skip_without_crashing_on_an_empty_submission(vendors):
    findings = evaluate_submission({}, None, vendors)
    assert len(findings) == len(CHECKS) - 1  # the authenticity check does not apply to an unknown country
    assert all(f["result"] in ("pass", "skipped", "fail", "error", "warn", "block") for f in findings)
    assert determine_status(findings) == "PENDING"


# --- reporting ----------------------------------------------------------------------------------------

def test_summary_reports_skips_separately():
    findings = [make_finding("a", "pass", ""), make_finding("b", "fail", ""), make_finding("c", "skipped", "")]
    assert _summary(findings) == "1 passed, 1 need attention, 1 skipped"
    assert _summary([make_finding("a", "pass", "")]) == "1 passed, 0 need attention"


@pytest.mark.parametrize("result", ["pass", "warn", "fail", "block", "error", "skipped"])
def test_make_finding_keeps_every_result_value(result):
    assert make_finding("x", result, "e")["result"] == result
