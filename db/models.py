"""SQLAlchemy models. No unique constraints on tax ID / bank account: duplicates must be
stored so the verification layer can detect and investigate them."""
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import ForeignKey, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def _now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(primary_key=True)
    legal_name: Mapped[str]
    country: Mapped[str]
    tax_id: Mapped[str]
    bank_account: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="active")  # active | blocked
    created_at: Mapped[datetime] = mapped_column(default=_now)

    def as_dict(self):
        return {
            "id": self.id,
            "legal_name": self.legal_name,
            "country": self.country,
            "tax_id": self.tax_id,
            "bank_account": self.bank_account,
            "status": self.status,
        }


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(primary_key=True)
    parent_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id"))
    input_json: Mapped[str] = mapped_column(Text)
    extracted_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None]
    vendor_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    check_id: Mapped[str]
    result: Mapped[str]
    evidence: Mapped[str | None] = mapped_column(Text)
    vendor_request: Mapped[str | None] = mapped_column(Text)
    human_review: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class StageEvent(Base):
    """One row per pipeline event (S1-S7), written as it happens to power the live run view."""
    __tablename__ = "stage_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    stage: Mapped[str]
    status: Mapped[str]  # started | completed | failed
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)


DEFAULT_URL = f"sqlite:///{Path(__file__).resolve().parent.parent / 'vendorflow.db'}"


class Document(Base):
    """One row per file a run was submitted with. The content hash is what lets the document-reuse
    rule notice the same file arriving under a different vendor, whatever it is renamed to."""
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    role: Mapped[str]  # tax | supporting
    filename: Mapped[str]
    sha256: Mapped[str] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Review(Base):
    """A human reviewer's action on a run. Stored apart from Run.status so the
    deterministic system result is never overwritten."""
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    reviewer: Mapped[str]
    decision: Mapped[str]  # approve | reject | request_info
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)


def make_session_factory(url=DEFAULT_URL):
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
