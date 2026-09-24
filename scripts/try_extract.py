"""Runs read + extract over every PDF in test_invoices/ and prints one summary line each."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.llm import extract_from_images, extract_from_text
from app.pipeline.read import read_pdf

TEST_DIR = Path(__file__).resolve().parent.parent / "test_invoices"


def format_line(filename: str, data) -> str:
    total = data.total if data.total is not None else "-"
    return (
        f"{filename:32s} source={data.source:6s} "
        f"vendor={(data.vendor_name or '-')[:28]:28s} "
        f"invoice_no={(data.invoice_number or '-')[:14]:14s} "
        f"po={(data.po_reference or '-')[:14]:14s} "
        f"total={str(total):>12s} lines={len(data.line_items)}"
    )


def main() -> None:
    pdf_paths = sorted(TEST_DIR.glob("*.pdf"))
    if not pdf_paths:
        print(f"No PDFs found in {TEST_DIR}")
        return

    for path in pdf_paths:
        try:
            pdf_bytes = path.read_bytes()
            result = read_pdf(pdf_bytes)
            if result.mode == "text":
                data = extract_from_text(result.text)
            else:
                data = extract_from_images(result.images)
            print(format_line(path.name, data))
        except Exception as e:
            print(f"{path.name:32s} ERROR: {e}")


if __name__ == "__main__":
    main()
