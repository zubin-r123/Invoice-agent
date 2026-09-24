"""Runs read + extract + rules engine over every test invoice and checks the outcome against
CLAUDE.md's expected-outcomes table. Prints file | expected | actual | driving rule_ids | PASS/FAIL.
"""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app import cache
from app.pipeline import decide, duplicates, match, validate
from app.pipeline.read import read_pdf

TEST_DIR = Path(__file__).resolve().parent.parent / "test_invoices"

EXPECTED = {
    "01_happy_acme.pdf": "APPROVE",
    "02_split_po_brightline.pdf": "NEEDS_REVIEW",
    "03_duplicate_northwind.pdf": "REJECT",
    "04_scanned_no_po_sahyadri.pdf": "NEEDS_REVIEW",
    "05_price_variance_metro.pdf": "NEEDS_REVIEW",
    "06_blocked_quantum.pdf": "REJECT",
}


def run_one(path: Path) -> tuple[str, list[str]]:
    pdf_bytes = path.read_bytes()
    file_hash = hashlib.sha256(pdf_bytes).hexdigest()

    result = read_pdf(pdf_bytes)
    invoice, _was_cached = cache.extract_cached(file_hash, result.mode, result.text, result.images)

    rule_results = []
    rule_results += validate.run(invoice)

    vendor, ven_results = match.match_vendor(invoice)
    rule_results += ven_results

    _po_number, po_results, _line_matches = match.match_po(invoice, vendor)
    rule_results += po_results

    rule_results += duplicates.check(invoice, file_hash, vendor, previous_hashes={}, previous_invoices=[])

    outcome, reasons = decide.decide(rule_results)
    return outcome, reasons


def main() -> None:
    pdf_paths = sorted(TEST_DIR.glob("*.pdf"))
    if not pdf_paths:
        print(f"No PDFs found in {TEST_DIR}")
        return

    pass_count = 0
    total = 0

    for path in pdf_paths:
        expected = EXPECTED.get(path.name)
        if expected is None:
            continue
        total += 1
        try:
            actual, reasons = run_one(path)
            status = "PASS" if actual == expected else "FAIL"
            if status == "PASS":
                pass_count += 1
            print(f"{path.name:32s} expected={expected:14s} actual={actual:14s} reasons={reasons} {status}")
        except Exception as e:
            print(f"{path.name:32s} ERROR: {e}")

    print(f"\n{pass_count}/{total} passed")


if __name__ == "__main__":
    main()
