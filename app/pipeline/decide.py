"""Aggregates RuleResults into an outcome + driving rule_ids. Pure, no LLM call —
Decision.summary/next_action are filled later by a runner calling llm.explain()."""

from typing import Literal

from app.models import RuleResult

REJECT_RULE_IDS = {"VEN-02", "DUP-01", "DUP-02"}


def decide(rule_results: list[RuleResult]) -> tuple[Literal["APPROVE", "NEEDS_REVIEW", "REJECT"], list[str]]:
    rejects = [r.rule_id for r in rule_results if r.rule_id in REJECT_RULE_IDS and r.status == "fail"]
    if rejects:
        return "REJECT", rejects

    flagged = [r.rule_id for r in rule_results if r.status in ("fail", "warn")]
    if flagged:
        return "NEEDS_REVIEW", flagged

    return "APPROVE", []


def deterministic_next_action(rule_results: list[RuleResult]) -> str | None:
    """Overrides the LLM's next_action for the duplicate rules, whose correct wording depends on
    a distinction (same-file internal re-upload vs. a genuine vendor resubmission against the
    ledger, vs. a duplicate against an earlier run) that the LLM was getting wrong when DUP-01
    and DUP-02 fired together — it defaulted to "notify the vendor" even for an internal re-upload.
    Returns None when neither duplicate rule fired, so the caller keeps the LLM's next_action."""
    by_id = {r.rule_id: r for r in rule_results}

    dup01 = by_id.get("DUP-01")
    if dup01 is not None and dup01.status == "fail":
        return "No action needed. Open the earlier run to confirm, then discard this upload."

    dup02 = by_id.get("DUP-02")
    if dup02 is not None and dup02.status == "fail":
        if dup02.evidence.get("source") == "run":
            return "No action needed. Open the earlier run to confirm this is the same invoice."
        original_number = dup02.evidence.get("original_invoice_number") or "the earlier invoice"
        original_date = dup02.evidence.get("original_invoice_date")
        when = f" dated {original_date}" if original_date else ""
        return (
            f"Notify the vendor about the duplicate invoice number (matches {original_number}"
            f"{when}) and confirm no payment is made."
        )

    return None
