"""
Compares each JSON file in outputs/ against its matching ground_truth/*.json,
flattening nested fields (including line_items arrays) into individual
comparisons, and reports field-level accuracy per document and overall.

line_items are aligned by list position (item[0] vs item[0], item[1] vs
item[1], ...) rather than matched by description similarity. This is a
deliberate tradeoff: a single dropped or inserted line item will cascade
into every item after it looking wrong, but the alternative (fuzzy-matching
items before comparing) adds its own scoring logic that could mask real
extraction errors -- and ordering mistakes are themselves useful signal.

Usage:
    python score.py                  # score every document, print full report
    python score.py --examples 10    # show more example mismatches per field
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

GROUND_TRUTH_DIR = Path("ground_truth")
OUTPUTS_DIR = Path("outputs")
REPORT_PATH = OUTPUTS_DIR / "scoring_report.json"

SCALAR_FIELDS = ["document_type", "document_number", "date", "vendor_name", "buyer_name", "total_amount"]
LINE_ITEM_FIELDS = ["description", "quantity", "unit_price", "total"]
NUMERIC_FIELDS = {"quantity", "unit_price", "total", "total_amount"}
NUMERIC_TOLERANCE = 0.01


def values_match(field_type: str, pred, truth) -> bool:
    if pred is None or truth is None:
        return pred is None and truth is None

    base_field = field_type.rsplit(".", 1)[-1]
    if base_field in NUMERIC_FIELDS:
        try:
            return abs(float(pred) - float(truth)) < NUMERIC_TOLERANCE
        except (TypeError, ValueError):
            return pred == truth

    if isinstance(pred, str) and isinstance(truth, str):
        return pred.strip() == truth.strip()

    return pred == truth


def flatten_comparison(pred: dict, truth: dict) -> list[dict]:
    """Flatten a predicted/ground-truth document pair into one row per field."""
    rows = []

    for field in SCALAR_FIELDS:
        rows.append({
            "field_type": field,
            "field_key": field,
            "pred": pred.get(field),
            "truth": truth.get(field),
        })

    pred_items = pred.get("line_items") or []
    truth_items = truth.get("line_items") or []
    for i in range(max(len(pred_items), len(truth_items))):
        p_item = pred_items[i] if i < len(pred_items) else {}
        t_item = truth_items[i] if i < len(truth_items) else {}
        for field in LINE_ITEM_FIELDS:
            field_type = f"line_items.{field}"
            rows.append({
                "field_type": field_type,
                "field_key": f"line_items[{i}].{field}",
                "pred": p_item.get(field),
                "truth": t_item.get(field),
            })

    for row in rows:
        row["match"] = values_match(row["field_type"], row["pred"], row["truth"])

    return rows


def score_document(doc_id: str) -> list[dict] | None:
    truth_path = GROUND_TRUTH_DIR / f"{doc_id}.json"
    output_path = OUTPUTS_DIR / f"{doc_id}.json"

    if not output_path.exists():
        print(f"  {doc_id}: SKIPPED (no extraction output found)")
        return None

    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    pred = json.loads(output_path.read_text(encoding="utf-8"))

    rows = flatten_comparison(pred, truth)
    for row in rows:
        row["doc_id"] = doc_id
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", type=int, default=3, help="Example mismatches to show per field type")
    args = parser.parse_args()

    doc_ids = sorted(p.stem for p in GROUND_TRUTH_DIR.glob("*.json"))
    if not doc_ids:
        print(f"No ground truth files found in {GROUND_TRUTH_DIR}/. Run generate_test_docs.py first.")
        return

    all_rows: list[dict] = []
    print(f"Scoring {len(doc_ids)} document(s)...\n")

    for doc_id in doc_ids:
        rows = score_document(doc_id)
        if rows is None:
            continue
        correct = sum(1 for r in rows if r["match"])
        total = len(rows)
        print(f"  {doc_id}: {correct}/{total} fields correct ({correct / total:.1%})")
        all_rows.extend(rows)

    if not all_rows:
        print("\nNo documents were scored.")
        return

    # --- Overall accuracy ---
    total_correct = sum(1 for r in all_rows if r["match"])
    total_fields = len(all_rows)
    overall_accuracy = total_correct / total_fields

    print(f"\n{'=' * 60}")
    print(f"OVERALL: {total_correct}/{total_fields} fields correct ({overall_accuracy:.1%})")
    print(f"{'=' * 60}\n")

    # --- Per-field-type breakdown, worst first ---
    by_field: dict[str, list[dict]] = defaultdict(list)
    for row in all_rows:
        by_field[row["field_type"]].append(row)

    field_stats = []
    for field_type, rows in by_field.items():
        correct = sum(1 for r in rows if r["match"])
        total = len(rows)
        field_stats.append({
            "field_type": field_type,
            "correct": correct,
            "total": total,
            "accuracy": correct / total,
            "mismatches": total - correct,
        })
    field_stats.sort(key=lambda s: (-s["mismatches"], s["field_type"]))

    print("Field-level accuracy (worst first):\n")
    print(f"  {'field':<20} {'accuracy':>10} {'mismatches':>12} {'n':>6}")
    print(f"  {'-' * 20} {'-' * 10} {'-' * 12} {'-' * 6}")
    for s in field_stats:
        print(f"  {s['field_type']:<20} {s['accuracy']:>9.1%} {s['mismatches']:>12} {s['total']:>6}")

    # --- Example mismatches for the worst fields ---
    if args.examples > 0:
        print(f"\nExample mismatches (top {min(3, len(field_stats))} worst fields):\n")
        for s in field_stats[:3]:
            if s["mismatches"] == 0:
                continue
            print(f"  {s['field_type']}:")
            examples = [r for r in by_field[s["field_type"]] if not r["match"]][: args.examples]
            for ex in examples:
                print(f"    [{ex['doc_id']}] {ex['field_key']}: "
                      f"predicted={ex['pred']!r}  ground_truth={ex['truth']!r}")
            print()

    # --- Persist a machine-readable report for reference ---
    report = {
        "documents_scored": len(doc_ids),
        "overall_accuracy": overall_accuracy,
        "total_fields": total_fields,
        "total_correct": total_correct,
        "field_stats": field_stats,
    }
    OUTPUTS_DIR.mkdir(exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Full report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
