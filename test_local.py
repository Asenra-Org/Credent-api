from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

files = {
    'financials': ('test.pdf', b'dummy content', 'application/pdf')
}
headers = {'X-Tenant-ID': 'tenant-xyz'}
data = {'case_id': 'case-123'}

r = client.post('/api/v1/documents/ingest/pdf', files=files, headers=headers, data=data)
print(r.status_code)
print(r.text)
