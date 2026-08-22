
import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.agents.input.document_ingestion import DocumentIngestionAgent

async def test_ingest():
    agent = DocumentIngestionAgent()
    # Mock PDF text
    raw_text = """
    HDFC Bank Statement. 
    Company Name: Acme Corp. 
    Revenue: 1000000. 
    Debt: 50000.
    """ * 100 # make it somewhat long
    print("Sending to Sarvam...")
    res = await agent.parse_financial_statement(raw_text)
    print("Ingestion Result:", res)

asyncio.run(test_ingest())

