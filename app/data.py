"""Loads data/*.csv once at startup into typed in-memory structures."""

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass
class Vendor:
    vendor_id: str
    legal_name: str
    aliases: list[str]
    gstin: str
    state: str
    status: str
    payment_terms_days: int
    bank_account_last4: str
    onboarded_on: date


@dataclass
class POLine:
    po_number: str
    vendor_id: str
    po_date: date
    status: str
    line_no: int
    item_code: str
    description: str
    qty: Decimal
    unit: str
    unit_price: Decimal
    tax_rate: Decimal
    currency: str


@dataclass
class LedgerEntry:
    ledger_id: str
    vendor_id: str
    invoice_number: str
    invoice_date: date
    po_number: str
    po_line_no: int
    qty_billed: Decimal
    line_amount: Decimal
    invoice_subtotal: Decimal
    invoice_tax: Decimal
    invoice_total: Decimal
    status: str
    processed_at: date


_vendors: dict[str, Vendor] = {}
_po_lines: dict[str, list[POLine]] = {}
_ledger: list[LedgerEntry] = []


def _parse_date(s: str) -> date:
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def _load_vendors() -> dict[str, Vendor]:
    vendors: dict[str, Vendor] = {}
    with open(DATA_DIR / "vendors.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            vendors[row["vendor_id"]] = Vendor(
                vendor_id=row["vendor_id"],
                legal_name=row["legal_name"],
                aliases=[a for a in row["aliases"].split("|") if a],
                gstin=row["gstin"],
                state=row["state"],
                status=row["status"],
                payment_terms_days=int(row["payment_terms_days"]),
                bank_account_last4=row["bank_account_last4"],
                onboarded_on=_parse_date(row["onboarded_on"]),
            )
    return vendors


def _load_po_lines() -> dict[str, list[POLine]]:
    pos: dict[str, list[POLine]] = {}
    with open(DATA_DIR / "purchase_orders.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            line = POLine(
                po_number=row["po_number"],
                vendor_id=row["vendor_id"],
                po_date=_parse_date(row["po_date"]),
                status=row["status"],
                line_no=int(row["line_no"]),
                item_code=row["item_code"],
                description=row["description"],
                qty=Decimal(row["qty"]),
                unit=row["unit"],
                unit_price=Decimal(row["unit_price"]),
                tax_rate=Decimal(row["tax_rate"]),
                currency=row["currency"],
            )
            pos.setdefault(line.po_number, []).append(line)
    return pos


def _load_ledger() -> list[LedgerEntry]:
    entries: list[LedgerEntry] = []
    with open(DATA_DIR / "ledger.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            entries.append(
                LedgerEntry(
                    ledger_id=row["ledger_id"],
                    vendor_id=row["vendor_id"],
                    invoice_number=row["invoice_number"],
                    invoice_date=_parse_date(row["invoice_date"]),
                    po_number=row["po_number"],
                    po_line_no=int(row["po_line_no"]),
                    qty_billed=Decimal(row["qty_billed"]),
                    line_amount=Decimal(row["line_amount"]),
                    invoice_subtotal=Decimal(row["invoice_subtotal"]),
                    invoice_tax=Decimal(row["invoice_tax"]),
                    invoice_total=Decimal(row["invoice_total"]),
                    status=row["status"],
                    processed_at=_parse_date(row["processed_at"]),
                )
            )
    return entries


def load() -> None:
    """Load all data files into module-level caches. Call once at startup."""
    global _vendors, _po_lines, _ledger
    _vendors = _load_vendors()
    _po_lines = _load_po_lines()
    _ledger = _load_ledger()


def get_vendor(vendor_id: str) -> Vendor | None:
    return _vendors.get(vendor_id)


def all_vendors() -> list[Vendor]:
    return list(_vendors.values())


def open_pos_for_vendor(vendor_id: str) -> list[str]:
    """Returns PO numbers with status 'open' belonging to this vendor."""
    return [
        po_number
        for po_number, lines in _po_lines.items()
        if lines and lines[0].vendor_id == vendor_id and lines[0].status == "open"
    ]


def po_lines(po_number: str) -> list[POLine]:
    return _po_lines.get(po_number, [])


def qty_billed(po_number: str, line_no: int) -> Decimal:
    """Total quantity already billed against a PO line, from the ledger."""
    return sum(
        (e.qty_billed for e in _ledger if e.po_number == po_number and e.po_line_no == line_no),
        Decimal("0"),
    )


def ledger_entries() -> list[LedgerEntry]:
    return list(_ledger)


load()


if __name__ == "__main__":
    print(f"vendors: {len(_vendors)}")
    print(f"purchase orders: {len(_po_lines)} ({sum(len(v) for v in _po_lines.values())} lines)")
    print(f"ledger entries: {len(_ledger)}")
