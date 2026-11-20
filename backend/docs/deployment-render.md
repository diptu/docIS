# Deploying the backend on Render

The repository root contains a Render Blueprint, `render.yaml`. It creates:

| Resource | Render type | Purpose |
|---|---|---|
| `docis-db` | Postgres (`basic-1gb`) | data; the migrations enable `pgvector` |
| `docis-broker` | Key Value (`starter`, `noeviction`) | Celery broker |
| `docis-api` | Docker web service (`standard`, 2 GB) | API + Celery worker + Celery beat, with a 10 GB disk at `/app/storage` |

Postgres and Key Value accept private-network connections only
(`ipAllowList: []`).

## Deploy

1. Push the branch containing `render.yaml` to GitHub (or GitLab).
2. In Render: **New → Blueprint**, then pick the repository.
3. Enter `ANTHROPIC_API_KEY` when prompted. Without it, content extraction
   and search still work, but invoice extraction jobs fail.
4. Apply. The first build takes a few minutes: it installs Tesseract and
   bakes the embedding model into the image.

Each deploy runs `alembic upgrade head` (the `preDeployCommand`) before the
new version takes traffic, and a failed migration aborts the deploy. Render
then checks `GET /health`.

Check the deploy with:

```bash
curl https://<service>.onrender.com/health            # {"status":"healthy"}
curl https://<service>.onrender.com/health/database   # {"database":"healthy"}
curl https://<service>.onrender.com/docs              # OpenAPI UI
```

## How the container runs

* `scripts/docker-entrypoint.sh` starts as root only to `chown` the disk
  (Render mounts disks root-owned), then runs everything as the `app` user
  with `HOME=/app`.
* `scripts/render-start.sh` runs `fastapi run` on `$PORT`, plus one Celery
  worker with beat embedded. If either process exits, the container exits
  and Render restarts it. `SIGTERM` on deploy gives the worker a warm
  shutdown; a task that gets cut off is recovered from the `processing_jobs`
  table.
* `DATABASE_URL` from Render (`postgresql://…`) is turned into the asyncpg
  form automatically (`app/core/config.py`).

Docker Compose is unaffected: its default command runs the API only, and the
worker and beat stay separate services.

## Limits of this setup

* **One instance.** Uploads are on a local disk, and a Render disk attaches
  to exactly one service. That is why the worker shares the API's container
  and why `numInstances` is 1. Services with a disk also have no
  zero-downtime deploys: expect a short gap while each deploy restarts.
  Scaling out (separate worker services, more API instances) requires moving
  storage to object storage such as S3 or R2 behind the existing
  `LocalStorage` interface.
* **Paid plans.** Disks and pre-deploy commands are not available on free
  instance types.
* **Throughput.** `WORKER_CONCURRENCY=1` processes one document at a time to
  keep memory within 2 GB. Raise it together with the instance size.
* **No authentication.** The API has none (see `tests/README.md`).
  Everything on the public URL is open to anyone who finds it. Add
  authentication, or keep the service private, before uploading real
  documents.

## Frontend (Vercel)

`frontend/vercel.json` deploys the frontend on Vercel. In Vercel, import the
repository and set **Root Directory** to `frontend`; everything else comes
from the file:

* Vite build (`npm ci`, `npm run build`, output `dist`) on Node 22
  (`engines` in `package.json`).
* `/api/*` is proxied to `https://docis-api.onrender.com/*`, so the browser
  stays on one origin and the backend needs no CORS. If Render gave the
  service a different URL, change it in `vercel.json` (rewrites cannot read
  environment variables).
* Every other path without a static file falls back to `index.html`, so
  deep links such as `/documents/<id>` and `/search?q=…` work on reload.
* Hashed files under `/assets/` are cached for a year.

Vercel's proxy may limit request body size and response time for
rewrites to external origins, which would cap uploads below the backend's
20 MB per-file limit (`MAX_UPLOAD_SIZE_MB`). If large uploads fail, build with
`VITE_API_BASE=https://docis-api.onrender.com` instead, and enable CORS for
the Vercel domain on the backend.
