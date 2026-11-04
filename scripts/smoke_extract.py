"""Manual live check: run every sample PDF through the real extraction path."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logic.extract import extract_document  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

for pdf in sorted(SAMPLES.glob("*.pdf")):
    r = extract_document(pdf)
    print(f"{pdf.name:26} {r['status']:10} {r['source']:15} {r['document_type']} | {r['legal_name']} | "
          f"{r['tax_id']} | signed={r['signed']} | {r['signed_date']} | {r['entity_names']}")
    if r["source"] == "offline_parser" or r["status"] != "ok":
        print(f"{'':26} note: {r['detail']}")
