
import asyncio
from dotenv import load_dotenv
load_dotenv()

async def test_full_pipeline():
    print("Starting full pipeline test...")
    
    # Just a mock ID and some dummy PDF data
    pdf_text = "HDFC Bank Statement. Company Name: Testing Full Pipeline Corp. Revenue: 50000000. Debt: 10000000. Sector: manufacturing. "
    
    # Simulate step 1: run ingestion
    print("Running ingestion...")
    from app.agents.input.document_ingestion import DocumentIngestionAgent
    ingest_agent = DocumentIngestionAgent()
    financials = await ingest_agent.parse_financial_statement(pdf_text)
    print("Ingestion Result:", financials)
    
    # Simulate step 2: Run parallel agents
    print("Running parallel agents...")
    from app.agents.analysis.financial_health import FinancialHealthAgent
    from app.agents.analysis.risk_intelligence import RiskIntelligenceAgent
    from app.agents.analysis.sector_context import SectorContextAgent
    from app.agents.input.realtime_intelligence import RealtimeIntelligenceAgent
    from app.agents.analysis.integrity_checker import DataIntegrityAgent
    
    health = FinancialHealthAgent()
    risk = RiskIntelligenceAgent()
    sector = SectorContextAgent()
    realtime = RealtimeIntelligenceAgent()
    integrity = DataIntegrityAgent()
    
    company_name = financials.get("company_name", "Unknown Entity")
    sector_val = financials.get("sector", "Unknown")
    
    research_coro = realtime.conduct_research(company_name, sector_val)
    health_coro = health.calculate_health_score(financials)
    sector_coro = sector.analyze_sector(sector_val)
    integrity_coro = integrity.check_consistency(financials, {})
    
    research_res, health_res, sector_res, integrity_res = await asyncio.gather(
        research_coro, health_coro, sector_coro, integrity_coro, return_exceptions=True
    )
    
    print("Research:", research_res)
    print("Health:", health_res)
    print("Sector:", sector_res)
    print("Integrity:", integrity_res)
    
    # Final CAM Generation
    print("Running CAM Generation...")
    from app.agents.orchestration.cam_generator import CAMGeneratorAgent
    cam_agent = CAMGeneratorAgent()
    
    cam_report = await cam_agent.generate_cam(
        extracted_financials=financials,
        integrity_flags=integrity_res if not isinstance(integrity_res, Exception) else [],
        web_research=research_res if not isinstance(research_res, Exception) else {},
        final_score=75
    )
    
    print("CAM Report Generated successfully:")
    print(cam_report)

asyncio.run(test_full_pipeline())

