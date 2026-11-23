# docIS — Document Intelligence System

An AI-powered document intelligence platform. It uploads, processes, extracts, validates, reviews and searches business documents: PDFs, scanned documents, images and spreadsheets.

## Features

- **Multi-format ingestion:**
  - Accepts PDF, JPG, PNG, TIFF, XLSX, XLS and CSV, many files per upload.
  - Checks magic bytes and parses each file to reject corrupted ones.
- **Document processing:**
  - PDFs use their text layer, falling back to OCR for scanned pages.
  - Images use Tesseract OCR; every frame of a multi-frame TIFF is read.
  - Every sheet of a workbook is parsed.
- **AI invoice extraction:**
  - Claude returns structured JSON: invoice number, dates, supplier, customer, subtotal, tax, total, currency and line items.
  - Missing values become `null`, and doubtful ones are flagged rather than invented.
- **Validation:**
  - Decimal arithmetic checks line items, subtotal, tax, total and amount due.
  - Also checks for missing fields, duplicates and low OCR confidence.
  - Anything that fails is flagged for manual review.
- **Human review:** correct extracted values in the UI. Every change is recorded and the checks run again.
- **Search:**
  - Keyword search: PostgreSQL full-text.
  - Semantic search: pgvector with FastEmbed embeddings.
  - Hybrid search combines the two with reciprocal rank fusion.
- **Comparison:**
  - Invoice to invoice: header fields, totals and line items.
  - Text diffs.
  - PDF/image ↔ Excel/CSV reconciliation.
- **Background processing:**
  - Celery workers with persistent job state, progress, retries with backoff, and recovery of stuck jobs (Celery beat).
- **Evaluation:** offline harness measuring extraction accuracy, validation precision/recall, processing time and success rate on labelled datasets.

## Tech Stack

| Layer | Technologies |
|---|---|
| Backend | Python 3.11, FastAPI, Pydantic |
| Database | PostgreSQL + pgvector, SQLAlchemy 2 (async), Alembic |
| Processing | pypdf, pypdfium2, Tesseract (pytesseract), Pillow, openpyxl, xlrd, csv |
| AI | Anthropic API (Claude Opus 5.5, structured output), FastEmbed (BAAI/bge-small-en-v1.5) |
| Background jobs | Celery + Redis |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS, TanStack Query |
| Testing | Pytest, Vitest, React Testing Library |
| Infrastructure | Docker Compose; Render (backend) and Vercel (frontend) configs |

## Documentation

| Document | Contents |
|---|---|
| [`backend/docs/architecture.md`](backend/docs/architecture.md) | Architecture and processing-pipeline diagrams |
| [`backend/docs/database_schema.md`](backend/docs/database_schema.md) | ER diagram, tables, indexes |
| [`backend/evaluation/README.md`](backend/evaluation/README.md) | Evaluation datasets, metrics and commands |
| [`deliverables.md`](deliverables.md) | Assignment submission: results, design decisions, AI disclosure |

## Project Structure

```text

docIS/
├── backend/
│   ├── app/
│   │   ├── api/           # API routes and dependencies
│   │   ├── compare/       # Document comparison and reconciliation
│   │   ├── core/          # Configuration, exceptions, logging
│   │   ├── db/            # Database setup and sessions
│   │   ├── extraction/    # LLM extraction, normalisation, validation, review
│   │   ├── models/        # Database models
│   │   ├── processing/    # OCR, PDF and spreadsheet processing
│   │   ├── repositories/  # Data access layer
│   │   ├── search/        # Chunking, embeddings and search
│   │   ├── services/      # Business logic, file-type checks
│   │   ├── storage/       # File storage
│   │   └── worker/        # Celery app, tasks, retries, recovery
│   ├── migrations/        # Alembic migrations
│   ├── tests/             # Unit, API, integration and E2E tests
│   ├── evaluation/        # Evaluation harness and datasets
│   └── docs/              # Architecture and schema docs
│
├── frontend/
│   └── src/
│       ├── api/           # API client, hooks and types
│       ├── components/    # Reusable UI components
│       ├── pages/         # Application pages
│       ├── lib/           # Shared utilities
│       └── test/          # Test setup and fixtures
│
└── README.md
```

## Quick Start with Docker (Ubuntu)

Run the whole app from the published images on [Docker Hub](https://hub.docker.com/r/diptu/docis/tags). You don't need the source code, Python, Node, Tesseract or a database: only Docker, one file and your Anthropic API key. PostgreSQL with pgvector, Redis, the database tables and the pgvector extension are all set up automatically on the first start. The images are built for `linux/amd64` and `linux/arm64`.

| Image | What it runs |
|---|---|
| `diptu/docis:backend-latest` | API, background worker and scheduler (with Tesseract OCR and the search model built in) |
| `diptu/docis:frontend-latest` | The web app (nginx), which forwards `/api` to the API |
| `diptu/docis:db-latest` | PostgreSQL 18 with pgvector |

Each image also has a fixed version tag, such as `backend-0.1.0`.

**1. Install Docker** (Ubuntu 22.04 or 24.04; skip if `docker compose version` already works):

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER    # run docker without sudo
newgrp docker                    # or log out and back in
docker compose version           # should print v2.x or later
```

**2. Download the compose file:**

```bash
mkdir -p ~/docis && cd ~/docis
curl -fsSLO https://raw.githubusercontent.com/diptu/docIS/main/deploy/docker-compose.yml
```

Or copy [`deploy/docker-compose.yml`](deploy/docker-compose.yml) from this repository into an empty folder.

**3. Add your Anthropic API key**, from [console.anthropic.com](https://console.anthropic.com). This is the only setting you need:

```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env    # replace with your key
```

Without a key, documents are still stored, OCR'd and searchable, but invoice extraction fails with a clear message.

Everything else has a working default. To change ports, upload limits, the worker count, the model or the database password, start from the full template instead: `curl -fsSL https://raw.githubusercontent.com/diptu/docIS/main/deploy/.env.example -o .env`, then edit it ([`deploy/.env.example`](deploy/.env.example) explains each setting).

**4. Start it:**

```bash
docker compose up -d       # pulls the images on first run (about 1.5 GB)
docker compose ps          # all services "running"/"healthy"; migrate "exited (0)"
```

Open **http://localhost:8080** (or `http://<server-ip>:8080` from another machine). The API answers on `http://localhost:8000` from the server itself, with interactive docs at `/docs`.

**Everyday commands** (run them in `~/docis`):

| Task | Command |
|---|---|
| Follow the logs | `docker compose logs -f api worker` |
| Apply changed `.env` settings | `docker compose up -d` |
| Update to the newest images | `docker compose pull && docker compose up -d` |
| Pin a version | set `DOCIS_VERSION=0.1.0` in `.env`, then `docker compose up -d` |
| Process more documents in parallel | `docker compose up -d --scale worker=3` |
| Stop (keeps all data) | `docker compose down` |
| Delete everything, including all documents | `docker compose down -v` |
| Back up the database | `docker compose exec db pg_dump -U docis docis > docis-backup.sql` |

Documents, the database and the job queue live in Docker volumes (`documents-data`, `db-data`, `redis-data`), so they survive restarts and updates.

**If something goes wrong:**
- **Port 8080 already in use:** set another `WEB_PORT` in `.env`, then `docker compose up -d`.
- **Invoice extraction failed: "out of credits" or "authentication":** check `ANTHROPIC_API_KEY` and your API balance, then click **Retry** on the document.
- **Can't reach it from another machine:** open the port in the firewall (`sudo ufw allow 8080/tcp`).
- **Security:** the app has no login. Anyone who can reach port 8080 can see, change and delete documents, so expose it only on a trusted network or behind a VPN or an authenticating proxy.

To build the images yourself from source instead, see [2a](#2a-run-everything-with-docker-quickest).

## Prerequisites

- Python 3.11.9+ and [uv](https://docs.astral.sh/uv/)
- Node.js 22 and npm
- Docker and Docker Compose (recommended; provides PostgreSQL + pgvector and Redis), or PostgreSQL 15+ with pgvector and Redis installed natively (see [1.2](#12-postgresql--pgvector))
- Tesseract OCR, needed only when running the backend outside Docker (`brew install tesseract` / `apt-get install tesseract-ocr`)
- An Anthropic API key, for invoice extraction
- Optional: [Task](https://taskfile.dev), for the shortcuts in `backend/Taskfile.yml`

## Getting Started

### 1. Clone and configure

```bash
git clone https://github.com/diptu/docIS.git
cd docIS
cp backend/.env.example backend/.env
```

The rest of this step covers what to put in `backend/.env`, setting up PostgreSQL + pgvector, and getting an Anthropic API key.

#### 1.1 The `.env` file

`backend/.env` is read by:
- the API, the Celery worker and beat (through `pydantic-settings`)
- Alembic
- Docker Compose (for variable substitution in `compose.yaml`)

It is gitignored. **Never commit it.** Only `backend/.env.example` belongs in git.

The variables you usually need to change:

| Variable | Default | When to change it |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(empty)* | **Required** for invoice extraction (see 1.3). |
| `DATABASE_URL` | `postgresql+asyncpg://docis:docis@localhost:5434/docis` | When the database is not the Docker one on port 5434, for example a native install on 5432 or a hosted database. `postgres://` and `postgresql://` URLs are accepted and converted to `postgresql+asyncpg://`. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` / `POSTGRES_PORT` | `docis` / `docis` / `docis` / `5434` | Credentials and host port for the Docker database. If you change them, update `DATABASE_URL` to match. **Use a strong password** anywhere other than your own machine. |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | When Redis runs somewhere else. |
| `STORAGE_DIR` | `storage` | Where uploaded originals are kept. |
| `MAX_UPLOAD_SIZE_MB` / `MAX_FILES_PER_UPLOAD` | `50` / `20` | Upload limits. |
| `OCR_LANGUAGES` / `OCR_EXTRA_LANGS` | `eng` / *(empty)* | To add OCR languages, e.g. `OCR_LANGUAGES=eng+deu` and `OCR_EXTRA_LANGS="deu"`. The languages must also be installed in Tesseract or built into the Docker image. |
| `SEMANTIC_SEARCH_ENABLED` | `true` | Set to `false` to skip embeddings; keyword search still works. |
| `INVOICE_EXTRACTION_ENABLED` | `true` | Set to `false` to run without an Anthropic key. Text extraction, OCR and search still work, but no invoice fields are extracted. |
| `LLM_MODEL` / `LLM_EFFORT` | `claude-opus-5-5` / `medium` | The Claude model and effort level used for extraction. |
| `LLM_CHUNK_CHARS` / `LLM_MAX_PARTS` / `LLM_PART_CONCURRENCY` | `50000` / `120` / `4` | Long documents are extracted in parts of about this many characters, split between pages and sheets, several at a time, then merged into one invoice. A document needing more than `LLM_MAX_PARTS` parts (about 6 million characters) fails, to cap cost. |
| `RATE_LIMIT_PER_MINUTE` / `RATE_LIMIT_PROCESSING_PER_MINUTE` | `600` / `20` | Requests per minute per client IP, and the smaller limit for uploads and retries. `RATE_LIMIT_ENABLED=false` turns limiting off. |
| `RATE_LIMIT_TRUSTED_PROXY_HOPS` | `0` | How many reverse proxies in front of the API add to `X-Forwarded-For`. Leave at `0` when clients connect directly. Behind proxies, set it to the number of proxies, or every user will share one limit. |

**After editing `.env`:**
- Restart the API, worker and beat; settings are read once at startup.
- With Docker, run `docker compose up -d` again so the containers are recreated with the new values.
- When upgrading from an older checkout, compare against the template with `diff backend/.env backend/.env.example` and copy over any variables you don't have yet.

#### 1.2 PostgreSQL + pgvector

docIS needs **PostgreSQL 15 or later with the [pgvector](https://github.com/pgvector/pgvector) extension**, which powers semantic search. Choose one of the three options below.

**Option A — Docker (recommended).** `backend/db/Dockerfile` builds PostgreSQL 18 with pgvector 0.8.7, so there is nothing to install:

```bash
cd backend
docker compose up -d db              # listens on localhost:5434
docker compose exec db pg_isready    # should print "accepting connections"
```

The default `DATABASE_URL` already points at this database. Data persists in the `db-data` Docker volume. `docker compose down -v` deletes it.

**Option B — native install.**

macOS (Homebrew):

```bash
brew install postgresql@17 pgvector
brew services start postgresql@17
```

Ubuntu/Debian, after adding the [PostgreSQL apt repository](https://www.postgresql.org/download/linux/ubuntu/):

```bash
sudo apt install postgresql-17 postgresql-17-pgvector
```

Then create the user and databases, and enable pgvector. Enabling the extension needs a superuser. The migrations run `CREATE EXTENSION IF NOT EXISTS vector`, but the app's user is not normally allowed to create it.

```bash
# macOS: psql postgres     Linux: sudo -u postgres psql
CREATE USER docis WITH PASSWORD 'change-me';
CREATE DATABASE docis OWNER docis;
CREATE DATABASE docis_test OWNER docis;          -- for the PostgreSQL integration tests
\c docis
CREATE EXTENSION IF NOT EXISTS vector;
\c docis_test
CREATE EXTENSION IF NOT EXISTS vector;
```

Then point `backend/.env` at it. A native install normally uses port 5432:

```dotenv
DATABASE_URL=postgresql+asyncpg://docis:change-me@localhost:5432/docis
```

**Option C — hosted PostgreSQL** (Render, Neon, Supabase, AWS RDS, …). Choose a provider that supports pgvector, run `CREATE EXTENSION IF NOT EXISTS vector;` once, and paste the connection URL into `DATABASE_URL`.

**Apply the schema and verify** (all options):

```bash
cd backend
uv run alembic upgrade head          # creates all tables and indexes, including HNSW and GIN
uv run alembic current               # shows the latest revision (head)
```

For Option A, check pgvector with:

```bash
docker compose exec db psql -U docis -d docis -c "SELECT extversion FROM pg_extension WHERE extname = 'vector';"
```

For a native or hosted database, run `psql "postgresql://docis:change-me@localhost:5432/docis" -c "SELECT extversion FROM pg_extension WHERE extname = 'vector';"`.

Troubleshooting:
- **`extension "vector" is not available`:** pgvector is not installed for this PostgreSQL version. Install the package that matches your server version.
- **`permission denied to create extension "vector"`:** run `CREATE EXTENSION vector;` once as a superuser, as shown above.
- **`connection refused` on 5434:** the Docker database isn't running (`docker compose up -d db`), or you are using a native install on 5432; update `DATABASE_URL`.

#### 1.3 Anthropic API key

Invoice extraction calls Claude through the Anthropic API.

1. Sign in at [console.anthropic.com](https://console.anthropic.com).
2. Add credits under **Settings → Billing**. Requests fail with a "credit balance too low" error when the balance is empty. The app shows that as an "out of credits" message on the document, and you can click **Retry** once credits are added.
3. Create a key under **Settings → API Keys → Create Key**. Keys start with `sk-ant-` and are shown only once.
4. Put it in `backend/.env`:

   ```dotenv
   ANTHROPIC_API_KEY=sk-ant-...
   ```

5. Optionally check that the key works:

   ```bash
   cd backend && set -a && source .env && set +a
   curl -s https://api.anthropic.com/v1/models \
     -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" | head -c 300
   ```

   A list of models means the key works; `authentication_error` means it doesn't.

6. Restart the worker; it is the process that calls Claude. Docker Compose passes the key to the worker container from `backend/.env`.

Guidelines:
- **Keep the key secret:**
  - Only in `backend/.env` or your host's secret store (on Render, `ANTHROPIC_API_KEY` is a `sync: false` secret).
  - Never in code, commits, screenshots, chat or logs.
- **Use a separate key per environment** (development, evaluation, production), so one can be revoked without affecting the others.
- **If a key leaks, revoke it** in the console immediately and create a new one.
- **Watch spend.** The model is set by `LLM_MODEL`, default `claude-opus-5-5`. As a reference, the 181-document evaluation cost $4.62, about $0.03 per document. Set a monthly spend limit in the console.
- **Running without a key:** set `INVOICE_EXTRACTION_ENABLED=false`. Everything except invoice-field extraction keeps working.
- **Paid evaluation:** `task eval` and `task eval-3p` call Claude and cost money. The `*-check` and `*-validate` tasks are free.

### 2a. Run everything with Docker (quickest)

```bash
cd backend
docker compose up --build
```

This builds the images from source and starts PostgreSQL + pgvector, runs the migrations, and starts the API on http://localhost:8000, the Celery worker, Celery beat, Redis and the web app on http://localhost:8080.

To publish the images to Docker Hub (maintainers; needs `docker login`), build them for both platforms and push:

```bash
V=0.1.0; P=linux/amd64,linux/arm64
docker buildx build --platform $P -t diptu/docis:db-$V       -t diptu/docis:db-latest       --push backend/db
docker buildx build --platform $P -t diptu/docis:frontend-$V -t diptu/docis:frontend-latest --push frontend
docker buildx build --platform $P -t diptu/docis:backend-$V  -t diptu/docis:backend-latest  --push backend
```

Run these from the repository root.

### 2b. Run the backend locally (development)

Start only the database and Redis in Docker:

```bash
cd backend
docker compose up -d db redis
uv sync --all-groups
uv run alembic upgrade head
```

Then run each process in its own terminal:

```bash
uv run fastapi dev app/main.py                                                   # API  (task dev)
uv run celery -A app.worker.celery_app worker --queues=documents --loglevel=INFO # worker (task worker)
uv run celery -A app.worker.celery_app beat --loglevel=INFO                      # beat   (task beat)
```

Celery beat schedules recovery of interrupted or undispatched jobs. Run exactly one beat process.

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The Vite dev server proxies `/api` to the backend on port 8000.

### API documentation

With the backend running:

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

Main endpoints:

| Method & path | Purpose |
|---|---|
| `POST /documents` | Upload one or more documents |
| `GET /documents` | List with filters (`status`, `file_type`, `needs_review`) |
| `GET /documents/{id}` · `/processing` · `/processing/history` | Document, live progress, job history |
| `POST /documents/{id}/processing/retry` | Reprocess a failed document |
| `GET /documents/{id}/pages` · `/sheets` | Extracted text and spreadsheet data |
| `GET /documents/{id}/invoice` | Extracted invoice fields and line items |
| `PUT /documents/{id}/invoice` | Manual correction (re-validates) |
| `GET /documents/{id}/invoice/corrections` | Correction history |
| `GET /documents/{id}/validation-errors` · `PATCH …/{error_id}` | Review and resolve validation errors |
| `GET /invoices/review` | Review queue |
| `GET /search?q=…&mode=keyword\|semantic\|hybrid` | Search |
| `GET /compare` | Compare documents |

## Testing

```bash
# Backend (from backend/)
uv run pytest                      # 829 tests; PostgreSQL and real-embedding tests are skipped
TEST_POSTGRES_URL=postgresql+asyncpg://docis:docis@localhost:5434/docis_test \
RUN_EMBEDDING_TESTS=1 uv run pytest  # full suite: 851 tests
task test-report                   # full suite, backend + frontend, with saved reports (see below)

# Frontend (from frontend/)
npm run test
npm run build
```

`task test-report` needs PostgreSQL running (`docker compose up -d db`). It
saves JUnit XML, coverage (XML and HTML) and a Markdown summary in
`backend/test-results/` and `frontend/test-results/`.

Latest results:

| Suite | Result |
|---|---|
| Backend | 851 passed, 0 failed, 99.74% coverage |
| Frontend | 96 passed in 16 files |

## Evaluation

```bash
cd backend
task eval-3p-validate   # validation precision/recall on the third-party dataset (free)
task eval-3p            # extraction accuracy with Claude (costs API credit)
task eval-report        # rebuild the HTML reports
task reconcile-generate # labelled PDF-PDF and PDF-Excel comparison pairs (free)
task reconcile          # measure cross-document comparison on them (free)
```

Results of the latest run are in [`deliverables.md`](deliverables.md#deliverable-7--evaluation-report). The run used 181 labelled third-party documents with Claude Opus 5.5:

- Field accuracy: 94.6%
- Exact-invoice accuracy: 80.6%
- Invented values: 0.0%
- Document success rate: 99.4%
- Median processing time: 5.8 s

## Application Pages

- **Documents** (`/`): browse, upload, filter and manage documents.
- **Document detail** (`/documents/:id`): view the original and its extracted text or sheets; review and correct invoice data; resolve validation errors; see processing and correction history.
- **Processing** (`/processing`): monitor background jobs across documents.
- **Compare** (`/compare`): compare two or more documents field by field, or reconcile a PDF/image with a spreadsheet.
- **Search** (`/search`): keyword, semantic or hybrid search across document content.

## License

This project is licensed under the [MIT License](LICENSE).
