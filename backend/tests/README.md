# Backend test suite

```bash
task test               # everything (PostgreSQL tests skip without TEST_POSTGRES_URL)
task test-unit          # ~2 s   pure logic
task test-api           # ~2 s   HTTP contract, no database
task test-integration   # ~15 s  real SQLite, filesystem, Tesseract, worker
task test-e2e           # ~5 s   client workflows through the whole app
task test-postgres      # ~6 s   PostgreSQL + pgvector (Docker db, database docis_test)
RUN_EMBEDDING_TESTS=1 uv run pytest -m embeddings   # the real embedding model
```

Each layer is a directory **and** a marker (applied automatically from the
directory in `tests/conftest.py`), so `pytest tests/api` and `pytest -m api`
select the same tests.

## Layers

| Layer | Directory | Real | Replaced | Answers |
|---|---|---|---|---|
| Unit | `unit/` | the function or class under test | everything it calls (autospecced doubles) | Is the rule right? |
| API | `api/` | routing, validation, serialisation, error handlers, dependency wiring | repository / service (autospec), database (`get_db` fails loudly if reached) | Is the HTTP contract right? |
| Integration | `integration/` | SQLite or **PostgreSQL**, the filesystem, Tesseract, pdfium, the job queue and worker | Claude (FakeInvoiceLLM), the embedding model (FakeEmbedder), Celery broker | Do the parts work together on real infrastructure? |
| E2E | `e2e/` | the whole app over HTTP: routes → services → repositories → storage → in-process worker | the same three external services | Does a user's workflow work? |

Boundaries in practice:

* A test that needs a database is integration or E2E, never unit or API.
* API tests assert status codes, response bodies, and *which collaborator was
  called with what*; they never assert database state.
* E2E tests drive only the HTTP API (plus `run_worker()` for background jobs
  and, rarely, a factory to force a state a client cannot reach, such as a
  job stuck in `running`).
* PostgreSQL-only behaviour (migrations, partial unique index, row locks,
  full-text search, pgvector) is tested in `integration/postgres/`. SQLite
  runs the portable fallbacks.

## Fixtures and isolation

| Fixture | Scope | Where | Provides |
|---|---|---|---|
| `db_engine`, `session_factory` | function | `conftest.py` | a fresh SQLite file with the full schema and foreign keys on |
| `make_runtime` | function | `conftest.py` | a worker `Runtime` on that database and a temp storage dir |
| `settings`, `overrides`, `client` | function | `api/conftest.py` | small-limit Settings; `app.dependency_overrides` (always cleared); `httpx.AsyncClient` over `ASGITransport` |
| `repository`, `service` | function | `api/conftest.py` | `create_autospec` doubles installed as dependency overrides |
| `client`, `run_worker` | function | `e2e/conftest.py` | AsyncClient on the real app + SQLite; `await run_worker(llm=None)` runs every queued job |
| `pg_url` → `migrated` | session | `integration/postgres/conftest.py` | schema dropped and rebuilt **by Alembic** once per run |
| `pg_engine`, `pg_factory` | function | `integration/postgres/conftest.py` | engine on the migrated DB; every table truncated after each test |

Shared code that is not a fixture lives in `support/`:
`builders.py` (unsaved ORM objects for mocked repositories),
`factories.py` (database rows for integration/E2E), `fakes.py` (LLM,
embedder, session, processor doubles), `helpers.py` (generated PDFs, images,
spreadsheets), `api.py` (client steps such as `upload`), `worker.py`
(`run_due_jobs`).

Isolation guarantees:

* Every database test gets its own SQLite file (or truncated PostgreSQL
  tables); every storage path is under `tmp_path`.
* Dependency overrides are cleared in fixture teardown, also on failure.
* `asyncio_mode = "auto"`, function-scoped loops; engines use `NullPool`
  so no connection outlives its event loop.
* The suite passes in reverse order and in shuffled orders (checked when
  this layout was introduced; a leaked Pillow global was found and fixed
  that way). There is no random-order plugin in CI; see gaps.
* The PostgreSQL fixtures refuse any database whose name does not end in
  `_test`, because they drop the schema.

## PostgreSQL setup

Local (the Docker Compose `db` service, port 5434):

```bash
docker exec docis-db-1 createdb -U docis docis_test   # once
task test-postgres
```

CI (`.github/workflows/ci.yml`, job *Database*) runs a
`pgvector/pgvector:pg18` service with `TEST_POSTGRES_URL` set, and runs
`pytest -m postgres` after the migration checks.

## Coverage and quality review

Line coverage is 99.4% with PostgreSQL enabled (99.0% without). The rest of
this section covers what coverage does not show.

### Untested or weakly tested behaviour

* **Real Celery and Redis.** `run_worker()` calls the same `execute_job` the
  Celery task calls, and `tests/integration/worker/test_tasks.py` covers the
  task wrapper, but no test sends a message through a real broker
  (acks_late redelivery, beat scheduling, worker shutdown).
* **Real Claude.** Every test uses FakeInvoiceLLM; `tests/unit/extraction/
  test_llm.py` checks request building and error mapping against a mocked
  SDK. Extraction quality is measured separately by `evaluation/` (`task
  eval`), which costs money and is not part of the test suite.
* **The real embedding model** runs only with `RUN_EMBEDDING_TESTS=1`
  (`tests/integration/search/test_embeddings.py`).
* **Load and limits.** No test covers large uploads near the size limit
  under concurrency, very large spreadsheets end to end, or the search
  candidate cap under a big corpus.
* **The frontend contract.** The frontend types in `frontend/src/api/types.ts`
  are maintained by hand; no test checks them against `/openapi.json`.
  `api/test_health.py` only checks that the schema builds and lists the
  routes.
* **Order independence is not enforced in CI.** Adding `pytest-randomly`
  (a new dev dependency) would catch the next leaked global automatically.

### Security, authorization and multi-tenancy

The application has **no authentication, authorization or tenant model**.
No route depends on a user; `app/models/user.py` defines a table nothing
uses. Consequently there are no auth tests: writing them would mean
inventing rules the code does not have. Today, any client that can reach
the API can:

* list, read, download, correct and **delete any document** (including bulk
  delete of up to 200 per request);
* search the full text of every document;
* trigger reprocessing (and so Claude spend) via
  `POST /documents/{id}/processing/retry`.

Before exposing the API beyond a trusted network, these need decisions, then
tests: authentication on every route except `/health`, document ownership or
tenant scoping (every repository query filtered by owner, with tests that
user A gets **404, not 403**, for user B's documents, so IDs are not
confirmed), role checks for delete and correction, and rate limits on
upload, retry and search.

What *is* tested on the security side: path traversal in upload file names,
content-type verification against magic bytes (renamed files rejected),
decompression bombs, password-protected PDFs, read-only stored originals in
private directories, `nosniff` and `Content-Security-Policy: sandbox` on
downloads, storage keys confined to the storage root, and validation of
every query parameter, path ID and request body.

### Assumptions

* Tesseract must be installed for OCR tests; they skip (`requires_tesseract`)
  otherwise. CI installs it.
* `TEST_POSTGRES_URL` must point at PostgreSQL 18 with pgvector available;
  the migrations run `CREATE EXTENSION vector`.
* E2E tests use `ASGITransport`, which does not run the app's lifespan (it
  only disposes the connection pool); `api/test_health.py` tests the
  lifespan directly.
