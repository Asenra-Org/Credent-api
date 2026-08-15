from celery import Celery

celery_app = Celery("credent_worker")

# Phase 7 Settings
celery_app.conf.update(
    broker_url="redis://localhost:6379/0",
    result_backend="redis://localhost:6379/0",
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
