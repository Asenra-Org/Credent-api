# Credent API

> **AI-Powered Credit Appraisal & Risk Assessment Platform**  
> A product of **Asenra** — Enterprise-grade intelligence for modern lending.

---

## Overview

Credent API is a FastAPI-based backend that automates credit appraisal workflows for financial institutions. It combines document intelligence, AI-driven analysis, real-time web research, and structured report generation to produce Credit Appraisal Memos (CAMs) with decisioning recommendations.

---

## Features

| Capability | Description |
|---|---|
| **Document Ingestion** | Upload PDFs; extract text via OCR and table parsing |
| **PDF Forensics** | Detect tampered documents using metadata & creator analysis |
| **Financial Parsing** | LLM-powered extraction of financial statements |
| **Integrity Verification** | Cross-validate GST returns vs. Bank Statements for fraud signals |
| **Web Research** | Real-time intelligence on company news, sector headwinds & litigation |
| **Risk Score Adjustment** | AI-adjusted credit scores from qualitative credit officer notes |
| **CAM Generation** | Full Credit Appraisal Memo with 5Cs framework & decision rationale |
| **Cloud Persistence** | Dual-write to Supabase (primary) and SQLite (fallback) |
| **History** | Retrieve past appraisal records from the database |

---

## Tech Stack

- **Runtime**: Python 3.11
- **Framework**: FastAPI + Uvicorn
- **AI/LLM**: LangChain + Groq (primary inference)
- **Document Intelligence**: PyMuPDF, pytesseract, tabula-py, pdf2image, pikepdf
- **Data**: Pandas, NumPy
- **Database**: Supabase (cloud) + SQLite (local fallback)
- **Containerization**: Docker

---

## Project Structure

```
credent-api/
├── app/
│   ├── main.py                  # FastAPI app entry point, CORS, route registration
│   ├── agents/
│   │   ├── input/               # Document ingestion & real-time intelligence agents
│   │   ├── analysis/            # Financial health, integrity, risk & sector agents
│   │   └── orchestration/       # CAM generator agent
│   ├── routes/
│   │   ├── documents.py         # PDF upload & forensics endpoints
│   │   ├── analysis.py          # Integrity check endpoints
│   │   ├── research.py          # Web research & score adjustment endpoints
│   │   ├── reports.py           # CAM generation & loan status update endpoints
│   │   └── history.py           # Appraisal history endpoints
│   └── database/
│       └── database.py          # Supabase + SQLite dual-write layer
├── Dockerfile
├── requirements.txt
└── .gitignore
```

---

## API Endpoints

### Documents — `/api/v1/documents`
| Method | Path | Description |
|---|---|---|
| `POST` | `/ingest/pdf` | Upload a PDF, run forensics & extract financial data |

### Analysis — `/api/v1/analysis`
| Method | Path | Description |
|---|---|---|
| `POST` | `/integrity-check` | Cross-validate GST vs. Bank Statement data |

### Research & Insights — `/api/v1/research`
| Method | Path | Description |
|---|---|---|
| `POST` | `/web-research` | Real-time company & sector intelligence lookup |
| `POST` | `/adjust-score` | Adjust credit score from qualitative field notes |

### Reports — `/api/v1/reports`
| Method | Path | Description |
|---|---|---|
| `POST` | `/generate-cam` | Generate a full Credit Appraisal Memo + decisioning |
| `PATCH` | `/update-status/{appraisal_id}` | Formally approve or reject a loan application |

### History — `/api/v1/history`
| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Fetch recent appraisal records |

### System
| Method | Path | Description |
|---|---|---|
| `GET` | `/` | API root ping |
| `GET` | `/health` | Health check with environment validation |

Interactive docs are available at **`/docs`** (Swagger UI) and **`/redoc`**.

---

## Getting Started

### Prerequisites

- Python 3.11+
- Tesseract OCR installed (`tesseract-ocr`)
- Poppler utilities installed (`poppler-utils`)
- Java Runtime (for `tabula-py`)
- A [Groq API Key](https://console.groq.com)
- A [Supabase](https://supabase.com) project (optional — SQLite fallback is available)

### 1. Clone the repository

```bash
git clone https://github.com/Asenra-Org/Credent-api.git
cd Credent-api
```

### 2. Create a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root:

```env
# Required — AI Inference
GROQ_API_KEY=your_groq_api_key_here

# Optional — Cloud Database (SQLite fallback used if not set)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_anon_or_service_key
```

### 5. Run the development server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`.

---

## Docker

### Build the image

```bash
docker build -t credent-api .
```

### Run the container

```bash
docker run -p 8000:8000 \
  -e GROQ_API_KEY=your_key \
  -e SUPABASE_URL=your_url \
  -e SUPABASE_KEY=your_key \
  credent-api
```

---

## Database

Credent uses a **dual-write strategy** for resilience:

1. **Supabase** (Primary) — Cloud-hosted Postgres. Required for multi-user institutional access and the status update workflow.
2. **SQLite** (Fallback) — Local file at `app/database/credent.db`. Automatically used if Supabase credentials are absent.

### Supabase Table: `loan_applications`

| Column | Type | Description |
|---|---|---|
| `id` | UUID | Auto-generated primary key |
| `borrower_name` | text | Company / borrower name |
| `sector` | text | Industry sector |
| `loan_amount` | float | Extracted revenue figure |
| `base_score` | int | Initial credit score (0–100) |
| `adjusted_score` | int | Qualitatively adjusted score |
| `decision` | text | `APPROVE` / `REJECT` / `MANUAL REVIEW` |
| `status` | text | `PENDING` / `APPROVED` / `REJECTED` / `UNDER_REVIEW` / `FLAGGED` |
| `recommended_loan_amount` | text | AI-recommended loan amount |
| `recommended_interest_rate` | text | AI-recommended interest rate |
| `decision_rationale` | text | Full narrative rationale |
| `cam_report` | jsonb | Full CAM output |
| `web_research` | jsonb | Web intelligence data |
| `integrity_flags` | jsonb | Document fraud signals |
| `raw_document_data` | jsonb | Extracted PDF data |
| `created_at` | timestamp | Record creation time |

---

## Testing

```bash
pytest
```

---

## Security Notes

- File uploads are validated for type (`.pdf` only), size (max 20 MB), and sanitized for path traversal.
- PDF forensics detects documents created or modified by image editors (Photoshop, Canva, GIMP, etc.).
- CORS is currently set to `allow_origins=["*"]` — **restrict this in production** to your frontend domain.

---

## License

Copyright © 2026 **Asenra**. All rights reserved.  
This software is proprietary and confidential. Unauthorized use, reproduction, or distribution is strictly prohibited.
