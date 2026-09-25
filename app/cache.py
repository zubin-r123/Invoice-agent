"""Dev-only extraction cache: skip the Groq call for a file hash already extracted before.

Keyed by the file's SHA-256 (the same hash DUP-01 uses), stored as InvoiceData JSON under
.cache/ (gitignored). Off by default — only active when EXTRACT_CACHE=1 is set in .env, so a
deployed instance (e.g. Render) always runs real extraction. Safe to delete .cache/ any time.
"""

import os
from pathlib import Path

from app.llm import extract_from_images, extract_from_text, mock_enabled
from app.models import InvoiceData

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"


def enabled() -> bool:
    return os.environ.get("EXTRACT_CACHE") == "1" or mock_enabled()


def _path(file_hash: str) -> Path:
    return CACHE_DIR / f"{file_hash}.json"


def get(file_hash: str) -> InvoiceData | None:
    if not enabled():
        return None
    path = _path(file_hash)
    if not path.exists():
        return None
    try:
        return InvoiceData.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def set(file_hash: str, invoice: InvoiceData) -> None:
    if not enabled():
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _path(file_hash).write_text(invoice.model_dump_json(), encoding="utf-8")


def extract_cached(file_hash: str, mode: str, text: str, images: list[bytes]) -> tuple[InvoiceData, bool]:
    """Returns (invoice, was_cached). On a cache miss (or with caching disabled) this calls the
    real LLM extraction and, when enabled, saves the result for next time."""
    cached = get(file_hash)
    if cached is not None:
        return cached, True
    if mock_enabled():
        raise RuntimeError("Not available in mock mode")
    invoice = extract_from_text(text) if mode == "text" else extract_from_images(images)
    set(file_hash, invoice)
    return invoice, False
