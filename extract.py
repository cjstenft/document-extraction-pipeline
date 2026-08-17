"""
Sends each PDF in documents/ to Claude for structured extraction using the
tool schema in schema.py, and writes one JSON file per document to outputs/.

Usage:
    python extract.py                # process every PDF in documents/
    python extract.py --limit 1      # process just the first PDF (sanity check)
"""

import argparse
import base64
import json
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from schema import EXTRACTION_TOOL

DOCUMENTS_DIR = Path("documents")
OUTPUTS_DIR = Path("outputs")
MODEL = "claude-opus-5"

EXTRACTION_PROMPT = (
    "Extract the structured data from this document using the "
    "extract_document_data tool."
)


def extract_document(client: anthropic.Anthropic, pdf_path: Path) -> dict:
    pdf_data = base64.standard_b64encode(pdf_path.read_bytes()).decode("utf-8")

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        thinking={"type": "disabled"},
        tools=[EXTRACTION_TOOL],
        tool_choice={"type": "tool", "name": "extract_document_data"},
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_data,
                    },
                },
                {"type": "text", "text": EXTRACTION_PROMPT},
            ],
        }],
    )

    tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
    if not tool_use_blocks:
        raise RuntimeError(f"No tool_use block in response (stop_reason={response.stop_reason})")

    return tool_use_blocks[0].input


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N documents")
    args = parser.parse_args()

    load_dotenv()
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    OUTPUTS_DIR.mkdir(exist_ok=True)

    pdf_paths = sorted(DOCUMENTS_DIR.glob("*.pdf"))
    if args.limit:
        pdf_paths = pdf_paths[: args.limit]

    if not pdf_paths:
        print(f"No PDFs found in {DOCUMENTS_DIR}/. Run generate_test_docs.py first.")
        return

    print(f"Processing {len(pdf_paths)} document(s) with model {MODEL}...\n")

    succeeded = 0
    failed = 0
    start_time = time.perf_counter()

    for i, pdf_path in enumerate(pdf_paths, start=1):
        doc_start = time.perf_counter()
        try:
            result = extract_document(client, pdf_path)
            out_path = OUTPUTS_DIR / f"{pdf_path.stem}.json"
            out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            elapsed = time.perf_counter() - doc_start
            print(f"[{i}/{len(pdf_paths)}] {pdf_path.name} -> {out_path} ({elapsed:.1f}s)")
            succeeded += 1
        except (anthropic.APIStatusError, anthropic.APIConnectionError, RuntimeError) as e:
            print(f"[{i}/{len(pdf_paths)}] {pdf_path.name} FAILED: {e}")
            failed += 1

    total_elapsed = time.perf_counter() - start_time
    minutes = total_elapsed / 60
    rate = succeeded / minutes if minutes > 0 else float("inf")

    print(f"\nDone: {succeeded} succeeded, {failed} failed in {total_elapsed:.1f}s "
          f"({rate:.1f} documents/minute)")


if __name__ == "__main__":
    main()
