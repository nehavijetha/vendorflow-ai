import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.seed import SEED_VENDORS  # noqa: E402


@pytest.fixture
def vendors():
    return [{"id": i + 1, **copy.deepcopy(v)} for i, v in enumerate(SEED_VENDORS)]


@pytest.fixture
def submission():
    return {
        "company_name": "Orion Components LLC",
        "country": "United States",
        "tax_id": "12-3456789",
        "bank_account_name": "Orion Components LLC",
        "bank_account_num": "000555666777",
        "documents": ["samples/w9_valid.pdf"],
    }


@pytest.fixture
def extracted():
    return {
        "status": "ok",
        "document_type": "w9",
        "legal_name": "Orion Components LLC",
        "tax_id": "12-3456789",
        "signed": True,
        "signed_date": "2026-10-28",
        "supporting_documents": [],
    }
