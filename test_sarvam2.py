
import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.core.llm import ChatGroqWithFallback
from app.agents.orchestration.cam_generator import CAMGeneratorAgent

async def test_llm():
    agent = CAMGeneratorAgent()
    print("Calling LLM...")
    try:
        res = await agent.generate_cam(
            extracted_pdf_data={"company_name": "Unknown Entity", "total_revenue": None},
            integrity_flags={},
            web_research={},
            final_score=50
        )
        print("RAW RESPONSE:", res)
    except Exception as e:
        print("EXCEPTION:", e)

asyncio.run(test_llm())

