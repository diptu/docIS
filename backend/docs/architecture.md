# System Architecture

PNG versions are in this folder:
- [`system_architecture.png`](system_architecture.png)
- [`processing_pipeline.png`](processing_pipeline.png)

They are rendered from the `.dot` sources with `dot -Tpng -Gdpi=200 <file>.dot -o <file>.png`.

![System architecture](system_architecture.png)

## Components

```mermaid
flowchart LR
    subgraph Client
        UI["React + Vite SPA<br/>Tailwind, TanStack Query"]
    end

    subgraph API["FastAPI (app/api)"]
        R1["/documents<br/>upload · status · invoice · corrections · retry"]
        R2["/search<br/>keyword · semantic · hybrid"]
        R3["/compare<br/>invoice · text · PDF↔Excel"]
        R4["/invoices/review · /processing/jobs"]
    end

    subgraph Infra
        FS[("File storage<br/>originals, read-only, SHA-256")]
        DB[("PostgreSQL 18 + pgvector<br/>metadata · text · invoices · errors · jobs · chunks")]
        Q[("Redis<br/>Celery broker")]
    end

    subgraph Workers
        W["Celery worker<br/>prefetch=1, acks_late"]
        B["Celery beat<br/>job recovery"]
    end

    subgraph External
        LLM["Anthropic API<br/>Claude Opus 5.5 · structured output"]
        EMB["FastEmbed<br/>bge-small-en-v1.5 (local)"]
    end

    UI -- "HTTP /api" --> API
    R1 -- "store original" --> FS
    R1 -- "document + queued job (1 transaction)" --> DB
    R1 -- "dispatch after commit" --> Q
    R2 --> DB
    R2 -- "embed query" --> EMB
    R3 --> DB
    R4 --> DB
    Q --> W
    B -- "re-dispatch stale / undispatched jobs" --> Q
    W -- "read original" --> FS
    W -- "progress, pages, sheets, invoice, errors" --> DB
    W -- "extract invoice" --> LLM
    W -- "embed chunks" --> EMB
```

## Processing pipeline (one document)

![Processing pipeline](processing_pipeline.png)

```mermaid
flowchart TD
    U["Upload (multipart, many files)"] --> V{"Validate<br/>size · count · magic bytes · parse check"}
    V -- "invalid / corrupted / empty" --> X["Rejected with a clear 4xx message"]
    V -- ok --> S["Store original + SHA-256<br/>insert document + queued job"]
    S --> J1["Job 1: extract_content"]

    J1 --> T{"File type"}
    T -- "digital PDF" --> P1["pypdf text layer, page by page"]
    T -- "scanned PDF page" --> P2["pypdfium2 render @300 DPI → Tesseract"]
    T -- "JPG / PNG / TIFF frames" --> P3["Pillow prep (EXIF orientation, grayscale, upscale) → Tesseract"]
    T -- "XLSX / XLS" --> P4["openpyxl / xlrd, every sheet"]
    T -- "CSV" --> P5["delimiter + encoding detection"]

    P1 & P2 & P3 --> PG["document_pages (text, method, OCR confidence)"]
    P4 & P5 --> SH["document_sheets (JSONB grid)"]
    PG & SH --> CH["Chunk + embed → document_chunks<br/>(GIN full-text + HNSW vector)"]
    CH --> J2["Job 2: extract_invoice"]

    J2 --> L["Claude structured output → InvoiceData<br/>missing → null, doubtful → uncertain_fields"]
    L --> N["Normalise (dates, currency, Decimal amounts)"]
    N --> VAL["Deterministic validation (Decimal)<br/>qty×price · Σlines=subtotal · tax · subtotal+tax=total · amount due"]
    VAL --> DUP["Duplicate checks<br/>SHA-256 · vendor+number · vendor+date+total"]
    DUP --> RV["invoices, invoice_line_items, validation_errors<br/>needs review if any unresolved error"]

    RV --> H["Human review in UI<br/>correct fields → invoice_corrections → re-validate"]
```

## Reliability

- **Job state lives in PostgreSQL**, not only in the queue. A job is claimed with `SELECT … FOR UPDATE`, so a duplicate delivery does nothing.
- **Heartbeat:** a running job writes progress and a heartbeat about once a second. Celery beat requeues a job whose heartbeat has gone stale, and re-sends queued jobs that were never dispatched.
- **Retries:** temporary failures retry with exponential backoff and jitter, up to `JOB_MAX_ATTEMPTS` (default 3) attempts. Permanent failures fail immediately with a readable error.
- **Manual retry:** `POST /documents/{id}/processing/retry` re-runs a failed document.

## Deployment

- **Local:** `docker compose up` (in `backend/`) starts the database, the migration step, the API, the worker, beat and Redis.
- **Hosted:**
  - Render (`render.yaml`) runs the API, the worker and beat in one Docker service with a persistent disk, plus managed Postgres and Redis.
  - Vercel (`frontend/vercel.json`) serves the static frontend and forwards `/api/*` to the API.
