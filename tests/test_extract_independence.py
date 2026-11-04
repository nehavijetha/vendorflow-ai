"""The extraction model must never see what the vendor typed into the form.

Grounding treats the form as an independent check on what the model read. If the model could see
the form values it could copy them, and a match would prove nothing. This test captures the real
HTTP request bodies, so a change to the prompt or the request that leaks form data fails here.
"""
import io
import json

from db.models import make_session_factory
from db.seed import seed_vendors
from logic.extract import GeminiClient
from pipeline.runner import run_pipeline
from scripts.make_sample_pdfs import build_all

FORM_VALUES = {
    "company_name": "Zyxwv Quasar Holdings",
    "tax_id": "77-7654321",
    "bank_account_name": "Qwerty Bank Holder Co",
    "bank_account_num": "4242424242",
}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_no_form_value_reaches_any_model_request(tmp_path, monkeypatch):
    build_all(tmp_path)
    bodies = []

    def fake_urlopen(request, timeout=None):
        bodies.append(request.data.decode())
        fields = {"legible": True, "document_type": "w9", "legal_name": "Orion Components LLC",
                  "tax_id": "12-3456789", "signed": True, "signed_date": "2026-10-28", "entity_names": []}
        return Resp(json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(fields)}]}}]}).encode())

    monkeypatch.setattr("logic.extract.urllib.request.urlopen", fake_urlopen)
    submission = {
        **FORM_VALUES, "country": "United States",
        "documents": [str(tmp_path / "w9_valid.pdf")],
        "supporting_documents": [str(tmp_path / "parent_entity_proof.pdf")],
    }
    with make_session_factory(f"sqlite:///{tmp_path / 'i.db'}")() as session:
        seed_vendors(session)
        run_pipeline(session, submission, client=GeminiClient(api_key="k", models=["m"]), use_llm=True)

    assert len(bodies) >= 2  # primary and supporting documents were both sent to the model
    for body in bodies:
        for name, value in FORM_VALUES.items():
            assert value not in body, f"form field {name} leaked into a model request"
    assert any("Orion Components LLC" in body for body in bodies)  # the document's own text did go
