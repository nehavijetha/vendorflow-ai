"""Grounding: text-layer PDFs must contain the value; scans must match the form exactly."""
import pytest

from db.models import make_session_factory
from db.seed import seed_vendors
from logic.extract import build_extracted
from logic.grounding import PRIMARY_ID, SCAN_NOTE, SUPPORTING_ID, ground_extraction, squash
from pipeline.runner import run_pipeline
from scripts.make_sample_pdfs import build_all

GOOD = {"legible": True, "document_type": "w9", "legal_name": "Orion Components LLC",
        "tax_id": "12-3456789", "signed": True, "signed_date": "2026-10-28", "entity_names": []}
PROOF = {"legible": True, "document_type": "dba_or_parent_proof", "legal_name": None, "tax_id": None,
         "signed": None, "signed_date": "2026-09-02",
         "entity_names": ["Orion Components LLC", "Orion Components Holdings LLC"]}


class Model:
    """Stands in for Gemini: returns a fixed answer per document type, records nothing else."""

    def __init__(self, primary=GOOD, proof=PROOF):
        self.primary, self.proof = primary, proof

    def generate(self, parts):
        text = parts[0]["text"]
        return self.proof if "Certificate of Ownership" in text else self.primary

    def write(self, system, prompt):
        return "Thanks for your submission; we need a little more."


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    out = tmp_path_factory.mktemp("grounding_samples")
    build_all(out)
    return out


def form(samples, doc="w9_valid.pdf", **overrides):
    base = {"company_name": "Orion Components LLC", "country": "United States", "tax_id": "12-3456789",
            "bank_account_name": "Orion Components LLC", "bank_account_num": "000555666777",
            "documents": [str(samples / doc)], "supporting_documents": []}
    return {**base, **overrides}


def ground(samples, model, doc="w9_valid.pdf", supporting=(), **overrides):
    data = form(samples, doc, supporting_documents=[str(samples / s) for s in supporting], **overrides)
    extracted = build_extracted(data["documents"][0], data["supporting_documents"], client=model, use_llm=False)
    return {f["id"]: f for f in ground_extraction(data, extracted)}, extracted


def test_squash_ignores_spacing_hyphens_case_and_accents():
    assert squash("12-3456 789") == squash("123456789")
    assert squash("Tremblay Équipement Ltée") == "tremblayequipementltee"


# --- text-layer PDFs -----------------------------------------------------------------------------

def test_text_layer_value_found_in_text_passes(samples):
    found, extracted = ground(samples, Model())
    assert extracted["source"] == "gemini-text"
    assert found[PRIMARY_ID]["result"] == "pass" and "found in the document text" in found[PRIMARY_ID]["evidence"]


@pytest.mark.parametrize("override,field", [
    ({"tax_id": "12-3456700"}, "tax_id"),
    ({"legal_name": "Completely Invented Corp"}, "legal_name"),
])
def test_text_layer_value_not_in_text_is_unverified(samples, override, field):
    found, _ = ground(samples, Model(primary={**GOOD, **override}))
    finding = found[PRIMARY_ID]
    assert finding["result"] == "fail" and field in finding["evidence"] and "unverified extraction" in finding["evidence"]
    assert finding["vendor_request"] and not finding["human_review"]


def test_text_layer_value_that_differs_from_the_form_still_grounds(samples):
    """The document really says 12-3456780. Whether that matches the form is a verification matter."""
    found, _ = ground(samples, Model(primary={**GOOD, "tax_id": "12-3456780"}), doc="w9_tax_mismatch.pdf")
    assert found[PRIMARY_ID]["result"] == "pass"


def test_text_layer_ignores_the_form_entirely(samples):
    found, _ = ground(samples, Model(), tax_id="99-9999999", company_name="Nothing Like It")
    assert found[PRIMARY_ID]["result"] == "pass"


# --- scans ---------------------------------------------------------------------------------------

def test_scan_that_matches_the_form_passes_with_a_note(samples):
    found, extracted = ground(samples, Model(), doc="w9_scanned.pdf")
    assert extracted["source"] == "gemini-vision"
    assert found[PRIMARY_ID]["result"] == "pass" and SCAN_NOTE in found[PRIMARY_ID]["evidence"]


def test_scan_match_is_exact_after_normalisation_only(samples):
    variants = {"legal_name": "Orion Components, L.L.C.", "tax_id": "12 3456789"}
    found, _ = ground(samples, Model(primary={**GOOD, **variants}), doc="w9_scanned.pdf")
    assert found[PRIMARY_ID]["result"] == "pass"


@pytest.mark.parametrize("override,field", [
    ({"tax_id": "12-3456780"}, "tax_id"),
    ({"legal_name": "Orion Component LLC"}, "legal_name"),
    ({"legal_name": "Orion Components Inc"}, "legal_name"),
])
def test_scan_that_differs_from_the_form_goes_to_a_person(samples, override, field):
    found, _ = ground(samples, Model(primary={**GOOD, **override}), doc="w9_scanned.pdf")
    finding = found[PRIMARY_ID]
    assert finding["result"] == "fail" and finding["human_review"] and field in finding["evidence"]
    assert "a person should look" in finding["evidence"]


def test_scan_cannot_pass_when_the_form_is_blank(samples):
    found, _ = ground(samples, Model(), doc="w9_scanned.pdf", tax_id="")
    assert found[PRIMARY_ID]["result"] == "fail" and found[PRIMARY_ID]["human_review"]


# --- nothing to ground ------------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["error", "unreadable", "missing"])
def test_unusable_extraction_is_skipped_not_passed(samples, status):
    data = form(samples)
    finding = ground_extraction(data, {"status": status})[0]
    assert finding["id"] == PRIMARY_ID and finding["result"] == "skipped"


def test_extraction_with_no_name_or_tax_id_has_nothing_to_ground(samples):
    found, _ = ground(samples, Model(primary={**GOOD, "legal_name": None, "tax_id": None}))
    assert found[PRIMARY_ID]["result"] == "pass" and "nothing to ground" in found[PRIMARY_ID]["evidence"]


def test_unreadable_pdf_text_is_skipped(samples):
    def broken(path):
        raise OSError("gone")

    finding = ground_extraction(form(samples), {"status": "ok", "tax_id": "1", "legal_name": "x"}, reader=broken)[0]
    assert finding["result"] == "skipped"


# --- supporting documents -----------------------------------------------------------------------------

def test_supporting_names_found_in_text_pass(samples):
    found, _ = ground(samples, Model(), supporting=["parent_entity_proof.pdf"],
                      bank_account_name="Orion Components Holdings LLC")
    assert found[SUPPORTING_ID]["result"] == "pass"


def test_supporting_name_not_in_text_fails(samples):
    invented = {**PROOF, "entity_names": ["Orion Components LLC", "Invented Parent Inc"]}
    found, _ = ground(samples, Model(proof=invented), supporting=["parent_entity_proof.pdf"])
    assert found[SUPPORTING_ID]["result"] == "fail" and "Invented Parent Inc" in found[SUPPORTING_ID]["evidence"]


def test_no_supporting_documents_means_no_supporting_finding(samples):
    found, _ = ground(samples, Model())
    assert SUPPORTING_ID not in found


def test_several_supporting_documents_merge_into_one_worst_finding(samples):
    invented = {**PROOF, "entity_names": ["Invented Parent Inc"]}
    calls = iter([GOOD, PROOF, invented])

    class Mixed(Model):
        def generate(self, parts):
            return next(calls)

    found, _ = ground(samples, Mixed(), supporting=["parent_entity_proof.pdf", "parent_entity_proof.pdf"])
    assert found[SUPPORTING_ID]["result"] == "fail" and " | " in found[SUPPORTING_ID]["evidence"]


# --- in the pipeline ------------------------------------------------------------------------------------

@pytest.fixture
def session(tmp_path):
    with make_session_factory(f"sqlite:///{tmp_path / 'g.db'}")() as s:
        seed_vendors(s)
        yield s


def test_hallucinated_tax_id_cannot_approve_a_vendor(session, samples):
    """The model invents a tax ID that matches the form; the document does not contain it."""
    data = form(samples, tax_id="55-1234567")
    model = Model(primary={**GOOD, "tax_id": "55-1234567"})
    result = run_pipeline(session, data, client=model, use_llm=True)
    assert result["status"] == "PENDING"
    assert PRIMARY_ID in result["decision"]["unresolved"]
    assert any(e["stage"] == "S4" and e["status"] == "completed" for e in result["events"])


def test_grounded_run_still_approves_and_shows_the_stage(session, samples):
    result = run_pipeline(session, form(samples), client=Model(), use_llm=True)
    assert result["status"] == "APPROVED"
    assert [e["name"] for e in result["events"] if e["stage"] == "S4"][0] == "Grounding"


def test_scanned_scenario_passes_only_through_the_form_match(session, samples):
    ok = run_pipeline(session, form(samples, doc="w9_scanned.pdf"), client=Model(), use_llm=True)
    assert ok["status"] == "APPROVED"
    session.rollback()
    bad = run_pipeline(session, form(samples, doc="w9_scanned.pdf", tax_id="12-3456788",
                                     bank_account_num="000999888777"),
                       client=Model(), use_llm=True)
    assert bad["status"] == "PENDING" and bad["decision"]["requires_human_review"]
