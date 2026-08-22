
import requests

url = 'http://127.0.0.1:8000/api/v1/reports/generate-cam'
headers = {
    'Authorization': 'Bearer ' + requests.post('http://127.0.0.1:8000/api/v1/auth/login', json={'email': 'maker@hdfc.com', 'password': 'TestPassword123!'}).json().get('access_token', '')
}
payload = {
    'extracted_pdf_data': {'company_name': 'Test Corp', 'total_revenue': '10000'},
    'integrity_flags': {},
    'web_research': {},
    'final_score': 75
}

r = requests.post(url, headers=headers, json=payload)
import json
print(json.dumps(r.json(), indent=2))

