import os, requests
from dotenv import load_dotenv
load_dotenv()
res = requests.get('https://api.groq.com/openai/v1/models', headers={'Authorization': f'Bearer {os.getenv("GROQ_API_KEY")}'})
models = [m['id'] for m in res.json().get('data', [])]
print(models)
