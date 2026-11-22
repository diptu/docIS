# docIS backend

FastAPI API, Celery worker and evaluation harness for docIS.

See the [project README](../README.md) for setup and commands.

The docs folder also has:
- [Architecture](docs/architecture.md)
- [Database schema](docs/database_schema.md)
- [Evaluation](evaluation/README.md)

Quick start:

```bash
cp .env.example .env          # set ANTHROPIC_API_KEY
docker compose up --build     # db, migrations, API, worker, beat, Redis
```
