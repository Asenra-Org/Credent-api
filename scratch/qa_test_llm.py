import asyncio
from app.core.llm import ChatGroqWithFallback
import os
from dotenv import load_dotenv

load_dotenv('.env')

async def run_qa():
    print("Initiating QA Test for LLM Fallback Architecture...")
    llm = ChatGroqWithFallback(temperature=0)
    print(f"Primary Configured: {os.getenv('PRIMARY_LLM_MODEL', 'openai/gpt-oss-20b')}")
    print("Sending ping to Groq...")
    
    try:
        res = await llm.ainvoke("Reply with exactly 'System Operational'.")
        print(f"Response: {res.content}")
        print("QA PASSED: The LLM responded successfully without 404/400 decommission errors!")
    except Exception as e:
        print(f"QA FAILED: {str(e)}")

asyncio.run(run_qa())
