import base64
import json
import os

from app.config import GROQ_TEXT_MODEL, GROQ_VISION_MODEL
from app.models import InvoiceData, RuleResult

_client = None


def mock_enabled() -> bool:
    return os.environ.get("MOCK_LLM") == "1"


def get_client():
    global _client
    if _client is not None:
        return _client

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Add it to your .env file (see .env.example) "
            "or export it before starting the app."
        )

    from groq import Groq

    _client = Groq(api_key=api_key)
    return _client


_SYSTEM_PROMPT = """You are an invoice data extraction assistant.
Extract structured data from the invoice content provided and output ONLY a single JSON object —
no prose, no markdown code fences, no explanation.

Output exactly these top-level keys:
vendor_name, vendor_gstin, invoice_number, invoice_date, po_reference, currency,
line_items, subtotal, tax_amount, total, tax_inclusive

Rules:
- If a field is not visible or not stated on the invoice, output JSON null. NEVER guess or infer
  a value that is not present in the source. Do not output "N/A", "Not specified", or "" — use null.
- invoice_number: copy EXACTLY as printed, including slashes, dashes, and letters. Do not reformat,
  reorder, or normalize it in any way.
- invoice_date: convert to ISO format "YYYY-MM-DD". If the date is not legible or not present, null.
- po_reference: the purchase order number referenced on the invoice, if any. If no PO number is
  mentioned anywhere on the invoice, output null. Do not guess one.
- line_items: always a JSON array (use [] if there are truly none, never null). Each item has
  description (string), quantity, unit_price, amount.
- All monetary/quantity fields (quantity, unit_price, amount, subtotal, tax_amount, total) must be
  plain decimal-number strings with NO thousands separators and NO currency symbols
  (e.g. "1508.00", not "1,508.00" or "₹1,508"). Copy the printed value — do NOT calculate or
  correct any of these fields yourself, even if the invoice's own arithmetic looks inconsistent.
- tax_inclusive: true ONLY if the invoice text explicitly states that unit/line prices already
  include tax (e.g. "prices inclusive of GST", "tax included"). Do not infer this from arithmetic.
  Default to false if not stated.
"""


def _user_prompt_text(text: str) -> str:
    return (
        "Below is the raw text extracted from an invoice PDF (pages separated by blank lines). "
        "Extract the fields as instructed.\n\n---\n" + text + "\n---"
    )


def _user_prompt_vision() -> str:
    return (
        "The following images are the pages of a scanned invoice, in order. "
        "Extract the fields as instructed."
    )


def _parse_json(raw: str | None) -> dict:
    if not raw:
        raise ValueError("LLM returned empty content")
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM did not return valid JSON: {e}\nRaw output: {raw[:500]!r}") from e


def extract_from_text(text: str) -> InvoiceData:
    client = get_client()
    response = client.chat.completions.create(
        model=GROQ_TEXT_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt_text(text)},
        ],
    )
    data = _parse_json(response.choices[0].message.content)
    data["source"] = "text"
    return InvoiceData(**data)


def extract_from_images(images: list[bytes]) -> InvoiceData:
    client = get_client()
    content = [{"type": "text", "text": _user_prompt_vision()}]
    for png_bytes in images:
        b64 = base64.b64encode(png_bytes).decode("ascii")
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
        )
    response = client.chat.completions.create(
        model=GROQ_VISION_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
    )
    data = _parse_json(response.choices[0].message.content)
    data["source"] = "vision"
    return InvoiceData(**data)


_EXPLAIN_SYSTEM_PROMPT = """You are writing a short plain-English note for an accounts-payable
manager who is not technical. A deterministic rules engine has ALREADY made the final decision —
you are not deciding anything and must not suggest a different outcome.

Output ONLY a single JSON object with exactly two keys:
"summary" (1-2 sentences plainly explaining why this outcome was reached) and "next_action" (one
short sentence telling the reviewer or approver what to do next). No prose, no markdown fences,
no other keys.

Rules for the writing itself:
- NEVER write a bare rule code such as "DUP-02", "PO-05", or "V-03" in the summary or next_action.
  Those are shown to the reader separately as tags — your job is to say in plain words what they
  mean, using the specific facts and numbers from the rule messages and evidence below (amounts,
  dates, quantities, vendor and invoice names/numbers).
- If a duplicate-file rule fired (the one about the exact same PDF being submitted before): this
  is an internal duplicate upload (e.g. someone clicked submit twice), NOT a new invoice from the
  vendor. Say so, and do not recommend contacting the vendor for this reason.
  If a duplicate-invoice-number rule fired instead (same vendor, matching invoice number, but not
  necessarily the same file): treat this as the vendor resubmitting or double-billing, and
  recommend notifying the vendor, citing the earlier invoice's number and date.
- Be concrete: name the vendor, the invoice number, and the actual amounts/dates/quantities
  involved rather than speaking generically."""


_NEXT_ACTION_BY_OUTCOME = {
    "APPROVE": "Route to AP for payment.",
    "NEEDS_REVIEW": "Review the flagged items above before approving.",
    "REJECT": "Do not pay; investigate the rejection reason above.",
}


def _deterministic_explain(
    rule_results: list[RuleResult], outcome: str, reasons: list[str]
) -> tuple[str, str]:
    flagged_messages = [r.message for r in rule_results if r.rule_id in reasons]
    summary = " ".join(flagged_messages) if flagged_messages else "All checks passed."
    return summary, _NEXT_ACTION_BY_OUTCOME[outcome]


def explain(
    invoice: InvoiceData,
    rule_results: list[RuleResult],
    outcome: str,
    reasons: list[str],
) -> tuple[str, str]:
    if mock_enabled():
        return _deterministic_explain(rule_results, outcome, reasons)
    client = get_client()
    lines = [
        f"{r.rule_id} [{r.status}]: {r.message} | evidence: {r.evidence}" for r in rule_results
    ]
    user_prompt = (
        f"Vendor: {invoice.vendor_name or 'unknown'}\n"
        f"Invoice number: {invoice.invoice_number or 'unknown'}\n"
        f"Outcome (already decided, final): {outcome}\n"
        f"Driving rule_ids (internal tags, do not print these codes in your prose): "
        f"{', '.join(reasons) if reasons else 'none'}\n\n"
        "All rule results, with evidence for you to cite real numbers from:\n" + "\n".join(lines)
    )
    response = client.chat.completions.create(
        model=GROQ_TEXT_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _EXPLAIN_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    data = _parse_json(response.choices[0].message.content)
    return str(data["summary"]), str(data["next_action"])
