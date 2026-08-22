import re

file_path = r"D:\coding\Credent-api\app\routes\documents.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Replace CeleryTransportAdapter import and usage with SyncLocalTransportAdapter
content = content.replace(
    "from app.services.outbox_dispatcher import OutboxDispatcher, CeleryTransportAdapter",
    "from app.services.outbox_dispatcher import OutboxDispatcher, CeleryTransportAdapter, SyncLocalTransportAdapter"
)

content = content.replace(
    "transport = CeleryTransportAdapter(celery_app)",
    "transport = SyncLocalTransportAdapter() if not os.getenv('CELERY_BROKER_URL') else CeleryTransportAdapter(celery_app)"
)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
print("documents.py patched successfully.")
