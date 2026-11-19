"""
Celery application for background document processing.

Run a worker:  celery -A app.worker.celery_app worker --loglevel=INFO
Run the beat:  celery -A app.worker.celery_app beat --loglevel=INFO

Delivery guarantees:

* ``task_acks_late`` + ``task_reject_on_worker_lost``: a task is acknowledged
  only after it finishes, so if its worker process dies the broker delivers
  it again.
* ``worker_prefetch_multiplier = 1``: a worker reserves one task at a time,
  so long OCR jobs do not hold back others queued behind them.
* The ``processing_jobs`` row, not the broker, is the source of truth; every
  task claims its row atomically, so duplicate deliveries are harmless.
* ``documents.recover`` (scheduled by beat) re-dispatches jobs whose task
  message was lost and recovers jobs whose whole worker died.
"""

from celery import Celery

from app.core.config import get_settings

PROCESS_TASK = "documents.process"
RECOVER_TASK = "documents.recover"
QUEUE = "documents"

settings = get_settings()

celery_app = Celery(
    "docis",
    broker=settings.celery_broker_url,
    include=["app.worker.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_default_queue=QUEUE,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=settings.celery_task_time_limit_seconds,
    # Redis redelivers unacknowledged tasks after this long. It must exceed
    # the longest task plus the longest retry countdown, or a slow task would
    # be delivered twice (harmless, but wasted work).
    broker_transport_options={
        "visibility_timeout": settings.celery_visibility_timeout_seconds
    },
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "recover-processing-jobs": {
            "task": RECOVER_TASK,
            "schedule": settings.job_recovery_interval_seconds,
        }
    },
)
