# docIS Sprint Tasks

## Deliverables

0. Git repository containing the source code.

- [x] **T 0.1 — Initialize Git Repository:** Set up project structure, `.gitignore`, and initial commit.
- [x] **T 0.2 — Database Integration:** Configure PostgreSQL connection, async SQLAlchemy engine, session management, and base ORM models.
- [x] **T 0.3 — Database Migrations:** Integrate Alembic, configure migration management, and create the initial database migration.
- [x] **T 0.4 — CI Pipeline:** Set up automated linting, type checking, and test workflows.
- [x] **T 0.5 — Dockerize Backend:** Create Dockerfile and Docker Compose configuration for FastAPI and PostgreSQL.
- [x] **T 0.6 — Environment Configuration:** Set up environment-based configuration and secure database credentials management.


1.1 Document ingestion : Route: /documents

- [x] **T 1.1.1- Upload multiple PDF files**
- [x] **T 1.1.2- Upload JPG, PNG and TIFF images**
- [x] **T 1.1.3- Upload XLSX, XLS and CSV files**
- [x] **T 1.1.4 Reject unsupported and corrupted files**
- [x] **T 1.1.5 Store original documents safely**

1.2 Document processing

- [x] **T 1.2.1-  Extract digital PDF text**
    - password protected files rejected
- [x] **T 1.2.2-  OCR scanned PDF pages and images**
- [x] **T 1.2.3- Parse multiple Excel sheets**
- [x] **T 1.2.4-  Parse CSV files**
- [x] **T 1.2.5-  Track background processing progress**
- [x] **T 1.2.6-  Retry failed processing jobs**
