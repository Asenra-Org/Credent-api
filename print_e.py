docs_path = r"D:\coding\Credent-api\app\routes\documents.py"
with open(docs_path, 'r', encoding='utf-8') as f:
    docs = f.read()
docs = docs.replace(
    'raise HTTPException(status_code=500, detail="Failed to initialize appraisal workflow.")',
    'raise HTTPException(status_code=500, detail=f"Failed to initialize appraisal workflow: {e}")'
)
with open(docs_path, 'w', encoding='utf-8') as f:
    f.write(docs)
