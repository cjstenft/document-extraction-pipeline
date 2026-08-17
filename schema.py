"""
Anthropic tool-use schema for structured extraction from purchase orders,
packing slips, and bill of lading documents.

Design notes:
- `strict: True` forces Claude's output to validate exactly against this
  schema (every property present, no extras) — this removes an entire class
  of "missing field" or "wrong type" errors before scoring ever runs.
- `date` is normalized to ISO 8601 (YYYY-MM-DD) regardless of how it's
  printed on the source document. Real downstream systems (accounting,
  ERPs) want a consistent date format, and normalizing also makes the
  ground-truth comparison in score.py format-independent — we don't have to
  worry about "03/15/2024" vs "March 15, 2024" being treated as a mismatch.
- `quantity`, `unit_price`, and `total` on line items are numbers (not
  strings) so score.py can do numeric comparison instead of string diffing,
  and so a downstream consumer could sum them directly.
"""

EXTRACTION_TOOL = {
    "name": "extract_document_data",
    "description": (
        "Extract structured data from a scanned or digital purchase order, "
        "packing slip, or bill of lading. Use every field visible on the "
        "document; if a field is genuinely not present on the document, "
        "still provide your best-supported value (e.g. an empty string for "
        "missing text, 0 for a missing numeric total) rather than omitting it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "document_type": {
                "type": "string",
                "enum": ["purchase_order", "packing_slip", "bill_of_lading"],
                "description": "The type of logistics/procurement document.",
            },
            "document_number": {
                "type": "string",
                "description": (
                    "The document's identifying number (PO number, packing "
                    "slip number, or BOL number), exactly as printed."
                ),
            },
            "date": {
                "type": "string",
                "description": (
                    "The document's primary date, normalized to ISO 8601 "
                    "format (YYYY-MM-DD) regardless of the format printed "
                    "on the document."
                ),
            },
            "vendor_name": {
                "type": "string",
                "description": "The name of the vendor, supplier, or shipper listed on the document.",
            },
            "buyer_name": {
                "type": "string",
                "description": "The name of the buyer, purchaser, or recipient listed on the document.",
            },
            "line_items": {
                "type": "array",
                "description": "Every line item listed on the document.",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "The product or item description.",
                        },
                        "quantity": {
                            "type": "number",
                            "description": "The quantity ordered/shipped for this line item.",
                        },
                        "unit_price": {
                            "type": "number",
                            "description": "The price per unit for this line item, in the document's currency.",
                        },
                        "total": {
                            "type": "number",
                            "description": "The extended total for this line item (quantity x unit_price).",
                        },
                    },
                    "required": ["description", "quantity", "unit_price", "total"],
                    "additionalProperties": False,
                },
            },
            "total_amount": {
                "type": "number",
                "description": "The document's grand total amount.",
            },
        },
        "required": [
            "document_type",
            "document_number",
            "date",
            "vendor_name",
            "buyer_name",
            "line_items",
            "total_amount",
        ],
        "additionalProperties": False,
    },
    "strict": True,
}
