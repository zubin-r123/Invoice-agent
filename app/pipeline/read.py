import io
from dataclasses import dataclass
from typing import Literal

import pdfplumber
import pypdfium2 as pdfium

from app.config import SCAN_TEXT_THRESHOLD

RENDER_DPI = 150  # matches the ~150 dpi used to rasterize test_invoices/04


@dataclass
class ReadResult:
    mode: Literal["text", "vision"]
    text: str | None
    images: list[bytes] | None
    page_count: int
    avg_chars_per_page: float


def read_pdf(pdf_bytes: bytes) -> ReadResult:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page_texts = [(page.extract_text() or "") for page in pdf.pages]
        page_count = len(pdf.pages)

    total_chars = sum(len(t) for t in page_texts)
    avg_chars_per_page = (total_chars / page_count) if page_count else 0.0

    if avg_chars_per_page >= SCAN_TEXT_THRESHOLD:
        return ReadResult(
            mode="text",
            text="\n\n".join(page_texts),
            images=None,
            page_count=page_count,
            avg_chars_per_page=avg_chars_per_page,
        )

    images = _render_pages_to_png(pdf_bytes)
    return ReadResult(
        mode="vision",
        text=None,
        images=images,
        page_count=page_count,
        avg_chars_per_page=avg_chars_per_page,
    )


def _render_pages_to_png(pdf_bytes: bytes) -> list[bytes]:
    scale = RENDER_DPI / 72
    doc = pdfium.PdfDocument(pdf_bytes)
    images: list[bytes] = []
    try:
        for page in doc:
            bitmap = page.render(scale=scale)
            pil_image = bitmap.to_pil()
            buf = io.BytesIO()
            pil_image.save(buf, format="PNG")
            images.append(buf.getvalue())
    finally:
        doc.close()
    return images
