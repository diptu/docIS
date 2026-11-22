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

## Prerequisites
- Python (V 3.11.9+) and uv Package manager
- Node.js and npm
- PostgreSQL with pgvector support
- Redis
- Tesseract OCR

## Getting Started

1. Clone the repository
```bash
git clone https://github.com/diptu/docIS.git
cd docIS
```
2. Configure the environment


Create the backend environment file using the project's environment template:
```bash
cp backend/.env.example backend/.env
```
Configure the required PostgreSQL connection, Redis connection, Anthropic API key, and other application settings in the environment file.


3. Start PostgreSQL and Redis

Ensure PostgreSQL with pgvector and Redis are running and accessible using the configured connection settings.

4. Backend Setup
```bash
cd backend
uv sync
```
Run database migrations:
```bash
uv run alembic upgrade head
```

Start the API server:
```bash
uv run uvicorn app.main:app --reload --port 8000
```

5. Start the Background Worker

In a separate terminal:
```bash
cd backend
uv run celery -A app.workers.celery_app worker --loglevel=info

```
Adjust the Celery application import path if the worker entry point differs in your checkout.

6. Frontend Setup

In a separate terminal:

```bash
cd frontend
npm install
npm run dev
```
Open the Vite development URL displayed in the terminal.

#### API Documentation

Once the backend is running, interactive API documentation is available at:

Swagger UI: ```http://localhost:8000/docs```

ReDoc:``` http://localhost:8000/redoc```

Development Commands

Backend
```bash
uv sync
uv run alembic upgrade head
uv run pytest
```
Frontend

```bash
npm install
npm run dev
npm run build
npm run test
```
Run commands from their respective backend/ and frontend/ directories.


## Application Pages

- **Documents** (`/`) — Browse, upload, filter, and manage documents.
- **Document Intelligence** (`/documents/:id`) — View extracted content, review invoice data, and inspect processing history.
- **Search** (`/search`) — Search across document content.

## License

This project is licensed under the [MIT License](LICENSE).