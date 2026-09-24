# Invoice Processing Agent — Build Guide for Claude Code

Read this file fully before writing code. It is the source of truth for scope, structure and rules.

## What we are building

A web app that takes a vendor invoice (PDF: text-based or scanned) and returns a reasoned decision —
**APPROVE**, **NEEDS_REVIEW**, or **REJECT** — with every stage visible as it runs, and a dashboard of
past runs. This is a take-home case study (Zamp, PS-1). It must run live on a public URL.

## The one principle that overrides everything

**The LLM reads, the code decides.**

- The LLM is used for exactly two things: (1) turning a PDF/image into structured `InvoiceData`,
  (2) writing a plain-English summary of rule results that have already been computed.
- Every check, match, tolerance comparison and the final decision is deterministic Python.
- Never ask the LLM "should this be approved?". Never let LLM output change a decision.
- Same invoice in → same decision out, every time.

## Working rules

- Always keep a working version. Build in the order below; test each stage with a plain script before
  wiring UI.
- Create the LLM client lazily (inside a function), never at import time. A missing `GROQ_API_KEY`
  must return a clean error, not crash startup.
- All LLM calls live in `app/llm.py` only. The rest of the app is provider-agnostic.
- Money: use `Decimal` (or integer paise) for all arithmetic, never float. Currency is INR.
- Missing values are `None` (null), not strings like "Not specified" — rules branch on them.
- Process uploads in memory. Only SQLite (`runs.db`) is written to disk.
- Tolerances and thresholds live in `app/config.py`, never hard-coded in rules.
- Log any new assumption in `ASSUMPTIONS.md`.

## Stack

Python 3.10+, FastAPI, Uvicorn, pdfplumber (text), pypdfium2 (render pages to images for scans),
Groq (text model for extraction + vision-capable model for scans; check current model list),
Pydantic, rapidfuzz (fuzzy matching), sse-starlette (streaming), sqlite3 (stdlib),
reportlab (test invoice generation only). Frontend: vanilla HTML/CSS/JS in `static/`, no build step.

## Folder structure

```
invoice-agent/
├── app/
│   ├── main.py            # routes
│   ├── models.py          # Pydantic models
│   ├── llm.py             # extract_from_text(), extract_from_images(), explain()
│   ├── config.py          # tolerances, thresholds
│   ├── data.py            # loads data/*.csv once at startup
│   ├── db.py              # SQLite run history
│   └── pipeline/
│       ├── runner.py      # orchestrates stages, yields events
│       ├── read.py        # text vs scan detection
│       ├── validate.py
│       ├── match.py       # vendor + PO matching
│       ├── duplicates.py
│       └── decide.py
├── data/                  # vendors.csv, purchase_orders.csv, ledger.csv
├── static/                # index.html (run view), dashboard.html, app.js, styles.css
├── test_invoices/         # generate_invoices.py + generated PDFs
├── ASSUMPTIONS.md
├── README.md
├── requirements.txt
└── .env / .env.example / .gitignore
```

## Data files (system of record)

- `data/vendors.csv` — vendor master. `aliases` is pipe-separated. `status` ∈ approved | blocked.
- `data/purchase_orders.csv` — one row per PO line. `status` ∈ open | closed.
  PO line amount = qty × unit_price. Tax is applied per line at `tax_rate`.
- `data/ledger.csv` — invoices already processed, one row per invoice line (flat ERP-style export;
  invoice header fields repeat on each line). Used for duplicate detection and cumulative billing.

## Routes

- `GET /` — run view (upload + live stages + decision card)
- `GET /dashboard` — run history
- `POST /runs` — accepts file upload, returns `run_id`
- `GET /runs/{run_id}/stream` — Server-Sent Events, one event per stage update
- `GET /api/runs` — list of runs (for dashboard)
- `GET /api/runs/{run_id}` — full stored result of one run
- `GET /health`

## Models (app/models.py)

```python
class LineItem(BaseModel):
    description: str
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None

class InvoiceData(BaseModel):
    vendor_name: str | None
    vendor_gstin: str | None
    invoice_number: str | None
    invoice_date: date | None
    po_reference: str | None
    currency: str | None
    line_items: list[LineItem]
    subtotal: Decimal | None
    tax_amount: Decimal | None
    total: Decimal | None
    tax_inclusive: bool          # True if line prices already include tax
    source: Literal["text", "vision"]

class RuleResult(BaseModel):
    rule_id: str                 # e.g. "PO-05"
    name: str
    status: Literal["pass", "warn", "fail", "skip"]
    message: str                 # human-readable, specific numbers
    evidence: dict               # values compared

class Decision(BaseModel):
    outcome: Literal["APPROVE", "NEEDS_REVIEW", "REJECT"]
    reasons: list[str]           # rule_ids that drove the outcome
    summary: str                 # LLM-written plain English, from rule results
    next_action: str             # e.g. note to approver or vendor
```

## Pipeline stages (runner.py yields an event per stage)

Event shape: `{"stage": str, "status": "running"|"done"|"failed", "data": {...}, "ms": int}`

1. **ingest** — SHA-256 of file bytes. Check against hashes of previous runs (DUP-01).
2. **read** — pdfplumber text per page. If average chars/page < `SCAN_TEXT_THRESHOLD`, treat as scan:
   render pages with pypdfium2 and route to vision extraction. Report which path was taken.
3. **extract** — forced structured output into `InvoiceData`. Return per-field found/missing.
4. **validate** — V-rules (arithmetic and completeness, on the invoice alone).
5. **match_vendor** — VEN-rules against vendors.csv (legal_name + aliases, rapidfuzz `token_sort_ratio`).
6. **match_po** — PO-rules. Explicit reference first; if missing, infer (see below).
7. **duplicates** — DUP-rules against ledger.csv and previous runs.
8. **decide** — apply decision logic, then call `llm.explain()` for summary + next_action.
9. Save full run (input filename, InvoiceData, all RuleResults, Decision, timings) to SQLite.

## Rules

| ID | Check | Fail → |
|---|---|---|
| V-01 | Required fields present: vendor_name, invoice_number, invoice_date, total | warn (review) |
| V-02 | Σ line amounts = subtotal (± `ROUNDING_TOLERANCE`); if `tax_inclusive`, Σ line amounts = total | fail |
| V-03 | subtotal + tax = total (± `ROUNDING_TOLERANCE`) | fail |
| V-04 | invoice_date not in future and ≤ `MAX_INVOICE_AGE_DAYS` old | warn |
| VEN-01 | Vendor matched ≥ `VENDOR_MATCH_AUTO`; between `VENDOR_MATCH_REVIEW` and auto → warn; below → unknown vendor | warn/fail |
| VEN-02 | Vendor status is approved | blocked → **REJECT** |
| PO-01 | PO identified: explicit ref found in POs, or inferred with confidence ≥ `PO_INFER_MIN_CONFIDENCE` | inferred → warn (human confirms); none → fail |
| PO-02 | PO belongs to matched vendor and status is open | fail |
| PO-03 | Each invoice line maps to a PO line (fuzzy on description ≥ `LINE_MATCH_MIN`) | fail |
| PO-04 | Pre-tax unit price within `PRICE_TOLERANCE_PCT` of PO unit price | fail |
| PO-05 | qty already billed (ledger) + qty on this invoice ≤ PO qty, per line | fail, show remaining balance |
| V-05 | Tax amount ≈ Σ(line pre-tax × line tax_rate from PO) | warn |
| DUP-01 | Exact file hash seen before | **REJECT** |
| DUP-02 | Same vendor + normalized invoice number exists in ledger/runs | **REJECT**, cite original |
| DUP-03 | Same vendor + same total + invoice date within `DUP_DATE_WINDOW_DAYS` (different number) | warn |

**Tax-inclusive invoices:** if `tax_inclusive` is True, derive pre-tax unit price =
`unit_price / (1 + tax_rate)` using the PO line's tax_rate before running PO-04.

**Invoice number normalization:** uppercase, take the trailing run of digits, strip leading zeros.
`NWL-2026-0042` → `42`; `NWL/26/42` → `42`. Only compare within the same vendor.

**PO inference (no PO reference):** candidate POs = open POs for the matched vendor. Score each by:
share of invoice lines that fuzzy-match a PO line (weight 0.7) + closeness of invoice subtotal to
PO remaining value (weight 0.3). Report top candidate and score as confidence. Always NEEDS_REVIEW.

## Decision logic (decide.py)

1. Any rule that maps to REJECT failed (VEN-02, DUP-01, DUP-02) → **REJECT**
2. Else any `fail` or `warn` → **NEEDS_REVIEW** (reasons = those rule IDs)
3. Else → **APPROVE**

Precedence matters: a duplicate is rejected even if it would also over-bill its PO.

## Config defaults (app/config.py)

```python
ROUNDING_TOLERANCE = Decimal("1.00")     # INR
PRICE_TOLERANCE_PCT = Decimal("2.0")
MAX_INVOICE_AGE_DAYS = 180
VENDOR_MATCH_AUTO = 90
VENDOR_MATCH_REVIEW = 75
LINE_MATCH_MIN = 70
PO_INFER_MIN_CONFIDENCE = 0.6
DUP_DATE_WINDOW_DAYS = 7
SCAN_TEXT_THRESHOLD = 50                 # avg chars per page
```

## Test invoices and expected outcomes

Generate with `test_invoices/generate_invoices.py` (reportlab). Each must produce the outcome below.
Treat these as acceptance tests: the rules engine is not done until all pass.

| File | Vendor | Scenario | Expected |
|---|---|---|---|
| 01_happy_acme.pdf | Acme Industrial Supplies | Bills PO-2026-0101 exactly (50 helmets @450, 20 vests @280, 10 kits @1200; subtotal 40,100; GST 7,218; total 47,318) | APPROVE |
| 02_split_po_brightline.pdf | Brightline Packaging | Bills 500 boxes @42 on PO-2026-0102. Ledger already has 600 billed of 1,000 | NEEDS_REVIEW (PO-05: over-bills by 100; 400 remaining) |
| 03_duplicate_northwind.pdf | Northwind Logistics | Invoice no. `NWL/26/42`, 14-Aug-2026, 3 FTL @38,500, total 1,29,360. Ledger has `NWL-2026-0042` | REJECT (DUP-02) |
| 04_scanned_no_po_sahyadri.pdf | Sahyadri Office Solutions | Image-only PDF, no PO ref. Paper, toner, markers (matches PO-2026-0104, not 0107) | NEEDS_REVIEW (PO-01 inferred) via vision path |
| 05_price_variance_metro.pdf (stretch) | Metro Electricals | Tax-inclusive prices; LED panel pre-tax 1,508 vs PO 1,450 (+4%) | NEEDS_REVIEW (PO-04) |
| 06_blocked_quantum.pdf (stretch) | Quantum Tech Traders | Otherwise clean invoice vs PO-2026-0106 | REJECT (VEN-02) |

Invoice styling should vary per vendor (layout, fonts, date format, "Qty/Rate" vs "Units/Price")
so extraction is genuinely tested. For 04: generate normally, rasterize at ~150 dpi, apply slight
rotation (~1°) and light noise, save as image-only PDF.

## UI requirements (graded)

**Run view (`/`):** drag-and-drop upload; vertical list of the 8 stages that light up
running → done/failed in real time with elapsed ms; extracted fields panel (flag missing ones);
rule results grouped by stage with pass/warn/fail badges and expandable evidence;
decision card at top once done (outcome colour, summary, next action). Buttons to load
each test invoice in one click, for the demo.

**Dashboard (`/dashboard`):** counts by outcome; table of runs (time, file, vendor, invoice no.,
total, outcome, duration); click a row to open that run's full detail (reuses run view rendering).

## Build order

1. Skeleton app + `/health` deployed to Render with env var working
2. Data loaders + test invoice generator
3. read + extract (script-tested on all invoices)
4. validate, match, duplicates, decide (script-tested — all expected outcomes pass)
5. runner + SSE + run view
6. SQLite + dashboard
7. Deploy, run every test invoice on the live URL

## Token discipline (for Claude Code)

- Do not open files in `test_invoices/*.pdf`, `.venv/`, or `runs.db`. Use the expected-outcomes table instead.
- Read only the files named in the task. Ask before exploring the repo.
- Prefer small edits over rewriting whole files. Don't re-print unchanged code.
- After a change, give a 2-line summary and the command to test it. No long explanations.
