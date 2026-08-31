import httpx
import os
import asyncio
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000/api/v1"
TEST_FOLDER = r"C:\Users\kpvlo\Downloads\CRESEM_EARLY_ACCESS_DEMO_TEST_PACK\CRESEM_EARLY_ACCESS_DEMO_TEST_PACK\01_MANUFACTURING_STANDARD"

async def main():
    async with httpx.AsyncClient() as client:
        # 1. Login
        print("[*] Logging in...")
        login_res = await client.post(f"{BASE_URL}/auth/login", json={
            "email": "admin@hdfc.com",
            "password": "TestPassword123!"
        })
        
        if login_res.status_code != 200:
            print(f"[!] Login failed: {login_res.text}")
            return
            
        print("[+] Login successful.")
        
        # 2. Get tokens from cookies (httpx manages this automatically in the session)
        # 3. Prepare files for batch upload
        print("[*] Preparing files...")
        folder_path = Path(TEST_FOLDER)
        files_to_upload = []
        open_files = []
        
        for file_path in folder_path.glob("*.pdf"):
            f = open(file_path, "rb")
            open_files.append(f)
            files_to_upload.append(("files", (file_path.name, f, "application/pdf")))
            print(f"    - Found: {file_path.name}")
            
        if not files_to_upload:
            print("[!] No PDF files found!")
            return
            
        # 4. Upload
        print("[*] Submitting batch upload (this may take time as Sarvam parses)...")
        # Ensure we pass the institution_id if required
        data = {"institution_id": "DEFAULT"}
        
        try:
            upload_res = await client.post(
                f"{BASE_URL}/documents/ingest/batch",
                data=data,
                files=files_to_upload,
                timeout=300.0  # 5 minutes timeout for slow LLMs
            )
            
            print(f"[*] Upload Status: {upload_res.status_code}")
            try:
                print(f"[*] Upload Response: {upload_res.json()}")
            except:
                print(f"[*] Upload Response: {upload_res.text}")
                
        except Exception as e:
            print(f"[!] Exception during upload: {e}")
            
        finally:
            for f in open_files:
                f.close()

if __name__ == "__main__":
    asyncio.run(main())
