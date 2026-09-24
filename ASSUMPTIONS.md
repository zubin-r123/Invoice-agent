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
