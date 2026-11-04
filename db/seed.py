"""Existing vendors that give the duplicate checks something to find."""
from sqlalchemy import select

from .models import Vendor

SEED_VENDORS = [
    {"legal_name": "Northwind Traders Inc", "country": "US", "tax_id": "98-7654321",
     "bank_account": "000111222333", "status": "active"},
    {"legal_name": "Globex Corporation Ltd", "country": "UK", "tax_id": "GB123456782",
     "bank_account": "000444555666", "status": "active"},
    {"legal_name": "Maple Leaf Supplies Ltd", "country": "CA", "tax_id": "123456782RT0001",
     "bank_account": "000777888999", "status": "active"},
    {"legal_name": "Shadow Supplies LLC", "country": "US", "tax_id": "55-5555555",
     "bank_account": "999000111222", "status": "blocked"},
]


def seed_vendors(session):
    """Idempotent: only inserts when the vendors table is empty."""
    if session.scalars(select(Vendor).limit(1)).first() is None:
        session.add_all(Vendor(**row) for row in SEED_VENDORS)
        session.commit()


def vendor_dicts(session):
    return [v.as_dict() for v in session.scalars(select(Vendor).order_by(Vendor.id))]
