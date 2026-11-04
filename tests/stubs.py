"""A stand-in for Gemini in tests. It never touches the network: it reads the document text it is
sent, the way a faithful model would, and answers in the strict format the real model must use.

It tests our pipeline, not the model: whether the real model can tell a genuine form from a lookalike
was measured live (see the README) and is held in the committed cache, not asserted here.
"""
import re

from logic import w9_reference
from logic.names import squash

COMPANIES = ["Orion Components LLC", "Meridian Tools LLC", "Cobalt Freight Inc", "Shadow Supplies LLC"]
EINS = ["12-3456789", "12-3456780", "20-4417283", "84-2201937", "55-5555555", "98-7654321"]
LAYOUT_ON = {name: True for name in w9_reference.FEATURES}
LAYOUT_OFF = {name: False for name in w9_reference.FEATURES}

COBALT_SCAN = {"legible": True, "document_type": "w9", "legal_name": "Cobalt Freight Inc", "tax_id": "84-2201937",
               "signed": True, "signed_date": "2026-10-28", "entity_names": []}


class StubModel:
    """layout: whether the page looks like the official form to the model (it sees ruled boxes)."""

    def __init__(self, layout=True, scan=None):
        self.layout, self.scan, self.requests = layout, scan or COBALT_SCAN, []

    def write(self, system, prompt):  # the template opening is used, as when no model is available
        raise RuntimeError("no wording in tests")

    def generate(self, parts):
        self.requests.append(parts)
        texts = [p["text"] for p in parts if "text" in p]
        has_reference = any(t.startswith("REFERENCE") for t in texts)
        images = [p for p in parts if "inline_data" in p]
        text = ""
        for t in texts:
            for marker in ("SUBMITTED DOCUMENT text layer (empty for a scan):\n\n", "Document text:\n\n"):
                if t.startswith(marker):
                    text = t[len(marker):]
        if not text.strip() and images:  # a scan: only the images carry the document
            return self._answer(dict(self.scan), has_reference, w9_like=True, looks_genuine=True)
        looks_genuine = w9_reference.text_signature(text)["matched"] >= w9_reference.MIN_PHRASES
        return self._answer(self._read(text), has_reference, w9_like="w-9" in text.lower(), looks_genuine=looks_genuine)

    def _read(self, text):
        low, squashed = text.lower(), squash(text)
        if "amount due" in low:
            return {"legible": True, "document_type": "utility_bill", "legal_name": "Orion Components LLC",
                    "tax_id": None, "signed": None, "signed_date": None, "entity_names": []}
        if "certificate of ownership" in low:
            names = [n for n in (*COMPANIES, "Orion Components Holdings LLC") if squash(n) in squashed]
            return {"legible": True, "document_type": "dba_or_parent_proof", "legal_name": None, "tax_id": None,
                    "signed": None, "signed_date": "2026-09-02", "entity_names": names}
        name = next((n for n in COMPANIES if squash(n) in squashed), None)
        ein = next((e for e in EINS if squash(e) in squashed), None)
        signed = "(signed)" in low or "10/28/2026" in text or "date: 2026-10-28" in low
        return {"legible": True, "document_type": "w9" if "w-9" in low else "other", "legal_name": name,
                "tax_id": ein, "signed": signed, "signed_date": "2026-10-28" if signed else None,
                "entity_names": []}

    def _answer(self, fields, has_reference, w9_like, looks_genuine):
        """Layout observations: ruled boxes are seen only on a page that really is the form (and only
        if this stub is configured to see them, to simulate a lookalike)."""
        fields["structure"] = None
        if has_reference and w9_like:
            on = self.layout and looks_genuine
            fields["structure"] = {**(LAYOUT_ON if on else LAYOUT_OFF), "revision_text": "Rev. March 2024"}
        return fields
