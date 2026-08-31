import os
import sys
import asyncio
from dotenv import load_dotenv

sys.path.append(r'D:\Credent\Credent-api')
load_dotenv(r'D:\Credent\Credent-api\.env')

from app.core.llm import active_provider, ChatGroqWithFallback
from langchain_core.messages import HumanMessage, SystemMessage

async def main():
    llm = ChatGroqWithFallback(temperature=0.1, max_tokens=100)
    messages = [HumanMessage(content="Extract name and age: Karan is 28. Output ONLY valid JSON.")]
    try:
        res = await llm.ainvoke(messages)
        print("content:", repr(res.content))
        print("additional_kwargs:", res.additional_kwargs)
        print("response_metadata:", res.response_metadata)
    except Exception as e:
        print("Error:", e)

if __name__ == '__main__':
    asyncio.run(main())
