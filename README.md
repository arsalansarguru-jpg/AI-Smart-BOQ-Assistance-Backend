# BOQ Automation — FastAPI Backend

PDF and Excel table extraction service for the BOQ frontend.

## Prerequisites

- Python 3.10 or newer
- pip

## Setup

```bash
cd backend
python -m venv .venv

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set at least:

- `GEMINI_API_KEY` — required for **Structure with AI** (Day 5, Google Gemini)
- `CORS_ORIGINS` — optional (default includes localhost:3000)

## Run (local)

From the `backend` folder (with venv activated):

```bash
python -m uvicorn app.main:app --reload --port 8000
```

- Health check: http://localhost:8000/health

## Deploy (production)

See [DEPLOY.md](../DEPLOY.md) in the repo root. Config files:

- `render.yaml` — Render Blueprint
- `railway.toml` + `Procfile` — Railway
- `Dockerfile` — container deploy
- API docs: http://localhost:8000/docs
- Extract endpoint: `POST http://localhost:8000/api/extract` (multipart file upload)
- Structure endpoint: `POST http://localhost:8000/api/structure` (JSON: filename + tables from extract)
- Export endpoint: `POST http://localhost:8000/api/export/excel` (JSON: structured BOQ items → `.xlsx` download)

## Frontend

The Next.js app calls this API when you click **Extract tables**, **Structure with AI**, then **Export to Excel** in the preview modal.

Add to `frontend/.env.local` (optional — default is localhost:8000):

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Run **both** terminals:

1. `cd frontend && npm run dev` → http://localhost:3000
2. `cd backend && python -m uvicorn app.main:app --reload --port 8000`

## Project layout

```
backend/
├── app/
│   ├── main.py              # FastAPI app + CORS
│   ├── routes/
│   │   ├── extract.py       # POST /api/extract
│   │   ├── structure.py     # POST /api/structure (Gemini)
│   │   └── export_boq.py    # POST /api/export/excel
│   ├── extraction/
│   │   ├── pdf.py           # pdfplumber
│   │   └── excel.py         # pandas + openpyxl
│   ├── export/excel.py      # openpyxl BOQ workbook builder
│   ├── ai/structure.py      # Google Gemini BOQ mapping
│   └── models/schemas.py
├── requirements.txt
└── README.md
```
