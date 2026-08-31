import os, json, asyncio
from dotenv import load_dotenv
from app.agents.orchestration.cam_generator import CAMGeneratorAgent

load_dotenv()

async def main():
    agent = CAMGeneratorAgent()
    prompt = agent._build_prompt()
    
    extracted_pdf_data = {"company_name": "Test Co", "revenue": "10000"}
    invoke_params = {
        "pdf_data": json.dumps(extracted_pdf_data),
        "integrity_data": "{}",
        "research_data": "{}",
        "score": 50,
        "citations": "{}"
    }
    
    print("Invoking...")
    res = await agent.llm.ainvoke(prompt.invoke(invoke_params))
    print(f"Content length: {len(res.content)}")
    print(f"Content: {res.content[:200]}")
    print(f"Usage: {res.response_metadata.get('token_usage')}")

if __name__ == '__main__':
    asyncio.run(main())
