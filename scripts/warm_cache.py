"""Read every sample PDF once with the real model and cache the answers, so demo runs are instant
and do not spend free-tier quota. Run before a demo: python scripts/warm_cache.py

Each scenario's tax document is read the way the app reads it: with the official W-9 as the visual
reference. The supporting document is read without it. The cache is keyed by file content, so it only
needs refreshing when a sample or the extraction prompt changes.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logic.cache import cached_extractor  # noqa: E402
from logic.extract import default_client  # noqa: E402
from ui import presets  # noqa: E402
from ui.app_state import cache_path  # noqa: E402

cache_path().parent.mkdir(parents=True, exist_ok=True)
presets.ensure_samples()
client = default_client()
if client is None:
    sys.exit("No GEMINI_API_KEY configured; nothing to cache.")
extract = cached_extractor(cache_path())

documents = sorted({p["fields"]["document"] for p in presets.PRESETS.values()} - {presets.UPLOAD_CHOICE})
jobs = [(name, True) for name in documents] + [(presets.PROOF_SAMPLE, False)]
failures = 0
for name, reference in jobs:
    started = time.time()
    result = extract(presets.SAMPLES_DIR / name, client, False, reference)
    ok = result["status"] == "ok" and result["source"].startswith("gemini")
    failures += not ok
    print(f"{name:32} {'W-9 reference' if reference else 'no reference':14} {result['status']:9} "
          f"{result['source']:20} {time.time() - started:5.1f}s  {'' if ok else result['detail'][:90]}", flush=True)
print(f"\n{len(jobs) - failures} of {len(jobs)} cached from the model.")
sys.exit(1 if failures else 0)
