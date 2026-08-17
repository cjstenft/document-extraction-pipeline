# Document Extraction Pipeline

A structured-extraction case study: pulling clean, schema-conformant JSON out
of purchase orders, packing slips, and bills of lading using the Anthropic
API, and measuring exactly how accurate that extraction is at the field
level — not just a single pass/fail number.

**In short:**

- 35 synthetic documents (12 purchase orders, 12 packing slips, 11 bills of
  lading), scored field-by-field against ground truth generated alongside
  each document; every scoring report behind these numbers is committed to
  [`results/`](results/) for independent verification.
- Field-level accuracy lands in the high 90s — 99.1–99.5% across repeat
  runs of identical code — essentially ceiling performance on this clean
  synthetic test set (see Limitations for what that does and doesn't mean).
- Claude Sonnet 5 matched or slightly beat Claude Opus 5's accuracy at
  roughly 60% of the cost, so the pipeline's default model was changed to
  Sonnet based on this finding (see Model comparison).
- Also documented here, left in rather than edited out: a schema change
  that was tried, made accuracy worse, and was reverted; and a
  results-directory contamination incident from a stray sanity-check run.

## Problem

Purchase orders, packing slips, and bills of lading are still routinely
re-keyed by hand into ERPs, accounting systems, and spreadsheets. Manual data
entry from these documents is slow, doesn't scale, and is a well-documented
source of transcription errors (wrong quantities, transposed digits in totals,
mis-typed vendor names) that cascade into downstream reconciliation problems.
Extracting structured data from semi-structured business documents like these
is one of the most common real-world applications of LLMs in the enterprise —
and one of the easiest to get wrong quietly, since a plausible-looking but
incorrect extraction is often harder to catch than an outright failure.

## Approach

The pipeline sends each document PDF directly to Claude using the
Anthropic API's **tool use** feature, with `tool_choice` forced to a
single extraction tool. This guarantees the model's response is
structured JSON matching a defined schema, rather than free-form text that
would need separate parsing.

The schema (`schema.py`) extracts:

- `document_type` — one of `purchase_order`, `packing_slip`, `bill_of_lading`
- `document_number`
- `date` — normalized to ISO 8601 regardless of how it's printed on the
  source document
- `vendor_name` / `buyer_name`
- `line_items` — array of `{description, quantity, unit_price, total}`
- `total_amount`

The tool definition uses `strict: true`, so every response is validated
against the schema exactly (no missing fields, no extra properties) before
it ever reaches scoring. Thinking is explicitly disabled for the extraction
call (`thinking={"type": "disabled"}`) — this is deterministic structured
extraction with a forced tool call, not a task that benefits from reasoning
depth. `effort` is left unset, which defaults to `high` on the Claude API;
Results includes a comparison against `effort=low` to check whether that
default is doing any real work on a task this constrained.

`claude-opus-5` was used for the primary evaluation to establish an accuracy
ceiling for this task before considering cheaper alternatives — this is
otherwise the most expensive model available for what is a fairly
deterministic extraction task, so that ceiling is only useful alongside a
comparison against a cheaper model, which Results includes. Based on that
comparison, `extract.py`'s default model was changed to `claude-sonnet-5`
after this evaluation; pass `--model claude-opus-5` to reproduce the
original evaluation exactly.

## Evaluation Method

There's no public labeled dataset of purchase orders / packing slips / bills
of lading suited to this, so the test set is synthetic: 35 documents
generated with `reportlab` (`generate_test_docs.py`), split roughly evenly
across the three document types. Generation is seeded (`--seed`, default
`42`) specifically so the committed `documents/` and `ground_truth/` are
reproducible — running `python generate_test_docs.py` with no arguments
regenerates the exact same 35 documents byte-for-byte, not a different
random test set. Critically, each document's ground truth JSON is written
from the *exact same data structure* used to render its PDF — there's no
separate hand-labeling step that could drift from what's actually on the
page. Ground truth was manually spot-checked during development, which is
how a rendering bug was caught where unescaped `&` characters in vendor
names were showing up as literal `&amp;` on the page.

Once the test set was generating clean, the first extraction pass scored a
perfect 100% — which meant there was nothing to analyze, and suggested the
documents were too easy rather than the pipeline being flawless. The
generator was then hardened with three realistic difficulty sources before
the final run:

- **Unit-suffixed quantities** — some line items print quantity as `"45 ea"`
  or `"12 cs"` instead of a bare number; the model has to parse past the unit.
- **Near-duplicate line items** — ~25% of documents include the same product
  twice at different quantity/price (simulating a split shipment or tiered
  pricing), testing whether the model tracks each row independently by
  position instead of merging them.
- **Inconsistent currency formatting** — about 1 in 5 documents omit the `$`
  sign entirely.

`score.py` then flattens each predicted/ground-truth document pair
(including `line_items`, aligned by list position) into individual field
comparisons — numeric fields use a small tolerance for floating-point
money values, string fields compare exactly — and reports accuracy per
document, overall, and broken down by field type.

## Results

```
Model:                 claude-sonnet-5 (the pipeline default)
Field-level accuracy:  99.6% (747/750 fields)
Throughput:            11.6 documents/minute
Cost/document:         $0.0150
Test set:              35 documents (12 purchase orders, 12 packing slips, 11 bills of lading)
```

`claude-opus-5` was the original evaluation baseline before this default
changed — see Model comparison for its numbers (99.1–99.5% across three
runs of identical code, at roughly 1.6x Sonnet's cost, for no measurable
accuracy gain). Repeat runs of that Opus baseline, on different days, have
scored 99.5%, 99.1%, and 99.3% — see the run-to-run variance bullet in
Limitations before treating any single number as precise; Sonnet has only
been run once here, so there's no equivalent range for it yet, though
there's no reason to expect it's any more precise than Opus turned out to
be. The full machine-readable scoring report behind every number in this
section is committed in [`results/`](results/) (linked per-model in the
comparison table below), so none of this is take-our-word-for-it.

This test set is entirely synthetic and clean by construction — every
document was generated by this same pipeline's PDF renderer, not sourced
from real-world scans. The first version of this evaluation scored a
perfect 100%, which was a sign the documents were too easy rather than the
pipeline being flawless; the test set was deliberately hardened (see
Evaluation Method above) before numbers like these were produced. Read
these results as validation that the extraction pipeline and the scoring
methodology work correctly end-to-end — not as a claim about accuracy on
real-world, messy documents.

Every field type scored a perfect 100% except one — a single narrow,
well-understood failure mode described below rather than diffuse errors
across many field types.

### Model comparison

`extract.py`'s model is configurable (`--model`), so the identical
35-document test set was also run against `claude-sonnet-5` to see whether
a cheaper model performs comparably on a task this deterministic. Documents
are processed sequentially, one API call at a time — throughput below is
latency-bound, not a measure of maximum achievable throughput, and would
change substantially under concurrent requests. Differences under roughly
1 document/minute between runs are within normal variance for sequential,
network-latency-bound API calls — the `effort=low` row's slightly lower
throughput than the default isn't a meaningful signal, despite that run
generating fewer output tokens. Costs are computed from measured token
usage (`response.usage`) at API pricing as of August 2026, not estimated.

| Model | Field-level accuracy | Throughput | Cost/document | Full report |
|---|---|---|---|---|
| `claude-opus-5` (effort=high, API default) | 99.3% (745/750) | 9.6 docs/min | $0.0247 | [`results/opus_effort_high_scoring_report.json`](results/opus_effort_high_scoring_report.json) |
| `claude-opus-5` (effort=low) | 99.3% (745/750) | 9.5 docs/min | $0.0242 | [`results/opus_effort_low_scoring_report.json`](results/opus_effort_low_scoring_report.json) |
| `claude-sonnet-5` | 99.6% (747/750) | 11.6 docs/min | $0.0150 | [`results/sonnet_scoring_report.json`](results/sonnet_scoring_report.json) |

In this run, Sonnet was both cheaper (~40% less per document) and slightly
more accurate than Opus. Both models' errors were the same failure mode
described below — near-duplicate line items causing description
over-explanation — just at different rates (5 mismatches for Opus at
either effort level, 3 for Sonnet, out of 135 line items each). With only
35 documents and no temperature control on either model, a few-mismatch
swing between runs is within normal variance (see Limitations), so this
isn't strong evidence that Sonnet is categorically more accurate than
Opus. It is good evidence that Opus's extra cost isn't buying a measurable
accuracy improvement on this task — which is the more actionable finding
of the two. Based on this, `extract.py`'s default model was changed from
`claude-opus-5` to `claude-sonnet-5` (see Approach) — pass
`--model claude-opus-5` to reproduce the numbers in this table's first row.

Effort made no difference at all, and the reason is more specific than
"the task is too constrained": thinking is fully disabled regardless of
effort, not just formally requested to be.
`response.usage.output_tokens_details.thinking_tokens` reads `0` at
`effort` unset (the API default, `high`), explicit `effort=high`, and
`effort=low` alike — verified directly against the API rather than
assumed. Effort's primary lever is thinking depth, and there is no
thinking budget here for it to expand or contract, so effort has almost
nothing left to modulate: output tokens differed by only ~5% across the
full 35-document set (404 vs 386 tokens/document, high vs low) and cost by
only ~2% ($0.0247 vs $0.0242). `effort=low` and the default `effort=high`
scored *identically* — 99.3% (5 mismatches each, on different specific
documents) — as clean a demonstration as this evaluation could produce
that effort has nothing to act on here.

### Iteration attempt: tightening the description field (reverted)

Given the `line_items.description` failure mode in Limitations below,
`schema.py`'s description field was tightened to explicitly forbid
unit/packaging suffixes ("no quantity, unit, or packaging information ...
even if it appears immediately next to the item"). Re-running the identical
35-document set against that stricter instruction made accuracy on the
targeted field *worse* — 90.4% (122/135, 13 mismatches) versus that day's
baseline of 94.8% (128/135, 7 mismatches; a different Opus run than the one
reported above, which landed at 96.3% (5 mismatches) — see the run-to-run
variance note in Limitations) — and changed the shape of the failure
entirely. Instead of fixing the near-duplicate disambiguation problem, the
model started truncating legitimate product names that happen to include
packaging text in the source data:

```
predicted='Case of Copy Paper'    ground_truth='Case of Copy Paper (10 reams)'
predicted='Nitrile Gloves'        ground_truth='Nitrile Gloves, Box of 100'
predicted='Whiteboard Marker'     ground_truth='Whiteboard Marker, Box of 12'
```

The instruction was too broad: it couldn't distinguish between the display
artifact this evaluation deliberately injects into the printed Qty column
(`"44 cs"`, which shouldn't leak into `description`) and packaging text
that's genuinely part of a product's real printed name (which should stay).
The schema change was reverted — every number elsewhere in this README
reflects the original, unmodified schema. This is left in deliberately: a
prompt/schema instruction that looks like a strict improvement on paper can
regress a model's behavior in an unanticipated direction, and the only way
to know is to re-run the eval, not to reason about it in the abstract.

## Limitations

The biggest limitation of this evaluation is the test set's realism, not
the extraction pipeline's accuracy:

- **The test set is entirely synthetic**, generated by this same pipeline's
  `reportlab` renderer. Real-world documents introduce failure modes this
  set doesn't cover at all: scanned (not born-digital) PDFs with noise and
  skew, handwritten annotations or corrections, inconsistent or
  company-specific layouts, multi-page documents with items split across
  page breaks, low-contrast or low-resolution scans, and non-English text.
  This evaluation's high-90s field-level accuracy on a clean synthetic set
  should not be read as an estimate of accuracy on messy real-world
  documents — it's a ceiling, not a floor.
- **35 documents is a small sample, and this measurably fluctuates.**
  Re-running the identical Opus extraction (same code, same documents, no
  code changes) on three different days scored 99.5%, 99.1%, and 99.3% —
  a spread of three mismatches out of 750 fields, all the same failure mode
  described below, from inherent model variance alone (Claude Opus 5 does
  not accept a temperature parameter — non-default values return a 400
  error — but is not perfectly deterministic). A sample this
  small is enough to characterize failure modes qualitatively, not enough
  to make a precise accuracy claim with tight confidence bounds — treat
  every specific number in this README as "approximately this," not exact.
- **`line_items` scoring is position-aligned**, not matched by description
  similarity (see `score.py`'s docstring for the reasoning). This means a
  single dropped or inserted line item would cascade into every subsequent
  item in that document looking wrong, understating accuracy on that
  specific failure mode rather than isolating it. That failure mode didn't
  occur in this run, but the scoring methodology doesn't handle it gracefully
  if it does.
- **A stray sanity-check run once silently contaminated a results
  directory.** While developing the model/effort comparison, a
  single-document `extract.py --limit 1` call was run against the default
  `outputs/` directory to verify a rotated API key — after the pipeline's
  default model had already been changed to `claude-sonnet-5`. That call
  overwrote one of the 35 files in what was otherwise a complete
  `claude-opus-5` run with a Sonnet result. At the time, nothing in the
  directory or the scoring report would have flagged the mix — it was
  caught only by manual review before publishing, not by the pipeline.
  `run_metadata.json` (model, effort, timestamp, document count, and token
  totals — written by `extract.py`, folded into every `scoring_report.json`
  by `score.py`) exists specifically to prevent a repeat: that same stray
  call today would leave `documents_processed: 1` sitting next to a report
  scored against all 35 ground-truth files, an unmissable mismatch. It's
  still a human who has to notice the report doesn't say what they expect,
  not an automated check that blocks a bad commit — the same limit as the
  reverted schema iteration above.

Within the results this evaluation did produce, one specific and
well-understood failure mode showed up:

**`line_items.description` was the only field type with any errors: 97.8%
accuracy (132/135 correct, 3 mismatches).** All three mismatches share the
exact same root cause — on the near-duplicate line items described above,
the model disambiguates the two otherwise-identical rows by appending the
unit-suffix hint into the description field, e.g.:

| Predicted | Ground truth |
|---|---|
| `USB-C Charging Cable, 6ft (cs)` | `USB-C Charging Cable, 6ft` |
| `USB-C Charging Cable, 6ft (ea)` | `USB-C Charging Cable, 6ft` |
| `Steel L-Bracket, 4in (box)` | `Steel L-Bracket, 4in` |

(This table reflects `claude-sonnet-5`, the pipeline's current default and
the run whose headline numbers appear at the top of Results. `claude-opus-5`
shows the same failure mode at a slightly higher rate — 96.3%, 5
mismatches, on the specific run reported in Model comparison — and counts
vary run to run regardless of model; see the run-to-run variance bullet
above.)

Every other field type — `document_type`, `document_number`, `date`,
`vendor_name`, `buyer_name`, `total_amount`, and `quantity`/`unit_price`/
`total` within line items — was 100% correct across all 35 documents,
including the documents with near-duplicate rows. The model is not
confusing the duplicate rows or misassigning their quantities/prices; it's
specifically over-explaining the `description` field to make the two rows
visually distinguishable, which technically violates the schema's implicit
contract that `description` is just the product name.

## How to Run Locally

```bash
# 1. Clone the repository
git clone https://github.com/cjstenft/document-extraction-pipeline.git
cd document-extraction-pipeline

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # macOS/Linux
venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up your API key
cp .env.example .env            # macOS/Linux
copy .env.example .env          # Windows
# then edit .env and set ANTHROPIC_API_KEY=sk-ant-your-actual-key

# 5. Generate the synthetic test set (35 PDFs + matching ground truth JSON)
python generate_test_docs.py

# 6. Run extraction against every document in documents/ (claude-sonnet-5 by default)
python extract.py

# 7. Score the extraction results against ground truth
python score.py
```

`extract.py` and `score.py` both accept flags for partial runs, a
different model, and a different output directory — see
`python extract.py --help` / `python score.py --help`. `extract.py`
defaults to `claude-sonnet-5` (see Results for why); to reproduce the
original Opus evaluation:
`python extract.py --model claude-opus-5 --output-dir outputs_opus`
followed by `python score.py --output-dir outputs_opus`. Extraction
results land in `outputs/` by default (gitignored, since they're
reproducible from `documents/` and this code): one JSON file per document,
plus `run_metadata.json` recording the model, effort, timestamp, document
count, and token totals for that run. `score.py` reads that file and folds
it into a full machine-readable report at `<output-dir>/scoring_report.json`.
