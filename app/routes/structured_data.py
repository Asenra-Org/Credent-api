# =============================================================================
# CREDENT — Structured Data Integrations Route
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from fastapi import Depends
from app.security.dependencies import require_role
from app.agents.input.structured_data import StructuredDataAgent

router = APIRouter()

try:
    data_agent = StructuredDataAgent()
except Exception as init_err:
    print(f"[WARN] StructuredDataAgent init failed: {init_err}")
    data_agent = None


class GstRequest(BaseModel):
    # Standard GSTIN format is 15 alphanumeric characters.
    gstin: str = Field(
        ...,
        min_length=15,
        max_length=15,
        description="15-character GST Identification Number"
    )


class ItrRequest(BaseModel):
    # Standard PAN format is 10 alphanumeric characters.
    pan: str = Field(
        ...,
        min_length=10,
        max_length=10,
        description="10-character Permanent Account Number"
    )


class BankStatementRequest(BaseModel):
    account_id: str = Field(
        ...,
        min_length=1,
        description="Bank Account ID or Number"
    )


@router.post("/gst", dependencies=[Depends(require_role(["Credit Analyst", "Credit Manager", "Admin", "Auditor"]))])
async def fetch_gst(request_data: GstRequest):
    """Retrieve structured GST filing data for a given GSTIN."""
    try:
        if data_agent is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "error",
                    "message": "Structured data service is not available."
                }
            )

        data = await data_agent.fetch_gst_data(request_data.gstin)
        return {"status": "success", "data": data}

    except Exception as e:
        print(f"[ROUTE /data/gst] Error: {e}")
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": f"Failed to retrieve GST data: {str(e)}"
            }
        )


@router.post("/itr", dependencies=[Depends(require_role(["Credit Analyst", "Credit Manager", "Admin", "Auditor"]))])
async def fetch_itr(request_data: ItrRequest):
    """Retrieve structured Income Tax Return data for a given PAN."""
    try:
        if data_agent is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "error",
                    "message": "Structured data service is not available."
                }
            )

        data = await data_agent.fetch_itr_data(request_data.pan)
        return {"status": "success", "data": data}

    except Exception as e:
        print(f"[ROUTE /data/itr] Error: {e}")
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": f"Failed to retrieve ITR data: {str(e)}"
            }
        )


@router.post("/bank-statement", dependencies=[Depends(require_role(["Credit Analyst", "Credit Manager", "Admin", "Auditor"]))])
async def fetch_bank_statement(request_data: BankStatementRequest):
    """Retrieve structured bank statement data for a given Account ID."""
    try:
        if data_agent is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "error",
                    "message": "Structured data service is not available."
                }
            )

        data = await data_agent.fetch_bank_statement(request_data.account_id)
        return {"status": "success", "data": data}

    except Exception as e:
        print(f"[ROUTE /data/bank-statement] Error: {e}")
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": f"Failed to retrieve bank statement data: {str(e)}"
            }
        )
