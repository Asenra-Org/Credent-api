# =============================================================================
# CREDENT — AI-Powered Credit Appraisal & Risk Assessment Platform
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================

import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

# LOAD ENVIRONMENT VARIABLES FIRST!
load_dotenv()

# Import API routers AFTER loading the .env
from app.routes import documents, analysis, research, reports, history, structured_data

app = FastAPI(
    title="Credent API",
    description="AI-Powered Credit Appraisal & Risk Assessment Platform by Asenra",
    version="1.0.0",
)

# CORS — allow frontend origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- Global Exception Handler ----
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch any unhandled exception and return a clean JSON error."""
    print(f"[GLOBAL ERROR] {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "message": f"Internal server error: {str(exc)}",
            "path": str(request.url.path)
        }
    )


# ---- Health & Status Endpoints ----
@app.get("/")
async def root():
    return {"message": "Credent API is running 🚀 | Powered by Asenra"}


@app.get("/health")
async def health_check():
    """Health check with environment validation."""
    issues = []

    if not os.getenv("GROQ_API_KEY"):
        issues.append("GROQ_API_KEY not set — AI features will use fallbacks")

    if not os.path.exists("temp_uploads"):
        issues.append("temp_uploads directory missing")

    return {
        "status": "healthy" if not issues else "degraded",
        "issues": issues if issues else None
    }


# ----- Route registration -----
app.include_router(documents.router, prefix="/api/v1/documents", tags=["Documents"])
app.include_router(analysis.router, prefix="/api/v1/analysis", tags=["Analysis"])
app.include_router(research.router, prefix="/api/v1/research", tags=["Research & Insights"])
app.include_router(reports.router, prefix="/api/v1/reports", tags=["Reports"])
app.include_router(history.router, prefix="/api/v1/history", tags=["History"])
app.include_router(structured_data.router, prefix="/api/v1/data", tags=["Structured Data"])