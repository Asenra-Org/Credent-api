import os
from celery import Celery

broker = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
backend = os.environ.get("CELERY_RESULT_BACKEND", broker)

celery_app = Celery("credent_worker", include=["app.queue.tasks"])

# Phase 7 Settings
celery_app.conf.update(
    broker_url=broker,
    result_backend=backend,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)
