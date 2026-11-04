"""Typed contracts at the boundaries: bad data must fail where it enters."""
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from db.models import Run, make_session_factory
from db.seed import seed_vendors
from logic.checks import evaluate_submission, make_finding
from logic.decision import decide
from logic.extract import DOC_TYPES, ExtractionError, build_extracted, validate
from logic.models import DocType, Extraction, Finding, ModelOutput, Submission
from pipeline.runner import PipelineError, run_pipeline
from scripts.make_sample_pdfs import build_all
from typing import get_args

GOOD = {"legible": True, "document_type": "w9", "legal_name": "Orion Components LLC", "tax_id": "12-3456789",
        "signed": True, "signed_date": "2026-10-28", "entity_names": []}


# --- Finding ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    {"id": "c", "result": "pas", "evidence": ""}, {"id": "", "result": "pass", "evidence": ""},
    {"id": "c", "result": "pass", "evidence": "", "surprise": 1}, {"id": "c", "result": "pass", "evidence": "",
                                                                    "human_review": "yes"},
    {"id": "c", "result": "PASS", "evidence": ""},
])
def test_finding_rejects_invented_values(bad):
    with pytest.raises(ValidationError):
        Finding(**bad)


def test_finding_round_trips_through_a_plain_dict():
    finding = make_finding("c", "skipped", "why", None, False)
    assert finding == {"id": "c", "result": "skipped", "evidence": "why", "vendor_request": None, "human_review": False}


def test_a_check_returning_an_invented_result_becomes_a_technical_error_not_a_pass(submission, extracted, vendors):
    def check_typo(*_):
        return make_finding("check_typo", "paas", "looks fine")

    findings = evaluate_submission(submission, extracted, vendors, checks=[check_typo])
    assert findings[0]["result"] == "error" and "paas" in findings[0]["evidence"]
    assert decide(findings)["status"] == "PENDING"


# --- Submission -------------------------------------------------------------------------------------

def test_submission_defaults_and_coercions(tmp_path):
    sub = Submission(company_name=None, tax_id=123456789, documents=[tmp_path / "a.pdf"], bank_account_num=42.0)
    assert sub.company_name == "" and sub.tax_id == "123456789" and sub.bank_account_num == "42.0"
    assert sub.documents == [str(tmp_path / "a.pdf")] and sub.supporting_documents == []


def test_submission_keeps_unknown_keys_and_tracks_what_was_provided():
    sub = Submission(company_name="Acme", declaration="none")
    assert sub.model_dump()["declaration"] == "none"
    assert sub.model_dump(exclude_unset=True) == {"company_name": "Acme", "declaration": "none"}


@pytest.mark.parametrize("bad", [{"documents": "a.pdf"}, {"documents": 5}, {"company_name": ["x"]},
                                 {"company_name": True}, {"supporting_documents": {"a": 1}}])
def test_submission_rejects_the_wrong_shape(bad):
    with pytest.raises(ValidationError):
        Submission(**bad)


@pytest.fixture
def session(tmp_path):
    with make_session_factory(f"sqlite:///{tmp_path / 'm.db'}")() as s:
        seed_vendors(s)
        yield s


def test_an_invalid_submission_stops_before_any_run_is_created(session):
    with pytest.raises(PipelineError, match="documents"):
        run_pipeline(session, {"company_name": "Acme", "documents": "w9.pdf"}, use_llm=False)
    assert session.scalars(select(Run)).all() == []


def test_the_pipeline_accepts_a_submission_object_and_numbers_for_ids(session, tmp_path):
    build_all(tmp_path)
    sub = Submission(company_name="Orion Components LLC", country="United States", tax_id="12-3456789",
                     bank_account_name="Orion Components LLC", bank_account_num=555666777,
                     documents=[tmp_path / "w9_valid.pdf"])
    result = run_pipeline(session, sub, use_llm=False)
    assert result["status"] == "APPROVED"
    assert session.get(Run, result["run_id"]).input_json.count('"555666777"') == 1


def test_a_partial_resubmission_does_not_blank_the_fields_it_did_not_send(session, tmp_path):
    build_all(tmp_path)
    first = run_pipeline(session, {"company_name": "Orion Components LLC", "country": "United States",
                                   "tax_id": "12-3456789", "bank_account_name": "Orion Components Holdings LLC",
                                   "bank_account_num": "000555666777", "documents": [str(tmp_path / "w9_valid.pdf")]},
                         use_llm=False)
    second = run_pipeline(session, {"supporting_documents": [str(tmp_path / "parent_entity_proof.pdf")]},
                          parent_run_id=first["run_id"], use_llm=False)
    assert second["status"] == "APPROVED"
    assert second["parent_run_id"] == first["run_id"]


# --- model output -------------------------------------------------------------------------------------

def test_the_enum_of_document_types_matches_the_extraction_prompt_schema():
    assert list(get_args(DocType)) == DOC_TYPES


def test_model_output_is_stripped_and_blank_becomes_none():
    out = ModelOutput(**{**GOOD, "legal_name": "  Orion  ", "tax_id": "   ", "entity_names": [" A ", "", "B"]})
    assert out.legal_name == "Orion" and out.tax_id is None and out.entity_names == ["A", "B"]


@pytest.mark.parametrize("bad,field", [
    ({"legible": "true"}, "legible"), ({"signed": "true"}, "signed"), ({"signed": 1}, "signed"),
    ({"document_type": "passport"}, "document_type"), ({"tax_id": 123456789}, "tax_id"),
    ({"entity_names": "Orion"}, "entity_names"), ({"legal_name": ["x"]}, "legal_name"),
])
def test_model_output_wrong_types_are_rejected_with_the_field_named(bad, field):
    with pytest.raises(ExtractionError, match=field):
        validate({**GOOD, **bad})


def test_model_output_extra_keys_are_ignored_and_missing_optionals_default():
    assert validate({"legible": True, "document_type": "other", "model_notes": "hi"}) == {
        "legible": True, "document_type": "other", "legal_name": None, "tax_id": None, "signed": None,
        "signed_date": None, "entity_names": []}


def test_a_null_entity_list_is_accepted():
    assert validate({**GOOD, "entity_names": None})["entity_names"] == []


# --- extraction result ------------------------------------------------------------------------------------

def test_build_extracted_output_is_a_valid_extraction(tmp_path):
    build_all(tmp_path)
    result = build_extracted(tmp_path / "w9_valid.pdf", [tmp_path / "parent_entity_proof.pdf"], use_llm=False)
    assert Extraction.model_validate(result).status == "ok" and result["supporting_documents"][0]["path"]


def test_extraction_status_is_one_of_four_values():
    assert Extraction(status="missing").model_dump()["supporting_documents"] == []
    with pytest.raises(ValidationError):
        Extraction(status="maybe")
    with pytest.raises(ValidationError):
        Extraction(status="ok", signed="yes")


def test_paths_in_supporting_documents_survive_as_text(tmp_path):
    build_all(tmp_path)
    result = build_extracted(tmp_path / "w9_valid.pdf", [Path(tmp_path / "parent_entity_proof.pdf")], use_llm=False)
    assert isinstance(result["supporting_documents"][0]["path"], str)
