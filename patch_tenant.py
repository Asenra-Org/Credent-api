import re

file_path = r"D:\coding\Credent-api\app\services\case_service.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Add Tenant import
content = content.replace(
    "from app.models.ase52 import Case, OutboxEvent, Job, AgentExecution, Document",
    "from app.models.ase52 import Case, OutboxEvent, Job, AgentExecution, Document, Tenant"
)

# Inject tenant auto-creation logic
tenant_logic = """
    # 0. Ensure tenant exists
    tenant_obj = session.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant_obj:
        session.add(Tenant(id=tenant_id, name="Default Organization", status="ACTIVE"))
        session.flush()

    # 1. Create Case"""

content = content.replace("    # 1. Create Case", tenant_logic)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
print("case_service.py patched successfully.")
