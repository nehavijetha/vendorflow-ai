import json

from logic.names import normalize_country

from .models import Vendor


def onboard_vendor(session, run):
    """Add the vendor behind an approved run to the vendors table (once)."""
    if run.vendor_id is not None:
        return session.get(Vendor, run.vendor_id)
    data = json.loads(run.input_json)
    vendor = Vendor(
        legal_name=data["company_name"],
        country=normalize_country(data.get("country")) or data.get("country"),
        tax_id=data["tax_id"],
        bank_account=data["bank_account_num"],
        status="active",
    )
    session.add(vendor)
    session.flush()
    run.vendor_id = vendor.id
    return vendor
