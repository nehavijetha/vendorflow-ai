"""Typed contracts at the three boundaries: the submission coming in, what the extraction model
returns, and the findings that drive every decision. Inside the pipeline everything stays a plain
dict (models are dumped at the boundary), so nothing else had to change.

The point is that bad data fails loudly where it enters: a model answer with a wrong type, or a
check returning an invented result value, can never reach the decision engine looking valid.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, create_model, field_validator

from .w9_reference import FEATURES

Result = Literal["pass", "warn", "fail", "block", "error", "skipped"]
DocType = Literal["w9", "vat_certificate", "cra_business_number_letter", "utility_bill",
                  "dba_or_parent_proof", "other"]
ExtractionStatus = Literal["ok", "unreadable", "error", "missing"]


class Finding(BaseModel):
    """One check's outcome: the single unit that drives the decision, the explanation and the email."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    result: Result
    evidence: str
    vendor_request: str | None = None
    human_review: StrictBool = False


class Submission(BaseModel):
    """What a vendor (or the UI) hands in. Unknown keys are kept; missing text is empty, not None."""
    model_config = ConfigDict(extra="allow")

    company_name: str = ""
    country: str = ""
    tax_id: str = ""
    bank_account_name: str = ""
    bank_account_num: str = ""
    documents: list[str] = Field(default_factory=list)
    supporting_documents: list[str] = Field(default_factory=list)

    @field_validator("company_name", "country", "tax_id", "bank_account_name", "bank_account_num", mode="before")
    @classmethod
    def _text(cls, value):
        if value is None:
            return ""
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError("must be text")
        return str(value)

    @field_validator("documents", "supporting_documents", mode="before")
    @classmethod
    def _paths(cls, value):
        if value is None:
            return []
        if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
            raise ValueError("must be a list of file paths")
        return [str(path) for path in value]


# What the model reports about the page layout: one strict boolean per feature in w9_reference.FEATURES.
Structure = create_model(
    "Structure",
    __config__=ConfigDict(extra="ignore"),
    revision_text=(str | None, None),
    **{name: (StrictBool, ...) for name in FEATURES},
)


class ModelOutput(BaseModel):
    """What the extraction model must return. Strict on purpose: "true" is not a boolean here."""
    model_config = ConfigDict(extra="ignore")

    legible: StrictBool
    document_type: DocType
    legal_name: str | None = None
    tax_id: str | None = None
    signed: StrictBool | None = None
    signed_date: str | None = None
    entity_names: list[str] = Field(default_factory=list)
    structure: Structure | None = None

    @field_validator("legal_name", "tax_id", "signed_date")
    @classmethod
    def _blank_is_none(cls, value):
        return value.strip() or None if value is not None else None

    @field_validator("entity_names", mode="before")
    @classmethod
    def _no_names(cls, value):
        return [] if value is None else value

    @field_validator("entity_names")
    @classmethod
    def _clean_names(cls, value):
        return [name.strip() for name in value if name.strip()]


class Extraction(BaseModel):
    """The combined result the checks read: the tax document's fields plus any supporting documents."""
    model_config = ConfigDict(extra="allow")

    status: ExtractionStatus
    source: str | None = None
    detail: str | None = None
    document_type: DocType | None = None
    legal_name: str | None = None
    tax_id: str | None = None
    signed: StrictBool | None = None
    signed_date: str | None = None
    entity_names: list[str] = Field(default_factory=list)
    structure: dict | None = None
    text_signature: dict | None = None
    supporting_documents: list[dict] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
