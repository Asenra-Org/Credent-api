import os
import requests
import json
import time
from dotenv import load_dotenv

load_dotenv(r'D:\Credent\Credent-api\.env')
api_key = os.getenv('SARVAM_API_KEY')

url = "https://api.sarvam.ai/v1/chat/completions"
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

payload = {
    "model": "sarvam-105b",
    "messages": [
        {"role": "user", "content": "Extract name and age: Karan is 28. Output ONLY valid JSON."}
    ],
    "temperature": 0.1,
    "max_tokens": 500
}

print("Sending request to Sarvam...")
start = time.time()
resp = requests.post(url, headers=headers, json=payload)
end = time.time()

print(f"Time taken: {end - start:.2f} seconds")
print("Status Code:", resp.status_code)
print("Response JSON:")
print(json.dumps(resp.json(), indent=2))
