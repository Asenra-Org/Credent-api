import os, json, asyncio
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
import httpx
from unittest.mock import patch

load_dotenv()

async def main():
    llm = ChatOpenAI(
        base_url="https://api.sarvam.ai/v1",
        api_key=os.getenv("SARVAM_API_KEY"),
        model="sarvam-105b",
        temperature=0.1,
        model_kwargs={"extra_body": {"max_tokens": 4096}}
    )
    
    original_send = httpx.AsyncClient.send
    async def mock_send(self, request, *args, **kwargs):
        print(f"Request Body: {request.read().decode('utf-8')}")
        return await original_send(self, request, *args, **kwargs)
        
    with patch('httpx.AsyncClient.send', new=mock_send):
        await llm.ainvoke("hi")

if __name__ == '__main__':
    asyncio.run(main())
