"""Read every sample PDF once with the real model and cache the answers, so demo runs are instant
and do not spend free-tier quota. Run before a demo: python scripts/warm_cache.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logic.cache import cached_extractor  # noqa: E402
from logic.extract import default_client  # noqa: E402
from ui import presets  # noqa: E402
from ui.app_state import CACHE_PATH  # noqa: E402

CACHE_PATH.parent.mkdir(exist_ok=True)
presets.ensure_samples()
client = default_client()
if client is None:
    sys.exit("No GEMINI_API_KEY configured; nothing to cache.")
extract = cached_extractor(CACHE_PATH)
for pdf in sorted(presets.SAMPLES_DIR.glob("*.pdf")):
    result = extract(pdf, client, False)
    print(f"{pdf.name:26} {result['status']:10} {result['source']:15} {result['detail']}")
