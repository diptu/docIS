# docIS

An AI-powered document intelligence system that ingests business documents, extracts structured invoice data, validates records, and enables document search.

## Features

- **Multi-format ingestion:** Upload PDF, JPG, PNG, TIFF, XLSX, XLS, and CSV files; unsupported and corrupted files are rejected.
- **Text extraction:** Digital PDF text, OCR for scanned pages and images, and sheet-by-sheet spreadsheet parsing.
- **AI-powered extraction:** Claude turns document content into schema-valid invoice data (fields and line items, `null` where absent).
- **Data validation:** Arithmetic checks of line items, subtotal, tax and total; duplicate detection; uncertain results flagged for review.
- **Human review:** Inspect documents next to their extracted data and correct fields manually, with an audit trail.
- **Comparison:** Compare two documents (PDF vs PDF, PDF vs Excel).
- **Search:** Keyword (full-text) and semantic (embedding) search.
- **Background processing:** Celery workers with progress tracking, automatic retries and crash recovery.

## Tech stack

| Component | Technology |
|---|---|
| Frontend | React 19, TypeScript, Tailwind CSS 4, TanStack Query, Vite |
| Backend | Python 3.11, FastAPI, SQLAlchemy (async), Alembic |
| Package management | uv (Python), npm (frontend) |
| Database | PostgreSQL 18 + pgvector |
| PDF processing | pypdf (text layer), PDFium (rendering for OCR) |
| OCR | Tesseract, pytesseract |
| Spreadsheets | openpyxl (XLSX), xlrd (XLS), csv |
| AI extraction | Claude (Anthropic API, structured outputs) |
| Semantic search | fastembed (bge-small-en-v1.5, runs locally), pgvector |
| Background processing | Celery, Redis |
| Testing | pytest, Vitest, Testing Library |

## Architecture

```text
 React frontend (Vite)
        │  /api
        ▼
   FastAPI API ───────────────► File storage (originals, read-only)
        │  commit job, then publish task
        ▼
 PostgreSQL ◄────────────── Celery worker(s) ◄──── Redis (broker)
 (documents, jobs,             │                     ▲
  pages, sheets, invoices,     ├─ PDF text / OCR /   │
  validation errors,           │  spreadsheet parse  │
  search chunks + vectors)     ├─ search indexing    Celery beat
                               └─ Claude extraction  (recovers lost
                                  → validation        or stuck jobs)
```

Detailed docs: [`backend/docs/database_schema.md`](backend/docs/database_schema.md) (tables and processing pipeline), [`backend/evaluation/README.md`](backend/evaluation/README.md) (accuracy measurement).

---

## Getting started

### Prerequisites

| Tool | Needed for | Install |
|---|---|---|
| Git | everything | — |
| Docker Desktop (or Docker Engine + Compose v2) | running the stack | [docker.com](https://docs.docker.com/get-docker/) |
| Node.js 22 + npm | the frontend | [nodejs.org](https://nodejs.org/) or `nvm install 22` |
| An Anthropic API key | AI invoice extraction | [console.anthropic.com](https://console.anthropic.com/) |
| uv ≥ 0.12, Task, Tesseract | local backend development only | see [Local development](#option-b-local-development) |

Without an API key everything except invoice extraction works: documents are still stored, OCRed, parsed and searchable, and their extraction jobs fail with a clear message that you can retry once a key is set.

### Option A: everything in Docker (quickest)

```bash
git clone https://github.com/diptu/docIS.git
cd docIS/backend

cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...

docker compose up -d --build      # first build takes a few minutes
docker compose ps                 # api, worker, db, redis healthy; beat running
```

This starts:

| Service | What it does | Address |
|---|---|---|
| `api` | FastAPI backend | http://localhost:8000 (docs: http://localhost:8000/docs) |
| `worker` | Celery worker: OCR, parsing, indexing, AI extraction | — |
| `beat` | Celery beat: recovers lost or stuck jobs | — |
| `db` | PostgreSQL 18 + pgvector | `localhost:5434` |
| `redis` | Celery broker | `127.0.0.1:6379` |
| `migrate` | Applies database migrations, then exits | — |

Then start the frontend (it is not part of the compose stack):

```bash
cd ../frontend
npm ci
npm run dev                       # http://localhost:5173
```

The dev server proxies `/api` to `http://localhost:8000`; set `API_URL` to point it elsewhere.

**Check that it works:**

```bash
curl http://localhost:8000/health/database        # {"database":"healthy"}
curl -F files=@some-invoice.pdf http://localhost:8000/documents
```

or open http://localhost:5173, click **Upload documents**, and open the document once processing finishes.

### Option B: local development

Run the infrastructure in Docker and the application code on your machine, with reload on save.

1. **Install the tools**

   ```bash
   # macOS
   brew install uv go-task tesseract
   # Debian/Ubuntu
   curl -LsSf https://astral.sh/uv/install.sh | sh
   sudo apt-get install tesseract-ocr        # Task: https://taskfile.dev/installation/
   ```

   uv installs the right Python (3.11.9, from `.python-version`) by itself.

2. **Start PostgreSQL and Redis, and configure**

   ```bash
   cd docIS/backend
   cp .env.example .env                       # set ANTHROPIC_API_KEY
   docker compose up -d --build db redis
   ```

   `.env` already points at them: `DATABASE_URL=…@localhost:5434/docis`, `CELERY_BROKER_URL=redis://localhost:6379/0`.

3. **Install dependencies and migrate**

   ```bash
   task install                                # uv sync --all-groups
   task db-upgrade                             # alembic upgrade head
   ```

4. **Run the services**, each in its own terminal:

   ```bash
   task dev                                    # API on http://localhost:8000, auto-reload
   task worker                                 # processes documents
   task beat                                   # job recovery (run exactly one)
   cd ../frontend && npm ci && npm run dev     # http://localhost:5173
   ```

   The first semantic search or document indexing downloads the embedding model (~64 MB) into `EMBEDDING_CACHE_DIR` (default: fastembed's cache folder).

### Configuration

All settings come from environment variables or `backend/.env`; [`backend/.env.example`](backend/.env.example) lists them all with defaults. The ones you are most likely to change:

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Claude access for invoice extraction |
| `LLM_MODEL` / `LLM_EFFORT` | `claude-opus-5-5` / `medium` | Extraction model and effort |
| `INVOICE_EXTRACTION_ENABLED` | `true` | Turn AI extraction off entirely |
| `OCR_LANGUAGES` | `eng` | Tesseract languages, e.g. `eng+deu` |
| `OCR_EXTRA_LANGS` | — | Docker only: languages to install in the image, e.g. `deu` (rebuild) |
| `SEMANTIC_SEARCH_ENABLED` | `true` | Turn embedding-based search off (keyword search stays) |
| `MAX_UPLOAD_SIZE_BYTES` / `MAX_FILES_PER_UPLOAD` | 25 MB / 20 | Upload limits |
| `WORKER_CONCURRENCY` | `2` | Parallel jobs per worker container |
| `JOB_MAX_ATTEMPTS` | `3` | Attempts before a job is marked failed |
| `POSTGRES_PORT` / `API_PORT` / `REDIS_PORT` | 5434 / 8000 / 6379 | Host ports (Docker) |

After changing `.env`, restart the affected services (`docker compose up -d`), or rebuild (`docker compose up -d --build`) for `OCR_EXTRA_LANGS`.

### Everyday commands

Run from `backend/` (`task --list` shows all):

| Command | What it does |
|---|---|
| `task test` | Backend tests with coverage |
| `task lint` / `task format` / `task typecheck` | Ruff lint, Ruff format, mypy |
| `task ci` | Everything CI runs, without modifying files |
| `task db-revision MSG="…"` / `task db-upgrade` | Create / apply a migration |
| `task db-check` | Fail if models and migrations disagree |
| `task reindex` | Rebuild the search index for all processed documents |
| `task eval-check` / `task eval` | Accuracy harness self-check / real accuracy run with Claude (costs money) |
| `task docker-up` / `task docker-down` / `task docker-logs` | Manage the compose stack |

Frontend (from `frontend/`): `npm run dev`, `npm test`, `npm run typecheck`, `npm run build`.

Scale workers with `docker compose up -d --scale worker=3`.

### Troubleshooting

| Problem | Fix |
|---|---|
| `port is already allocated` on 5434, 6379 or 8000 | Another service uses the port: set `POSTGRES_PORT`, `REDIS_PORT` or `API_PORT` in `.env` and `docker compose up -d` again |
| Documents stay at "Extract invoice failed: No Claude credentials…" | Set `ANTHROPIC_API_KEY` in `.env`, `docker compose up -d worker`, then retry the document (button on its page, or `POST /documents/{id}/processing/retry`) |
| Upload succeeds but nothing is processed | Check the worker: `docker compose logs worker`; locally, make sure `task worker` is running and Redis is up |
| German/other-language scans are read poorly | Install the language: `OCR_EXTRA_LANGS=deu`, `OCR_LANGUAGES=eng+deu`, then `docker compose up -d --build` |
| Search finds nothing for older documents | They were processed before search existed: `task reindex` (or `docker compose exec worker python -m app.search.reindex`) |
| `type "vector" does not exist` when migrating | The database lacks pgvector. Use the compose `db` service (built from `backend/db/Dockerfile`) or install pgvector into your PostgreSQL |
| Tests hang or fail around OCR | Install Tesseract (`tesseract --version`); OCR tests are skipped when it is missing (except in CI) |

To start completely fresh (deletes all documents and data): `docker compose down -v`.

---

## Project structure

```text
docIS/
├── backend/
│   ├── app/
│   │   ├── api/            # FastAPI routes and dependencies
│   │   ├── core/           # settings, exceptions
│   │   ├── db/             # engine, session, declarative base
│   │   ├── models/         # SQLAlchemy models
│   │   ├── schema/         # API request/response models
│   │   ├── repositories/   # database access
│   │   ├── services/       # upload, validation of files, corrections
│   │   ├── storage/        # safe local file storage
│   │   ├── processing/     # text layer, OCR, spreadsheet parsing
│   │   ├── extraction/     # Claude extraction, normalisation, validation, review
│   │   ├── compare/        # document comparison
│   │   ├── search/         # chunking, embeddings, keyword/semantic search
│   │   ├── worker/         # Celery app, tasks, job queue, retries
│   │   └── main.py
│   ├── migrations/         # Alembic
│   ├── evaluation/         # extraction-accuracy harness (T4.2)
│   ├── db/Dockerfile       # PostgreSQL + pgvector image
│   ├── tests/
│   ├── docs/
│   ├── compose.yaml
│   ├── Dockerfile
│   ├── Taskfile.yml
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── api/            # typed API client and query hooks
│   │   ├── components/
│   │   └── pages/
│   └── package.json
└── .github/workflows/ci.yml
```

## License

Developed as a technical assignment.
