from decimal import Decimal

ROUNDING_TOLERANCE = Decimal("1.00")     # INR
PRICE_TOLERANCE_PCT = Decimal("2.0")
MAX_INVOICE_AGE_DAYS = 180
VENDOR_MATCH_AUTO = 90
VENDOR_MATCH_REVIEW = 75
LINE_MATCH_MIN = 70
PO_INFER_MIN_CONFIDENCE = 0.6
DUP_DATE_WINDOW_DAYS = 7
SCAN_TEXT_THRESHOLD = 50                 # avg chars per page

# Verified against a live `client.models.list()` call on 2026-09-24 — see ASSUMPTIONS.md
GROQ_TEXT_MODEL = "openai/gpt-oss-120b"
GROQ_VISION_MODEL = "qwen/qwen3.8-27b"
