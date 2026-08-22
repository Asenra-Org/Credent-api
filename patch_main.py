import re

main_path = r"app\main.py"
with open(main_path, 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace(
    "from app.routes import documents, analysis, research, reports, history, structured_data, policies",
    "from app.routes import documents, analysis, research, reports, history, structured_data, policies, auth\nfrom fastapi import Depends\nfrom app.auth.dependencies import get_current_user"
)

content = content.replace('app.include_router(documents.router, prefix="/api/v1/documents", tags=["Documents"])', 'app.include_router(auth.router)\napp.include_router(documents.router, prefix="/api/v1/documents", tags=["Documents"], dependencies=[Depends(get_current_user)])')

for route in ['analysis', 'research', 'reports', 'history', 'structured_data', 'policies']:
    content = content.replace(f'app.include_router({route}.router, prefix=', f'app.include_router({route}.router, dependencies=[Depends(get_current_user)], prefix=')

with open(main_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("main.py patched.")
