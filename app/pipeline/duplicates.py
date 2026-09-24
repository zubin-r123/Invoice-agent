"""DUP-01, DUP-02, DUP-03: duplicate detection against ledger.csv and prior runs (app/db.py)."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Literal, Mapping

from app import data
from app.config import DUP_DATE_WINDOW_DAYS, ROUNDING_TOLERANCE
from app.data import Vendor
from app.format import format_inr
from app.models import InvoiceData, RuleResult

_TRAILING_DIGITS = re.compile(r"(\d+)$")


@dataclass
class DupRecord:
    """A prior invoice to compare against — either a static ledger.csv line (an already-paid
    invoice) or an earlier run of this app (runs.db). The two sources get different wording and
    a different next_action downstream, so `source` is load-bearing, not cosmetic."""

    vendor_id: str
    invoice_number: str
    invoice_date: date
    total: Decimal
    source: Literal["ledger", "run"] = "ledger"
    run_id: str | None = None
    created_at: str | None = None
    outcome: str | None = None


@dataclass
class HashRecord:
    """Identifies which earlier run produced a given file hash, so DUP-01 can name it. A hash
    can only ever match another run of this app, never the static ledger — so it's always cited
    by run timestamp/outcome, never an invoice date."""

    run_id: str
    filename: str
    invoice_number: str | None
    created_at: str
    outcome: str | None


def normalize_invoice_number(raw: str | None) -> str | None:
    if raw is None:
        return None
    match = _TRAILING_DIGITS.search(raw.strip().upper())
    if not match:
        return None
    return str(int(match.group(1)))


def format_run_timestamp(iso_str: str | None) -> str:
    """Human-friendly run timestamp for duplicate messages, e.g. '2026-09-24 20:14 UTC'."""
    if not iso_str:
        return "an earlier run"
    try:
        return datetime.fromisoformat(iso_str).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return iso_str


def check(
    invoice: InvoiceData,
    file_hash: str,
    vendor: Vendor | None,
    previous_hashes: Mapping[str, HashRecord] = MappingProxyType({}),
    previous_invoices: list[DupRecord] = (),
) -> list[RuleResult]:
    records = [
        DupRecord(e.vendor_id, e.invoice_number, e.invoice_date, e.invoice_total, source="ledger")
        for e in data.ledger_entries()
    ] + list(previous_invoices)

    return [
        _dup01_file_hash(file_hash, previous_hashes),
        _dup02_duplicate_invoice_number(invoice, vendor, records),
        _dup03_similar_invoice(invoice, vendor, records),
    ]


def _dup01_file_hash(file_hash: str, previous_hashes: Mapping[str, HashRecord]) -> RuleResult:
    record = previous_hashes.get(file_hash)
    if record is not None:
        original = record.invoice_number or record.filename
        when = format_run_timestamp(record.created_at)
        return RuleResult(
            rule_id="DUP-01",
            name="File not previously processed",
            status="fail",
            message=(
                f"This exact PDF was already submitted as {original} in an earlier run "
                f"({when}, outcome {record.outcome}) — same file re-uploaded, not a new invoice "
                "from the vendor. No vendor action needed; open the earlier run."
            ),
            evidence={
                "file_hash": file_hash,
                "original_run_id": record.run_id,
                "original_filename": record.filename,
                "original_invoice_number": record.invoice_number,
                "original_run_created_at": record.created_at,
                "original_run_outcome": record.outcome,
            },
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
            if record.source == "run":
                when = format_run_timestamp(record.created_at)
                message = (
                    f"Invoice {invoice.invoice_number} matches one already processed in an "
                    f"earlier run ({when}, outcome {record.outcome}) — likely the same invoice "
                    "submitted twice. No vendor action needed; open the earlier run to confirm."
                )
                evidence = {
                    "invoice_number": invoice.invoice_number,
                    "original_invoice_number": record.invoice_number,
                    "original_run_id": record.run_id,
                    "original_run_created_at": record.created_at,
                    "original_run_outcome": record.outcome,
                    "source": "run",
                }
            else:
                message = (
                    f"Invoice {invoice.invoice_number} matches an already-processed invoice from "
                    f"this vendor — {record.invoice_number} on {record.invoice_date.isoformat()} "
                    f"for {format_inr(record.total)}. This looks like a resubmission from the "
                    "vendor; notify them before paying again."
                )
                evidence = {
                    "invoice_number": invoice.invoice_number,
                    "original_invoice_number": record.invoice_number,
                    "original_invoice_date": record.invoice_date.isoformat(),
                    "original_total": str(record.total),
                    "source": "ledger",
                }
            return RuleResult(
                rule_id="DUP-02",
                name="No duplicate invoice number",
                status="fail",
                message=message,
                evidence=evidence,
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
        if record.source == "run":
            when = f"an earlier run ({format_run_timestamp(record.created_at)}, outcome {record.outcome})"
            evidence = {
                "invoice_number": invoice.invoice_number,
                "similar_invoice_number": record.invoice_number,
                "similar_run_id": record.run_id,
                "similar_run_created_at": record.created_at,
                "similar_run_outcome": record.outcome,
                "total": str(invoice.total),
                "source": "run",
            }
        else:
            when = f"invoice {record.invoice_number} from {record.invoice_date.isoformat()}"
            evidence = {
                "invoice_number": invoice.invoice_number,
                "similar_invoice_number": record.invoice_number,
                "similar_invoice_date": record.invoice_date.isoformat(),
                "total": str(invoice.total),
                "source": "ledger",
            }
        return RuleResult(
            rule_id="DUP-03",
            name="No suspiciously similar invoice",
            status="warn",
            message=(
                f"Same vendor and same amount ({format_inr(invoice.total)}) as {when}, but with a "
                "different invoice number — worth a manual check before paying"
            ),
            evidence=evidence,
        )
    return RuleResult(
        rule_id="DUP-03",
        name="No suspiciously similar invoice",
        status="pass",
        message="No suspiciously similar invoice found",
        evidence={},
    )
