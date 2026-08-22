
import asyncio
from app.agents.orchestration.cam_generator import CAMGeneratorAgent

async def test():
    agent = CAMGeneratorAgent()
    pdf_data = {'company_name': 'Test Corp', 'total_revenue': '10000', 'sector': 'Tech', 'total_debt': '500'}
    integrity = {'status': 'PASS'}
    research = {'mca_status': 'VERIFIED'}
    score = 75
    
    res = await agent.generate_cam(pdf_data, integrity, research, score, {})
    print(res)

if __name__ == '__main__':
    asyncio.run(test())

