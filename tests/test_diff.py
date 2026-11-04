"""Resubmission diff: findings matched by check id between a run and its parent."""
from stubs import StubModel
import pytest

from db.models import make_session_factory
from db.seed import seed_vendors
from pipeline.runner import run_pipeline
from scripts.make_sample_pdfs import build_all
from ui.data import compare_findings, run_diff


def f(check_id, result, evidence=""):
    return {"id": check_id, "result": result, "evidence": evidence or f"{check_id} {result}"}


def ids(rows):
    return [r["id"] for r in rows]


def test_a_failed_check_that_passes_is_resolved():
    diff = compare_findings([f("a", "fail")], [f("a", "pass")])
    assert ids(diff["resolved"]) == ["a"] and not diff["new"] and not diff["still_open"]


def test_a_skipped_check_that_later_passes_counts_as_resolved():
    diff = compare_findings([f("a", "skipped")], [f("a", "pass")])
    assert ids(diff["resolved"]) == ["a"]
    assert diff["resolved"][0]["before"] == "skipped" and diff["resolved"][0]["after"] == "pass"


def test_a_pass_that_becomes_a_problem_is_new():
    diff = compare_findings([f("a", "pass")], [f("a", "fail")])
    assert ids(diff["new"]) == ["a"] and not diff["resolved"]


def test_a_pass_that_can_no_longer_run_is_new_not_unchanged():
    assert ids(compare_findings([f("a", "pass")], [f("a", "skipped")])["new"]) == ["a"]


def test_a_skipped_check_that_now_finds_a_problem_is_new():
    assert ids(compare_findings([f("a", "skipped")], [f("a", "fail")])["new"]) == ["a"]


@pytest.mark.parametrize("before,after", [("fail", "fail"), ("fail", "warn"), ("warn", "error"), ("fail", "block"),
                                          ("skipped", "skipped"), ("fail", "skipped")])
def test_problems_that_remain_are_still_open(before, after):
    diff = compare_findings([f("a", before)], [f("a", after)])
    assert ids(diff["still_open"]) == ["a"] and not diff["resolved"] and not diff["new"]
    assert (diff["still_open"][0]["before"], diff["still_open"][0]["after"]) == (before, after)


def test_a_check_that_exists_only_in_the_child_is_new_if_it_flags_and_ignored_if_it_passes():
    diff = compare_findings([f("a", "pass")], [f("a", "pass"), f("b", "fail"), f("c", "pass")])
    assert ids(diff["new"]) == ["b"] and diff["unchanged_pass"] == 1


def test_a_check_that_only_the_parent_had_is_resolved_if_it_was_open():
    diff = compare_findings([f("a", "fail"), f("b", "pass")], [])
    assert ids(diff["resolved"]) == ["a"] and diff["unchanged_pass"] == 0


def test_unchanged_passes_are_counted_not_listed():
    diff = compare_findings([f("a", "pass"), f("b", "pass")], [f("a", "pass"), f("b", "pass")])
    assert diff["unchanged_pass"] == 2 and not (diff["resolved"] or diff["new"] or diff["still_open"])


def test_diff_uses_the_latest_evidence_and_readable_check_names():
    diff = compare_findings([f("check_company_bank_name", "warn", "old")], [f("check_company_bank_name", "pass", "new")])
    assert diff["resolved"][0]["check"] == "Company vs bank holder name" and diff["resolved"][0]["evidence"] == "new"


# --- through the pipeline ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    out = tmp_path_factory.mktemp("diff_samples")
    build_all(out)
    return out


@pytest.fixture
def session(tmp_path):
    with make_session_factory(f"sqlite:///{tmp_path / 'd.db'}")() as s:
        seed_vendors(s)
        yield s


def sub(samples, **overrides):
    base = {"company_name": "Orion Components LLC", "country": "United States", "tax_id": "12-3456789",
            "bank_account_name": "Orion Components Holdings LLC", "bank_account_num": "000555666777",
            "documents": [str(samples / "w9_valid.pdf")]}
    return {**base, **overrides}


def test_dba_resubmission_diff_end_to_end(session, samples):
    first = run_pipeline(session, sub(samples), client=StubModel(), use_llm=True)
    second = run_pipeline(session, {"supporting_documents": [str(samples / "parent_entity_proof.pdf")]},
                          parent_run_id=first["run_id"], client=StubModel(), use_llm=True)
    diff = run_diff(session, second["run_id"])
    assert (diff["parent_status"], diff["status"], diff["parent_run_id"]) == ("PENDING", "APPROVED", first["run_id"])
    assert ids(diff["resolved"]) == ["check_company_bank_name"]
    assert diff["new"] == [] and diff["still_open"] == []  # the proof's own grounding check is new but passes


def test_a_resubmission_that_breaks_something_shows_it_as_new(session, samples):
    first = run_pipeline(session, sub(samples), client=StubModel(), use_llm=True)
    second = run_pipeline(session, {"tax_id": "12-3456780", "supporting_documents": [str(samples / "parent_entity_proof.pdf")]},
                          parent_run_id=first["run_id"], client=StubModel(), use_llm=True)
    diff = run_diff(session, second["run_id"])
    assert "check_form_document_tax_id" in ids(diff["new"]) and "check_company_bank_name" in ids(diff["resolved"])
    assert diff["status"] == "PENDING"


def test_original_runs_have_no_diff(session, samples):
    assert run_diff(session, run_pipeline(session, sub(samples), client=StubModel(), use_llm=True)["run_id"]) is None
    assert run_diff(session, "run-nope") is None
