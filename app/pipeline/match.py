"""VEN-01, VEN-02 (vendor matching) and PO-01..PO-05, V-05 (PO matching).

Vendor matching uses rapidfuzz.fuzz.token_sort_ratio against each vendor's legal_name and
aliases. PO matching either takes an explicit po_reference or infers the PO from the matched
vendor's open POs, then maps invoice lines to PO lines (greedy, 1:1) to drive PO-03/04/05 and V-05.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from rapidfuzz import fuzz

from app import data
from app.config import (
    LINE_MATCH_MIN,
    PO_INFER_MIN_CONFIDENCE,
    PRICE_TOLERANCE_PCT,
    ROUNDING_TOLERANCE,
    VENDOR_MATCH_AUTO,
    VENDOR_MATCH_REVIEW,
)
from app.data import POLine, Vendor
from app.models import InvoiceData, LineItem, RuleResult


def match_vendor(invoice: InvoiceData) -> tuple[Vendor | None, list[RuleResult]]:
    best_vendor: Vendor | None = None
    best_score = 0

    if invoice.vendor_name:
        invoice_name = invoice.vendor_name.lower()
        for vendor in data.all_vendors():
            candidates = [vendor.legal_name, *vendor.aliases]
            score = round(max(fuzz.token_sort_ratio(invoice_name, c.lower()) for c in candidates))
            if score > best_score:
                best_score = score
                best_vendor = vendor

    ven01 = _ven01_vendor_matched(best_vendor, best_score)

    identified_vendor = best_vendor if best_score >= VENDOR_MATCH_REVIEW else None
    ven02 = _ven02_vendor_status(identified_vendor)

    return identified_vendor, [ven01, ven02]


def _ven01_vendor_matched(vendor: Vendor | None, score: int) -> RuleResult:
    evidence = {
        "candidate_vendor_id": vendor.vendor_id if vendor else None,
        "candidate_name": vendor.legal_name if vendor else None,
        "score": score,
    }
    if score >= VENDOR_MATCH_AUTO:
        status: Literal["pass", "warn", "fail"] = "pass"
        message = f"Vendor matched to {vendor.legal_name} (score {score})"
    elif score >= VENDOR_MATCH_REVIEW:
        status = "warn"
        message = f"Vendor tentatively matched to {vendor.legal_name} (score {score}), needs review"
    else:
        status = "fail"
        message = f"Unknown vendor (best candidate score {score})"
    return RuleResult(rule_id="VEN-01", name="Vendor matched", status=status, message=message, evidence=evidence)


def _ven02_vendor_status(vendor: Vendor | None) -> RuleResult:
    if vendor is None:
        return RuleResult(
            rule_id="VEN-02",
            name="Vendor status is approved",
            status="skip",
            message="Vendor not identified, cannot check status",
            evidence={},
        )
    if vendor.status == "approved":
        return RuleResult(
            rule_id="VEN-02",
            name="Vendor status is approved",
            status="pass",
            message=f"Vendor {vendor.legal_name} is approved",
            evidence={"vendor_id": vendor.vendor_id, "status": vendor.status},
        )
    return RuleResult(
        rule_id="VEN-02",
        name="Vendor status is approved",
        status="fail",
        message=f"Vendor {vendor.legal_name} is {vendor.status}",
        evidence={"vendor_id": vendor.vendor_id, "status": vendor.status},
    )


@dataclass
class LinePair:
    invoice_line: LineItem
    po_line: POLine
    score: float


def match_po(invoice: InvoiceData, vendor: Vendor | None) -> tuple[str | None, list[RuleResult]]:
    if vendor is None:
        return None, _skip_po_results("Vendor not identified, cannot match PO")

    po_number, po01 = _po01_identify(invoice, vendor)

    if po_number is None:
        return None, [po01, *_skip_po_results("No PO identified", skip_po01=True)]

    po_lines = data.po_lines(po_number)
    po02 = _po02_belongs_and_open(po_number, po_lines, vendor)

    matched_pairs, unmatched_lines = _match_lines_to_po(invoice.line_items, po_lines)
    po03 = _po03_lines_mapped(unmatched_lines)
    po04 = _po04_price_tolerance(invoice, matched_pairs)
    po05 = _po05_quantity_balance(po_number, matched_pairs)
    v05 = _v05_tax_matches(invoice, matched_pairs)

    return po_number, [po01, po02, po03, po04, po05, v05]


_PO_RULE_NAMES = [
    ("PO-01", "PO identified"),
    ("PO-02", "PO belongs to vendor and is open"),
    ("PO-03", "Invoice lines map to PO lines"),
    ("PO-04", "Unit price within tolerance"),
    ("PO-05", "Quantity within PO balance"),
    ("V-05", "Tax amount matches PO tax rate"),
]


def _skip_po_results(message: str, skip_po01: bool = False) -> list[RuleResult]:
    rule_names = _PO_RULE_NAMES[1:] if skip_po01 else _PO_RULE_NAMES
    return [
        RuleResult(rule_id=rid, name=name, status="skip", message=message, evidence={})
        for rid, name in rule_names
    ]


def _po01_identify(invoice: InvoiceData, vendor: Vendor) -> tuple[str | None, RuleResult]:
    if invoice.po_reference:
        normalized_ref = invoice.po_reference.strip()
        for candidate in (normalized_ref, normalized_ref.upper()):
            lines = data.po_lines(candidate)
            if lines:
                return lines[0].po_number, RuleResult(
                    rule_id="PO-01",
                    name="PO identified",
                    status="pass",
                    message=f"Explicit PO reference {lines[0].po_number} found",
                    evidence={"po_reference": invoice.po_reference, "po_number": lines[0].po_number},
                )

    candidates = data.open_pos_for_vendor(vendor.vendor_id)
    if not candidates:
        return None, RuleResult(
            rule_id="PO-01",
            name="PO identified",
            status="fail",
            message="No PO reference on invoice and no open POs for vendor",
            evidence={"po_reference": invoice.po_reference, "explicit_ref_invalid": bool(invoice.po_reference)},
        )

    best_po, confidence, share, closeness, remaining = _infer_po(invoice, candidates)

    evidence = {
        "po_reference": invoice.po_reference,
        "explicit_ref_invalid": bool(invoice.po_reference),
        "po_number": best_po,
        "confidence": round(confidence, 3),
        "line_match_share": round(share, 3),
        "subtotal_closeness": round(closeness, 3),
        "po_remaining_value": str(remaining),
    }

    if confidence >= PO_INFER_MIN_CONFIDENCE:
        return best_po, RuleResult(
            rule_id="PO-01",
            name="PO identified",
            status="warn",
            message=f"PO inferred as {best_po} (confidence {confidence:.2f}), needs human confirmation",
            evidence=evidence,
        )
    return None, RuleResult(
        rule_id="PO-01",
        name="PO identified",
        status="fail",
        message=f"No PO reference on invoice; best inferred candidate {best_po} confidence {confidence:.2f} too low",
        evidence=evidence,
    )


def _infer_po(invoice: InvoiceData, candidates: list[str]) -> tuple[str, float, float, float, Decimal]:
    best_po = candidates[0]
    best_confidence = -1.0
    best_share = 0.0
    best_closeness = 0.0
    best_remaining = Decimal("0")

    for po_number in candidates:
        lines = data.po_lines(po_number)

        if invoice.line_items:
            matched_count = 0
            for li in invoice.line_items:
                best_line_score = max(
                    (fuzz.WRatio(li.description, pl.description) for pl in lines), default=0
                )
                if best_line_score >= LINE_MATCH_MIN:
                    matched_count += 1
            share = matched_count / len(invoice.line_items)
        else:
            share = 0.0

        remaining = sum(
            ((pl.qty - data.qty_billed(po_number, pl.line_no)) * pl.unit_price for pl in lines),
            start=Decimal("0"),
        )

        subtotal = invoice.subtotal if invoice.subtotal is not None else Decimal("0")
        denom = max(subtotal, remaining, Decimal("0.01"))
        closeness = float(max(Decimal("0"), min(Decimal("1"), 1 - abs(subtotal - remaining) / denom)))

        confidence = 0.7 * share + 0.3 * closeness

        if confidence > best_confidence:
            best_confidence = confidence
            best_po = po_number
            best_share = share
            best_closeness = closeness
            best_remaining = remaining

    return best_po, best_confidence, best_share, best_closeness, best_remaining


def _po02_belongs_and_open(po_number: str, po_lines: list[POLine], vendor: Vendor) -> RuleResult:
    header = po_lines[0]
    evidence = {"po_number": po_number, "po_vendor_id": header.vendor_id, "vendor_id": vendor.vendor_id, "status": header.status}
    if header.vendor_id == vendor.vendor_id and header.status == "open":
        return RuleResult(
            rule_id="PO-02",
            name="PO belongs to vendor and is open",
            status="pass",
            message=f"{po_number} belongs to {vendor.legal_name} and is open",
            evidence=evidence,
        )
    return RuleResult(
        rule_id="PO-02",
        name="PO belongs to vendor and is open",
        status="fail",
        message=f"{po_number} vendor/status mismatch (po_vendor={header.vendor_id}, status={header.status})",
        evidence=evidence,
    )


def _match_lines_to_po(invoice_lines: list[LineItem], po_lines: list[POLine]) -> tuple[list[LinePair], list[LineItem]]:
    unclaimed = list(po_lines)
    pairs: list[LinePair] = []
    unmatched: list[LineItem] = []

    for li in invoice_lines:
        if not unclaimed:
            unmatched.append(li)
            continue
        scored = [(fuzz.WRatio(li.description, pl.description), pl) for pl in unclaimed]
        best_score, best_pl = max(scored, key=lambda x: x[0])
        if best_score >= LINE_MATCH_MIN:
            pairs.append(LinePair(invoice_line=li, po_line=best_pl, score=best_score))
            unclaimed.remove(best_pl)
        else:
            unmatched.append(li)

    return pairs, unmatched


def _po03_lines_mapped(unmatched_lines: list[LineItem]) -> RuleResult:
    if unmatched_lines:
        return RuleResult(
            rule_id="PO-03",
            name="Invoice lines map to PO lines",
            status="fail",
            message=f"{len(unmatched_lines)} line(s) did not match any PO line",
            evidence={"unmatched_descriptions": [li.description for li in unmatched_lines]},
        )
    return RuleResult(
        rule_id="PO-03",
        name="Invoice lines map to PO lines",
        status="pass",
        message="All invoice lines matched to PO lines",
        evidence={},
    )


def _pre_tax_unit_price(invoice: InvoiceData, pair: LinePair) -> Decimal | None:
    if pair.invoice_line.unit_price is None:
        return None
    if invoice.tax_inclusive:
        return pair.invoice_line.unit_price / (1 + pair.po_line.tax_rate)
    return pair.invoice_line.unit_price


def _po04_price_tolerance(invoice: InvoiceData, pairs: list[LinePair]) -> RuleResult:
    if not pairs:
        return RuleResult(
            rule_id="PO-04",
            name="Unit price within tolerance",
            status="skip",
            message="No matched lines to compare",
            evidence={},
        )

    worst_pct = Decimal("0")
    worst_evidence = None
    any_fail = False

    for pair in pairs:
        pre_tax = _pre_tax_unit_price(invoice, pair)
        if pre_tax is None or pair.po_line.unit_price == 0:
            continue
        pct_delta = abs(pre_tax - pair.po_line.unit_price) / pair.po_line.unit_price * 100
        if pct_delta > worst_pct:
            worst_pct = pct_delta
            worst_evidence = {
                "description": pair.invoice_line.description,
                "invoice_pre_tax_unit_price": str(pre_tax),
                "po_unit_price": str(pair.po_line.unit_price),
                "pct_delta": str(round(pct_delta, 2)),
            }
        if pct_delta > PRICE_TOLERANCE_PCT:
            any_fail = True

    if any_fail:
        return RuleResult(
            rule_id="PO-04",
            name="Unit price within tolerance",
            status="fail",
            message=f"Price variance {worst_evidence['pct_delta']}% exceeds tolerance {PRICE_TOLERANCE_PCT}%",
            evidence=worst_evidence or {},
        )
    return RuleResult(
        rule_id="PO-04",
        name="Unit price within tolerance",
        status="pass",
        message="All matched line prices within tolerance",
        evidence={"worst_pct_delta": str(round(worst_pct, 2))},
    )


def _po05_quantity_balance(po_number: str, pairs: list[LinePair]) -> RuleResult:
    if not pairs:
        return RuleResult(
            rule_id="PO-05",
            name="Quantity within PO balance",
            status="skip",
            message="No matched lines to compare",
            evidence={},
        )

    overages = []
    for pair in pairs:
        if pair.invoice_line.quantity is None:
            continue
        already = data.qty_billed(po_number, pair.po_line.line_no)
        remaining = pair.po_line.qty - already
        if pair.invoice_line.quantity > remaining:
            overages.append(
                {
                    "description": pair.invoice_line.description,
                    "po_line_no": pair.po_line.line_no,
                    "already_billed": str(already),
                    "po_qty": str(pair.po_line.qty),
                    "remaining_before_this_invoice": str(remaining),
                    "invoice_qty": str(pair.invoice_line.quantity),
                    "overage": str(pair.invoice_line.quantity - remaining),
                }
            )

    if overages:
        return RuleResult(
            rule_id="PO-05",
            name="Quantity within PO balance",
            status="fail",
            message=f"{len(overages)} line(s) over-bill their PO balance",
            evidence={"overages": overages},
        )
    return RuleResult(
        rule_id="PO-05",
        name="Quantity within PO balance",
        status="pass",
        message="All matched line quantities within PO balance",
        evidence={},
    )


def _v05_tax_matches(invoice: InvoiceData, pairs: list[LinePair]) -> RuleResult:
    if not pairs or invoice.tax_amount is None:
        return RuleResult(
            rule_id="V-05",
            name="Tax amount matches PO tax rate",
            status="skip",
            message="No matched lines or tax_amount missing",
            evidence={},
        )

    expected_tax = Decimal("0")
    for pair in pairs:
        pre_tax = _pre_tax_unit_price(invoice, pair)
        if pre_tax is None or pair.invoice_line.quantity is None:
            continue
        expected_tax += pre_tax * pair.invoice_line.quantity * pair.po_line.tax_rate

    evidence = {"expected_tax": str(expected_tax), "invoice_tax_amount": str(invoice.tax_amount)}
    if abs(expected_tax - invoice.tax_amount) <= ROUNDING_TOLERANCE:
        return RuleResult(
            rule_id="V-05",
            name="Tax amount matches PO tax rate",
            status="pass",
            message=f"Tax amount ({invoice.tax_amount}) matches expected ({expected_tax})",
            evidence=evidence,
        )
    return RuleResult(
        rule_id="V-05",
        name="Tax amount matches PO tax rate",
        status="warn",
        message=f"Tax amount ({invoice.tax_amount}) differs from expected ({expected_tax})",
        evidence=evidence,
    )
