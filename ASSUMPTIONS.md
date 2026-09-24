# Assumptions

## Phase 3 — Read + Extract

- **Groq model IDs** (`GROQ_TEXT_MODEL`, `GROQ_VISION_MODEL` in `app/config.py`) were verified
  against a live `client.models.list()` call on 2026-09-24, not guessed from training knowledge.
  At that time the account had access to `openai/gpt-oss-120b` (text; `json_mode` +
  `structured_outputs` support) for extraction and `qwen/qwen3.8-27b` (the only model in the list
  with `image` in `input_modalities`) for the vision path. Re-run `client.models.list()` if these
  ever 404 — Groq's hosted lineup changes.
- Model IDs live in `config.py` even though they're provider identifiers, not tolerances/thresholds
  — done so they stay a one-line change and `app/llm.py` has no hardcoded provider strings.
- `response_format={"type": "json_object"}` (Groq's JSON mode) only guarantees syntactically valid
  JSON, not the exact `InvoiceData` schema. Schema correctness is enforced by the system prompt's
  explicit key list plus Pydantic validation after the call, not by provider-side schema
  enforcement.
- Confirmed via a live run: `response_format=json_object` works fine together with image content
  blocks on `qwen/qwen3.8-27b` — no fallback was needed for the vision path.
- Scanned pages are rendered at 150 DPI (`app/pipeline/read.py`), matching the DPI used to
  rasterize the scanned test invoice, not an empirically tuned value.
- `app/models.py` defensively strips thousands-separator commas and currency symbols
  (`,`, `₹`, `Rs.`, `Rs`, `INR`) before parsing amounts as `Decimal`, and tries several date formats
  before ISO, because the LLM may not perfectly follow the prompt's plain-decimal/ISO-date
  instructions when reading Indian-formatted invoices.
- An unparseable `invoice_date` from the LLM is treated as `None` (same as missing) rather than
  raising, since downstream rules already branch on `None`.
- Malformed JSON or a schema-violating LLM response raises (`ValueError` / Pydantic
  `ValidationError`) rather than retrying — there's a fixed 6-file test set, so failures are meant
  to be fixed by adjusting the prompt or model choice, not silently caught at runtime.
- `InvoiceData.source` is set by the calling function (`extract_from_text` → `"text"`,
  `extract_from_images` → `"vision"`), never taken from the LLM's own JSON output.

## Phase 4 — Rules engine

- `data/purchase_orders.csv`'s `tax_rate` column is a fraction (e.g. `0.18` for 18% GST), not a
  percentage — confirmed against test invoice 1's numbers (7218 / 40100 = 0.18). All tax-rate math
  in `match.py` multiplies directly rather than dividing by 100.
- Vendor fuzzy-match ties (equal `token_sort_ratio` score across two vendors) are broken by
  `vendors.csv` row order (first one wins), since the spec doesn't cover ties.
- An explicit `po_reference` on the invoice that doesn't match any real `po_number` falls back to
  PO inference rather than failing PO-01 outright — the rules table only distinguishes
  found/inferred/none, not "found but invalid." The invalid printed reference is still recorded in
  evidence (`explicit_ref_invalid`).
- Invoice-line-to-PO-line matching (feeding PO-03/04/05/V-05) is greedy and exclusive: each invoice
  line claims its best-scoring *unclaimed* PO line (score ≥ `LINE_MATCH_MIN`), one PO line matches
  at most one invoice line. Not an optimal (Hungarian-style) assignment — simple and deterministic,
  and the spec gives no tie-breaking guidance.
- PO inference's "share of invoice lines that fuzzy-match a PO line" reuses `LINE_MATCH_MIN` as the
  per-line match threshold rather than introducing a second, unconfigured constant.
- V-05's expected-tax sum only includes invoice lines that matched a PO line; lines PO-03 already
  flagged as unmatched are excluded rather than double-counted.
- `validate.run` takes an optional `today: date | None` parameter (defaults to `date.today()`),
  not in the CLAUDE.md spec, purely so V-04 can be tested deterministically without depending on
  the real calendar date.
- V-02, V-03, and V-05 return `skip` (not `fail`) when a required input field is `None` (e.g.
  missing `subtotal`/`tax_amount`), since a missing value is already flagged by V-01 (or PO-03 for
  unmatched lines) rather than being double-counted as a second failure.
- DUP-01 (file-hash reuse) and the "previous runs" half of DUP-02/DUP-03 have no real store yet —
  `app/db.py` (SQLite run history) doesn't exist until a later phase. `duplicates.check()` takes
  `previous_hashes`/`previous_invoices` as parameters defaulting to empty, so it's fully testable
  against `ledger.csv` now and wires up to real run history later without a signature change.
- **V-02's tax-inclusive clause replaces the comparison, it doesn't add to it.** Read literally,
  "Σ line amounts = subtotal; if tax_inclusive, Σ line amounts = total" could mean "check both."
  Verified against test invoice 5 (tax-inclusive, `line_sum == total` exactly, `line_sum != subtotal`
  by design since subtotal is the pre-tax figure): checking both would make every tax-inclusive
  invoice fail V-02, which can't be intended since invoice 5's only expected reason is PO-04. So
  when `tax_inclusive` is true, V-02 compares line-sum to `total` only; otherwise to `subtotal` only.
- Vendor-name and line-description fuzzy matching lowercase both sides before scoring.
  `rapidfuzz.fuzz.*` is case-sensitive, and OCR/vision extraction of an all-caps printed name
  (confirmed on test invoice 4, `"SAHYADRI OFFICE SOLUTIONS"` vs `vendors.csv`'s
  `"Sahyadri Office Solutions"`) or an all-caps line item otherwise tanks the score for no
  substantive reason.
- Line-item-to-PO-line matching (PO-03/04/05, V-05, and the PO-inference share) uses
  `rapidfuzz.fuzz.WRatio`, not `token_sort_ratio`. CLAUDE.md specifies `token_sort_ratio`
  explicitly only for vendor matching; for line descriptions it just says "fuzzy on description."
  `token_sort_ratio` scored test invoice 4's "A4 copier paper 75 GSM ream" vs the PO's "A4 copier
  paper 75gsm (ream)" at 65 (below `LINE_MATCH_MIN=70`) purely because "75gsm" is one token on the
  PO and two on the invoice; `WRatio` handles that kind of partial/reworded overlap and scores it
  above threshold, matching the intent of the test case (that invoice is described as fully billing
  PO-2026-0104's three lines).

## Phase 5 — Runner, SSE, DB, run view + dashboard

- `POST /runs` generates `run_id` immediately and starts the pipeline as an `asyncio.create_task`
  wrapping `run_in_threadpool` (blocking pdfplumber/pypdfium2/Groq calls run off the event loop).
  Progress is fanned out through an in-memory, per-`run_id` event history + subscriber-queue store
  in `app/pipeline/runner.py` — not durable, and lost on process restart. This is acceptable
  because SQLite (`runs.db`) is the durable record; a live SSE reconnect after a restart just gets
  no history, and `/api/runs/{run_id}` remains the source of truth for a completed run.
- The runner yields a 9th synthetic event, `{"stage": "saved", "status": "done", ...}`, after
  `decide`'s `done` event, once the run has actually been persisted to SQLite. This is the SSE
  stream's real termination signal (along with any `status: "failed"`) — it is not one of the 8
  stages shown in the UI stepper, since CLAUDE.md's UI spec is explicit about "8 stages."
- `RuleResult` has no `stage` field (CLAUDE.md defines its fields exactly, and rule stage is
  fully determined by `rule_id`). Grouping rule results by stage — needed both live (SSE) and on
  REST replay (`GET /api/runs/{run_id}`, used by the dashboard's row-click reuse of the run view)
  — is done via a static `rule_id` prefix map in `static/app.js` (`stageForRuleId`): `V-01..04 →
  validate`, `VEN-01/02 → match_vendor`, `PO-01..05` and `V-05 → match_po`, `DUP-01..03 →
  duplicates`.
- `llm.explain()`'s failure (e.g. a transient Groq error) falls back to a small deterministic
  templated summary/next_action in `runner.py`, not the LLM — this only protects the `decide`
  stage's cosmetic prose. A **missing `GROQ_API_KEY` still fails the run at the `extract` stage**,
  since extraction itself is an LLM call, not a cosmetic one; there is no code-only fallback for
  turning a PDF into structured data. Failed runs are persisted with `status="failed"` so they
  still show up on the dashboard.
- Dashboard's "reuses run view rendering" requirement is implemented by having `/dashboard`'s row
  links navigate to `/?run_id=<id>` (full page nav, no client router) rather than duplicating the
  stepper/fields/rules markup in `dashboard.html`; `runView.init()` detects `?run_id=` and loads
  from `GET /api/runs/{run_id}` instead of waiting for an upload, rendering through the exact same
  Alpine component and markup as a live run.

## Phase 5.1 — Non-technical-reader messaging pass (no logic changes)

- Added `app/format.py` (`format_inr`, `format_qty`) implementing Indian digit grouping (last 3
  digits, then pairs) by hand in pure Python, rather than relying on the stdlib `locale` module —
  that needs an `en_IN` locale installed on the host, which isn't guaranteed on Render. These are
  presentation-only helpers, never used in comparisons; all rule *evidence* dicts still hold plain
  Decimal-as-string values, only rule *messages* and `format.py` callers use them.
- `duplicates.check()`'s `previous_hashes` parameter changed from `set[str]` to
  `Mapping[str, HashRecord]` (new dataclass: `run_id, filename, invoice_number, invoice_date`) so
  DUP-01 can name which earlier run produced the matching file hash — membership testing
  (`file_hash in previous_hashes`) behaves identically for a dict's keys as for a set, so DUP-01's
  pass/fail condition is unchanged; only the data available for its message grew.
  `db.previous_hash_records()` (replacing `db.previous_hashes()`) resolves each hash to its
  *first* matching run (`ORDER BY created_at ASC`), i.e. the original submission.
- DUP-01 (exact same file re-uploaded) and DUP-02 (same vendor + invoice number, possibly a
  different file) are framed differently in both the rule message and `llm.explain()`'s system
  prompt: DUP-01 is an internal duplicate upload, no vendor action; DUP-02 is treated as a vendor
  resubmission, notify the vendor. In practice, an exact file re-upload almost always also trips
  DUP-02 (same bytes imply same invoice number), so both usually fire together — the distinct
  framing mainly matters for `llm.explain()`'s prose, which synthesizes both messages coherently.
- `llm.explain()` now receives each rule's `evidence` dict (previously omitted to keep the prompt
  short) and is explicitly instructed never to print raw rule codes (e.g. "DUP-02") in the prose —
  those remain visible to the reader separately as `reasons` tags. The non-LLM fallback path
  (`_fallback_explain` in `runner.py`, used when `GROQ_API_KEY` is missing or the explain call
  fails) was updated the same way: it now joins the flagged rules' own plain-English messages
  instead of listing their rule_ids.
- UI money formatting (₹, 2 decimals, Indian grouping) uses `Intl.NumberFormat('en-IN', {style:
  'currency', currency: 'INR'})` in `app.js`, applied to the extracted-fields panel's
  subtotal/tax_amount/total and the dashboard's total column. Rule `evidence` dicts and the raw
  `/api/runs` JSON are left as plain unformatted decimal strings — formatting is presentation-only
  and happens at render time, not in stored/transmitted data.
