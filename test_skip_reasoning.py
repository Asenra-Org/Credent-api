import os, requests
from dotenv import load_dotenv
load_dotenv()
res = requests.post(
    'https://api.sarvam.ai/v1/chat/completions',
    headers={'Authorization': f'Bearer {os.getenv("SARVAM_API_KEY")}', 'Content-Type': 'application/json'},
    json={'model': 'sarvam-105b', 'messages': [{'role': 'user', 'content': 'Write a 500 word essay. Output the essay immediately. Do not think. Do not use reasoning.'}], 'max_tokens': 50}
)
print(res.text)
