# =============================================================================
# CREDENT — AI-Powered Credit Appraisal & Risk Assessment Platform
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================

import os
import time
from contextlib import asynccontextmanager
import re
import uuid
from contextvars import ContextVar
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

# LOAD ENVIRONMENT VARIABLES FIRST!
load_dotenv()

# ContextVar for async task correlation tracing
correlation_id_ctx: ContextVar[str | None] = ContextVar("correlation_id_ctx", default=None)

_CORRELATION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

def _sanitize_or_generate_correlation_id(header_val: str | None) -> str:
    """Validate client-supplied correlation header or fallback to a 32-char hex UUID."""
    if header_val and _CORRELATION_ID_REGEX.match(header_val):
        return header_val
    return uuid.uuid4().hex

TEMP_FILE_CLEANUP_MAX_AGE_SECONDS = int(os.getenv("TEMP_FILE_CLEANUP_MAX_AGE_SECONDS", "3600"))

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager handling non-blocking application startup garbage collection of orphaned temporary upload files.

    Under the verified single-process deployment model and configured retention threshold,
    regular files older than TEMP_FILE_CLEANUP_MAX_AGE_SECONDS in temp_uploads/ are purged
    during application boot without interfering with active request streams.
    """
    temp_dir = "temp_uploads"
    if os.path.exists(temp_dir) and os.path.isdir(temp_dir):
        now = time.time()
        cleaned_count = 0
        try:
            for entry in os.scandir(temp_dir):
                if entry.is_file():
                    try:
                        file_age = now - entry.stat().st_mtime
                        if file_age > TEMP_FILE_CLEANUP_MAX_AGE_SECONDS:
                            os.remove(entry.path)
                            cleaned_count += 1
                    except Exception as file_err:
                        print(f"[STARTUP CLEANUP] Warning: Failed to remove {entry.name}: {file_err}")
        except Exception as scan_err:
            print(f"[STARTUP CLEANUP] Warning: Error scanning {temp_dir}: {scan_err}")
        if cleaned_count > 0:
            print(f"🧹 [STARTUP CLEANUP] Successfully purged {cleaned_count} orphaned temporary file(s) older than {TEMP_FILE_CLEANUP_MAX_AGE_SECONDS}s.")
    yield

# Import API routers AFTER loading the .env
from app.routes import documents, analysis, research, reports, history, structured_data, policies

app = FastAPI(
    title="Credent API",
    description="AI-Powered Credit Appraisal & Risk Assessment Platform by Asenra",
    version="1.0.0",
    lifespan=lifespan,
)

@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Starlette HTTP middleware managing X-Correlation-ID request tracing, dual context storage, and resetting the ContextVar token in a finally block regardless of success or failure."""
    raw_header = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")
    correlation_id = _sanitize_or_generate_correlation_id(raw_header)

    request.state.correlation_id = correlation_id
    token = correlation_id_ctx.set(correlation_id)

    try:
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response
    finally:
        correlation_id_ctx.reset(token)

# CORS — allow trusted origins (Fixed Starlette allow_credentials wildcard error)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "*"
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- Global Exception Handler ----
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch any unhandled exception and return a clean JSON error. Intentionally attaches X-Correlation-ID header to guarantee propagation on 500 responses even if exceptions bypass call_next."""
    cid = getattr(request.state, "correlation_id", None) or correlation_id_ctx.get()
    print(f"[GLOBAL ERROR] [{cid}] {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "message": f"Internal server error: {str(exc)}",
            "path": str(request.url.path),
            "correlation_id": cid
        },
        headers={"X-Correlation-ID": cid} if cid else None
    )


# ---- Health & Status Endpoints ----
@app.get("/")
async def root():
    return {"message": "Credent API is running 🚀 | Powered by Asenra"}


@app.get("/health")
@app.get("/healthz")
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

@app.get("/readyz")
async def readiness_check():
    """Readiness check verifying local SQLite connection."""
    try:
        from app.database.database import get_sqlite_connection
        conn = get_sqlite_connection()
        conn.close()
        return {"status": "ready", "database": "online"}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "not_ready", "error": str(e)})


# ----- Route registration -----
app.include_router(documents.router, prefix="/api/v1/documents", tags=["Documents"])
app.include_router(analysis.router, prefix="/api/v1/analysis", tags=["Analysis"])
app.include_router(research.router, prefix="/api/v1/research", tags=["Research & Insights"])
app.include_router(reports.router, prefix="/api/v1/reports", tags=["Reports"])
app.include_router(history.router, prefix="/api/v1/history", tags=["History"])
app.include_router(structured_data.router, prefix="/api/v1/data", tags=["Structured Data"])
app.include_router(policies.router, prefix="/api/v1", tags=["Policies"])