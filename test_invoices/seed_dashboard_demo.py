"""Temporary dev/test script — seeds runs.db with fake data for visual QA of the dashboard
restyle. Not part of the graded app; safe to delete. Clean up seeded rows afterward via the
"Reset demo" button in the nav bar (POST /api/reset).

Usage: python test_invoices/seed_dashboard_demo.py
"""

import sys
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.models import Decision, InvoiceData, LineItem, RuleResult

FAIL = {
    "PO-04": "Price within tolerance",
    "DUP-02": "Duplicate invoice number",
    "VEN-02": "Vendor is approved",
    "V-02": "Line items sum to subtotal",
    "PO-05": "Quantity within PO balance",
    "V-04": "Invoice not too old",
}


def rule(rule_id, status="fail", message=""):
    return RuleResult(
        rule_id=rule_id,
        name=FAIL[rule_id],
        status=status,
        message=message or f"{FAIL[rule_id]} check failed.",
        evidence={},
    )


def invoice(vendor_name, invoice_number, total, days_ago):
    total = Decimal(total)
    return InvoiceData(
        vendor_name=vendor_name,
        vendor_gstin=None,
        invoice_number=invoice_number,
        invoice_date=date.today() - timedelta(days=days_ago),
        po_reference=None,
        currency="INR",
        line_items=[LineItem(description="Goods", quantity=Decimal(1), unit_price=total, amount=total)],
        subtotal=total,
        tax_amount=Decimal("0"),
        total=total,
        tax_inclusive=False,
        source="text",
    )


SEEDS = [
    ("Acme Industrial Supplies", "ACM-1001", "47318.00", 1, "APPROVE", []),
    ("Acme Industrial Supplies", "ACM-1002", "12500.00", 3, "APPROVE", []),
    ("Brightline Packaging", "BPK-2201", "21000.00", 2, "NEEDS_REVIEW", ["PO-05"]),
    ("Metro Electricals", "MET-330", "15080.00", 5, "NEEDS_REVIEW", ["PO-04"]),
    ("Sahyadri Office Solutions", "SAH-77", "8420.00", 6, "NEEDS_REVIEW", ["V-04"]),
    ("Northwind Logistics", "NWL-2026-0099", "129360.00", 0, "REJECT", ["DUP-02"]),
    ("Quantum Tech Traders", "QTT-410", "56000.00", 4, "REJECT", ["VEN-02"]),
    ("Brightline Packaging", "BPK-2205", "9800.00", 7, "NEEDS_REVIEW", ["V-02"]),
]


def main():
    db.init_db()
    for vendor_name, invoice_number, total, days_ago, outcome, fail_rules in SEEDS:
        inv = invoice(vendor_name, invoice_number, total, days_ago)
        rule_results = [rule(rid) for rid in fail_rules]
        decision = Decision(
            outcome=outcome,
            reasons=fail_rules,
            summary=(
                f"{outcome.replace('_', ' ').title()}: " + ", ".join(FAIL[r] for r in fail_rules)
                if fail_rules
                else "All checks passed."
            ),
            next_action="Review before payment." if outcome != "APPROVE" else "None — schedule for payment.",
        )
        db.save_run(
            run_id=uuid.uuid4().hex,
            filename=f"{invoice_number}.pdf",
            file_hash=uuid.uuid4().hex,
            created_at=(datetime.now() - timedelta(days=days_ago)).isoformat(),
            status="done",
            invoice=inv,
            rule_results=rule_results,
            decision=decision,
            vendor=None,
            duration_ms=1200 + days_ago * 37,
        )
    print(f"Seeded {len(SEEDS)} fake runs into runs.db.")


if __name__ == "__main__":
    main()
