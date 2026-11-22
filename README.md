# docIS — Document Intelligence System

An AI-powered document intelligence platform for uploading, processing, extracting, reviewing, and searching information across PDFs, scanned documents, and spreadsheets.

## Features

- **Document Management** — Upload, organize, view, and delete documents.
- **Document Processing** — Extract text using PDF parsing and OCR.
- **Invoice Intelligence** — Extract invoice data, validate fields, detect issues, and review corrections.
- **Search** — Find document content using keyword and semantic search.
- **Background Processing** — Run asynchronous jobs with progress tracking and retry support.
- **Evaluation** — Measure extraction quality against a curated dataset.

## Tech Stack

| Layer | Technologies |
|---|---|
| Backend | Python, FastAPI |
| Database | PostgreSQL, SQLAlchemy, Alembic |
| Processing | OCR, PDF parsing, spreadsheet extraction |
| Background Jobs | Celery |
| Search | Text chunking, embeddings, semantic search |
| Frontend | React, TypeScript, Vite |
| Styling | Tailwind CSS |
| Data Fetching | TanStack Query |
| Testing | Pytest, Vitest, React Testing Library |
| Infrastructure | Docker Compose |

## Project Structure

```text
docIS/
├── backend/
│   ├── app/
│   │   ├── api/           # API routes and dependencies
│   │   ├── core/          # Configuration and shared utilities
│   │   ├── db/            # Database setup and sessions
│   │   ├── models/        # Database models
│   │   ├── processing/    # OCR, PDF and spreadsheet processing
│   │   ├── repositories/  # Data access layer
│   │   ├── search/        # Chunking, embeddings and search
│   │   ├── services/      # Business logic
│   │   ├── storage/       # File storage
│   │   └── worker/        # Background jobs
│   ├── migrations/        # Database migrations
│   ├── tests/             # Unit, API, integration and E2E tests
│   ├── evaluation/        # Extraction evaluation and datasets
│   └── docs/              # Architecture and technical docs
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

## Application Pages

- **Documents** (`/`) — Browse, upload, filter, and manage documents.
- **Document Intelligence** (`/documents/:id`) — View extracted content, review invoice data, and inspect processing history.
- **Search** (`/search`) — Search across document content.

## Development

The backend and frontend are maintained as separate applications.

- Backend dependencies and commands are managed with `uv` and the project's Taskfile.
- Frontend dependencies and scripts are managed with npm.
- Docker Compose provides the supporting infrastructure.
- Database schema changes are managed through Alembic migrations.

Refer to `backend/docs/` and the backend/frontend project configuration files for detailed setup and development instructions.

## Testing

- **Backend:** Pytest — unit, API, integration, and end-to-end tests.
- **Frontend:** Vitest and React Testing Library.

## Project Goal

Make unstructured business documents easier to process and understand by combining document extraction, structured data validation, human review, and intelligent search in one platform.

