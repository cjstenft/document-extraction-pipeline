"""
Generates a synthetic test set of purchase orders, packing slips, and bills
of lading as PDFs (documents/), plus a matching hand-labeled ground truth
JSON file for each one (ground_truth/).

The ground truth is written from the exact same data structure used to
render the PDF, so it is guaranteed accurate by construction — there's no
separate "labeling" step that could drift from what's actually on the page.

Usage:
    python generate_test_docs.py                # generate all 35 documents
    python generate_test_docs.py --count 1       # generate just doc_001 (sanity check)
    python generate_test_docs.py --count 5 --seed 7
"""

import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

DOCUMENTS_DIR = Path("documents")
GROUND_TRUTH_DIR = Path("ground_truth")

NUM_DOCUMENTS = 35
DEFAULT_SEED = 42

DOCUMENT_TYPES = ["purchase_order", "packing_slip", "bill_of_lading"]

DOC_TYPE_LABELS = {
    "purchase_order": "PURCHASE ORDER",
    "packing_slip": "PACKING SLIP",
    "bill_of_lading": "BILL OF LADING",
}

# Real-world documents label the two parties differently depending on
# document type (a PO says "Vendor"/"Buyer", a BOL says "Shipper"/
# "Consignee") even though they map to the same two schema fields
# (vendor_name, buyer_name). This variation is intentional — it forces the
# extraction model to generalize the field mapping rather than pattern-match
# a single label string.
PARTY_LABELS = {
    "purchase_order": ("Vendor", "Buyer"),
    "packing_slip": ("Shipped From", "Shipped To"),
    "bill_of_lading": ("Shipper", "Consignee"),
}

ITEM_HEADERS = {
    "purchase_order": ["Item Description", "Qty Ordered", "Unit Price", "Extended Price"],
    "packing_slip": ["Item", "Qty Shipped", "Unit Price", "Line Total"],
    "bill_of_lading": ["Commodity", "Qty", "Unit Price", "Total"],
}

DOC_NUMBER_PREFIXES = {
    "purchase_order": "PO",
    "packing_slip": "PS",
    "bill_of_lading": "BOL",
}

VENDOR_NAMES = [
    "Summit Ridge Supply Co.", "Blue Anchor Industrial", "Meridian Hardware Group",
    "Cascade Fastener & Tool", "Northgate Distribution", "Ironvale Manufacturing",
    "Pinecrest Logistics", "Harborview Materials", "Redstone Components Inc.",
    "Alderwood Freight Systems", "Copperfield Industrial Supply", "Vantage Point Wholesale",
    "Silverline Packaging Co.", "Brightwater Electronics", "Stonebridge Fabrication",
    "Westfield Tool & Die", "Amberlake Textiles", "Granite Peak Chemical Supply",
    "Fieldstone Equipment Rental", "Timberline Building Materials",
]

BUYER_NAMES = [
    "Orion Manufacturing LLC", "Cedarbrook Retail Group", "Palisade Construction Co.",
    "Beacon Hill Enterprises", "Riverside Assembly Inc.", "Crestwood Contractors",
    "Lakeshore Automotive Parts", "Northstar Foods Corp.", "Willowmere Electronics",
    "Marlowe Industrial Partners", "Southgate Retailers Inc.", "Highland Park Grocers",
    "Fairview Auto Repair", "Brookline Restaurant Group", "Ashford Warehouse Solutions",
]

# (description, min unit price, max unit price)
PRODUCTS = [
    ("Wireless Optical Mouse", 12.50, 45.00),
    ("USB-C Charging Cable, 6ft", 4.25, 15.00),
    ("Steel L-Bracket, 4in", 1.10, 3.50),
    ("Case of Copy Paper (10 reams)", 38.00, 62.00),
    ("Industrial Rubber Gasket, 2in", 2.75, 8.00),
    ("Adjustable Laptop Stand", 18.00, 55.00),
    ("Safety Goggles, Case of 12", 22.00, 40.00),
    ("Corrugated Shipping Box, 18x18x18", 1.85, 4.20),
    ("Hydraulic Fitting, 1/2in NPT", 6.50, 14.00),
    ("LED Panel Light, 2x4ft", 45.00, 95.00),
    ("Stainless Steel Hex Bolt, M8", 0.15, 0.60),
    ("Pallet Wrap Film, 18in x 1500ft", 12.00, 28.00),
    ("Nitrile Gloves, Box of 100", 8.50, 18.00),
    ("Cordless Drill, 20V", 65.00, 140.00),
    ("Anti-Static Wrist Strap", 3.00, 9.00),
    ("Aluminum Extrusion, 1m Length", 9.50, 24.00),
    ("Ball Bearing, 608ZZ", 0.45, 1.80),
    ("Packing Tape, Case of 36 Rolls", 32.00, 58.00),
    ("Ceramic Coffee Mug, 12oz", 2.50, 6.00),
    ("Office Chair, Ergonomic Mesh Back", 85.00, 220.00),
    ("Whiteboard Marker, Box of 12", 6.00, 14.00),
    ("Extension Cord, 25ft 12AWG", 15.00, 35.00),
    ("Toner Cartridge, High Yield", 45.00, 110.00),
    ("Wooden Shipping Pallet, 48x40", 12.00, 22.00),
    ("Vinyl Floor Marking Tape, 2in", 8.00, 18.00),
]

# Printed date formats. Ground truth always stores ISO 8601 regardless of
# which of these is used on the rendered page (see schema.py's date field).
DATE_FORMATS = [
    "%m/%d/%Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%m-%d-%y",
    "%Y-%m-%d",
]

# Printed quantity sometimes carries a unit abbreviation ("45 ea") instead of
# a bare number. Ground truth always stores the clean integer -- the model
# has to parse past the unit, which is a realistic extraction difficulty
# that a bare-number quantity never exercises.
QUANTITY_UNIT_SUFFIXES = ["ea", "cs", "pcs", "units", "box"]

NOTES_POOL = [
    "Please inspect all items upon receipt and report discrepancies within 5 business days.",
    "Payment due within 30 days of invoice date.",
    "All items subject to standard 1-year warranty unless otherwise noted.",
    "Freight collect. Consignee responsible for unloading.",
    "Backordered items will ship separately at no additional freight charge.",
]


def random_date(rng):
    start = date(2023, 1, 1)
    end = date(2024, 12, 31)
    return start + timedelta(days=rng.randint(0, (end - start).days))


def build_document(rng, index):
    doc_type = DOCUMENT_TYPES[index % len(DOCUMENT_TYPES)]

    date_obj = random_date(rng)
    printed_date = date_obj.strftime(rng.choice(DATE_FORMATS))

    prefix = DOC_NUMBER_PREFIXES[doc_type]
    if doc_type == "purchase_order":
        document_number = f"{prefix}-{date_obj.year}-{rng.randint(1, 9999):05d}"
    elif doc_type == "packing_slip":
        document_number = f"{prefix}-{rng.randint(100000, 999999)}"
    else:
        document_number = f"{prefix}-{rng.randint(10000, 99999)}"

    vendor_name = rng.choice(VENDOR_NAMES)
    buyer_name = rng.choice(BUYER_NAMES)

    # Weighted so most documents have 2-5 items, but a meaningful minority
    # sit at the extremes (1 item, or 6-7 items) -- this is what makes
    # field-level accuracy scoring informative rather than uniform.
    item_count = rng.choices(
        [1, 2, 3, 4, 5, 6, 7],
        weights=[3, 4, 5, 5, 4, 3, 2],
    )[0]

    chosen_products = rng.sample(PRODUCTS, k=min(item_count, len(PRODUCTS)))
    line_items = []
    for description, price_min, price_max in chosen_products:
        quantity = rng.randint(1, 50)
        unit_price = round(rng.uniform(price_min, price_max), 2)
        total = round(quantity * unit_price, 2)
        line_items.append({
            "description": description,
            "quantity": quantity,
            "unit_price": unit_price,
            "total": total,
        })

    # Near-duplicate line item: same product, different quantity/price --
    # simulates a split shipment or tiered pricing. Tests whether the model
    # tracks each line independently by position instead of merging two
    # rows with the same description into one.
    if len(line_items) >= 1 and rng.random() < 0.25:
        source = rng.choice(line_items)
        quantity = rng.randint(1, 50)
        unit_price = round(source["unit_price"] * rng.uniform(0.85, 1.15), 2)
        total = round(quantity * unit_price, 2)
        duplicate = {
            "description": source["description"],
            "quantity": quantity,
            "unit_price": unit_price,
            "total": total,
        }
        line_items.insert(rng.randint(0, len(line_items)), duplicate)

    # Per-item printed quantity, possibly with a unit suffix (PDF only --
    # ground truth quantity below always stays the clean integer).
    quantity_display = [
        f"{item['quantity']} {rng.choice(QUANTITY_UNIT_SUFFIXES)}" if rng.random() < 0.3 else str(item["quantity"])
        for item in line_items
    ]

    # Not every real document prints a currency symbol next to amounts.
    show_currency_symbol = rng.random() > 0.2

    subtotal = round(sum(item["total"] for item in line_items), 2)

    # Purchase orders often carry a tax line that a packing slip/BOL
    # wouldn't -- this makes total_amount a genuine extraction target
    # rather than something derivable by summing line items yourself.
    if doc_type == "purchase_order" and rng.random() < 0.6:
        tax_rate = round(rng.uniform(0.0, 0.085), 4)
        total_amount = round(subtotal * (1 + tax_rate), 2)
    else:
        total_amount = subtotal

    notes = rng.choice(NOTES_POOL) if rng.random() < 0.4 else None

    ground_truth = {
        "document_type": doc_type,
        "document_number": document_number,
        "date": date_obj.isoformat(),
        "vendor_name": vendor_name,
        "buyer_name": buyer_name,
        "line_items": line_items,
        "total_amount": total_amount,
    }

    return ground_truth, printed_date, notes, quantity_display, show_currency_symbol


def render_pdf(path, doc, printed_date, notes, quantity_display, show_currency_symbol):
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("DocTitle", parent=styles["Title"], fontSize=18, spaceAfter=6)
    label_style = ParagraphStyle("Label", parent=styles["Normal"], fontSize=10, leading=14)
    cell_style = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=9, leading=11)
    total_style = ParagraphStyle("Total", parent=styles["Normal"], fontSize=12, alignment=TA_RIGHT)
    notes_style = ParagraphStyle("Notes", parent=styles["Italic"], fontSize=8, textColor=colors.grey)

    elements = [
        Paragraph(DOC_TYPE_LABELS[doc["document_type"]], title_style),
        Paragraph(f"Document #: {doc['document_number']}", label_style),
        Paragraph(f"Date: {printed_date}", label_style),
        Spacer(1, 14),
    ]

    vendor_label, buyer_label = PARTY_LABELS[doc["document_type"]]
    party_data = [
        [Paragraph(f"<b>{vendor_label}</b>", label_style), Paragraph(f"<b>{buyer_label}</b>", label_style)],
        [
            Paragraph(xml_escape(doc["vendor_name"]), label_style),
            Paragraph(xml_escape(doc["buyer_name"]), label_style),
        ],
    ]
    party_table = Table(party_data, colWidths=[3.25 * inch, 3.25 * inch])
    party_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.append(party_table)
    elements.append(Spacer(1, 20))

    currency = "$" if show_currency_symbol else ""
    headers = ITEM_HEADERS[doc["document_type"]]
    table_data = [headers]
    for item, qty_display in zip(doc["line_items"], quantity_display):
        table_data.append([
            Paragraph(xml_escape(item["description"]), cell_style),
            qty_display,
            f"{currency}{item['unit_price']:,.2f}",
            f"{currency}{item['total']:,.2f}",
        ])
    item_table = Table(table_data, colWidths=[3.1 * inch, 1.0 * inch, 1.1 * inch, 1.2 * inch])
    item_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2d3748")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(item_table)
    elements.append(Spacer(1, 14))
    elements.append(Paragraph(f"<b>Total: {currency}{doc['total_amount']:,.2f}</b>", total_style))

    if notes:
        elements.append(Spacer(1, 28))
        elements.append(Paragraph(xml_escape(notes), notes_style))

    pdf = SimpleDocTemplate(
        str(path), pagesize=letter,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
    )
    pdf.build(elements)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=NUM_DOCUMENTS, help="Number of documents to generate")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Random seed for reproducibility")
    args = parser.parse_args()

    DOCUMENTS_DIR.mkdir(exist_ok=True)
    GROUND_TRUTH_DIR.mkdir(exist_ok=True)

    rng = random.Random(args.seed)
    type_counts = {t: 0 for t in DOCUMENT_TYPES}

    for i in range(1, args.count + 1):
        ground_truth, printed_date, notes, quantity_display, show_currency_symbol = build_document(rng, i - 1)
        type_counts[ground_truth["document_type"]] += 1

        stem = f"doc_{i:03d}"
        pdf_path = DOCUMENTS_DIR / f"{stem}.pdf"
        json_path = GROUND_TRUTH_DIR / f"{stem}.json"

        render_pdf(pdf_path, ground_truth, printed_date, notes, quantity_display, show_currency_symbol)
        json_path.write_text(json.dumps(ground_truth, indent=2), encoding="utf-8")

        print(f"  {stem}: {ground_truth['document_type']:<15} "
              f"{len(ground_truth['line_items'])} item(s)  ${ground_truth['total_amount']:,.2f}")

    print(f"\nGenerated {args.count} document(s): {type_counts}")


if __name__ == "__main__":
    main()
