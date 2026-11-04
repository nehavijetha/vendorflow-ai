"""End-to-end verification + decision scenarios (no LLM involved)."""
from helpers import clean_history
from logic import checks
from logic.checks import evaluate_submission
from logic.decision import decide, determine_status

PROOF = {
    "document_type": "dba_or_parent_proof",
    "entity_names": ["Orion Components LLC", "Orion Components Holdings LLC"],
}


def run(submission, extracted, vendors):
    findings = evaluate_submission(submission, extracted, vendors, history=clean_history(submission))
    return {f["id"]: f for f in findings}, decide(findings)


def test_happy_path(submission, extracted, vendors):
    by_id, decision = run(submission, extracted, vendors)
    assert len(by_id) == 11
    assert all(f["result"] == "pass" for f in by_id.values())
    assert decision["status"] == "APPROVED"
    assert decision["vendor_requests"] == []


def test_dba_mismatch_is_pending_and_asks_for_proof(submission, extracted, vendors):
    submission["bank_account_name"] = "Orion Components Holdings LLC"
    by_id, decision = run(submission, extracted, vendors)
    assert by_id["check_company_bank_name"]["result"] == "warn"
    assert decision["status"] == "PENDING"
    assert any("DBA" in r for r in decision["vendor_requests"])


def test_dba_proof_resolves_name_mismatch(submission, extracted, vendors):
    submission["bank_account_name"] = "Orion Components Holdings LLC"
    extracted["supporting_documents"] = [PROOF]
    by_id, decision = run(submission, extracted, vendors)
    assert by_id["check_company_bank_name"]["result"] == "pass"
    assert "parent-entity proof" in by_id["check_company_bank_name"]["evidence"]
    assert decision["status"] == "APPROVED"


def test_proof_must_name_both_entities(submission, extracted, vendors):
    submission["bank_account_name"] = "Orion Components Holdings LLC"
    extracted["supporting_documents"] = [{**PROOF, "entity_names": ["Orion Components LLC", "Someone Else Inc"]}]
    by_id, _ = run(submission, extracted, vendors)
    assert by_id["check_company_bank_name"]["result"] == "warn"


def test_very_different_names_go_to_manual_review_not_rejection(submission, extracted, vendors):
    submission["bank_account_name"] = "Zenith Logistics Group"
    by_id, decision = run(submission, extracted, vendors)
    assert by_id["check_company_bank_name"]["result"] == "fail"
    assert decision["status"] == "PENDING"
    assert decision["requires_human_review"]


def test_tax_id_mismatch_is_pending_not_rejected(submission, extracted, vendors):
    extracted["tax_id"] = "12-3456780"
    by_id, decision = run(submission, extracted, vendors)
    assert by_id["check_form_document_tax_id"]["result"] == "fail"
    assert decision["status"] == "PENDING"


def test_duplicate_bank_account_other_vendor_is_pending_without_leaking(submission, extracted, vendors):
    submission["bank_account_num"] = "000111222333"  # Northwind Traders
    by_id, decision = run(submission, extracted, vendors)
    assert by_id["check_duplicate_bank_account"]["result"] == "fail"
    assert decision["status"] == "PENDING"
    assert decision["requires_human_review"]
    assert not any("Northwind" in r for r in decision["vendor_requests"])


def test_bank_account_of_same_vendor_is_not_flagged(submission, extracted, vendors):
    submission.update(tax_id="98-7654321", company_name="Northwind Traders Inc",
                      bank_account_name="Northwind Traders Inc", bank_account_num="000111222333")
    extracted["tax_id"] = "98-7654321"
    by_id, _ = run(submission, extracted, vendors)
    assert by_id["check_duplicate_bank_account"]["result"] == "pass"


def test_wrong_document(submission, extracted, vendors):
    extracted["document_type"] = "utility_bill"
    by_id, decision = run(submission, extracted, vendors)
    assert by_id["check_document_type"]["result"] == "fail"
    assert decision["status"] == "PENDING"
    assert any("W-9" in r for r in decision["vendor_requests"])


def test_unsigned_document(submission, extracted, vendors):
    extracted.update(signed=False, signed_date=None)
    by_id, decision = run(submission, extracted, vendors)
    assert by_id["check_signature_and_date"]["result"] == "fail"
    assert by_id["check_signature_and_date"]["evidence"] == "Document is missing: signature, date."
    assert decision["status"] == "PENDING"


def test_tax_id_format_edge_inputs(submission, extracted, vendors):
    def fmt(country, tax_id):
        return checks.check_tax_id_format({"country": country, "tax_id": tax_id}, None, vendors)["result"]

    assert fmt("UK", "gb123456789") == "pass"  # input case is forgiven
    assert fmt("United States", "١٢-٣٤٥٦٧٨٩") == "fail"  # non-ASCII digits
    assert fmt("United States", "12-3456789\n") == "pass"  # surrounding whitespace is stripped
    assert fmt("United States", "12-3456789-0") == "fail"
    assert fmt("CA", "123456789RT0001") == "pass"
    assert fmt("CA", "123456789RT1") == "fail"


def test_missing_field_request_uses_readable_labels(submission, extracted, vendors):
    submission["bank_account_num"] = ""
    by_id, _ = run(submission, extracted, vendors)
    request = by_id["check_required_fields"]["vendor_request"]
    assert "bank account number" in request and "bank_account_num" not in request


def test_unreadable_document_asks_for_clear_copy(submission, vendors):
    by_id, decision = run(submission, {"status": "unreadable"}, vendors)
    assert decision["status"] == "PENDING"
    assert any("clear copy" in r for r in decision["vendor_requests"])
    assert not decision["technical_errors"]


def test_extraction_error_is_never_a_pass(submission, vendors):
    by_id, decision = run(submission, {"status": "error"}, vendors)
    assert by_id["check_document_type"]["result"] == "error"
    assert decision["status"] == "PENDING"
    assert decision["technical_errors"]


def test_missing_extraction_is_an_error_not_a_pass(submission, vendors):
    _, decision = run(submission, None, vendors)
    assert decision["status"] == "PENDING"
    assert decision["technical_errors"]


def test_duplicate_tax_id_same_company_is_pending(submission, extracted, vendors):
    submission.update(tax_id="98-7654321", company_name="Northwind Traders Incorporated",
                      bank_account_name="Northwind Traders Incorporated")
    extracted["tax_id"] = "98-7654321"
    by_id, decision = run(submission, extracted, vendors)
    assert by_id["check_duplicate_tax_id"]["result"] == "warn"
    assert decision["status"] == "PENDING"


def test_duplicate_tax_id_different_company_needs_review(submission, extracted, vendors):
    submission["tax_id"] = "98-7654321"
    extracted["tax_id"] = "98-7654321"
    by_id, decision = run(submission, extracted, vendors)
    assert by_id["check_duplicate_tax_id"]["result"] == "fail"
    assert decision["status"] == "PENDING"
    assert decision["requires_human_review"]


def test_blocked_vendor_tax_id_is_rejected(submission, extracted, vendors):
    submission["tax_id"] = "55-5555555"
    extracted["tax_id"] = "55-5555555"
    by_id, decision = run(submission, extracted, vendors)
    assert by_id["check_duplicate_tax_id"]["result"] == "block"
    assert decision["status"] == "REJECTED"
    assert decision["blocking"] == ["check_duplicate_tax_id"]


def test_blocked_vendor_bank_account_is_rejected(submission, extracted, vendors):
    submission["bank_account_num"] = "9990 0011-1222"
    _, decision = run(submission, extracted, vendors)
    assert decision["status"] == "REJECTED"


def test_three_independent_problems_yield_three_findings(submission, extracted, vendors):
    """Acceptance test: the engine must not stop at the first failure."""
    submission["tax_id"] = "123456789"  # wrong US format
    submission["bank_account_name"] = "Orion Components Holdings LLC"  # partial name match
    extracted.update(signed=False, signed_date=None)  # unsigned
    by_id, decision = run(submission, extracted, vendors)
    assert len(by_id) == 11
    problems = {k for k, f in by_id.items() if f["result"] != "pass"}
    assert {"check_tax_id_format", "check_company_bank_name", "check_signature_and_date"} <= problems
    assert decision["status"] == "PENDING"
    assert len(decision["vendor_requests"]) >= 3


def test_missing_fields_all_listed(submission, extracted, vendors):
    submission.update(company_name="", bank_account_num="  ", documents=[])
    by_id, decision = run(submission, extracted, vendors)
    evidence = by_id["check_required_fields"]["evidence"]
    assert all(name in evidence for name in ("company_name", "bank_account_num", "tax_document"))
    assert decision["status"] == "PENDING"


def test_unknown_country_goes_to_manual_review(submission, extracted, vendors):
    submission["country"] = "Germany"
    by_id, decision = run(submission, extracted, vendors)
    assert by_id["check_tax_id_format"]["result"] == "fail"
    assert decision["status"] == "PENDING"
    assert decision["requires_human_review"]


def test_uk_and_canada_happy_paths(vendors):
    uk = {"company_name": "Delta Parts Ltd", "country": "UK", "tax_id": "GB987654353",
          "bank_account_name": "Delta Parts Limited", "bank_account_num": "111222333444", "documents": ["x.pdf"]}
    uk_doc = {"status": "ok", "document_type": "vat_certificate", "tax_id": "GB987654353", "legal_name": "Delta Parts Limited", "signed_date": "2026-01-05"}
    assert run(uk, uk_doc, vendors)[1]["status"] == "APPROVED"
    ca = {"company_name": "Tremblay Équipement Ltée", "country": "Canada", "tax_id": "987654324RT0001", "legal_name": "Tremblay Equipement Ltd",
          "bank_account_name": "Tremblay Equipement Ltd", "bank_account_num": "222333444555", "documents": ["x.pdf"]}
    ca_doc = {"status": "ok", "document_type": "cra_business_number_letter", "tax_id": "987654324RT0001", "legal_name": "Tremblay Equipement Ltd",
              "signed_date": "2026-02-10"}
    assert run(ca, ca_doc, vendors)[1]["status"] == "APPROVED"


def test_crashing_check_becomes_error_and_others_still_run(submission, extracted, vendors):
    def check_boom(*_):
        raise RuntimeError("db down")

    findings = evaluate_submission(submission, extracted, vendors, checks=[check_boom, checks.check_tax_id_format])
    assert findings[0]["result"] == "error" and findings[0]["id"] == "check_boom"
    assert findings[1]["result"] == "pass"
    assert determine_status(findings) == "PENDING"


def test_on_finding_callback_receives_each_finding(submission, extracted, vendors):
    seen = []
    evaluate_submission(submission, extracted, vendors, on_finding=seen.append, history=clean_history(submission))
    assert len(seen) == 11


def test_decision_engine_edge_inputs():
    assert determine_status([]) == "PENDING"
    assert determine_status([{"result": "pass"}, {"result": "surprise"}]) == "PENDING"
    assert determine_status([{"result": "fail"}, {"result": "block"}]) == "REJECTED"
