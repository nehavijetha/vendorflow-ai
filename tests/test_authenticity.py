"""Is the document the official IRS form, or only a page that says it is?"""
import base64
import io
import json

import pytest

from db.models import make_session_factory
from db.seed import seed_vendors
from helpers import clean_history
from logic import cache as cache_mod
from logic import w9_reference as w9
from logic.checks import CHECKS, check_document_authenticity, evaluate_submission
from logic.decision import decide
from logic.extract import ExtractionError, GeminiClient, SYSTEM_PROMPT, RESPONSE_SCHEMA, build_extracted, validate
from pipeline.runner import run_pipeline
from scripts.make_sample_pdfs import build_all
from stubs import LAYOUT_ON, LAYOUT_OFF, StubModel

ALL = {name: True for name in w9.FEATURES}
GOOD = {"legible": True, "document_type": "w9", "legal_name": "Orion Components LLC", "tax_id": "12-3456789",
        "signed": True, "signed_date": "2026-10-28", "entity_names": []}


def sig(matched=18, layer=True):
    return {"matched": matched, "total": 18, "missing": [f"phrase {i}" for i in range(18 - matched)], "layer": layer}


# --- the rule ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("signature,structure,expected", [
    (sig(18), ALL, "pass"),
    (sig(15), ALL, "pass"),                                   # the threshold itself
    (sig(14), ALL, "fail"),                                   # one phrase short
    (sig(2), ALL, "fail"),                                    # a typed fake: text decides before the model does
    (sig(0), ALL, "fail"),
    (sig(18), None, "error"),                                 # no model: a text match alone is not enough
    (sig(18), {**ALL, "same_ruled_layout": False}, "fail"),   # the lookalike: right words, no ruled layout
    (sig(18), {**ALL, "tin_boxes": False}, "pass"),           # one clipped feature is tolerated
    (sig(18), {**ALL, "tin_boxes": False, "form_footer": False}, "fail"),
    (sig(0, layer=False), ALL, "pass"),                       # a scan has no text: layout alone decides
    (sig(0, layer=False), None, "error"),
    (sig(0, layer=False), {**ALL, "same_ruled_layout": False}, "fail"),
    (None, ALL, "pass"),                                      # no signature was computed: layout alone
])
def test_the_rule(signature, structure, expected):
    assert w9.judge(signature, structure)[0] == expected


def test_a_failure_always_goes_to_a_person_and_a_pass_never_does():
    assert w9.judge(sig(2), ALL)[2] is True and w9.judge(sig(18), None)[2] is True
    assert w9.judge(sig(18), ALL)[2] is False


def test_the_evidence_names_what_was_found_and_what_is_missing():
    evidence = w9.judge(sig(2), ALL)[1]
    assert "2 of 18" in evidence and "needs 15" in evidence and "phrase 0" in evidence
    assert "11 of 11" in w9.judge(sig(18), ALL)[1]
    assert "same_ruled_layout" in w9.judge(sig(18), {**ALL, "same_ruled_layout": False})[1]
    assert "No text layer" in w9.judge(sig(0, layer=False), ALL)[1]


# --- the check ----------------------------------------------------------------------------------------

def check(submission, extracted):
    return check_document_authenticity(submission, extracted, [], clean_history(submission))


def test_a_genuine_form_passes(submission, extracted):
    assert check(submission, extracted)["result"] == "pass"


def test_a_fake_is_unresolved_and_asks_for_the_official_form(submission, extracted):
    extracted["text_signature"] = sig(2)
    finding = check(submission, extracted)
    assert finding["result"] == "fail" and finding["human_review"]
    assert "official IRS Form W-9" in finding["vendor_request"] and "irs.gov/FormW9" in finding["vendor_request"]


def test_without_the_model_it_is_a_technical_error_not_a_pass(submission, extracted):
    extracted["structure"] = None
    finding = check(submission, extracted)
    assert finding["result"] == "error" and finding["vendor_request"] is None and finding["human_review"]


def test_a_wrong_document_type_is_left_to_the_document_type_check(submission, extracted):
    extracted["document_type"] = "utility_bill"
    finding = check(submission, extracted)
    assert finding["result"] == "skipped" and finding["vendor_request"] is None


@pytest.mark.parametrize("status,result", [("missing", "skipped"), ("unreadable", "skipped"), ("error", "error")])
def test_an_unusable_extraction_is_skipped_or_an_error(submission, status, result):
    assert check(submission, {"status": status})["result"] == result


@pytest.mark.parametrize("country", ["United Kingdom", "Canada"])
def test_it_does_not_apply_where_no_reference_layout_exists(submission, extracted, vendors, country):
    submission["country"] = country
    assert check(submission, extracted) is None
    findings = evaluate_submission(submission, extracted, vendors, history=clean_history(submission))
    assert "check_document_authenticity" not in {f["id"] for f in findings}


def test_it_applies_to_a_us_submission_and_sits_after_the_document_type_check(submission, extracted, vendors):
    findings = evaluate_submission(submission, extracted, vendors, history=clean_history(submission))
    ids = [f["id"] for f in findings]
    assert len(ids) == len(CHECKS)
    assert ids.index("check_document_authenticity") == ids.index("check_document_type") + 1


# --- real documents through the whole pipeline ------------------------------------------------------------

@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    out = tmp_path_factory.mktemp("authenticity_samples")
    build_all(out)
    return out


@pytest.fixture
def session(tmp_path):
    with make_session_factory(f"sqlite:///{tmp_path / 'a.db'}")() as s:
        seed_vendors(s)
        yield s


def sub(samples, doc="w9_valid.pdf", **overrides):
    base = {"company_name": "Orion Components LLC", "country": "United States", "tax_id": "12-3456789",
            "bank_account_name": "Orion Components LLC", "bank_account_num": "000555666777",
            "documents": [str(samples / doc)]}
    return {**base, **overrides}


def go(session, samples, doc, model=None, **overrides):
    return run_pipeline(session, sub(samples, doc, **overrides), client=model or StubModel(), use_llm=True)


def authenticity(result):
    return {f["id"]: f for f in result["findings"]}["check_document_authenticity"]


def test_the_genuine_form_is_approved(session, samples):
    result = go(session, samples, "w9_meridian.pdf", company_name="Meridian Tools LLC", tax_id="20-4417283",
                bank_account_name="Meridian Tools LLC", bank_account_num="000333444555")
    assert result["status"] == "APPROVED"
    assert authenticity(result)["result"] == "pass" and "18 of 18" in authenticity(result)["evidence"]


def test_a_typed_fake_is_pending_even_though_a_keyword_match_would_approve_it(session, samples):
    result = go(session, samples, "spoof_fake_w9.pdf")
    assert result["status"] == "PENDING" and "check_document_authenticity" in result["decision"]["unresolved"]
    assert "official IRS Form W-9" in result["vendor_message"]
    assert authenticity(result)["human_review"]


def test_a_lookalike_with_every_word_passes_the_text_test_and_fails_the_layout(session, samples):
    result = go(session, samples, "spoof_lookalike_w9.pdf", model=StubModel(layout=False))
    finding = authenticity(result)
    assert result["status"] == "PENDING" and finding["result"] == "fail"
    assert "layout does not match" in finding["evidence"] and "0 of 11" in finding["evidence"]
    assert result["extracted"]["text_signature"]["matched"] == 18  # the wording alone would have passed


def test_the_lookalike_defence_is_the_models_layout_observation(session, samples):
    """The honest limit: if the model wrongly reports ruled boxes on a retyped page, the text signature
    still passes and nothing else can object. The live model was measured on this document (see the
    README); this test pins down that the system relies on that observation."""
    result = go(session, samples, "spoof_lookalike_w9.pdf", model=StubModel(layout=True))
    assert authenticity(result)["result"] == "pass"


def test_a_letter_that_only_mentions_a_w9_is_not_one(session, samples):
    result = go(session, samples, "spoof_letter_mentions_w9.pdf")
    assert result["status"] == "PENDING" and authenticity(result)["result"] == "fail"


def test_a_blank_genuine_template_is_a_real_form_with_nothing_on_it(session, samples):
    result = go(session, samples, "w9_blank_template.pdf")
    assert authenticity(result)["result"] == "pass"
    assert result["status"] == "PENDING" and "check_form_document_tax_id" in result["decision"]["unresolved"]


def test_a_scanned_genuine_form_passes_on_layout_alone(session, samples):
    result = go(session, samples, "w9_scanned.pdf", company_name="Cobalt Freight Inc", tax_id="84-2201937",
                bank_account_name="Cobalt Freight Inc", bank_account_num="000666777888")
    assert result["status"] == "APPROVED" and "No text layer" in authenticity(result)["evidence"]


def test_a_scan_the_model_sees_as_plain_text_is_not_accepted(session, samples):
    result = go(session, samples, "w9_scanned.pdf", model=StubModel(layout=False), company_name="Cobalt Freight Inc",
                tax_id="84-2201937", bank_account_name="Cobalt Freight Inc", bank_account_num="000666777888")
    assert authenticity(result)["result"] == "fail" and result["status"] == "PENDING"


def test_when_the_model_is_unavailable_a_genuine_form_is_not_approved(session, samples):
    class Down:
        def generate(self, parts):
            raise ExtractionError("HTTP 503")

        def write(self, system, prompt):
            raise RuntimeError

    result = go(session, samples, "w9_valid.pdf", model=Down())
    assert result["extracted"]["source"] == "offline_parser"
    assert authenticity(result)["result"] == "error" and result["status"] == "PENDING"
    assert result["decision"]["technical_errors"]


def test_with_no_model_at_all_nothing_is_approved(session, samples):
    result = run_pipeline(session, sub(samples), use_llm=False)
    assert result["status"] == "PENDING" and authenticity(result)["result"] == "error"


def test_a_utility_bill_asks_once_for_the_right_document(session, samples):
    result = go(session, samples, "utility_bill.pdf")
    assert authenticity(result)["result"] == "skipped"
    assert "check_document_authenticity" not in result["decision"]["unresolved"]


# --- what the model is shown ----------------------------------------------------------------------------------

def test_the_reference_goes_with_the_primary_w9_and_not_with_supporting_documents(session, samples):
    model = StubModel()
    run_pipeline(session, {**sub(samples, bank_account_name="Orion Components Holdings LLC"),
                           "supporting_documents": [str(samples / "parent_entity_proof.pdf")]}, client=model, use_llm=True)
    primary, supporting = model.requests
    assert primary[0]["text"].startswith("REFERENCE") and len([p for p in primary if "inline_data" in p]) >= 2
    assert base64.b64encode(w9.reference_image_png()).decode() == primary[1]["inline_data"]["data"]
    assert not any(p.get("text", "").startswith("REFERENCE") for p in supporting)


def test_no_reference_is_sent_for_other_countries(session, samples):
    model = StubModel()
    run_pipeline(session, sub(samples, country="United Kingdom"), client=model, use_llm=True)
    assert not any(p.get("text", "").startswith("REFERENCE") for p in model.requests[0])


def test_the_reference_is_in_the_real_request_and_the_form_values_still_are_not(tmp_path, monkeypatch):
    build_all(tmp_path)
    bodies = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        bodies.append(request.data.decode())
        answer = json.dumps({**GOOD, "structure": {**LAYOUT_ON, "revision_text": None}})
        return Resp(json.dumps({"candidates": [{"content": {"parts": [{"text": answer}]}}]}).encode())

    monkeypatch.setattr("logic.extract.urllib.request.urlopen", fake_urlopen)
    form = {"company_name": "Zyxwv Quasar Holdings", "tax_id": "77-7654321", "bank_account_name": "Qwerty Bank Co",
            "bank_account_num": "4242424242"}
    with make_session_factory(f"sqlite:///{tmp_path / 'i.db'}")() as s:
        seed_vendors(s)
        run_pipeline(s, {**form, "country": "United States", "documents": [str(tmp_path / "w9_valid.pdf")]},
                     client=GeminiClient(api_key="k", models=["m"]), use_llm=True)
    assert base64.b64encode(w9.reference_image_png()).decode() in bodies[0]
    for body in bodies:
        assert not any(value in body for value in form.values())


# --- the contract ------------------------------------------------------------------------------------------------

def test_the_prompt_and_schema_list_exactly_the_features_the_code_judges():
    assert all(name in SYSTEM_PROMPT for name in w9.FEATURES)
    props = RESPONSE_SCHEMA["properties"]["structure"]["properties"]
    assert set(props) == set(w9.FEATURES) | {"revision_text"}
    assert set(RESPONSE_SCHEMA["properties"]["structure"]["required"]) == set(w9.FEATURES)
    assert "never infer a feature from text alone" in SYSTEM_PROMPT.lower()


def test_layout_observations_are_validated_strictly():
    ok = validate({**GOOD, "structure": {**ALL, "revision_text": "Rev. March 2024"}})
    assert ok["structure"]["same_ruled_layout"] is True
    for bad in ({**ALL, "tin_boxes": "true"}, {k: v for k, v in ALL.items() if k != "tin_boxes"}, "plain text"):
        with pytest.raises(ExtractionError):
            validate({**GOOD, "structure": bad})
    assert validate(GOOD)["structure"] is None


def test_the_reference_is_part_of_the_cache_key(samples, monkeypatch):
    path = samples / "w9_valid.pdf"
    assert cache_mod._key(path, True) != cache_mod._key(path, False)  # read differently, cached apart
    before = cache_mod._key(path, True)
    monkeypatch.setattr(w9, "reference_image_png", lambda: b"a different reference")
    assert cache_mod._key(path, True) != before  # a new reference invalidates the old answers


def test_cached_answers_keep_the_layout_observations(samples, tmp_path):
    cache = tmp_path / "c.json"
    run = cache_mod.cached_extractor(cache)
    first = run(samples / "w9_valid.pdf", StubModel(), True, True)
    second = run(samples / "w9_valid.pdf", None, False, True)  # no model at all: served from the cache
    assert second["detail"].endswith("(cached)") and second["structure"] == first["structure"] == {**LAYOUT_ON, "revision_text": "Rev. March 2024"}
    assert second["text_signature"]["matched"] == 18


def test_build_extracted_carries_the_evidence_to_the_checks(samples):
    extracted = build_extracted(samples / "w9_valid.pdf", client=StubModel(), use_llm=False, expected_type="w9")
    assert extracted["text_signature"]["matched"] == 18 and extracted["structure"]["same_ruled_layout"] is True
    plain = build_extracted(samples / "w9_valid.pdf", client=StubModel(), use_llm=False)  # no W-9 expected
    assert plain["structure"] is None
