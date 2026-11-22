# Extraction accuracy (T4.2)

Measures how well invoice extraction works, end to end: content extraction
(PDF text layer, OCR, spreadsheet parsing) → Claude → normalisation, scored
against labelled ground truth.

```
evaluation/
├── dataset/
│   ├── documents/              # NW-2026-0001.pdf, ... (.tiff/.xlsx/.csv too)
│   └── ground_truth/           # NW-2026-0001.json: the expected InvoiceData
├── validation_expectations/    # NW-2026-0001.json: which amount checks must fail
├── results/
│   ├── extraction_results.json # last extraction run: metrics + every mistake per case
│   ├── validation_results.json # last validation run: metrics + every wrong scenario
│   ├── evaluation_summary.json # headline figures of both
│   └── evaluation_report.html  # readable report of both (open in a browser)
├── evaluate_extraction.py      # run documents through the pipeline and score them
├── evaluate_validation.py      # check the amount checks against the expectations
├── metrics.py                  # field scoring and aggregate metrics
├── reporting.py                # paths and result files
└── generate.py                 # synthetic labelled dataset + expectations
```

Every case is named after its invoice number: the document, its ground truth
and its validation expectations share that name.

```bash
task eval-generate            # 40 synthetic labelled invoices + expectations
task eval-check               # harness check with an "oracle" model (free; expect 100%)
task eval                     # real run with Claude (costs money; needs ANTHROPIC_API_KEY)
task eval -- --limit 10       # a smaller (cheaper) run
task eval-validate            # amount-check evaluation (free, no model)
```

Each run overwrites its own results file and rebuilds `evaluation_summary.json`
and `evaluation_report.html` from whichever runs exist (`task eval-report`
rebuilds the reports on their own). The extraction tables are also printed to the
terminal.

## Dataset

Synthetic invoices with known answers, generated deterministically from a
seed: US, UK and German layouts (labels, date and number formats), five
formats (digital PDF, **scanned** PDF and **scanned** multi-page TIFF that go
through OCR, XLSX, CSV; they rotate, so 40 cases give 8 of each),
1–6 line items, and optional fields deliberately left out so null handling
is measured.

**Add real invoices** for a more representative score: put the file in
`dataset/documents/<invoice number>.<ext>` and its label in
`dataset/ground_truth/<invoice number>.json`:

```json
{"format": "pdf", "locale": "de", "expected": { ...InvoiceData... }}
```

`expected` uses the same schema as `GET /documents/{id}/invoice` → `invoice`
(every field present; `null` where the document doesn't state it).

## Metrics

Each field is `correct`, `correct_null`, `wrong`, `missed` (value present,
returned null) or `hallucinated` (value invented for an absent field). Text
compares letters and digits case-insensitively; dates exactly; amounts within
0.01.

| Metric | Meaning |
|---|---|
| Documents fully correct | every field, line item and line field right |
| Field accuracy | share of all fields right (including correct nulls) |
| Value / null accuracy | split by whether the document states the field |
| Hallucination rate | invented values among absent fields: should be ~0 |
| Line-item precision / recall / F1 | extracted lines that match / expected lines found |
| Amount checks pass | extractions whose subtotal/tax/total add up |
| Document success / failure rate | documents processed end to end without an error, and the rest (split by stage: content extraction, AI extraction, other) |
| Processing time | per successful document: mean, median, p95 and max for content extraction, the model call and the total; totals per format |

Breakdowns by format and per field show where errors concentrate (e.g. OCR
on scans, or day/month order on US dates).

## Validation

`evaluate_validation.py` tests `app/extraction/validation.py` (the amount
checks) against `validation_expectations/<invoice number>.json`:

```json
{
  "invoice_number": "NW-2026-0001",
  "expected_failed_checks": [],
  "injected_errors": [
    {
      "name": "subtotal",
      "description": "Subtotal increased by 10.00",
      "set": {"subtotal": 22596.37},
      "must_fail": ["total"]
    }
  ]
}
```

* As labelled, the invoice must fail exactly `expected_failed_checks`. Any
  other failure is a false alarm. Generated invoices are consistent, so this
  is `[]`. A real invoice that doesn't add up lists the checks it should fail.
* Each injected error changes the ground truth (`set` takes dotted paths such
  as `line_items.0.amount`), and every check in `must_fail` has to fail.
  Other checks may also fail as a knock-on effect. The generator injects a
  wrong unit price, line amount, subtotal, tax amount and total, and an amount
  due above the total, wherever the invoice has the fields for it. Its
  `must_fail` lists come from what each check compares, not from running the
  checks.

Invoices without an expectations file are skipped and listed.
Check names: `line_amounts`, `lines_subtotal`, `tax_amount`, `total`, `amount_due`.

| Metric | Meaning |
|---|---|
| Detection rate | injected errors where every `must_fail` check failed |
| False-alarm rate | invoices (as labelled) failing a check they shouldn't |
| Scenarios correct | labelled invoices and injected errors with no miss and no false alarm |
| Precision / recall / F1 / specificity | the checks as a detector of inconsistent invoices |
| Per check | expected failures caught (recall) and false alarms, per check |
| By injected error | how often each kind of error is caught |
