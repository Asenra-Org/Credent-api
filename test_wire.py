import os, json, asyncio
from dotenv import load_dotenv
from app.agents.orchestration.cam_generator import CAMGeneratorAgent

import httpx
from unittest.mock import patch

load_dotenv()

async def main():
    agent = CAMGeneratorAgent()
    
    # We will intercept the HTTP request to see the exact payload sent by ChatOpenAI
    original_send = httpx.AsyncClient.send
    
    async def mock_send(self, request, *args, **kwargs):
        print(f"Request URL: {request.url}")
        print(f"Request Body: {request.read().decode('utf-8')}")
        return await original_send(self, request, *args, **kwargs)
        
    with patch('httpx.AsyncClient.send', new=mock_send):
        try:
            res = await agent.llm.ainvoke("hi")
            print("Response:", res.content)
        except Exception as e:
            print("Error:", e)

if __name__ == '__main__':
    asyncio.run(main())
