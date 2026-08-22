import re

docs_path = r"D:\coding\Credent-api\app\routes\documents.py"
with open(docs_path, 'r', encoding='utf-8') as f:
    docs = f.read()

docs = docs.replace('async def ingest_pdf_document', 'def ingest_pdf_document')
docs = docs.replace('content = await f.read()', 'content = f.file.read()')
docs = docs.replace('async def get_case_status', 'def get_case_status')

with open(docs_path, 'w', encoding='utf-8') as f:
    f.write(docs)
