"""SQLite run history (runs.db). Only file written to disk besides the source data/*.csv."""

import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

from app.data import Vendor
from app.models import Decision, InvoiceData, RuleResult
from app.pipeline.duplicates import DupRecord, HashRecord, normalize_invoice_number

DB_PATH = Path(__file__).resolve().parent.parent / "runs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    outcome TEXT,
    vendor_id TEXT,
    vendor_name TEXT,
    invoice_number TEXT,
    invoice_number_norm TEXT,
    invoice_date TEXT,
    total TEXT,
    duration_ms INTEGER,
    invoice_json TEXT,
    rule_results_json TEXT,
    decision_json TEXT,
    line_matches_json TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_hash ON runs(file_hash);
CREATE INDEX IF NOT EXISTS idx_runs_vendor_invnum ON runs(vendor_id, invoice_number_norm);
CREATE INDEX IF NOT EXISTS idx_runs_vendor_total_date ON runs(vendor_id, total, invoice_date);
"""


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        try:
            conn.execute("ALTER TABLE runs ADD COLUMN line_matches_json TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists (runs.db created before this field was added)


def reset_runs() -> None:
    """Clears all saved runs (runs.db only). Never touches data/*.csv (vendors, POs, ledger)."""
    with _connect() as conn:
        conn.execute("DELETE FROM runs")


def save_run(
    run_id: str,
    filename: str,
    file_hash: str,
    created_at: str,
    status: str,
    invoice: InvoiceData | None,
    rule_results: list[RuleResult],
    decision: Decision | None,
    vendor: Vendor | None,
    duration_ms: int,
    error: str | None = None,
    line_matches: list[dict] | None = None,
) -> None:
    invoice_number = invoice.invoice_number if invoice else None
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO runs (
                run_id, filename, file_hash, created_at, status, outcome,
                vendor_id, vendor_name, invoice_number, invoice_number_norm,
                invoice_date, total, duration_ms, invoice_json, rule_results_json,
                decision_json, line_matches_json, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                filename,
                file_hash,
                created_at,
                status,
                decision.outcome if decision else None,
                vendor.vendor_id if vendor else None,
                (invoice.vendor_name if invoice else None) or (vendor.legal_name if vendor else None),
                invoice_number,
                normalize_invoice_number(invoice_number),
                invoice.invoice_date.isoformat() if invoice and invoice.invoice_date else None,
                str(invoice.total) if invoice and invoice.total is not None else None,
                duration_ms,
                invoice.model_dump_json() if invoice else None,
                json.dumps([r.model_dump(mode="json") for r in rule_results]),
                decision.model_dump_json() if decision else None,
                json.dumps(line_matches or []),
                error,
            ),
        )


def get_run(run_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def list_runs() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT run_id, created_at, filename, vendor_name, invoice_number,
                   total, outcome, status, duration_ms
            FROM runs ORDER BY created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def previous_hash_records() -> dict[str, HashRecord]:
    """Maps file_hash -> the earlier run that produced it, so DUP-01 can name it (uses the first
    run seen for each hash, i.e. the original submission, not a later duplicate)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT run_id, filename, file_hash, invoice_number, created_at, outcome, status "
            "FROM runs ORDER BY created_at ASC"
        ).fetchall()
    records: dict[str, HashRecord] = {}
    for row in rows:
        if row["file_hash"] in records:
            continue
        records[row["file_hash"]] = HashRecord(
            run_id=row["run_id"],
            filename=row["filename"],
            invoice_number=row["invoice_number"],
            created_at=row["created_at"],
            outcome=row["outcome"] or row["status"],
        )
    return records


def previous_invoices() -> list[DupRecord]:
    from datetime import date as _date

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT run_id, vendor_id, invoice_number, invoice_date, total, created_at, outcome, status
            FROM runs
            WHERE vendor_id IS NOT NULL AND invoice_number IS NOT NULL
                  AND invoice_date IS NOT NULL AND total IS NOT NULL
            """
        ).fetchall()
    return [
        DupRecord(
            vendor_id=row["vendor_id"],
            invoice_number=row["invoice_number"],
            invoice_date=_date.fromisoformat(row["invoice_date"]),
            total=Decimal(row["total"]),
            source="run",
            run_id=row["run_id"],
            created_at=row["created_at"],
            outcome=row["outcome"] or row["status"],
        )
        for row in rows
    ]


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["invoice"] = json.loads(d.pop("invoice_json")) if d.get("invoice_json") else None
    d["rule_results"] = json.loads(d.pop("rule_results_json")) if d.get("rule_results_json") else []
    d["decision"] = json.loads(d.pop("decision_json")) if d.get("decision_json") else None
    d["line_matches"] = json.loads(d.pop("line_matches_json")) if d.get("line_matches_json") else []
    return d
