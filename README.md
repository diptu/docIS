# docIS

An AI-powered document intelligence system that ingests business documents, extracts structured invoice data, validates records, and enables document search.

## Features

- **Multi-format ingestion:** Upload PDF, JPG, PNG, TIFF, XLSX, XLS, and CSV files.
- **Text extraction:** Extract text from digital PDFs and spreadsheets.
- **OCR processing:** Extract text from scanned PDFs and images.
- **AI-powered extraction:** Convert document content into structured invoice data.
- **Data validation:** Detect missing fields, incorrect totals, and duplicate invoices.
- **Human review:** Review and manually correct extracted information.
- **Document search:** Search documents using keywords and semantic similarity.
- **Background processing:** Process large documents asynchronously with status tracking and retries.

## Tech Stack

| Component | Technology |
|---|---|
| Frontend | React, Tailwind CSS |
| Backend | Python, FastAPI |
| Package Management | uv |
| Database | PostgreSQL |
| PDF Processing | PyMuPDF |
| OCR | Tesseract, pytesseract |
| Spreadsheet Processing | pandas, openpyxl |
| Background Processing | Redis, Celery |
| Testing | pytest, Vitest |

## Architecture

```text
React + Tailwind CSS
         |
         v
    FastAPI API
         |
    +----+----+
    |         |
    v         v
PostgreSQL  File Storage
    |
    v
Background Job Queue
    |
    v
Document Processing
    |
    +---- PDF Extraction
    +---- OCR Processing
    +---- Excel/CSV Parsing
    |
    v
AI Data Extraction
    |
    v
Data Validation
    |
    v
PostgreSQL
```


## Core Workflow

1. Upload business documents.
2. Store files and create processing records.
3. Extract text using PDF parsers, OCR, or spreadsheet readers.
4. Extract invoice fields and line items using an AI model.
5. Validate extracted data using deterministic business rules.
6. Store structured results in PostgreSQL.
7. Review and correct extracted information.
8. Search documents using keywords and semantic similarity.

## Project Structure

```text
docIS/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── workers/
│   │   └── main.py
│   ├── tests/
│   ├── pyproject.toml
│   └── uv.lock
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   └── services/
│   └── package.json
├── docs/
│   ├── architecture.md
│   └── database-schema.md
├── .env.example
└── README.md
```

## MVP Scope

The initial version focuses on the essential workflow:

- Document upload and ingestion.
- PDF, image, Excel, and CSV parsing.
- Structured invoice data extraction.
- Data validation and duplicate detection.
- Manual review and correction.
- Basic document search.

Advanced analytics and complex reconciliation can be added in future iterations.

## License

Developed as an technical assignment.
