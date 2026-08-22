
import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.agents.orchestration.coordinator import CreditAppraisalCoordinator
import time
import json
import logging

logging.basicConfig(level=logging.DEBUG)

async def run_test():
    coordinator = CreditAppraisalCoordinator()
    mock_application = {
        "company_name": "Test Sarvam Ltd", 
        "requested_amount": 5000000,
        "pdf_path": "none"
    }
    
    try:
        t0 = time.time()
        print("Starting full pipeline test...", flush=True)
        res = await coordinator.run_appraisal_with_state(mock_application)
        print(f"SUCCESS! Took {time.time()-t0:.2f}s", flush=True)
    except Exception as e:
        print(f"ERROR: {e}", flush=True)

asyncio.run(run_test())

