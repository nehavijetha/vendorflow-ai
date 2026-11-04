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


def test_sample_pdfs_have_the_expected_text_layers(tmp_path):
    from logic.w9_reference import MIN_PHRASES, text_signature

    names = set(build_all(tmp_path))
    assert {"w9_valid.pdf", "w9_unsigned.pdf", "w9_tax_mismatch.pdf", "w9_meridian.pdf", "w9_blocked.pdf",
            "w9_blank_template.pdf", "w9_scanned.pdf", "spoof_fake_w9.pdf", "spoof_lookalike_w9.pdf",
            "spoof_letter_mentions_w9.pdf", "utility_bill.pdf", "parent_entity_proof.pdf"} <= names

    def text(name):
        return PdfReader(str(tmp_path / name)).pages[0].extract_text()

    def phrases(name):
        return text_signature(text(name))["matched"]

    for genuine in ("w9_valid.pdf", "w9_unsigned.pdf", "w9_tax_mismatch.pdf", "w9_meridian.pdf", "w9_blocked.pdf",
                    "w9_blank_template.pdf"):
        assert phrases(genuine) == 18, genuine  # the real IRS page: every distinctive phrase is there
    assert phrases("spoof_lookalike_w9.pdf") == 18  # the wording is all there; only the layout is wrong
    assert phrases("spoof_fake_w9.pdf") < MIN_PHRASES and phrases("spoof_letter_mentions_w9.pdf") < MIN_PHRASES
    assert text("w9_scanned.pdf").strip() == ""  # a photocopy: no text layer at all
    assert "Amount due" in text("utility_bill.pdf")


def test_sample_forms_carry_the_values_written_on_the_real_form(tmp_path):
    build_all(tmp_path)

    def squashed(name):
        from logic.names import squash
        return squash(PdfReader(str(tmp_path / name)).pages[0].extract_text())

    assert "orioncomponentsllc" in squashed("w9_valid.pdf") and "123456789" in squashed("w9_valid.pdf")
    assert "123456780" in squashed("w9_tax_mismatch.pdf") and "meridiantoolsllc" in squashed("w9_meridian.pdf")
    assert "20441728" in squashed("w9_meridian.pdf") and "shadowsuppliesllc" in squashed("w9_blocked.pdf")
    assert "10282026" in squashed("w9_valid.pdf") and "10282026" not in squashed("w9_unsigned.pdf")  # date only when signed
    assert "orioncomponentsllc" not in squashed("w9_blank_template.pdf")
