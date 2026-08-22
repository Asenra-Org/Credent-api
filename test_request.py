
import requests
import json

url = "http://127.0.0.1:8000/api/v1/documents/ingest/pdf"
files = {"file": ("test.pdf", b"dummy pdf content", "application/pdf")}
try:
    response = requests.post(url, files=files)
    print("Ingestion Response:", response.status_code)
    print(response.text)
except Exception as e:
    print("Request failed:", e)

