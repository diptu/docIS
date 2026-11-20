#!/bin/bash
# Run the API, a Celery worker and Celery beat in one container.
#
# Why one container: uploads are stored on a local disk, and the worker
# must read the files the API wrote. A Render persistent disk attaches to a
# single service, so the worker cannot be a separate service without moving
# storage to object storage (e.g. S3). Beat runs embedded in the worker
# (--beat), which is safe because there is exactly one instance.
#
# If either process exits, the other is stopped and the script exits with
# that status, so the platform restarts the whole container. SIGTERM (sent
# on deploys and restarts) is passed on: the worker finishes running tasks
# (warm shutdown); anything cut off is recovered from the jobs table.
set -uo pipefail

celery -A app.worker.celery_app worker \
    --beat --schedule=/tmp/celerybeat-schedule \
    --loglevel=INFO --queues=documents \
    --concurrency="${WORKER_CONCURRENCY:-1}" &
worker=$!

fastapi run app/main.py --host 0.0.0.0 --port "${PORT:-8000}" &
api=$!

stop() {
    kill -TERM "$api" "$worker" 2>/dev/null
}
trap 'stop; wait; exit 0' TERM INT

# Wait for the first process to exit, then take the other one down too.
wait -n "$api" "$worker"
status=$?
echo "render-start: a process exited with status $status; stopping the container" >&2
stop
wait
exit "$status"
