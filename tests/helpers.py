from pathlib import Path


def clean_history(submission, prior=(), chain=()):
    """Run history for tests that call the checks directly: this submission's files, no other runs."""
    documents = [{"role": "tax", "filename": Path(p).name, "sha256": f"hash-of-{Path(p).name}"}
                 for p in submission.get("documents") or []]
    return {"current_documents": documents, "chain_run_ids": set(chain), "prior_documents": list(prior)}
