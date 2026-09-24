"""Generate the 6 test invoices defined in CLAUDE.md.
Run from repo root:  python test_invoices/generate_invoices.py
Needs: reportlab, pypdfium2, pillow
"""
import random
from decimal import Decimal as D, ROUND_HALF_UP
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).parent
W, H = A4
BUYER = ["Bill to: Kestrel Manufacturing Pvt Ltd", "Plot 14, MIDC Chakan, Pune 410501",
         "GSTIN: 27AAECK4455P1Z3"]


def money(x, indian=False):
    x = D(x).quantize(D("0.01"), ROUND_HALF_UP)
    if not indian:
        return f"{x:,.2f}"
    whole, frac = f"{x:.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:]); head = head[:-2]
    if head: groups.insert(0, head)
    return ",".join(groups + [tail]) + "." + frac


# Each invoice: vendor block, meta, lines (desc, qty, rate), tax rate, style knobs.
INVOICES = [
    dict(file="01_happy_acme.pdf", font="Helvetica", indian=False,
         vendor=["ACME Industrial Supplies Pvt Ltd", "Unit 7, Bhosari MIDC, Pune 411026",
                 "GSTIN: 27AABCA1234F1Z5"],
         meta=[("Invoice No", "ACM-INV-3342"), ("Invoice Date", "2026-09-19"),
               ("PO Number", "PO-2026-0101"), ("Terms", "Net 30")],
         cols=("Description", "Qty", "Rate", "Amount"), tax=D("0.18"),
         lines=[("Safety helmet (ISI marked)", 50, 450), ("High-visibility safety vest", 20, 280),
                ("First aid kit (industrial)", 10, 1200)]),
    dict(file="02_split_po_brightline.pdf", font="Times-Roman", indian=True,
         vendor=["Brightline Packaging Solutions Pvt Ltd", "No. 22, Peenya Industrial Area, Bengaluru 560058",
                 "GSTIN: 29AAFCB5678K1Z2"],
         meta=[("Tax Invoice #", "BPS/INV/0934"), ("Date", "15-09-2026"),
               ("Your Order Ref", "PO-2026-0102"), ("Dispatch", "Partial - batch 2")],
         cols=("Item", "Units", "Unit Price", "Value"), tax=D("0.18"),
         lines=[("Corrugated shipping box - large", 500, 42)]),
    dict(file="03_duplicate_northwind.pdf", font="Helvetica", indian=True,
         vendor=["Northwind Logistics LLP", "Warehouse 3, Chakan Road, Pune 410501",
                 "GSTIN: 27AAGFN4321M1ZQ"],
         meta=[("Invoice", "NWL/26/42"), ("Dated", "14 Aug 2026"), ("PO", "PO-2026-0103")],
         cols=("Service", "Trips", "Rate/Trip", "Amount"), tax=D("0.12"),
         lines=[("FTL freight Pune - Chennai", 3, 38500)]),
    dict(file="04_scanned_no_po_sahyadri.pdf", font="Courier", indian=True, scan=True,
         vendor=["SAHYADRI OFFICE SOLUTIONS", "Shop 4, Laxmi Road, Pune 411030",
                 "GSTIN: 27ABCPS9876R1ZX"],
         meta=[("Bill No", "SOS-1123"), ("Date", "18/09/2026")],
         cols=("Particulars", "Qty", "Rate", "Amt"), tax=D("0.18"),
         lines=[("A4 copier paper 75 GSM ream", 200, 245), ("Toner cartridge HP 26A", 12, 3850),
                ("Whiteboard marker box (10 pcs)", 30, 320)]),
    dict(file="05_price_variance_metro.pdf", font="Helvetica", indian=True, inclusive=True,
         vendor=["Metro Electricals & Co", "1832, Bhagirath Palace, Chandni Chowk, Delhi 110006",
                 "GSTIN: 07AAHFM2468J1Z9"],
         meta=[("Invoice No", "MEC-26-2291"), ("Date", "Sep 17, 2026"), ("PO Ref", "PO-2026-0105")],
         cols=("Description", "Qty", "Rate (incl. GST)", "Amount"), tax=D("0.18"),
         lines=[("LED panel light 2x2 40W", 80, 1508), ("Copper cable 2.5 sqmm 90m coil", 25, 2350)]),
    dict(file="06_blocked_quantum.pdf", font="Helvetica", indian=False,
         vendor=["Quantum Tech Traders Pvt Ltd", "45 Anna Salai, Chennai 600002",
                 "GSTIN: 33AAJCQ1357L1Z4"],
         meta=[("Invoice No", "QTT/2026/518"), ("Invoice Date", "2026-09-12"),
               ("PO Number", "PO-2026-0106")],
         cols=("Description", "Qty", "Rate", "Amount"), tax=D("0.18"),
         lines=[("24-inch LED monitor", 10, 11200)]),
]


def draw(inv, path):
    c = canvas.Canvas(str(path), pagesize=A4)
    f, fb = inv["font"], {"Helvetica": "Helvetica-Bold", "Times-Roman": "Times-Bold",
                          "Courier": "Courier-Bold"}[inv["font"]]
    m, fmt, rate = 50, (lambda x: money(x, inv["indian"])), inv["tax"]
    inclusive = inv.get("inclusive", False)

    c.setFont(fb, 15); c.drawString(m, H - 60, inv["vendor"][0])
    c.setFont(f, 9)
    for i, t in enumerate(inv["vendor"][1:]): c.drawString(m, H - 76 - i * 12, t)
    c.setFont(fb, 13); c.drawRightString(W - m, H - 60, "TAX INVOICE")
    c.setFont(f, 9)
    for i, (k, v) in enumerate(inv["meta"]): c.drawRightString(W - m, H - 78 - i * 13, f"{k}: {v}")
    for i, t in enumerate(BUYER): c.drawString(m, H - 140 - i * 12, t)

    y, xs = H - 200, [m, 290, 400, W - m]
    c.setFont(fb, 9)
    c.drawString(xs[0], y, inv["cols"][0])
    for x, t in zip(xs[1:], inv["cols"][1:]): c.drawRightString(x + (0 if x == xs[3] else 40), y, t)
    c.line(m, y - 4, W - m, y - 4); c.setFont(f, 9)

    total_lines, taxable = D(0), D(0)
    for desc, qty, r in inv["lines"]:
        y -= 18
        unit = (D(r) * (1 + rate)).quantize(D("0.01"), ROUND_HALF_UP) if inclusive else D(r)
        amt = unit * qty
        total_lines += amt; taxable += D(r) * qty
        c.drawString(xs[0], y, desc)
        c.drawRightString(xs[1] + 40, y, str(qty))
        c.drawRightString(xs[2] + 40, y, fmt(unit))
        c.drawRightString(xs[3], y, fmt(amt))
    c.line(m, y - 8, W - m, y - 8)

    if inclusive:
        total = total_lines
        tax = total - taxable
        rows = [("Taxable value", taxable), (f"GST @ {int(rate*100)}% (included)", tax), ("Invoice Total", total)]
        note = f"All rates are inclusive of GST @ {int(rate*100)}%."
    else:
        tax = (taxable * rate).quantize(D("0.01"), ROUND_HALF_UP)
        total = taxable + tax
        label = f"IGST @ {int(rate*100)}%" if inv["vendor"][2][7:9] != "27" else \
            f"CGST {rate*50:.0f}% + SGST {rate*50:.0f}%"
        rows = [("Subtotal", taxable), (label, tax), ("Total Amount Due (INR)", total)]
        note = "Payment by NEFT within agreed terms."
    for i, (k, v) in enumerate(rows):
        y -= 18
        c.setFont(fb if i == len(rows) - 1 else f, 10 if i == len(rows) - 1 else 9)
        c.drawRightString(xs[2] + 40, y, k); c.drawRightString(xs[3], y, fmt(v))
    c.setFont(f, 8); c.drawString(m, 60, note); c.drawString(m, 48, "This is a computer generated invoice.")
    c.save()
    return taxable, tax, total


def to_scan(path, dpi=150):
    img = pdfium.PdfDocument(str(path))[0].render(scale=dpi / 72).to_pil().convert("L")
    img = img.rotate(1.1, expand=True, fillcolor=255, resample=Image.BICUBIC)
    px = img.load(); rnd = random.Random(42)
    for _ in range(img.width * img.height // 60):
        x, y = rnd.randrange(img.width), rnd.randrange(img.height)
        px[x, y] = rnd.choice((60, 120, 200))
    img.filter(ImageFilter.GaussianBlur(0.6)).save(path, "PDF", resolution=dpi)


if __name__ == "__main__":
    for inv in INVOICES:
        p = OUT / inv["file"]
        sub, tax, tot = draw(inv, p)
        if inv.get("scan"): to_scan(p)
        print(f"{inv['file']:32} taxable={sub:>12} tax={tax:>10} total={tot:>12}")
