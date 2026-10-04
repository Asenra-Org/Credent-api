import requests
import json
import urllib3
import time

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
RENDER_API_KEY = "rnd_9235XSmdb3Qx5bYfHqAQyWg1mV2Q"
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Authorization": f"Bearer {RENDER_API_KEY}"
}

def create_db(owner_id):
    url = "https://api.render.com/v1/postgres"
    payload = {
        "name": "cresem-db",
        "ownerId": owner_id,
        "plan": "free",
        "databaseName": "cresem_db",
        "databaseUser": "cresem_user",
        "version": "16"
    }
    print(f"Creating PostgreSQL database...")
    response = requests.post(url, headers=HEADERS, json=payload, verify=False)
    if response.status_code == 201:
        data = response.json()
        db_id = data.get('id')
        print(f"Created Database with ID: {db_id}")
        return db_id
    else:
        print(f"Error creating DB: {response.status_code} - {response.text}")
        return None

if __name__ == "__main__":
    owner_id = 'tea-cuf3qgogph6c73fs5ie0'
    create_db(owner_id)
