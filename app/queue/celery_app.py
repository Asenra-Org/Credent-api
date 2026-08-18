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
    broker_connection_retry_on_startup=True,
    
    # 11G Required Settings
    visibility_timeout=3600,
    unacked_check_interval=300,
    task_acks_late=True,
)

celery_app.conf.beat_schedule = {
    "durable-outbox-sweeper": {
        "task": "app.queue.tasks.sweep_outbox",
        "schedule": 10.0,
    },
}
