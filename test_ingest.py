import asyncio
import time
import os
from dotenv import load_dotenv

load_dotenv(r'D:\Credent\Credent-api\.env')

from app.agents.input.document_ingestion import DocumentIngestionAgent

async def main():
    agent = DocumentIngestionAgent()
    # Create a dummy text that simulates a balance sheet
    test_text = "Apex Precision Components. Revenue: 1500000. Debt: 500000. Equity: 800000. Sector: Manufacturing. Net Profit: 250000."
    print("Starting ingestion test...")
    start = time.time()
    
    res = await agent.parse_financial_statement(test_text)
    end = time.time()
    
    print(f"Time taken: {end - start:.2f} seconds")
    print("Result:", res)

if __name__ == '__main__':
    asyncio.run(main())
