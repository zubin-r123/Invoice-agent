"""DUP-01, DUP-02, DUP-03: duplicate detection against ledger.csv and (once app/db.py exists)
prior runs.

`previous_hashes`/`previous_invoices` default to empty since there is no persisted run history
yet; wiring them to real SQLite data is a later phase and needs no change to this module's API.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import data
from app.config import DUP_DATE_WINDOW_DAYS, ROUNDING_TOLERANCE
from app.data import Vendor
from app.models import InvoiceData, RuleResult

_TRAILING_DIGITS = re.compile(r"(\d+)$")


@dataclass
class DupRecord:
    vendor_id: str
    invoice_number: str
    invoice_date: date
    total: Decimal


def normalize_invoice_number(raw: str | None) -> str | None:
    if raw is None:
        return None
    match = _TRAILING_DIGITS.search(raw.strip().upper())
    if not match:
        return None
    return str(int(match.group(1)))


def check(
    invoice: InvoiceData,
    file_hash: str,
    vendor: Vendor | None,
    previous_hashes: set[str] = frozenset(),
    previous_invoices: list[DupRecord] = (),
) -> list[RuleResult]:
    records = [
        DupRecord(e.vendor_id, e.invoice_number, e.invoice_date, e.invoice_total) for e in data.ledger_entries()
    ] + list(previous_invoices)

    return [
        _dup01_file_hash(file_hash, previous_hashes),
        _dup02_duplicate_invoice_number(invoice, vendor, records),
        _dup03_similar_invoice(invoice, vendor, records),
    ]


def _dup01_file_hash(file_hash: str, previous_hashes: set[str]) -> RuleResult:
    if file_hash in previous_hashes:
        return RuleResult(
            rule_id="DUP-01",
            name="File not previously processed",
            status="fail",
            message="This exact file has been processed before",
            evidence={"file_hash": file_hash},
        )
    return RuleResult(
        rule_id="DUP-01",
        name="File not previously processed",
        status="pass",
        message="File hash not seen before",
        evidence={"file_hash": file_hash},
    )


def _dup02_duplicate_invoice_number(
    invoice: InvoiceData, vendor: Vendor | None, records: list[DupRecord]
) -> RuleResult:
    if vendor is None or invoice.invoice_number is None:
        return RuleResult(
            rule_id="DUP-02",
            name="No duplicate invoice number",
            status="skip",
            message="Vendor not identified or invoice number missing",
            evidence={},
        )

    norm = normalize_invoice_number(invoice.invoice_number)
    for record in records:
        if record.vendor_id == vendor.vendor_id and normalize_invoice_number(record.invoice_number) == norm:
            return RuleResult(
                rule_id="DUP-02",
                name="No duplicate invoice number",
                status="fail",
                message=f"Duplicate of invoice {record.invoice_number} ({record.invoice_date.isoformat()})",
                evidence={
                    "invoice_number": invoice.invoice_number,
                    "original_invoice_number": record.invoice_number,
                    "original_invoice_date": record.invoice_date.isoformat(),
                    "original_total": str(record.total),
                },
            )
    return RuleResult(
        rule_id="DUP-02",
        name="No duplicate invoice number",
        status="pass",
        message="No matching invoice number found",
        evidence={},
    )


def _dup03_similar_invoice(invoice: InvoiceData, vendor: Vendor | None, records: list[DupRecord]) -> RuleResult:
    if vendor is None or invoice.total is None or invoice.invoice_date is None:
        return RuleResult(
            rule_id="DUP-03",
            name="No suspiciously similar invoice",
            status="skip",
            message="Vendor, total, or invoice date missing",
            evidence={},
        )

    norm = normalize_invoice_number(invoice.invoice_number)
    for record in records:
        if record.vendor_id != vendor.vendor_id:
            continue
        if abs(record.total - invoice.total) > ROUNDING_TOLERANCE:
            continue
        if abs((record.invoice_date - invoice.invoice_date).days) > DUP_DATE_WINDOW_DAYS:
            continue
        if normalize_invoice_number(record.invoice_number) == norm:
            continue
        return RuleResult(
            rule_id="DUP-03",
            name="No suspiciously similar invoice",
            status="warn",
            message=f"Same vendor/total as invoice {record.invoice_number} ({record.invoice_date.isoformat()}), different number",
            evidence={
                "invoice_number": invoice.invoice_number,
                "similar_invoice_number": record.invoice_number,
                "similar_invoice_date": record.invoice_date.isoformat(),
                "total": str(invoice.total),
            },
        )
    return RuleResult(
        rule_id="DUP-03",
        name="No suspiciously similar invoice",
        status="pass",
        message="No suspiciously similar invoice found",
        evidence={},
    )
