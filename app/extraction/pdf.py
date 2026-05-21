import io
import json
import os
import re
from typing import Any

import pdfplumber
from google.genai import types

from app.ai.structure import _get_gemini_api_key, _create_gemini_client, DEFAULT_GEMINI_MODEL
from app.models.schemas import TableBlock

OCR_PROMPT = """You are an expert OCR table extractor.
Your job is to read this scanned PDF document, locate all Bill of Quantities (BOQ) or estimation tables, and extract the raw rows exactly as they appear in the tables.

Rules:
1. Do not skip any rows. Return all row data (headers, body, values, remarks).
2. For each detected table, return a list of rows, where each row is a list of cell strings.
3. Keep empty cells as null. Do not invent columns.
4. Output format must be raw JSON matching this schema:
{
  "tables": [
    {
      "page": number,
      "rows": [
        ["cell1", "cell2", "cell3", ...],
        ...
      ]
    }
  ]
}
Return valid, raw JSON only."""


def _normalize_cell(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _normalize_rows(rows: list[list[Any]]) -> list[list[str | None]]:
    return [[_normalize_cell(c) for c in row] for row in rows]


def extract_tables_via_gemini_ocr(pdf_bytes: bytes) -> list[TableBlock]:
    api_key = _get_gemini_api_key()
    if not api_key:
        print("Gemini API key is not set; skipping Gemini OCR fallback.")
        return []

    client = _create_gemini_client(api_key)
    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL

    try:
        pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        response = client.models.generate_content(
            model=model,
            contents=[pdf_part, OCR_PROMPT],
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )

        content = response.text
        if not content:
            return []

        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        parsed = json.loads(cleaned)
        raw_tables = parsed.get("tables", [])

        blocks = []
        for t in raw_tables:
            page = t.get("page")
            rows = t.get("rows", [])
            if rows:
                blocks.append(TableBlock(page=page, rows=rows))
        return blocks
    except Exception as exc:
        print(f"Gemini OCR fallback failed: {exc}")
        return []


def extract_pdf_tables(pdf_bytes: bytes) -> tuple[list[TableBlock], int]:
    tables: list[TableBlock] = []
    page_count = 0

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page_count = len(pdf.pages)
            for index, page in enumerate(pdf.pages):
                raw_tables = page.extract_tables() or []
                for raw in raw_tables:
                    if not raw:
                        continue
                    normalized = _normalize_rows(raw)
                    if any(any(cell for cell in row) for row in normalized):
                        tables.append(TableBlock(page=index + 1, rows=normalized))
    except Exception as e:
        print(f"pdfplumber extraction failed: {e}")

    if not tables:
        print("pdfplumber found no tables. Falling back to Gemini OCR...")
        tables = extract_tables_via_gemini_ocr(pdf_bytes)
        if tables:
            page_count = max([t.page for t in tables if t.page] or [0])

    return tables, page_count

