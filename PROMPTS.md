# Claude Code prompts — one per phase

Run `/clear` before each phase. CLAUDE.md reloads automatically, so each phase starts lean.
Use plan mode (Shift+Tab) for phases 3, 4 and 5: approve the plan, then let it write.
When something fails, paste only the last ~20 lines of the error, not the whole log.

---

## Phase 1 — Skeleton + deploy (0:00–0:45)

```
Read CLAUDE.md. Create only: app/main.py (FastAPI with GET /health returning {"status":"ok"} and
GET / serving static/index.html), app/config.py (defaults from CLAUDE.md), app/llm.py with a lazy
get_client() that raises a clear error if GROQ_API_KEY is missing, a placeholder static/index.html,
requirements.txt, .env.example, .gitignore, and render.yaml (start command:
uvicorn app.main:app --host 0.0.0.0 --port $PORT). Nothing else yet.
```

Then: push to GitHub, deploy on Render, set GROQ_API_KEY, confirm /health works on the live URL.

## Phase 2 — Data layer (0:45–1:15)

```
Implement app/data.py: load data/vendors.csv, data/purchase_orders.csv, data/ledger.csv once at
startup into typed structures (Decimal for money, date for dates, aliases split on "|").
Add helpers: get_vendor(id), open_pos_for_vendor(vendor_id), po_lines(po_number),
qty_billed(po_number, line_no) from the ledger. Add a tiny __main__ that prints counts.
Don't read the PDFs.
```

Then run `python test_invoices/generate_invoices.py` yourself.

## Phase 3 — Read + Extract (1:15–2:30)

```
Implement app/models.py (all models from CLAUDE.md), app/pipeline/read.py (pdfplumber text;
if below SCAN_TEXT_THRESHOLD, render pages to PNG bytes with pypdfium2), and in app/llm.py:
extract_from_text() and extract_from_images() returning InvoiceData via forced JSON output.
Prompt rules: null for anything not on the invoice, never guess; keep invoice_number exactly as
printed; set tax_inclusive true only if the invoice says rates include tax.
Then write scripts/try_extract.py that runs every PDF in test_invoices/ and prints a compact
one-line summary per file (source, vendor, invoice_number, po_reference, total, line count).
```

Check: file 04 says `source=vision`, and all totals match the table in CLAUDE.md.

## Phase 4 — Rules engine (2:30–3:30), the most important phase

```
Implement app/pipeline/validate.py, match.py, duplicates.py, decide.py exactly per the Rules,
PO inference, normalization and Decision logic sections of CLAUDE.md. Each rule returns a
RuleResult with specific numbers in message and evidence. Pure functions, no LLM calls.
Then write scripts/acceptance.py: for each test invoice, run extract + rules and print
file | expected | actual | driving rule_ids | PASS/FAIL, using the expected-outcomes table.
Stop and show me the output.
```

Loop with: `Fix only the failing case(s). Don't touch passing logic.` until all 6 pass.

## Phase 5 — Streaming + run view (3:30–4:45)

```
Implement app/pipeline/runner.py as a generator yielding the stage events defined in CLAUDE.md,
then POST /runs and GET /runs/{run_id}/stream (sse-starlette). Add llm.explain(rule_results) that
returns summary + next_action from already-computed results only. Build static/index.html +
app.js + styles.css per the UI requirements (run view). Clean, professional finance-tool look:
neutral palette, green/amber/red only for outcomes, one-click buttons for the 6 test invoices
(serve test_invoices/ read-only at /samples).
```

## Phase 6 — History + dashboard (4:45–5:30)

```
Implement app/db.py (SQLite: runs table storing file name, hash, outcome, vendor, invoice number,
total, duration_ms, created_at, full JSON result). Save each run at the end of runner.py. Add
GET /api/runs, GET /api/runs/{id}, and static/dashboard.html per UI requirements, reusing the
run-view rendering for the detail view. Make DUP-01 and DUP-02 also check previous runs in the DB.
```

## Phase 7 — Deploy + rehearse (5:30–6:00)

```
Review the repo for deploy issues on Render free tier: paths relative to the repo root,
no writes outside runs.db, requirements complete, lazy LLM client. List problems only; don't
refactor.
```

Then run all 6 invoices on the live URL, in demo order: 01, 02, 03, 04 (05 and 06 if built).
