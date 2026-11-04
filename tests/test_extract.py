"""Extraction tests: no network. A fake client stands in for Gemini."""
import json

import pytest

from helpers import clean_history
from logic.checks import evaluate_submission
from logic.decision import decide
from logic.extract import ExtractionError, build_extracted, extract_document, offline_parse, validate
from scripts.make_sample_pdfs import build_all


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    out = tmp_path_factory.mktemp("samples")
    build_all(out)
    return out


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def generate(self, parts):
        self.calls.append(parts)
        if self.error:
            raise self.error
        return self.response


GOOD = {"legible": True, "document_type": "w9", "legal_name": "Orion Components LLC",
        "tax_id": "12-3456789", "signed": True, "signed_date": "2026-10-28", "entity_names": []}


def test_text_pdf_sends_text_not_images(samples):
    client = FakeClient(GOOD)
    result = extract_document(samples / "w9_valid.pdf", client)
    assert result["status"] == "ok" and result["source"] == "gemini-text"
    assert len(client.calls[0]) == 1 and "Form W-9" in client.calls[0][0]["text"]


def test_scanned_pdf_sends_page_images(samples):
    client = FakeClient(GOOD)
    result = extract_document(samples / "w9_scanned.pdf", client)
    assert result["source"] == "gemini-vision"
    assert client.calls[0][1]["inline_data"]["mime_type"] == "image/png"


def test_illegible_document_is_unreadable_not_error(samples):
    client = FakeClient({**GOOD, "legible": False})
    assert extract_document(samples / "w9_valid.pdf", client)["status"] == "unreadable"


@pytest.mark.parametrize("bad", [
    {"legible": "yes", "document_type": "w9"},
    {"legible": True, "document_type": "passport"},
    {**GOOD, "signed": "true"},
    {**GOOD, "tax_id": 123456789},
    {**GOOD, "entity_names": "Orion"},
    ["not", "an", "object"],
])
def test_malformed_model_output_is_rejected(bad):
    with pytest.raises(ExtractionError):
        validate(bad)


def test_api_failure_on_text_pdf_falls_back_and_says_so(samples):
    result = extract_document(samples / "w9_valid.pdf", FakeClient(error=ExtractionError("HTTP 429")))
    assert result["status"] == "ok" and result["source"] == "offline_parser"
    assert "HTTP 429" in result["detail"]
    assert result["tax_id"] == "12-3456789" and result["signed"] is True


def test_api_failure_on_scan_is_an_error_never_a_pass(samples):
    result = extract_document(samples / "w9_scanned.pdf", FakeClient(error=ExtractionError("HTTP 503")))
    assert result["status"] == "error"


def test_no_client_scan_is_an_error(samples):
    assert extract_document(samples / "w9_scanned.pdf", use_llm=False)["status"] == "error"


def test_missing_and_corrupt_files(tmp_path):
    assert extract_document(tmp_path / "nope.pdf", use_llm=False)["status"] == "error"
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"this is not a pdf")
    assert extract_document(bad, use_llm=False)["status"] == "unreadable"


def test_offline_parser_on_every_sample(samples):
    def parse(name):
        return extract_document(samples / name, use_llm=False)

    valid = parse("w9_valid.pdf")
    assert (valid["document_type"], valid["legal_name"], valid["tax_id"], valid["signed"], valid["signed_date"]) == (
        "w9", "Orion Components LLC", "12-3456789", True, "2026-10-28")
    unsigned = parse("w9_unsigned.pdf")
    assert unsigned["signed"] is False and unsigned["signed_date"] is None
    assert parse("w9_tax_mismatch.pdf")["tax_id"] == "12-3456780"
    assert parse("utility_bill.pdf")["document_type"] == "utility_bill"
    proof = parse("parent_entity_proof.pdf")
    assert proof["document_type"] == "dba_or_parent_proof"
    assert proof["entity_names"] == ["Orion Components LLC", "Orion Components Holdings LLC"]


def test_offline_parse_unknown_text():
    assert offline_parse("Just some unrelated letter text about nothing in particular.")["document_type"] == "other"


SUBMISSION = {"company_name": "Orion Components LLC", "country": "United States", "tax_id": "12-3456789",
              "bank_account_name": "Orion Components LLC", "bank_account_num": "000555666777",
              "documents": ["w9_valid.pdf"]}
VENDORS = [{"id": 1, "legal_name": "Northwind Traders Inc", "tax_id": "98-7654321",
            "bank_account": "000111222333", "status": "active"}]


def decide_for(samples, primary, supporting=(), submission=SUBMISSION):
    extracted = build_extracted(samples / primary, [samples / s for s in supporting], use_llm=False)
    return decide(evaluate_submission(submission, extracted, VENDORS, history=clean_history(submission))), extracted


def test_end_to_end_happy_path_from_real_pdf(samples):
    decision, extracted = decide_for(samples, "w9_valid.pdf")
    assert decision["status"] == "APPROVED"
    assert extracted["sources"] == ["w9_valid.pdf: offline_parser"]


def test_end_to_end_unsigned_w9(samples):
    decision, _ = decide_for(samples, "w9_unsigned.pdf")
    assert decision["status"] == "PENDING" and "check_signature_and_date" in decision["unresolved"]


def test_end_to_end_tax_id_mismatch(samples):
    decision, _ = decide_for(samples, "w9_tax_mismatch.pdf")
    assert decision["unresolved"] == ["check_form_document_tax_id"]


def test_end_to_end_wrong_document(samples):
    decision, _ = decide_for(samples, "utility_bill.pdf")
    assert decision["status"] == "PENDING" and "check_document_type" in decision["unresolved"]


def test_end_to_end_scan_without_vision_is_pending_with_error(samples):
    decision, _ = decide_for(samples, "w9_scanned.pdf")
    assert decision["status"] == "PENDING" and decision["technical_errors"]


def test_end_to_end_dba_proof_from_real_pdfs(samples):
    submission = {**SUBMISSION, "bank_account_name": "Orion Components Holdings LLC"}
    pending, _ = decide_for(samples, "w9_valid.pdf", submission=submission)
    assert pending["status"] == "PENDING" and pending["unresolved"] == ["check_company_bank_name"]
    approved, extracted = decide_for(samples, "w9_valid.pdf", ["parent_entity_proof.pdf"], submission)
    assert approved["status"] == "APPROVED"
    assert len(extracted["supporting_documents"]) == 1


def test_non_proof_supporting_documents_are_ignored(samples):
    extracted = build_extracted(samples / "w9_valid.pdf", [samples / "utility_bill.pdf"], use_llm=False)
    assert extracted["supporting_documents"] == []


def test_extracted_dict_is_json_serialisable(samples):
    json.dumps(build_extracted(samples / "w9_valid.pdf", use_llm=False))
