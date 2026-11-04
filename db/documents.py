"""Storing the files a run was submitted with, and building the history the document-reuse rule reads."""
import json
from pathlib import Path

from sqlalchemy import select

from logic.docs import file_sha256
from logic.names import normalize_tax_id

from .models import Document, Run


def store_documents(session, run_id, data):
    """Record the hash of every readable submitted file. Returns the stored rows as dicts."""
    stored = []
    for role, paths in (("tax", data.get("documents") or []), ("supporting", data.get("supporting_documents") or [])):
        for path in paths:
            digest = file_sha256(path)
            if digest is None:
                continue  # a missing file is reported by extraction, not here
            session.add(Document(run_id=run_id, role=role, filename=Path(path).name, sha256=digest))
            stored.append({"role": role, "filename": Path(path).name, "sha256": digest})
    session.commit()
    return stored


def chain_run_ids(session, parent_run_id):
    """Every run in the same resubmission chain as parent_run_id: its root and all descendants."""
    if not parent_run_id:
        return set()
    root = parent_run_id
    while True:
        run = session.get(Run, root)
        if run is None or run.parent_run_id is None:
            break
        root = run.parent_run_id
    ids, frontier = {root}, [root]
    while frontier:
        children = session.scalars(select(Run.id).where(Run.parent_run_id.in_(frontier))).all()
        frontier = [c for c in children if c not in ids]
        ids.update(frontier)
    return ids


def build_history(session, run_id, parent_run_id, current_documents):
    chain = chain_run_ids(session, parent_run_id) | {run_id}
    prior = []
    for document in session.scalars(select(Document).where(Document.run_id.notin_(chain))):
        run = session.get(Run, document.run_id)
        prior.append({"run_id": document.run_id, "sha256": document.sha256, "filename": document.filename,
                      "tax_id": normalize_tax_id(json.loads(run.input_json).get("tax_id"))})
    return {"current_documents": current_documents, "chain_run_ids": chain, "prior_documents": prior}
