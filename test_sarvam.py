
import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.core.llm import ChatGroqWithFallback
from langchain_core.messages import HumanMessage

async def test_llm():
    llm = ChatGroqWithFallback()
    print("Calling LLM with large prompt...")
    prompt = "Hello. " * 3000  # 3000 words
    try:
        res = await llm.ainvoke([HumanMessage(content=prompt)])
        print("RAW RESPONSE:")
        print(repr(res.content))
    except Exception as e:
        print("EXCEPTION:", e)

asyncio.run(test_llm())

