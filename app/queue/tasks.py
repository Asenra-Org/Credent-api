import logging
import asyncio
from app.queue.celery_app import celery_app

logger = logging.getLogger(__name__)

@celery_app.task(name="app.queue.tasks.sweep_outbox")
def sweep_outbox():
    logger.info("sweep_outbox task contract preserved")
    pass

@celery_app.task(name="app.queue.tasks.stage_1_ingest", bind=True)
def credent_ingest(self, data: dict):
    logger.info("stage_1_ingest task contract preserved")
    pass

@celery_app.task(name="app.queue.tasks.stage_2_analysis_group", bind=True)
def credent_analysis(self, data: dict):
    logger.info("stage_2_analysis_group task contract preserved")
    pass

@celery_app.task(name="app.queue.tasks.stage_3_synthesis_chord", bind=True)
def credent_synthesis(self, data: dict):
    logger.info("stage_3_synthesis_chord task contract preserved")
    pass

@celery_app.task(name="app.queue.tasks.ping")
def ping(payload: str = None):
    logger.info("Ping received")
    return True

@celery_app.task(name="app.queue.tasks.process_batch_item", bind=True)
def process_batch_item(self, case_id: str, storage_path: str, institution_id: str = "DEFAULT"):
    """
    Dedicated Celery task for the Batch Ingestion API.
    Delegates to the monolithic AppraisalWorker execution.
    """
    from app.services.appraisal_worker import run_appraisal_job

    logger.info(f"Executing batch item case_id={case_id}")

    # AppraisalWorker requires an event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(
            run_appraisal_job(
                case_id=case_id,
                storage_path_handle=storage_path,
                institution_id=institution_id
            )
        )
        return result
    finally:
        loop.close()
