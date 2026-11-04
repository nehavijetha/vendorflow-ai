import pytest

from db.models import Finding, Run, make_session_factory
from db.reviews import add_review, latest_review, run_outcome
from logic.checks import evaluate_submission
from logic.decision import decide
from logic.names import entity_type_conflict, name_similarity, split_name
from logic.review import ReviewError, effective_outcome


def by_id(submission, extracted, vendors):
    return {f["id"]: f for f in evaluate_submission(submission, extracted, vendors)}


# --- gap 1: legal-form handling -------------------------------------------------------

def test_split_name_only_strips_trailing_legal_forms():
    assert split_name("Limited Brands Inc", "US") == ("limited brands", ["inc"])
    assert split_name("Limited", "US") == ("limited", [])
    assert split_name("Acme Co Ltd", "UK") == ("acme co", ["ltd"])


def test_missing_suffix_on_bank_account_name_is_a_pass(submission, extracted, vendors):
    submission["bank_account_name"] = "Orion Components"
    assert by_id(submission, extracted, vendors)["check_company_bank_name"]["result"] == "pass"


def test_different_entity_type_is_flagged_separately(submission, extracted, vendors):
    submission["bank_account_name"] = "Orion Components Inc"
    finding = by_id(submission, extracted, vendors)["check_company_bank_name"]
    assert entity_type_conflict("Orion Components LLC", "Orion Components Inc", "US")
    assert finding["result"] == "warn"
    assert "entity type" in finding["vendor_request"]


def test_entity_type_conflict_cleared_by_proof(submission, extracted, vendors):
    submission["bank_account_name"] = "Orion Components Inc"
    extracted["supporting_documents"] = [{
        "document_type": "dba_or_parent_proof",
        "entity_names": ["Orion Components LLC", "Orion Components Inc"],
    }]
    assert by_id(submission, extracted, vendors)["check_company_bank_name"]["result"] == "pass"


def test_empty_names_never_match():
    assert name_similarity("", "", "US") == 0.0


# --- gap 2: document legal name vs form company name ----------------------------------

def test_document_name_matches(submission, extracted, vendors):
    assert by_id(submission, extracted, vendors)["check_form_document_name"]["result"] == "pass"


def test_w9_for_a_different_company_is_not_accepted(submission, extracted, vendors):
    """Right EIN, wrong entity: the tax-ID check alone would have passed this."""
    extracted["legal_name"] = "Zenith Logistics Group"
    found = by_id(submission, extracted, vendors)
    assert found["check_form_document_tax_id"]["result"] == "pass"
    assert found["check_form_document_name"]["result"] == "fail"
    assert found["check_form_document_name"]["human_review"]
    assert decide(list(found.values()))["status"] == "PENDING"


def test_trade_name_on_form_with_legal_name_on_w9_needs_proof(submission, extracted, vendors):
    submission["company_name"] = "Orion Components Holdings LLC"
    submission["bank_account_name"] = "Orion Components Holdings LLC"
    assert by_id(submission, extracted, vendors)["check_form_document_name"]["result"] == "warn"
    extracted["supporting_documents"] = [{
        "document_type": "dba_or_parent_proof",
        "entity_names": ["Orion Components LLC", "Orion Components Holdings LLC"],
    }]
    assert by_id(submission, extracted, vendors)["check_form_document_name"]["result"] == "pass"


def test_document_name_not_assessed_for_wrong_document(submission, extracted, vendors):
    extracted["document_type"] = "utility_bill"
    finding = by_id(submission, extracted, vendors)["check_form_document_name"]
    assert finding["result"] == "skipped" and finding["vendor_request"] is None


def test_document_without_a_readable_name(submission, extracted, vendors):
    extracted["legal_name"] = None
    assert by_id(submission, extracted, vendors)["check_form_document_name"]["result"] == "fail"


# --- gap 3: reviewer actions ----------------------------------------------------------

RUN_INPUT = (
    '{"company_name": "Orion Components LLC", "country": "United States", "tax_id": "12-3456789", '
    '"bank_account_name": "Orion Components LLC", "bank_account_num": "000555666777"}'
)


@pytest.fixture
def session(tmp_path):
    with make_session_factory(f"sqlite:///{tmp_path / 'r.db'}")() as s:
        s.add_all([
            Run(id="pending", input_json=RUN_INPUT, status="PENDING"),
            Run(id="approved", input_json=RUN_INPUT, status="APPROVED"),
            Run(id="rejected", input_json=RUN_INPUT, status="REJECTED"),
            Run(id="errored", input_json=RUN_INPUT, status="PENDING"),
            Finding(run_id="errored", check_id="check_document_type", result="error"),
        ])
        s.commit()
        yield s


def test_reviewer_approval_sits_beside_the_system_result(session):
    add_review(session, "pending", "Priya", "approve", "Called vendor; DBA confirmed by phone.")
    assert session.get(Run, "pending").status == "PENDING"  # system result untouched
    assert run_outcome(session, "pending") == {"status": "APPROVED", "source": "reviewer"}


def test_latest_review_wins(session):
    add_review(session, "pending", "Priya", "approve", "Looks fine.")
    add_review(session, "pending", "Sam", "reject", "Bank letter was forged.")
    assert latest_review(session, "pending")["reviewer"] == "Sam"
    assert run_outcome(session, "pending")["status"] == "REJECTED"


def test_request_info_keeps_run_pending(session):
    add_review(session, "pending", "Priya", "request_info", "Need a bank letter.")
    assert run_outcome(session, "pending") == {"status": "PENDING", "source": "system"}


@pytest.mark.parametrize("run_id", ["approved", "rejected"])
def test_only_pending_runs_can_be_reviewed(session, run_id):
    with pytest.raises(ReviewError):
        add_review(session, run_id, "Priya", "approve", "x")


def test_cannot_approve_over_a_technical_error_but_can_reject(session):
    with pytest.raises(ReviewError, match="technical error"):
        add_review(session, "errored", "Priya", "approve", "Looks fine.")
    add_review(session, "errored", "Priya", "reject", "Not onboarding without verification.")


@pytest.mark.parametrize("decision,reviewer,note", [
    ("approve", "", "note"), ("approve", "Priya", "  "), ("maybe", "Priya", "note"),
])
def test_review_requires_name_note_and_known_decision(session, decision, reviewer, note):
    with pytest.raises(ReviewError):
        add_review(session, "pending", reviewer, decision, note)


def test_unknown_run(session):
    with pytest.raises(ReviewError, match="Unknown run"):
        add_review(session, "nope", "Priya", "approve", "x")


def test_effective_outcome_defaults_to_system():
    assert effective_outcome("APPROVED") == {"status": "APPROVED", "source": "system"}
    assert effective_outcome("REJECTED", {"decision": "approve"})["status"] == "REJECTED"
