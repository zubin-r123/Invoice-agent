"""V-01..V-04: invoice-only checks (arithmetic, completeness). No PO/vendor context.

V-05 (tax amount vs PO tax_rate) needs the matched PO's per-line tax_rate and lives in
match.py's PO stage instead.
"""

from datetime import date
from decimal import Decimal

from app.config import MAX_INVOICE_AGE_DAYS, ROUNDING_TOLERANCE
from app.models import InvoiceData, RuleResult


def run(invoice: InvoiceData, today: date | None = None) -> list[RuleResult]:
    return [
        _v01_required_fields(invoice),
        _v02_line_sum(invoice),
        _v03_subtotal_tax_total(invoice),
        _v04_invoice_age(invoice, today or date.today()),
    ]


def _v01_required_fields(invoice: InvoiceData) -> RuleResult:
    required = {
        "vendor_name": invoice.vendor_name,
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date,
        "total": invoice.total,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        return RuleResult(
            rule_id="V-01",
            name="Required fields present",
            status="warn",
            message=f"Missing required field(s): {', '.join(missing)}",
            evidence={"missing_fields": missing},
        )
    return RuleResult(
        rule_id="V-01",
        name="Required fields present",
        status="pass",
        message="All required fields present",
        evidence={},
    )


def _v02_line_sum(invoice: InvoiceData) -> RuleResult:
    compare_to = invoice.total if invoice.tax_inclusive else invoice.subtotal
    label = "total" if invoice.tax_inclusive else "subtotal"

    if compare_to is None:
        return RuleResult(
            rule_id="V-02",
            name="Line items sum to subtotal",
            status="skip",
            message=f"{label.capitalize()} missing, cannot compare",
            evidence={},
        )

    line_sum = sum((li.amount for li in invoice.line_items if li.amount is not None), start=Decimal("0"))

    evidence = {
        "line_sum": str(line_sum),
        "compared_to": label,
        "value": str(compare_to),
        "tax_inclusive": invoice.tax_inclusive,
    }

    if abs(line_sum - compare_to) <= ROUNDING_TOLERANCE:
        return RuleResult(
            rule_id="V-02",
            name="Line items sum to subtotal",
            status="pass",
            message=f"Line items sum ({line_sum}) matches {label} ({compare_to})",
            evidence=evidence,
        )
    return RuleResult(
        rule_id="V-02",
        name="Line items sum to subtotal",
        status="fail",
        message=f"Line items sum ({line_sum}) does not match {label} ({compare_to})",
        evidence=evidence,
    )


def _v03_subtotal_tax_total(invoice: InvoiceData) -> RuleResult:
    if invoice.subtotal is None or invoice.tax_amount is None or invoice.total is None:
        return RuleResult(
            rule_id="V-03",
            name="Subtotal + tax = total",
            status="skip",
            message="Subtotal, tax_amount, or total missing, cannot compare",
            evidence={},
        )

    computed_total = invoice.subtotal + invoice.tax_amount
    evidence = {
        "subtotal": str(invoice.subtotal),
        "tax_amount": str(invoice.tax_amount),
        "computed_total": str(computed_total),
        "total": str(invoice.total),
    }
    if abs(computed_total - invoice.total) <= ROUNDING_TOLERANCE:
        return RuleResult(
            rule_id="V-03",
            name="Subtotal + tax = total",
            status="pass",
            message=f"Subtotal + tax ({computed_total}) matches total ({invoice.total})",
            evidence=evidence,
        )
    return RuleResult(
        rule_id="V-03",
        name="Subtotal + tax = total",
        status="fail",
        message=f"Subtotal + tax ({computed_total}) does not match total ({invoice.total})",
        evidence=evidence,
    )


def _v04_invoice_age(invoice: InvoiceData, today: date) -> RuleResult:
    if invoice.invoice_date is None:
        return RuleResult(
            rule_id="V-04",
            name="Invoice date within range",
            status="skip",
            message="Invoice date missing, cannot check",
            evidence={},
        )

    age_days = (today - invoice.invoice_date).days
    evidence = {"invoice_date": invoice.invoice_date.isoformat(), "today": today.isoformat(), "age_days": age_days}

    if age_days < 0:
        return RuleResult(
            rule_id="V-04",
            name="Invoice date within range",
            status="warn",
            message=f"Invoice date ({invoice.invoice_date.isoformat()}) is in the future",
            evidence=evidence,
        )
    if age_days > MAX_INVOICE_AGE_DAYS:
        return RuleResult(
            rule_id="V-04",
            name="Invoice date within range",
            status="warn",
            message=f"Invoice is {age_days} days old, exceeds max age of {MAX_INVOICE_AGE_DAYS} days",
            evidence=evidence,
        )
    return RuleResult(
        rule_id="V-04",
        name="Invoice date within range",
        status="pass",
        message=f"Invoice is {age_days} days old",
        evidence=evidence,
    )
