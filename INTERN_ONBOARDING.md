# 👋 Welcome to Credent — Intern Onboarding Guide

> **Credent** is an AI-powered Credit Appraisal & Risk Assessment Platform.
> Built and owned by **Asenra** | [asenra.in](https://asenra.in)

---

## 📌 Table of Contents

1. [About Asenra](#1-about-asenra)
2. [What is Credent?](#2-what-is-credent)
3. [How the Product Works](#3-how-the-product-works)
4. [Tech Stack](#4-tech-stack)
5. [Project Structure](#5-project-structure)
6. [Setting Up Your Dev Environment](#6-setting-up-your-dev-environment)
7. [Running the API Locally](#7-running-the-api-locally)
8. [API Reference for Interns](#8-api-reference-for-interns)
9. [Architecture Deep Dive](#9-architecture-deep-dive)
10. [The Agent System Explained](#10-the-agent-system-explained)
11. [Database Layer](#11-database-layer)
12. [Code Ownership & Standards](#12-code-ownership--standards)
13. [Intern Contribution Guidelines](#13-intern-contribution-guidelines)
14. [FAQ](#14-faq)

---

## 1. About Asenra

**Asenra** is a high-performance digital architecture and AI agency based in India.

> *"Intelligent Automations • Scalable Infrastructure • Elite Web Architectures"*

We build cinematic websites, autonomous AI agents, and full-stack web infrastructure for elite Indian businesses. Credent is one of our in-house AI products — a platform that automates the credit appraisal workflow used by banks and NBFCs.

- 🌐 Website: [asenra.in](https://asenra.in)
- 💼 LinkedIn: [linkedin.com/company/asenra](https://www.linkedin.com/company/asenra/)
- 🐙 GitHub: [github.com/Asenra-Org](https://github.com/Asenra-Org)
- 📸 Instagram: [@asenra.in](https://www.instagram.com/asenra.in/)

---

## 2. What is Credent?

### The Problem We're Solving

Traditional credit appraisal at banks and NBFCs is **slow, manual, and error-prone**. A loan officer has to:

- Read through 50–100 pages of financial documents (balance sheets, P&L, GST returns)
- Cross-check bank statements against GST filings to detect fraud
- Research the company online for news, litigation, and promoter background
- Write a Credit Appraisal Memo (CAM) from scratch
- Make an APPROVE / REJECT / MANUAL REVIEW decision

This process takes **2–5 days** per application and relies heavily on individual judgment.

### What Credent Does

Credent automates this entire pipeline using AI:

```
Upload PDF → Extract Financials → Check Integrity → Research Online
→ Adjust Score → Generate CAM → APPROVE / REJECT Decision
```

In under **60 seconds**, a credit officer gets:
- Extracted financial data (revenue, debt, equity, etc.)
- Fraud signals (PDF tampering, GST-Bank mismatches, circular trading)
- Real-time company and sector intelligence
- A complete Credit Appraisal Memo with the Five Cs analysis
- A loan decision with recommended amount and interest rate

### Who Uses This?

- **Banks and NBFCs** — automate MSME and SME loan appraisals
- **Credit Officers** — use the dashboard to review AI-generated CAMs
- **Risk Teams** — monitor integrity flags and fraud signals

---

## 3. How the Product Works

### The Full Workflow (Step by Step)

```
Step 1:  Credit officer uploads borrower's PDF (Balance Sheet / P&L / CMA data)
         └─► POST /api/v1/documents/ingest/pdf

Step 2:  System runs PDF Forensics
         └─► Checks metadata for Photoshop, Canva, GIMP (tamper detection)

Step 3:  AI extracts financial data from the PDF
         └─► LLM (Groq / LLaMA 3.1) reads the document and returns JSON
         └─► Fields: revenue, debt, equity, current assets/liabilities, credit score

Step 4:  Integrity Check — GST vs Bank Cross-Validation
         └─► POST /api/v1/analysis/integrity-check
         └─► Detects: Revenue Discrepancy, Circular Trading

Step 5:  Web Research — Real-time Intelligence
         └─► POST /api/v1/research/web-research
         └─► Searches: company news, litigation signals, sector headwinds

Step 6:  Score Adjustment — Qualitative Field Notes
         └─► POST /api/v1/research/adjust-score
         └─► Credit officer adds manual notes; AI adjusts score accordingly

Step 7:  CAM Generation — Final Decision
         └─► POST /api/v1/reports/generate-cam
         └─► Output: Five Cs analysis, APPROVE/REJECT/MANUAL REVIEW + rationale
         └─► Record is saved to Supabase (cloud) and local SQLite (fallback)

Step 8:  Formal Status Update
         └─► PATCH /api/v1/reports/update-status/{appraisal_id}
         └─► Decision is locked in the cloud database
```

### Decision Logic (Scoring)

| Score Range | Decision |
|---|---|
| ≥ 75 AND no severe flags AND Current Ratio ≥ 1.0 | **APPROVE** |
| 60–74 OR Current Ratio < 1.0 OR moderate risk | **MANUAL REVIEW** |
| < 60 OR severe fraud/defaults found | **REJECT** |
| Financials missing/null | **MANUAL REVIEW** (always) |

---

## 4. Tech Stack

| Layer | Technology | Why |
|---|---|---|
| **Web Framework** | FastAPI (Python) | Async, fast, auto Swagger docs |
| **AI / LLM** | LangChain + Groq (LLaMA 3.1 8B) | Fast inference, free tier available |
| **PDF Extraction** | PyMuPDF, PyPDF2, pytesseract, pdf2image | Digital + scanned PDF support |
| **Table Extraction** | tabula-py | Java-based PDF table reader |
| **PDF Forensics** | pikepdf | Metadata inspection for tampering |
| **Web Search** | DuckDuckGo Search (LangChain) | Free, no API key needed |
| **Data Processing** | Pandas, NumPy | GST/Bank statement analysis |
| **Database (Cloud)** | Supabase (Postgres) | Cloud persistence, real-time |
| **Database (Local)** | SQLite | Fallback, works without internet |
| **Containerization** | Docker | Consistent deployment |
| **Testing** | Pytest, pytest-asyncio | Async test support |
| **Runtime** | Python 3.11 | Stable, fast |

---

## 5. Project Structure

```
credent-api/
│
├── app/                          ← Main application package
│   ├── main.py                   ← FastAPI app, CORS, route registration
│   │
│   ├── routes/                   ← API endpoint handlers (HTTP layer)
│   │   ├── documents.py          ← PDF upload, forensics, extraction
│   │   ├── analysis.py           ← GST vs Bank integrity check
│   │   ├── research.py           ← Web research + score adjustment
│   │   ├── reports.py            ← CAM generation + loan status update
│   │   └── history.py            ← Fetch recent appraisal records
│   │
│   ├── agents/                   ← AI agents (the "brain" of Credent)
│   │   ├── input/
│   │   │   ├── document_ingestion.py   ← PDF text + table extraction + LLM parsing
│   │   │   ├── realtime_intelligence.py ← DuckDuckGo search + LLM synthesis
│   │   │   └── structured_data.py      ← GST/ITR/Bank API stubs (future)
│   │   │
│   │   ├── analysis/
│   │   │   ├── integrity_verification.py  ← Pandas-based fraud detection
│   │   │   ├── risk_intelligence.py       ← Score adjustment via LLM
│   │   │   ├── financial_health.py        ← Ratio analysis (stub)
│   │   │   ├── management_quality.py      ← Promoter analysis (stub)
│   │   │   └── sector_context.py          ← Sector/macro analysis (stub)
│   │   │
│   │   └── orchestration/
│   │       ├── cam_generator.py    ← CAM + Five Cs + decision via LLM
│   │       └── coordinator.py      ← Multi-agent orchestrator (stub)
│   │
│   └── database/
│       ├── database.py             ← Supabase primary + SQLite fallback
│       └── credent.db              ← Local SQLite database file
│
├── temp_uploads/                  ← Temporary PDF storage (auto-cleaned)
├── Dockerfile                     ← Docker container definition
├── requirements.txt               ← Python dependencies
├── README.md                      ← Public-facing API documentation
├── INTERN_ONBOARDING.md           ← This file ✅
└── .env                           ← Environment variables (never commit!)
```

### What's a "stub"?

Files marked `(stub)` have class/method definitions but raise `NotImplementedError`. They are **planned features** that interns may be assigned to implement. This is intentional — the architecture is designed, but the logic is not yet written.

---

## 6. Setting Up Your Dev Environment

### Prerequisites

Before you start, install these on your machine:

| Tool | Purpose | Download |
|---|---|---|
| **Python 3.11+** | Runtime | [python.org](https://python.org) |
| **Git** | Version control | [git-scm.com](https://git-scm.com) |
| **Tesseract OCR** | OCR for scanned PDFs | See below |
| **Poppler** | PDF-to-image conversion | See below |
| **Java 8+** | Required by tabula-py | [adoptium.net](https://adoptium.net) |
| **VS Code** (recommended) | Code editor | [code.visualstudio.com](https://code.visualstudio.com) |

#### Installing Tesseract on Windows
```
1. Download: https://github.com/UB-Mannheim/tesseract/wiki
2. Run the installer
3. Add to PATH: C:\Program Files\Tesseract-OCR
```

#### Installing Poppler on Windows
```
1. Download: https://github.com/oschwartz10612/poppler-windows/releases
2. Extract and add the bin/ folder to your PATH
```

### Step-by-Step Setup

**1. Clone the repository**
```bash
git clone https://github.com/Asenra-Org/Credent-api.git
cd Credent-api
```

**2. Create a virtual environment**
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

> ⚠️ You should see `(venv)` in your terminal prompt. Always activate the venv before working.

**3. Install Python dependencies**
```bash
pip install -r requirements.txt
```

> This may take 3–5 minutes on first install.

**4. Set up your environment variables**

Create a `.env` file in the project root (copy from the template below):

```env
# === REQUIRED — AI Inference ===
GROQ_API_KEY=your_groq_api_key_here

# === OPTIONAL — Cloud Database ===
# If not set, the app falls back to local SQLite automatically
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_anon_key
```

**Getting a free Groq API key:**
1. Go to [console.groq.com](https://console.groq.com)
2. Sign up with Google
3. Go to API Keys → Create API Key
4. Paste it in your `.env`

> 🔒 **Never commit your `.env` file.** It is already in `.gitignore`.

---

## 7. Running the API Locally

```bash
# Make sure your venv is activated first!
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
✅ Supabase integration active.   ← (or "Using local SQLite" if no Supabase key)
```

### Accessing the API

| Interface | URL |
|---|---|
| **Swagger UI** (interactive docs) | http://localhost:8000/docs |
| **ReDoc** (clean reference) | http://localhost:8000/redoc |
| **Health Check** | http://localhost:8000/health |
| **Root Ping** | http://localhost:8000/ |

> 💡 **Swagger UI is your best friend.** Open it in the browser — you can call any endpoint directly without Postman.

### Running Tests

```bash
pytest
```

---

## 8. API Reference for Interns

Here is a quick-reference guide to every endpoint. Use Swagger UI at `/docs` to test them interactively.

### 📄 Documents — `/api/v1/documents`

#### `POST /api/v1/documents/ingest/pdf`
Upload a PDF and get extracted financial data + forensics report.

**Request:** `multipart/form-data` with a `file` field (PDF only, max 20MB)

**Response:**
```json
{
  "status": "success",
  "filename": "balance_sheet.pdf",
  "tables_found": 3,
  "forensics": {
    "is_suspicious": false,
    "flags": [],
    "metadata": { "creator": "Microsoft Word", "producer": "Adobe PDF" }
  },
  "ai_analysis": {
    "company_name": "ABC Pvt Ltd",
    "sector": "Manufacturing",
    "total_revenue": 620000000,
    "total_debt": 85000000,
    "base_score": 72,
    "qualitative_notes": "Healthy GSTR-3B filing pattern...",
    "financial_commitments": ["CC limit of 50 Lakhs with SBI"],
    "legal_risks": [],
    "sanction_details": []
  }
}
```

---

### 🔍 Analysis — `/api/v1/analysis`

#### `POST /api/v1/analysis/integrity-check`
Cross-validate GST returns against bank statements to detect fraud.

**Request:**
```json
{
  "gst_data": [
    { "type": "SALE", "counterparty_gstin": "27ABCDE1234F1Z5", "taxable_value": 5000000 }
  ],
  "bank_data": [
    { "type": "CREDIT", "amount": 4800000 }
  ]
}
```

**Response:**
```json
{
  "status": "completed",
  "flags_detected": 1,
  "flags": [
    {
      "flag": "Revenue Discrepancy",
      "severity": "MEDIUM",
      "details": "GST Sales (5,000,000) differ from Bank Inflows (4,800,000) by 4.0%"
    }
  ]
}
```

---

### 🌐 Research — `/api/v1/research`

#### `POST /api/v1/research/web-research`
Run a live DuckDuckGo search for company news and sector intelligence.

**Request:**
```json
{ "company_name": "ABC Pvt Ltd", "sector": "Manufacturing" }
```

#### `POST /api/v1/research/adjust-score`
Let the AI adjust a credit score based on field officer notes.

**Request:**
```json
{ "base_score": 72, "qualitative_notes": "Factory visited. 60% capacity utilization. Owner has a history of delayed repayments with HDFC." }
```

---

### 📋 Reports — `/api/v1/reports`

#### `POST /api/v1/reports/generate-cam`
Generate the final Credit Appraisal Memo and decision.

**Request:**
```json
{
  "extracted_pdf_data": { ... },
  "integrity_flags": { "flags_detected": 0, "flags": [] },
  "web_research": { "company_news": [...], "sector_headwinds": [...], "litigation_signals": [] },
  "final_score": 72
}
```

**Response:**
```json
{
  "status": "success",
  "cam_report": {
    "five_cs": {
      "character": "Management has a clean track record...",
      "capacity": "Revenue of ₹6.2 Cr supports repayment...",
      "capital": "Net worth of ₹2.1 Cr is adequate...",
      "collateral": "No collateral mentioned. Unsecured basis.",
      "conditions": "Manufacturing sector faces moderate headwinds..."
    },
    "decision": "MANUAL REVIEW",
    "recommended_loan_amount": "INR 50,00,000",
    "recommended_interest_rate": "14.5%",
    "decision_rationale": "Score of 72 falls in the MANUAL REVIEW band..."
  }
}
```

#### `PATCH /api/v1/reports/update-status/{appraisal_id}`
Formally approve or reject a loan in the database.

---

### 🕐 History — `/api/v1/history`

#### `GET /api/v1/history/recent?limit=10`
Fetch the last N appraisal records from the database.

---

## 9. Architecture Deep Dive

### Request Flow Diagram

```
Frontend / Postman
       │
       ▼
  FastAPI Router          ← app/routes/*.py
       │
       ▼
    Agent Layer           ← app/agents/**/*.py
  ┌────────────────────────────────────────┐
  │  Input Agents     Analysis Agents      │
  │  ┌──────────┐    ┌──────────────────┐  │
  │  │Document  │    │Integrity         │  │
  │  │Ingestion │    │Verification      │  │
  │  └──────────┘    └──────────────────┘  │
  │  ┌──────────┐    ┌──────────────────┐  │
  │  │Realtime  │    │Risk Intelligence │  │
  │  │Intel     │    │Agent             │  │
  │  └──────────┘    └──────────────────┘  │
  │         Orchestration                  │
  │         ┌────────────┐                 │
  │         │CAM Generator│                │
  │         └────────────┘                 │
  └────────────────────────────────────────┘
       │
       ▼
  Database Layer          ← app/database/database.py
  ┌─────────────┐   ┌─────────────┐
  │  Supabase   │   │   SQLite    │
  │  (Primary)  │   │  (Fallback) │
  └─────────────┘   └─────────────┘
```

### Why FastAPI?

- **Async by default** — handles multiple requests concurrently without blocking
- **Auto-generated docs** — Swagger UI at `/docs` is built from the code itself
- **Pydantic validation** — request/response types are validated automatically
- **Fast** — one of the fastest Python web frameworks

### Why Groq + LLaMA?

- **Free tier available** — interns can use it without a credit card
- **Very fast** — 300–500 tokens/second (GPT-4 is ~50 tokens/second)
- **LLaMA 3.1 8B** — small enough to be free, smart enough for structured JSON extraction

---

## 10. The Agent System Explained

Credent uses a **multi-agent architecture**. Each agent is a Python class that does one specific job.

### Agent Directory

| Agent | File | What It Does |
|---|---|---|
| `DocumentIngestionAgent` | `agents/input/document_ingestion.py` | Reads PDFs, runs OCR, sends text to LLM, returns structured JSON |
| `RealtimeIntelligenceAgent` | `agents/input/realtime_intelligence.py` | Searches DuckDuckGo, feeds results to LLM, returns research report |
| `IntegrityVerificationAgent` | `agents/analysis/integrity_verification.py` | Uses Pandas to compare GST and bank data, flags discrepancies |
| `RiskIntelligenceAgent` | `agents/analysis/risk_intelligence.py` | Takes a base score + field notes and returns an adjusted score |
| `CAMGeneratorAgent` | `agents/orchestration/cam_generator.py` | Takes all data inputs, generates the full CAM with Five Cs + decision |

### How an Agent Works (Example: DocumentIngestionAgent)

```python
class DocumentIngestionAgent:
    def __init__(self):
        # Initialize the LLM (LLaMA 3.1 via Groq)
        self.llm = ChatGroq(model="llama-3.1-8b-instant", ...)

    async def ingest_pdf(self, file_path):
        # Step 1: Try standard PDF text extraction (PyPDF2)
        # Step 2: If text is too short, fall back to OCR (Tesseract)
        # Step 3: Return raw text

    async def parse_financial_statement(self, raw_text):
        # Step 1: Try structured LLM output (most reliable)
        # Step 2: If that fails, try raw LLM + JSON parsing
        # Step 3: If that fails, return safe defaults
        # Returns: dict with company_name, revenue, debt, score, etc.
```

### The "3-Attempt Fallback" Pattern

Every AI call in Credent uses this pattern — it ensures the API **never crashes**, even if the AI fails:

```
Attempt 1: LLM with structured output (Pydantic schema enforcement)
     ↓ (if fails)
Attempt 2: Raw LLM + manual JSON parsing with regex
     ↓ (if fails)
Attempt 3: Return safe default values
```

This is critical for production reliability. As an intern, maintain this pattern in any AI code you write.

---

## 11. Database Layer

### Dual-Write Strategy

Credent writes to **two databases simultaneously**:

```
save_appraisal(data)
      │
      ├─► Supabase (cloud Postgres)  ← Primary
      │         └─ Used if SUPABASE_URL + SUPABASE_KEY are in .env
      │
      └─► SQLite (local file)        ← Fallback / Resilience
                └─ Always runs. File: app/database/credent.db
```

This means even if Supabase is down or not configured, data is never lost.

### Supabase Table: `loan_applications`

| Column | Type | Description |
|---|---|---|
| `id` | UUID | Auto-generated primary key |
| `borrower_name` | text | Company name |
| `sector` | text | Industry sector |
| `loan_amount` | float | Extracted revenue (used as proxy) |
| `base_score` | int | Initial AI credit score |
| `adjusted_score` | int | Final adjusted score |
| `decision` | text | `APPROVE` / `REJECT` / `MANUAL REVIEW` |
| `status` | text | `PENDING` / `APPROVED` / `REJECTED` / `UNDER_REVIEW` / `FLAGGED` |
| `cam_report` | jsonb | Full CAM output |
| `web_research` | jsonb | Research data |
| `integrity_flags` | jsonb | Fraud detection results |
| `raw_document_data` | jsonb | Extracted PDF fields |
| `created_at` | timestamp | Auto-set on insert |

---

## 12. Code Ownership & Standards

All code in this repository is the **exclusive property of Asenra**.

Every file must begin with this header — do not remove it:

```python
# =============================================================================
# CREDENT — [Module Description]
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
```

### Coding Standards

| Rule | Details |
|---|---|
| **Language** | Python 3.11+ |
| **Async** | All agent methods must be `async def` |
| **Error handling** | Never let exceptions propagate to the user. Always return a clean JSON response. |
| **Fallbacks** | All AI calls must have a 3-attempt fallback (see Section 10) |
| **Type hints** | Use Python type hints for all function signatures |
| **No bare excepts** | Use `except Exception as e:` and log the error |
| **Env vars** | Use `os.getenv()` — never hardcode API keys or secrets |
| **Comments** | Write comments for any non-obvious logic |

### Git Workflow

```bash
# Create a feature branch (never push directly to main)
git checkout -b feature/your-feature-name

# Commit with a clear message
git commit -m "feat: add financial ratio computation to FinancialHealthAgent"

# Push and open a Pull Request for review
git push origin feature/your-feature-name
```

**Commit message prefixes:**
- `feat:` — new feature
- `fix:` — bug fix
- `docs:` — documentation only
- `refactor:` — code restructure, no logic change
- `test:` — adding or fixing tests

---

## 13. Intern Contribution Guidelines

### What You'll Likely Work On

| Task Type | Examples |
|---|---|
| **Implement stubs** | Write logic for `FinancialHealthAgent`, `ManagementQualityAgent`, `SectorContextAgent`, `AgentCoordinator` |
| **Write tests** | Add `pytest` test cases for existing routes and agents |
| **Improve prompts** | Tune the LLM prompts in `DocumentIngestionAgent` and `CAMGeneratorAgent` for better accuracy |
| **Add endpoints** | New routes in `app/routes/` (always follow the existing pattern) |
| **Bug fixes** | Investigate and fix failing edge cases |

### Before You Start Any Task

1. **Read the relevant file** completely before writing a single line
2. **Understand the data flow** — trace how data moves from the route → agent → database
3. **Check if there's a stub** — many classes already have method signatures; just implement the body
4. **Ask before redesigning** — if you think the architecture should change, discuss with the team first

### Testing Your Changes

```bash
# Run all tests
pytest

# Run a specific test file
pytest tests/test_documents.py -v

# Run with print output visible
pytest -s
```

### Do NOT Do This

- ❌ Commit your `.env` file
- ❌ Remove the Asenra copyright headers from any file
- ❌ Push directly to `main` branch
- ❌ Hardcode API keys or secrets anywhere
- ❌ `import *` — always import explicitly
- ❌ Leave `print()` debug statements in production code (use proper logging)

---

## 14. FAQ

**Q: I get `GROQ_API_KEY not set` warnings on startup. Is that okay?**
> Yes — the app will still run. AI extraction will return default/fallback values instead of real AI output. Set the key in `.env` for real AI responses.

**Q: The PDF extraction returns no text. What's wrong?**
> The document is likely a scanned image. The app falls back to Tesseract OCR automatically. Make sure Tesseract and Poppler are installed and on your PATH.

**Q: I got `tabula` errors about Java. How do I fix it?**
> Install Java 8+ from [adoptium.net](https://adoptium.net) and make sure `java` is accessible from your terminal (`java -version` should work).

**Q: The Supabase save is failing. Can I still test?**
> Yes. If Supabase credentials are missing or wrong, all data saves to local `app/database/credent.db` (SQLite) instead. Development works entirely offline.

**Q: Where do I find the interactive API docs?**
> Run the server and go to [http://localhost:8000/docs](http://localhost:8000/docs). You can test every endpoint there without Postman.

**Q: What model does the AI use?**
> `llama-3.1-8b-instant` via Groq. It's fast and free-tier eligible. Temperature is set to `0` for deterministic/consistent outputs in most agents.

**Q: Can I use a different LLM?**
> In development, feel free to experiment. In production code, stick to the approved model unless a team lead approves a change. Swapping models requires re-testing all prompts.

**Q: What does `MANUAL REVIEW` mean vs `APPROVE`/`REJECT`?**
> `MANUAL REVIEW` means the AI is not confident enough to give a final decision — a human credit officer must step in. This is a safety net, not a bug.

---

## 📞 Need Help?

- Check `/docs` (Swagger UI) for live API testing
- Read the file-level comments — every file has a description of what it does
- Read `README.md` for the public-facing technical documentation
- Reach out to your Asenra team lead on WhatsApp or internal channels

---

*© 2026 Asenra. All rights reserved. This document is confidential and intended for Asenra interns only.*
