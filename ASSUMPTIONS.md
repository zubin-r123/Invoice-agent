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
