"""Cache of successful model extractions, keyed by file content.

Makes demo reruns instant and independent of API availability. Offline-parser results and
errors are never cached, so a transient failure cannot become a permanent answer.
"""
import hashlib
import json
from pathlib import Path

from .extract import RESPONSE_SCHEMA, SYSTEM_PROMPT, extract_document


def _fingerprint():
    """Changes when the prompt or schema changes, so stale answers are not reused. The model is not
    part of the key: the client may fall back between models and the answer should still be reused."""
    basis = SYSTEM_PROMPT + json.dumps(RESPONSE_SCHEMA, sort_keys=True)
    return hashlib.sha256(basis.encode()).hexdigest()[:12]


def _key(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() + ":" + _fingerprint()


def cached_extractor(cache_path, extractor=extract_document):
    cache_path = Path(cache_path)

    def load():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def run(path, client=None, use_llm=True):
        try:
            key = _key(path)
        except OSError:
            return extractor(path, client, use_llm)  # let the extractor report the problem
        store = load()
        if key in store:
            hit = dict(store[key])
            hit["detail"] = hit["detail"] + " (cached)"
            return hit
        result = extractor(path, client, use_llm)
        if result["status"] == "ok" and result["source"].startswith("gemini"):
            store[key] = result
            cache_path.write_text(json.dumps(store, indent=1), encoding="utf-8")
        return result

    return run
