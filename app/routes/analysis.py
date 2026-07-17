# CREDENT — Integrity & Credit Appraisal Analysis Routes
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from app.agents.analysis.integrity_verification import IntegrityVerificationAgent
from app.agents.analysis.financial_health import FinancialHealthAgent
from app.agents.analysis.management_quality import ManagementQualityAgent
from app.agents.analysis.sector_context import SectorContextAgent

router = APIRouter()

# Safe agent initialization
try:
    integrity_agent = IntegrityVerificationAgent()
except Exception as init_err:
    print(f"[WARN] IntegrityVerificationAgent init failed: {init_err}")
    integrity_agent = None

try:
    financial_agent = FinancialHealthAgent()
except Exception as init_err:
    print(f"[WARN] FinancialHealthAgent init failed: {init_err}")
    financial_agent = None

try:
    management_agent = ManagementQualityAgent()
except Exception as init_err:
    print(f"[WARN] ManagementQualityAgent init failed: {init_err}")
    management_agent = None

try:
    sector_agent = SectorContextAgent()
except Exception as init_err:
    print(f"[WARN] SectorContextAgent init failed: {init_err}")
    sector_agent = None


# --- Request Models ---

class IntegrityCheckRequest(BaseModel):
    gst_data: List[Dict[str, Any]] = Field(default_factory=list)
    bank_data: List[Dict[str, Any]] = Field(default_factory=list)


# --- Response Models ---

class RatioMetrics(BaseModel):
    current_ratio: float = Field(..., description="Current assets divided by current liabilities")
    debt_to_equity: float = Field(..., description="Total debt divided by total equity")
    quick_ratio: float = Field(..., description="Quick assets divided by current liabilities")
    interest_coverage_ratio: float = Field(..., description="Earnings before interest and tax divided by interest expense")

class CashFlowMetrics(BaseModel):
    status: str = Field(..., description="Overall cash flow health (e.g., Stable, Strong, Weak)")
    operating_cash_flow: Optional[float] = Field(default=None, description="Net cash provided by operating activities")
    free_cash_flow: Optional[float] = Field(default=None, description="Operating cash flow minus capital expenditures")
    trend: str = Field(..., description="Cash flow trend over the analyzed period (e.g., Positive, Declining)")

class FinancialHealthResponse(BaseModel):
    status: str = Field("success", description="Response status")
    company_name: str = Field(..., description="Name of the company being analyzed")
    financial_health_score: float = Field(..., description="Overall financial health score out of 100")
    risk_level: str = Field(..., description="Assessed financial risk level (e.g., Low, Medium, High)")
    ratios: RatioMetrics = Field(..., description="Calculated financial ratios")
    cash_flow_assessment: CashFlowMetrics = Field(..., description="Assessment of cash flow dynamics")
    recommendation: str = Field(..., description="Lending suitability recommendation based on financials")


class PromoterDetail(BaseModel):
    name: str = Field(..., description="Name of the promoter")
    experience_years: int = Field(..., description="Years of industry experience")
    risk_flags: List[str] = Field(default_factory=list, description="Identified risk flags or regulatory actions")
    verdict: str = Field(..., description="Verification summary for the promoter")

class GovernanceMetrics(BaseModel):
    board_independence: str = Field(..., description="Assessment of board independence (e.g., Good, Adequate, Poor)")
    regulatory_compliance: str = Field(..., description="Compliance status with regulatory bodies")
    risk_level: str = Field(..., description="Governance-related risk level")

class ManagementQualityResponse(BaseModel):
    status: str = Field("success", description="Response status")
    company_name: str = Field(..., description="Name of the company being analyzed")
    management_score: float = Field(..., description="Management quality score out of 100")
    risk_level: str = Field(..., description="Overall management risk level")
    promoter_analysis: List[PromoterDetail] = Field(..., description="Detailed checks for each promoter/director")
    governance_assessment: GovernanceMetrics = Field(..., description="Assessment of governance and board structure")


class RbiPolicyDetail(BaseModel):
    circular_ref: str = Field(..., description="Reference number of the RBI circular")
    summary: str = Field(..., description="Key highlight or summary of the circular")
    impact: str = Field(..., description="Impact on the borrower's business (e.g., Favorable, Neutral, Unfavorable)")

class SectorContextResponse(BaseModel):
    status: str = Field("success", description="Response status")
    sector: str = Field(..., description="Name of the sector being analyzed")
    outlook: str = Field(..., description="Sector growth and stability outlook (e.g., Positive, Stable, Negative)")
    growth_rate_projected: str = Field(..., description="Projected annual growth rate of the sector")
    risk_level: str = Field(..., description="Macro/sectoral risk level")
    risk_factors: List[str] = Field(..., description="Key headwinds or risks affecting the sector")
    rbi_policy_impact: List[RbiPolicyDetail] = Field(..., description="Applicable RBI policies and circulars and their impact")


# --- Endpoint Handlers ---

@router.post("/integrity-check")
async def check_data_integrity(raw_request: Request):
    """Cross-validate GST returns against Bank Statements to detect fraud."""
    try:
        body = await raw_request.json()
        
        # Parse request with defaults
        request = IntegrityCheckRequest(**body)
        
        if integrity_agent is None:
            return {
                "status": "completed",
                "flags_detected": 0,
                "flags": [],
                "warning": "Integrity verification service not available."
            }
        
        # Validate we have data to work with
        if not request.gst_data and not request.bank_data:
            return {
                "status": "completed",
                "flags_detected": 0,
                "flags": [],
                "warning": "No GST or bank data provided."
            }
        
        results = await integrity_agent.cross_validate(request.gst_data, request.bank_data)
        return results
        
    except Exception as e:
        print(f"[ROUTE /integrity-check] Error: {e}")
        return {
            "status": "completed",
            "flags_detected": 0,
            "flags": [],
            "warning": f"Integrity check encountered an error: {str(e)}"
        }


@router.get("/financial-health", response_model=FinancialHealthResponse)
async def get_financial_health(company_name: str = "Asenra Corp"):
    """Evaluate financial health, cash flows, and balance sheet metrics for a company."""
    # Mock data to be replaced with FinancialHealthAgent when integrated
    return FinancialHealthResponse(
        status="success",
        company_name=company_name,
        financial_health_score=85.0,
        risk_level="Low",
        ratios=RatioMetrics(
            current_ratio=1.85,
            debt_to_equity=1.20,
            quick_ratio=1.45,
            interest_coverage_ratio=4.50
        ),
        cash_flow_assessment=CashFlowMetrics(
            status="Strong",
            operating_cash_flow=15000000.0,
            free_cash_flow=8000000.0,
            trend="Positive"
        ),
        recommendation="Recommended for credit approval with standard interest terms."
    )


@router.get("/management-quality", response_model=ManagementQualityResponse)
async def get_management_quality(company_name: str = "Asenra Corp"):
    """Assess promoter profiles, corporate governance, and management track record."""
    # Mock data to be replaced with ManagementQualityAgent when integrated
    return ManagementQualityResponse(
        status="success",
        company_name=company_name,
        management_score=78.5,
        risk_level="Low",
        promoter_analysis=[
            PromoterDetail(
                name="Aditya Sen",
                experience_years=18,
                risk_flags=[],
                verdict="Clean background check, experienced leader"
            ),
            PromoterDetail(
                name="Rajesh Rao",
                experience_years=12,
                risk_flags=[],
                verdict="No defaults detected, strong industry credentials"
            )
        ],
        governance_assessment=GovernanceMetrics(
            board_independence="Good",
            regulatory_compliance="Fully Compliant",
            risk_level="Low"
        )
    )


@router.get("/sector-context", response_model=SectorContextResponse)
async def get_sector_context(sector: str = "Manufacturing"):
    """Analyze sector-level macroeconomic factors and relevant RBI circulars."""
    # Mock data to be replaced with SectorContextAgent when integrated
    return SectorContextResponse(
        status="success",
        sector=sector,
        outlook="Positive",
        growth_rate_projected="7.2%",
        risk_level="Medium",
        risk_factors=[
            "Raw material price inflation",
            "Global logistics challenges"
        ],
        rbi_policy_impact=[
            RbiPolicyDetail(
                circular_ref="RBI/2026-27/45",
                summary="Refinancing and interest subvention guidelines for MSME manufacturers.",
                impact="Favorable"
            )
        ]
    )
=======
# =============================================================================
# CREDENT — Integrity & Credit Appraisal Analysis Routes
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from app.agents.analysis.integrity_verification import IntegrityVerificationAgent
from app.agents.analysis.financial_health import FinancialHealthAgent
from app.agents.analysis.management_quality import ManagementQualityAgent
from app.agents.analysis.sector_context import SectorContextAgent

router = APIRouter()

# Safe agent initialization
try:
    integrity_agent = IntegrityVerificationAgent()
except Exception as init_err:
    print(f"[WARN] IntegrityVerificationAgent init failed: {init_err}")
    integrity_agent = None

try:
    financial_agent = FinancialHealthAgent()
except Exception as init_err:
    print(f"[WARN] FinancialHealthAgent init failed: {init_err}")
    financial_agent = None

try:
    management_agent = ManagementQualityAgent()
except Exception as init_err:
    print(f"[WARN] ManagementQualityAgent init failed: {init_err}")
    management_agent = None

try:
    sector_agent = SectorContextAgent()
except Exception as init_err:
    print(f"[WARN] SectorContextAgent init failed: {init_err}")
    sector_agent = None


# --- Request Models ---

class IntegrityCheckRequest(BaseModel):
    gst_data: List[Dict[str, Any]] = Field(default_factory=list)
    bank_data: List[Dict[str, Any]] = Field(default_factory=list)


# --- Response Models ---

class RatioMetrics(BaseModel):
    current_ratio: Optional[float] = Field(None, description="Current assets divided by current liabilities")
    debt_to_equity: Optional[float] = Field(None, description="Total debt divided by total equity")
    quick_ratio: Optional[float] = Field(None, description="Quick assets divided by current liabilities")
    interest_coverage_ratio: Optional[float] = Field(None, description="Earnings before interest and tax divided by interest expense")

class CashFlowMetrics(BaseModel):
    status: str = Field(..., description="Overall cash flow health (e.g., Stable, Strong, Weak)")
    operating_cash_flow: Optional[float] = Field(default=None, description="Net cash provided by operating activities")
    free_cash_flow: Optional[float] = Field(default=None, description="Operating cash flow minus capital expenditures")
    trend: str = Field(..., description="Cash flow trend over the analyzed period (e.g., Positive, Declining)")

class FinancialHealthResponse(BaseModel):
    status: str = Field("success", description="Response status")
    company_name: str = Field(..., description="Name of the company being analyzed")
    financial_health_score: float = Field(..., description="Overall financial health score out of 100")
    risk_level: str = Field(..., description="Assessed financial risk level (e.g., Low, Medium, High)")
    ratios: RatioMetrics = Field(..., description="Calculated financial ratios")
    cash_flow_assessment: CashFlowMetrics = Field(..., description="Assessment of cash flow dynamics")
    recommendation: str = Field(..., description="Lending suitability recommendation based on financials")


class PromoterDetail(BaseModel):
    name: str = Field(..., description="Name of the promoter")
    experience_years: int = Field(..., description="Years of industry experience")
    risk_flags: List[str] = Field(default_factory=list, description="Identified risk flags or regulatory actions")
    verdict: str = Field(..., description="Verification summary for the promoter")

class GovernanceMetrics(BaseModel):
    board_independence: str = Field(..., description="Assessment of board independence (e.g., Good, Adequate, Poor)")
    regulatory_compliance: str = Field(..., description="Compliance status with regulatory bodies")
    risk_level: str = Field(..., description="Governance-related risk level")

class ManagementQualityResponse(BaseModel):
    status: str = Field("success", description="Response status")
    company_name: str = Field(..., description="Name of the company being analyzed")
    management_score: float = Field(..., description="Management quality score out of 100")
    risk_level: str = Field(..., description="Overall management risk level")
    promoter_analysis: List[PromoterDetail] = Field(..., description="Detailed checks for each promoter/director")
    governance_assessment: GovernanceMetrics = Field(..., description="Assessment of governance and board structure")


class RbiPolicyDetail(BaseModel):
    circular_ref: str = Field(..., description="Reference number of RBI circular")
    summary: str = Field(..., description="Key highlight or summary of the circular")
    impact: str = Field(..., description="Impact on the sector: Favorable, Neutral, or Unfavorable")

class SectorContextResponse(BaseModel):
    status: str = Field("success", description="Response status")
    sector: str = Field(..., description="Name of the sector being analyzed")
    outlook: str = Field(..., description="Sector growth and stability outlook (e.g., Positive, Stable, Negative)")
    growth_rate_projected: str = Field(..., description="Projected annual growth rate of the sector")
    risk_level: str = Field(..., description="Macro/sectoral risk level")
    risk_factors: List[str] = Field(..., description="Key headwinds or risks affecting the sector")
    rbi_policy_impact: List[RbiPolicyDetail] = Field(..., description="Applicable RBI circulars and their impact on the sector")


# --- Endpoint Handlers ---

@router.post("/integrity-check")
async def check_data_integrity(raw_request: Request):
    """Cross-validate GST returns against Bank Statements to detect fraud."""
    try:
        body = await raw_request.json()

        # Parse request with defaults
        request = IntegrityCheckRequest(**body)

        if integrity_agent is None:
            return {
                "status": "completed",
                "flags_detected": 0,
                "flags": [],
                "warning": "Integrity verification service not available."
            }

        # Validate we have data to work with
        if not request.gst_data and not request.bank_data:
            return {
                "status": "completed",
                "flags_detected": 0,
                "flags": [],
                "warning": "No GST or bank data provided."
            }

        results = await integrity_agent.cross_validate(request.gst_data, request.bank_data)
        return results

    except Exception as e:
        print(f"[ROUTE /integrity-check] Error: {e}")
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": f"Integrity check encountered an error: {str(e)}"
            }
        )


@router.get("/financial-health", response_model=FinancialHealthResponse)
async def get_financial_health(company_name: str = "Asenra Corp"):
    """Evaluate financial health, cash flows, and balance sheet metrics for a company."""
    if financial_agent is None:
        raise HTTPException(status_code=503, detail="Financial health service not available.")

    data = await financial_agent.analyze({"company_name": company_name})

    ratios = data.get("ratios", {})
    cash_flow = data.get("cash_flow_assessment", {})

    # TODO(API-v2): Semantic Mismatch Fix Required
    # The agent calculates 'dscr' (Net Operating Income / Debt Service).
    # The API contract expects 'interest_coverage_ratio' (EBIT / Interest Expense).
    # We are mapping DSCR to interest_coverage_ratio here strictly for backward compatibility.

    return FinancialHealthResponse(
        status=data.get("status", "error"),
        company_name=data.get("company_name", company_name),
        financial_health_score=data.get("financial_health_score", 0.0),
        risk_level=data.get("risk_level", "Undetermined"),
        ratios=RatioMetrics(
            current_ratio=ratios.get("current_ratio"),
            debt_to_equity=ratios.get("debt_to_equity"),
            quick_ratio=ratios.get("quick_ratio"),
            interest_coverage_ratio=ratios.get("dscr")  # Temporary legacy mapping
        ),
        cash_flow_assessment=CashFlowMetrics(
            status=cash_flow.get("status", "Undetermined"),
            operating_cash_flow=cash_flow.get("operating_cash_flow"),
            free_cash_flow=cash_flow.get("free_cash_flow"),
            trend=cash_flow.get("trend", "Undetermined")
        ),
        recommendation=data.get("recommendation", "Analysis incomplete.")
    )


@router.get("/management-quality", response_model=ManagementQualityResponse)
async def get_management_quality(company_name: str = "Asenra Corp"):
    """Assess promoter profiles, corporate governance, and management track record."""
    if management_agent is None:
        raise HTTPException(status_code=503, detail="Management quality service not available.")

    data = await management_agent.analyze({"company_name": company_name})

    return ManagementQualityResponse(
        status=data.get("status", "error"),
        company_name=data.get("company_name", company_name),
        management_score=data.get("management_score", 0.0),
        risk_level=data.get("risk_level", "Undetermined"),
        promoter_analysis=data.get("promoter_analysis", []),
        governance_assessment=data.get("governance_assessment", {
            "board_independence": "Undetermined",
            "regulatory_compliance": "Undetermined",
            "risk_level": "Undetermined"
        })
    )


@router.get("/sector-context", response_model=SectorContextResponse)
async def get_sector_context(sector: str = "Manufacturing"):
    """Analyze sector-level macroeconomic factors and RBI regulatory context.

    Sector-level only: this endpoint does not accept or evaluate borrower
    raw text. Individual borrower compliance checks are handled by other
    agents (per mentor guidance).
    """
    if sector_agent is None:
        return {
            "status": "error",
            "sector": sector,
            "outlook": "Unavailable",
            "growth_rate_projected": "N/A",
            "risk_level": "Unknown",
            "risk_factors": [],
            "rbi_policy_impact": []
        }

    result = await sector_agent.get_sector_outlook(sector)
    rbi = await sector_agent.check_rbi_policies(sector)

    return SectorContextResponse(
        status="success",
        **result,
        rbi_policy_impact=rbi
    )
