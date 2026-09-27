# ClearPay

**Invoice PDF in, a reasoned decision out.**

ClearPay automates the first pass of accounts payable. Give it a vendor invoice (clean PDF or scanned image) and it checks it against the company's vendors, purchase orders and payment history, then returns **Approve**, **Needs review** or **Reject**, with every check visible as it runs.



- **Live app:** [https://invoice-agent-19jq.onrender.com/]
- **Demo video (5 min):** [https://www.loom.com/share/a5bdec7d07ae4368b31a63e9b2072e4b]

> Hosted on a free tier: the first load after a quiet spell can take up to a minute while the server wakes up.

---

## The core design principle

**The AI reads, the code decides.**

The language model is used for exactly two things:

1. Turning a messy invoice (text PDF or scanned image) into structured data.
2. Writing a plain-English summary of results that have already been computed.

Every check, tolerance, match and final decision is deterministic Python. The same invoice always gets the same decision, and a finance team can audit exactly why. The next action (for example "notify the vendor" versus "no action, this was our own re-upload") is also decided in code, not by the model.

---

## How it works

Each invoice runs through eight stages, streamed live to the browser:

| Stage | What happens |
|---|---|
| **Ingest** | Fingerprints the file (SHA-256) to catch exact re-uploads |
| **Read** | Extracts the text layer; if there is almost none, treats it as a scan and renders the page to an image |
| **Extract** | The LLM (or vision model for scans) fills a strict schema: vendor, invoice number, dates, PO reference, line items, totals |
| **Validate** | Checks the invoice against itself: required fields, arithmetic, dates, tax rate |
| **Match vendor** | Fuzzy-matches the vendor against the approved vendor list and checks its status |
| **Match PO** | Finds the purchase order (explicit or inferred from line items), compares every line on quantity and price, including what has already been billed |
| **Duplicates** | Compares against past invoices and past runs, with invoice-number normalisation |
| **Decide** | Applies the decision rules, then writes a plain-English summary and next action |

### The 15 checks

| Group | Rule | Check |
|---|---|---|
| Invoice is correct | V-01 | Vendor, invoice number, date and total are present |
| | V-02 | Line items sum to the subtotal (or the total, for tax-inclusive invoices) |
| | V-03 | Subtotal + tax = total |
| | V-04 | Date is not in the future and not older than 180 days |
| | V-05 | Tax matches the rate on the PO |
| Vendor is trusted | VEN-01 | Vendor is recognised (fuzzy match on name and aliases) |
| | VEN-02 | Vendor is approved, not blocked |
| Matches the order | PO-01 | PO identified, from the reference or inferred from line items |
| | PO-02 | PO belongs to this vendor and is still open |
| | PO-03 | Every invoice line maps to a PO line |
| | PO-04 | Unit price within 2% of the PO price (tax backed out for tax-inclusive invoices) |
| | PO-05 | Quantity already billed + this invoice does not exceed the PO quantity |
| Not seen before | DUP-01 | Exact same file was processed before |
| | DUP-02 | Same vendor and same normalised invoice number (`NWL-2026-0042` = `NWL/26/42`) |
| | DUP-03 | Same vendor, same total, within 7 days, under a different number (warning) |

**Decision logic:** a blocked vendor or confirmed duplicate is **Reject**. Any other failure or warning is **Needs review**. Everything passing is **Approve**.

---

## Test invoices and edge cases

Each edge case is designed to trip exactly one rule, so the reason for every decision is unambiguous. `scripts/acceptance.py` runs all six and checks each against its expected outcome.

| File | Scenario | Expected | Rule |
|---|---|---|---|
| `01_happy_acme.pdf` | Clean invoice that exactly matches its PO | Approve | none |
| `02_split_po_brightline.pdf` | Bills 500 of 1,000 boxes, but 600 were already billed | Needs review | PO-05 |
| `03_duplicate_northwind.pdf` | Paid invoice resubmitted with a reformatted number | Reject | DUP-02 |
| `04_scanned_no_po_sahyadri.pdf` | Image-only scan, no PO reference, vendor has two open orders | Needs review | PO-01 |
| `05_price_variance_metro.pdf` | Prices 4% above the PO, hidden inside tax-inclusive amounts | Needs review | PO-04 |
| `06_blocked_quantum.pdf` | Valid invoice and open PO, but the vendor is blocked | Reject | VEN-02 |

Uploading the same file twice triggers DUP-01. ClearPay recognises it as an internal re-upload and points to the earlier run rather than asking to notify the vendor.

The test data is a fictional company (Kestrel Manufacturing) with its own vendor master, purchase orders and payment ledger in `data/`.

---

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python, FastAPI, Uvicorn |
| PDF reading | pdfplumber (text layer), pypdfium2 (render scans to images) |
| AI | Groq: `openai/gpt-oss-120b` for extraction and summaries, a Qwen vision model for scanned invoices |
| Structured output | Pydantic schemas, forced JSON output; all money in `Decimal` |
| Matching | rapidfuzz for vendor names and reworded line descriptions |
| Live updates | Server-sent events, one event per pipeline stage |
| History | SQLite |
| Frontend | Plain HTML, Tailwind, Alpine.js, Chart.js; no build step |
| Hosting | Render (single web service serving UI and API) |
| Built with | Claude Code |

All model calls live in `app/llm.py`, so the provider can be swapped in one place.

---

## Project structure

```
app/
  main.py          routes (UI, run creation, SSE stream, history API)
  models.py        Pydantic models: InvoiceData, RuleResult, Decision
  llm.py           all LLM calls (extraction, vision extraction, summary)
  config.py        tolerances and thresholds
  data.py          loads vendors, POs and ledger at startup
  db.py            SQLite run history
  pipeline/
    runner.py      orchestrates stages, yields live events
    read.py        text vs scan detection
    validate.py    invoice-level checks
    match.py       vendor and PO matching
    duplicates.py  duplicate detection
    decide.py      decision logic and next action
data/              vendors.csv, purchase_orders.csv, ledger.csv
static/            run view, dashboard, styles, scripts
test_invoices/     generator script and the six test PDFs
scripts/           acceptance tests and dev tools
ASSUMPTIONS.md     assumptions and decisions made during the build
CLAUDE.md          build guide used with Claude Code
```

---

## Running locally

```bash
git clone [repo URL]
cd invoice-agent
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

Create a `.env` file (see `.env.example`):

```
GROQ_API_KEY=your_key_here
MOCK_LLM=0
EXTRACT_CACHE=0
```

Generate the test invoices and start the server:

```bash
python test_invoices/generate_invoices.py
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000` and click any sample invoice.

### Development modes

| Variable | Effect |
|---|---|
| `MOCK_LLM=1` | No Groq calls at all: extraction uses cached results, summaries are generated from the rule results. Used for UI work so free-tier quota is saved for real runs. |
| `EXTRACT_CACHE=1` | Saves each successful extraction by file hash and reuses it. |

Both are off by default and never set on the deployed app, so production always performs real extraction.

### Tests

```bash
python scripts/acceptance.py
```

Runs all six invoices through extraction and the rules engine and prints expected vs actual outcome for each.

---

## Known limitations

- **Free-tier model limits.** Groq's free tier caps tokens per minute, so runs are best spaced about 30 seconds apart. A rate-limited run fails at the Extract stage with a clear message.
- **History resets on Render.** The free tier has an ephemeral disk, so the dashboard clears when the server restarts. Duplicate and over-billing checks are unaffected, since they use the ledger in `data/`.
- **GSTINs are format-plausible, not checksum-validated.**
- **Single currency (INR)** and one buyer company in the test data.
- See `ASSUMPTIONS.md` for the full list of assumptions.

## What I'd build next

- Human override on any decision, with an audit trail of who overrode what and why.
- Ingesting invoices directly from an AP email inbox.
- Vendor bank-detail change detection as a fraud check.
- Persistent hosted database for history.
