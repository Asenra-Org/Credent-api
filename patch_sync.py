import re

file_path = r"D:\coding\Credent-api\app\services\outbox_dispatcher.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

sync_adapter = """
class SyncLocalTransportAdapter(MessageBrokerTransport):
    \"\"\"Local fallback adapter that runs tasks synchronously in the current thread when Celery/Redis is unavailable.\"\"\"
    def publish(self, queue_name: str, task_name: str, payload_json: str, tenant_id: str) -> str:
        try:
            import app.queue.tasks as tasks
            import logging
            logger = logging.getLogger(__name__)
            
            if task_name == "app.queue.tasks.stage_1_ingest":
                tasks.credent_ingest.apply(kwargs={"payload": payload_json})
            elif task_name == "app.queue.tasks.stage_2_analysis_group":
                tasks.credent_analysis.apply(kwargs={"payload": payload_json})
            elif task_name == "app.queue.tasks.stage_3_synthesis_chord":
                tasks.credent_synthesis.apply(kwargs={"payload": payload_json})
            elif task_name == "app.queue.tasks.ping":
                pass
            else:
                logger.warning(f"Unknown task {task_name} in SyncLocalTransportAdapter")
                
            return "sync_executed_msg_123"
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Sync execution failed: {e}")
            raise TransientTransportError(f"Sync execution failed: {e}")

class CeleryTransportAdapter
"""

content = re.sub(r'class SyncLocalTransportAdapter.*?class CeleryTransportAdapter', sync_adapter, content, flags=re.DOTALL)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
print("outbox_dispatcher.py patched with SyncLocalTransportAdapter.")
