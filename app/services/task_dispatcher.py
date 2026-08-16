# =============================================================================
# CREDENT — Task Dispatcher (ASE-52)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
"""
TaskDispatcher — Environment-aware task routing for async appraisal jobs.

Architecture: Strategy Pattern with two concrete adapters.

┌──────────────────────────────────────────────────────────────┐
│                     TaskTransport (ABC)                       │
│  dispatch(case_id, storage_path, institution_id) → None      │
└─────────────────────┬────────────────────────────────────────┘
                      │
          ┌───────────┴───────────┐
          │                       │
┌─────────▼──────────┐  ┌────────▼────────────┐
│ BackgroundTask     │  │ CeleryAdapter        │
│ Adapter            │  │                      │
│ (USE_CELERY=false) │  │ (USE_CELERY=true)    │
│                    │  │                      │
│ Runs appraisal     │  │ Enqueues to Redis    │
│ in FastAPI         │  │ for Celery worker    │
│ BackgroundTasks    │  │ consumption          │
└────────────────────┘  └──────────────────────┘

Usage in routes:
    dispatcher = get_dispatcher(background_tasks)
    dispatcher.dispatch(case_id, storage_path, institution_id)
"""
import asyncio
import logging
import os
from abc import ABC, abstractmethod
from fastapi import BackgroundTasks

logger = logging.getLogger(__name__)


# =============================================================================
# Abstract Interface
# =============================================================================

class TaskTransport(ABC):
    """
    Abstract task transport. Implementations must dispatch an appraisal job
    without blocking the HTTP request thread.
    """

    @abstractmethod
    def dispatch(self, case_id: str, storage_path: str, institution_id: str) -> None:
        """
        Dispatch an appraisal job for the given case.
        Must return immediately without waiting for completion.
        """
        ...


# =============================================================================
# Adapter 1: BackgroundTaskAdapter (Local Development)
# =============================================================================

class BackgroundTaskAdapter(TaskTransport):
    """
    Dispatches appraisal jobs using FastAPI's built-in BackgroundTasks.

    Trade-offs vs Celery:
      ✓ Zero infrastructure — no Redis or worker process needed
      ✓ Jobs run in the same process — easy to debug
      ✗ Not persistent across server restarts
      ✗ No distributed execution across multiple workers

    Appropriate for: local development, staging environments, single-instance deploys.
    """

    def __init__(self, background_tasks: BackgroundTasks):
        self._background_tasks = background_tasks

    def dispatch(self, case_id: str, storage_path: str, institution_id: str) -> None:
        """Register the appraisal job to run after the HTTP response is sent."""
        self._background_tasks.add_task(
            _run_job_in_background,
            case_id=case_id,
            storage_path=storage_path,
            institution_id=institution_id
        )
        logger.info(f"[BackgroundTaskAdapter] ✓ Dispatched case_id={case_id} via BackgroundTasks")


def _run_job_in_background(case_id: str, storage_path: str, institution_id: str) -> None:
    """
    Sync wrapper around the async appraisal worker.
    BackgroundTasks runs in a thread pool, so we need to create an event loop.
    """
    from app.services.appraisal_worker import run_appraisal_job
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_appraisal_job(case_id, storage_path, institution_id))
        finally:
            loop.close()
    except Exception as e:
        logger.error(f"[BackgroundTaskAdapter] Unhandled error in background job case_id={case_id}: {e}", exc_info=True)


# =============================================================================
# Adapter 2: CeleryAdapter (Production)
# =============================================================================

class CeleryAdapter(TaskTransport):
    """
    Dispatches appraisal jobs to a Celery queue backed by Redis.

    Trade-offs vs BackgroundTaskAdapter:
      ✓ Persistent across server restarts (jobs survive crashes)
      ✓ Distributed — scales horizontally with multiple workers
      ✓ Full observability via Celery monitoring tools (Flower, etc.)
      ✗ Requires Redis and a running Celery worker process

    Appropriate for: production deployments with proper infrastructure.
    """

    QUEUE_NAME = "appraisal"
    TASK_NAME = "app.queue.tasks.stage_1_ingest"

    def dispatch(self, case_id: str, storage_path: str, institution_id: str) -> None:
        """Publish the appraisal task to the Celery queue."""
        try:
            from app.queue.celery_app import celery_app
            result = celery_app.send_task(
                self.TASK_NAME,
                kwargs={
                    "case_id": case_id,
                    "storage_path": storage_path,
                    "institution_id": institution_id
                },
                queue=self.QUEUE_NAME,
            )
            logger.info(f"[CeleryAdapter] ✓ Dispatched case_id={case_id} → Celery task_id={result.id}")
        except Exception as e:
            logger.error(f"[CeleryAdapter] Failed to dispatch case_id={case_id}: {e}", exc_info=True)
            raise RuntimeError(f"Celery dispatch failed: {e}") from e


# =============================================================================
# Factory Function
# =============================================================================

def get_dispatcher(background_tasks: BackgroundTasks) -> TaskTransport:
    """
    Factory that returns the appropriate TaskTransport based on environment config.

    Environment variable:
        USE_CELERY=true   → CeleryAdapter (production)
        USE_CELERY=false  → BackgroundTaskAdapter (default, local dev)

    The explicit default of `false` means the system works out-of-the-box
    on any developer machine without needing Redis or a Celery worker.
    """
    use_celery = os.getenv("USE_CELERY", "false").lower().strip() == "true"

    if use_celery:
        logger.info("[TaskDispatcher] Using CeleryAdapter (USE_CELERY=true)")
        return CeleryAdapter()
    else:
        logger.info("[TaskDispatcher] Using BackgroundTaskAdapter (USE_CELERY=false)")
        return BackgroundTaskAdapter(background_tasks)
