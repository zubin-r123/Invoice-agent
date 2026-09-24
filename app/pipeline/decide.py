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
