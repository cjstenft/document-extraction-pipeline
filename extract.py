"""
Sends each PDF in documents/ to Claude for structured extraction using the
tool schema in schema.py, and writes one JSON file per document to an
output directory.

Usage:
    python extract.py                            # every PDF, claude-sonnet-5, outputs/
    python extract.py --limit 1                  # just the first PDF (sanity check)
    python extract.py --model claude-opus-5      # reproduce the original evaluation
    python extract.py --effort low --output-dir outputs_low_effort
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
DEFAULT_OUTPUT_DIR = Path("outputs")
DEFAULT_MODEL = "claude-sonnet-5"  # changed from claude-opus-5 -- see README Results

# USD per 1M tokens, standard (non-introductory) API pricing. Used only to
# print an approximate cost estimate at the end of a run -- not load-bearing
# for extraction itself, and not adjusted for any time-limited intro pricing.
PRICING = {
    "claude-opus-5": {"input": 5.00, "output": 25.00},
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
}

EXTRACTION_PROMPT = (
    "Extract the structured data from this document using the "
    "extract_document_data tool."
)


def extract_document(client: anthropic.Anthropic, pdf_path: Path, model: str, effort: str | None = None):
    pdf_data = base64.standard_b64encode(pdf_path.read_bytes()).decode("utf-8")

    request_kwargs = {}
    if effort is not None:
        request_kwargs["output_config"] = {"effort": effort}

    response = client.messages.create(
        model=model,
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
        **request_kwargs,
    )

    tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
    if not tool_use_blocks:
        raise RuntimeError(f"No tool_use block in response (stop_reason={response.stop_reason})")

    return tool_use_blocks[0].input, response.usage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N documents")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model ID to use (default: {DEFAULT_MODEL})")
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=None,
                         help="Override output_config.effort (default: unset, which the API defaults to 'high')")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                         help=f"Directory to write extracted JSON to (default: {DEFAULT_OUTPUT_DIR})")
    args = parser.parse_args()

    load_dotenv()
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    args.output_dir.mkdir(exist_ok=True)

    pdf_paths = sorted(DOCUMENTS_DIR.glob("*.pdf"))
    if args.limit:
        pdf_paths = pdf_paths[: args.limit]

    if not pdf_paths:
        print(f"No PDFs found in {DOCUMENTS_DIR}/. Run generate_test_docs.py first.")
        return

    effort_note = f", effort={args.effort}" if args.effort else ""
    print(f"Processing {len(pdf_paths)} document(s) with model {args.model}{effort_note} -> {args.output_dir}/...\n")

    succeeded = 0
    failed = 0
    total_input_tokens = 0
    total_output_tokens = 0
    start_time = time.perf_counter()

    for i, pdf_path in enumerate(pdf_paths, start=1):
        doc_start = time.perf_counter()
        try:
            result, usage = extract_document(client, pdf_path, args.model, args.effort)
            out_path = args.output_dir / f"{pdf_path.stem}.json"
            out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            total_input_tokens += usage.input_tokens
            total_output_tokens += usage.output_tokens
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

    if succeeded:
        print(f"Tokens: {total_input_tokens:,} input, {total_output_tokens:,} output "
              f"({total_input_tokens / succeeded:.0f} in / {total_output_tokens / succeeded:.0f} out per document)")
        pricing = PRICING.get(args.model)
        if pricing:
            total_cost = (total_input_tokens / 1_000_000 * pricing["input"]
                          + total_output_tokens / 1_000_000 * pricing["output"])
            print(f"Estimated cost: ${total_cost:.4f} total (${total_cost / succeeded:.4f}/document) "
                  f"at standard pricing (${pricing['input']:.2f} in / ${pricing['output']:.2f} out per MTok)")


if __name__ == "__main__":
    main()
