"""Orchestrates read -> extract -> validate -> match_vendor -> match_po -> duplicates -> decide,
yielding one {"stage", "status", "data", "ms"} event per step (CLAUDE.md's event shape), then
persists the run to SQLite. Also holds the in-memory event history + subscriber queues that the
SSE route fans events out through, since the pipeline runs on a background thread while zero or
more clients may be listening on /runs/{run_id}/stream.
"""

import asyncio
import hashlib
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Generator

from app import cache, db
from app.llm import explain, mock_enabled
from app.models import Decision, InvoiceData, RuleResult
from app.pipeline import decide as decide_mod
from app.pipeline import duplicates, match, validate
from app.pipeline.read import read_pdf

STAGE_ORDER = ["ingest", "read", "extract", "validate", "match_vendor", "match_po", "duplicates", "decide"]

_EXTRACT_FIELDS = [
    "vendor_name", "vendor_gstin", "invoice_number", "invoice_date",
    "po_reference", "currency", "subtotal", "tax_amount", "total",
]


def _event(stage: str, status: str, data: dict, ms: int | None) -> dict:
    return {"stage": stage, "status": status, "data": data, "ms": ms}


def _fields_missing(invoice: InvoiceData) -> list[str]:
    missing = [f for f in _EXTRACT_FIELDS if getattr(invoice, f) is None]
    if not invoice.line_items:
        missing.append("line_items")
    return missing


def _fallback_explain(outcome: str, reasons: list[str], rule_results: list[RuleResult]) -> tuple[str, str]:
    # No rule IDs in the prose (they're shown separately as tags) — use each flagged rule's own
    # plain-English, numbers-included message instead.
    flagged_messages = [r.message for r in rule_results if r.rule_id in reasons]
    if flagged_messages:
        summary = " ".join(flagged_messages)
    else:
        summary = "All checks passed."
    next_action = {
        "APPROVE": "Route to AP for payment.",
        "NEEDS_REVIEW": "Review the flagged items above before approving.",
        "REJECT": "Do not pay; investigate the rejection reason above.",
    }[outcome]
    return summary, next_action


def run_pipeline(pdf_bytes: bytes, filename: str, run_id: str) -> Generator[dict, None, None]:
    start = time.perf_counter()
    rule_results: list[RuleResult] = []
    invoice: InvoiceData | None = None
    vendor = None
    line_matches: list[dict] = []

    def elapsed_ms() -> int:
        return round((time.perf_counter() - start) * 1000)

    current_stage = "ingest"

    try:
        # ingest
        yield _event("ingest", "running", {}, None)
        t0 = time.perf_counter()
        file_hash = hashlib.sha256(pdf_bytes).hexdigest()
        prev_hashes = db.previous_hash_records()
        prev_invoices = db.previous_invoices()
        yield _event(
            "ingest", "done",
            {"file_hash": file_hash, "seen_before": file_hash in prev_hashes},
            round((time.perf_counter() - t0) * 1000),
        )

        # read
        current_stage = "read"
        yield _event("read", "running", {}, None)
        t0 = time.perf_counter()
        read_result = read_pdf(pdf_bytes)
        yield _event(
            "read", "done",
            {
                "mode": read_result.mode,
                "page_count": read_result.page_count,
                "avg_chars_per_page": round(read_result.avg_chars_per_page, 1),
            },
            round((time.perf_counter() - t0) * 1000),
        )

        # extract
        current_stage = "extract"
        yield _event("extract", "running", {}, None)
        t0 = time.perf_counter()
        invoice, was_cached = cache.extract_cached(
            file_hash, read_result.mode, read_result.text, read_result.images
        )
        yield _event(
            "extract", "done",
            {
                "source": invoice.source,
                "invoice": invoice.model_dump(mode="json"),
                "fields_missing": _fields_missing(invoice),
                "cached": was_cached,
                "mock": mock_enabled(),
            },
            round((time.perf_counter() - t0) * 1000),
        )

        # validate
        current_stage = "validate"
        yield _event("validate", "running", {}, None)
        t0 = time.perf_counter()
        validate_results = validate.run(invoice)
        rule_results += validate_results
        yield _event(
            "validate", "done",
            {"results": [r.model_dump(mode="json") for r in validate_results]},
            round((time.perf_counter() - t0) * 1000),
        )

        # match_vendor
        current_stage = "match_vendor"
        yield _event("match_vendor", "running", {}, None)
        t0 = time.perf_counter()
        vendor, ven_results = match.match_vendor(invoice)
        rule_results += ven_results
        ven01_evidence = next((r.evidence for r in ven_results if r.rule_id == "VEN-01"), {})
        yield _event(
            "match_vendor", "done",
            {
                "results": [r.model_dump(mode="json") for r in ven_results],
                "vendor_id": vendor.vendor_id if vendor else None,
                "vendor_name": vendor.legal_name if vendor else None,
                "match_score": ven01_evidence.get("score"),
            },
            round((time.perf_counter() - t0) * 1000),
        )

        # match_po
        current_stage = "match_po"
        yield _event("match_po", "running", {}, None)
        t0 = time.perf_counter()
        po_number, po_results, line_matches = match.match_po(invoice, vendor)
        rule_results += po_results
        po01_evidence = next((r.evidence for r in po_results if r.rule_id == "PO-01"), {})
        if not po01_evidence.get("po_number"):
            explicit_or_inferred = "none"
        elif "confidence" in po01_evidence:
            explicit_or_inferred = "inferred"
        else:
            explicit_or_inferred = "explicit"
        yield _event(
            "match_po", "done",
            {
                "results": [r.model_dump(mode="json") for r in po_results],
                "po_number": po_number,
                "explicit_or_inferred": explicit_or_inferred,
                "confidence": po01_evidence.get("confidence"),
                "line_matches": line_matches,
            },
            round((time.perf_counter() - t0) * 1000),
        )

        # duplicates
        current_stage = "duplicates"
        yield _event("duplicates", "running", {}, None)
        t0 = time.perf_counter()
        dup_results = duplicates.check(
            invoice, file_hash, vendor,
            previous_hashes=prev_hashes, previous_invoices=prev_invoices,
        )
        rule_results += dup_results
        yield _event(
            "duplicates", "done",
            {
                "results": [r.model_dump(mode="json") for r in dup_results],
                "fired": [r.rule_id for r in dup_results if r.status in ("warn", "fail")],
            },
            round((time.perf_counter() - t0) * 1000),
        )

        # decide
        current_stage = "decide"
        yield _event("decide", "running", {}, None)
        t0 = time.perf_counter()
        outcome, reasons = decide_mod.decide(rule_results)
        try:
            summary, next_action = explain(invoice, rule_results, outcome, reasons)
        except Exception:
            summary, next_action = _fallback_explain(outcome, reasons, rule_results)
        deterministic_action = decide_mod.deterministic_next_action(rule_results)
        if deterministic_action:
            next_action = deterministic_action
        decision = Decision(outcome=outcome, reasons=reasons, summary=summary, next_action=next_action)
        yield _event(
            "decide", "done",
            {
                "outcome": outcome, "reasons": reasons, "summary": summary,
                "next_action": next_action, "mock": mock_enabled(),
            },
            round((time.perf_counter() - t0) * 1000),
        )

        db.save_run(
            run_id=run_id,
            filename=filename,
            file_hash=file_hash,
            created_at=datetime.now(timezone.utc).isoformat(),
            status="done",
            invoice=invoice,
            rule_results=rule_results,
            decision=decision,
            vendor=vendor,
            duration_ms=elapsed_ms(),
            line_matches=line_matches,
        )
        yield _event("saved", "done", {"run_id": run_id}, elapsed_ms())

    except Exception as e:
        yield _event(current_stage, "failed", {"error": str(e)}, elapsed_ms())
        db.save_run(
            run_id=run_id,
            filename=filename,
            file_hash=hashlib.sha256(pdf_bytes).hexdigest(),
            created_at=datetime.now(timezone.utc).isoformat(),
            status="failed",
            invoice=invoice,
            rule_results=rule_results,
            decision=None,
            vendor=vendor,
            duration_ms=elapsed_ms(),
            error=str(e),
            line_matches=line_matches,
        )


# --- In-memory event history + pub/sub for the SSE route ---------------------------------

_lock = threading.Lock()
_events: dict[str, list[dict]] = {}
_subscribers: dict[str, list[tuple[asyncio.AbstractEventLoop, "asyncio.Queue[dict]"]]] = {}


def _publish(run_id: str, event: dict) -> None:
    with _lock:
        _events.setdefault(run_id, []).append(event)
        subs = list(_subscribers.get(run_id, []))
    for loop, queue in subs:
        loop.call_soon_threadsafe(queue.put_nowait, event)


def get_history(run_id: str) -> list[dict]:
    with _lock:
        return list(_events.get(run_id, []))


def clear_history() -> None:
    """Drops all in-memory event history (used alongside db.reset_runs() for the demo reset)."""
    with _lock:
        _events.clear()


def subscribe(run_id: str, loop: asyncio.AbstractEventLoop) -> "asyncio.Queue[dict]":
    queue: "asyncio.Queue[dict]" = asyncio.Queue()
    with _lock:
        _subscribers.setdefault(run_id, []).append((loop, queue))
    return queue


def unsubscribe(run_id: str, queue: "asyncio.Queue[dict]") -> None:
    with _lock:
        subs = _subscribers.get(run_id, [])
        _subscribers[run_id] = [(l, q) for l, q in subs if q is not queue]


def execute(run_id: str, pdf_bytes: bytes, filename: str) -> None:
    """Runs the full pipeline and publishes each event. Call via run_in_threadpool — this
    function performs blocking I/O (pdfplumber/pypdfium2/Groq)."""
    for event in run_pipeline(pdf_bytes, filename, run_id):
        _publish(run_id, event)
