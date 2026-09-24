from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, field_validator


def _to_decimal(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return None
        for junk in (",", "₹", "Rs.", "Rs", "INR"):
            s = s.replace(junk, "")
        s = s.strip()
        try:
            return Decimal(s)
        except InvalidOperation:
            return None
    return v


def _to_date(v):
    if v is None or isinstance(v, date):
        return v
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return None
        try:
            return date.fromisoformat(s)
        except ValueError:
            pass
        for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%B %d, %Y", "%d %B %Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None
    return v


def _blank_to_none(v):
    return None if isinstance(v, str) and v.strip() == "" else v


class LineItem(BaseModel):
    description: str
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None

    @field_validator("quantity", "unit_price", "amount", mode="before")
    @classmethod
    def _parse_amounts(cls, v):
        return _to_decimal(v)


class InvoiceData(BaseModel):
    vendor_name: str | None
    vendor_gstin: str | None
    invoice_number: str | None
    invoice_date: date | None
    po_reference: str | None
    currency: str | None
    line_items: list[LineItem]
    subtotal: Decimal | None
    tax_amount: Decimal | None
    total: Decimal | None
    tax_inclusive: bool
    source: Literal["text", "vision"]

    @field_validator("subtotal", "tax_amount", "total", mode="before")
    @classmethod
    def _parse_amounts(cls, v):
        return _to_decimal(v)

    @field_validator(
        "vendor_name", "vendor_gstin", "invoice_number", "po_reference", "currency", mode="before"
    )
    @classmethod
    def _parse_blank_strings(cls, v):
        return _blank_to_none(v)

    @field_validator("invoice_date", mode="before")
    @classmethod
    def _parse_invoice_date(cls, v):
        return _to_date(v)


class RuleResult(BaseModel):
    rule_id: str
    name: str
    status: Literal["pass", "warn", "fail", "skip"]
    message: str
    evidence: dict


class Decision(BaseModel):
    outcome: Literal["APPROVE", "NEEDS_REVIEW", "REJECT"]
    reasons: list[str]
    summary: str
    next_action: str
