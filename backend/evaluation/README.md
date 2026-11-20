# Extraction accuracy (T4.2)

Measures how well invoice extraction works, end to end: content extraction
(PDF text layer, OCR, spreadsheet parsing) → Claude → normalisation, scored
against labelled ground truth.

```bash
task eval-generate            # 40 synthetic labelled invoices -> evaluation/dataset
task eval-check               # harness check with an "oracle" model (free; expect 100%)
task eval                     # real run with Claude (costs money; needs ANTHROPIC_API_KEY)
task eval -- --limit 10       # a smaller (cheaper) run
```

Reports go to `evaluation/results/<timestamp>-<model>/`:
`report.md` (summary tables), `summary.json` (all metrics) and
`cases.json` (every mistake per case, with expected and extracted values).

## Dataset

Synthetic invoices with known answers, generated deterministically from a
seed: US, UK and German layouts (labels, date and number formats), four
formats (digital PDF, **scanned** PDF that goes through OCR, XLSX, CSV),
1–6 line items, and optional fields deliberately left out so null handling
is measured.

**Add real invoices** for a more representative score: put the file in the
dataset folder next to `<same name>.json`:

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
