import pytest
from pypdf import PdfReader

from db.models import Finding, Run, make_session_factory
from db.seed import seed_vendors, vendor_dicts
from logic.names import name_similarity, normalize_name
from scripts.make_sample_pdfs import build_all


@pytest.mark.parametrize("variant", ["Acme Inc.", "ACME Incorporated", "Acme, Inc"])
def test_legal_suffix_variants_normalise_together(variant):
    assert normalize_name(variant, "US") == normalize_name("Acme Inc", "US") == "acme inc"


def test_suffix_rules_are_country_specific():
    assert normalize_name("Tremblay Équipement Ltée", "Canada") == "tremblay equipement ltd"
    assert normalize_name("Acme Corporation", "UK") == "acme corporation"  # not a UK suffix


def test_original_names_are_not_mutated():
    name = "Acme, Inc."
    normalize_name(name)
    assert name == "Acme, Inc."


def test_similarity_bands():
    assert name_similarity("Orion Components LLC", "orion components, L.L.C.", "US") >= 90
    assert 60 <= name_similarity("Orion Components LLC", "Orion Components Holdings LLC", "US") < 90
    assert name_similarity("Orion Components LLC", "Zenith Logistics Group", "US") < 60


def test_seed_is_idempotent_and_duplicates_are_storable(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 't.db'}")
    with factory() as session:
        seed_vendors(session)
        seed_vendors(session)
        rows = vendor_dicts(session)
        assert len(rows) == 4
        assert any(r["status"] == "blocked" for r in rows)
        run = Run(id="run-1", input_json="{}", status="PENDING")
        session.add(run)
        session.add(Finding(run_id="run-1", check_id="c", result="warn"))
        session.add(Run(id="run-2", parent_run_id="run-1", input_json="{}", status="APPROVED"))
        session.commit()
        assert session.get(Run, "run-2").parent_run_id == "run-1"


def test_sample_pdfs_have_expected_text_layers(tmp_path):
    names = build_all(tmp_path)
    assert {"w9_valid.pdf", "w9_unsigned.pdf", "w9_scanned.pdf", "utility_bill.pdf",
            "w9_tax_mismatch.pdf", "parent_entity_proof.pdf"} <= set(names)

    def text(name):
        return PdfReader(str(tmp_path / name)).pages[0].extract_text()

    assert "12-3456789" in text("w9_valid.pdf") and "(signed)" in text("w9_valid.pdf")
    assert "(signed)" not in text("w9_unsigned.pdf")
    assert "12-3456780" in text("w9_tax_mismatch.pdf")
    assert text("w9_scanned.pdf").strip() == ""
    assert "Amount due" in text("utility_bill.pdf")
