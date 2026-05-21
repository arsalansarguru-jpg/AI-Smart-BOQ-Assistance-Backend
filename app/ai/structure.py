import json
import os
import re
from typing import Any

import httpx
from fastapi import HTTPException
from google import genai
from google.genai import types

from app.models.schemas import BoqLineItem, StructureResponse, TableBlock

MAX_ROWS_FOR_AI = 150
MAX_CELL_CHARS = 500
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = """You are an expert construction estimator assistant. You receive raw table rows extracted from tender BOQ PDFs or Excel sheets.

Your job: clean, normalize, and structure these messy rows into a clean Bill of Quantities line-item list in JSON format.

Core Tasks & Rules:
1. Identify real BOQ line items. Skip blank rows, pure headers, page numbers, subtotal/total rows, and footer noise.
2. Correct OCR mistakes: Fix misread numbers (e.g., "l" -> "1", "S" -> "5", "O" -> "0" in numeric columns), misread letters, and misread unit symbols (e.g., "rn" or "Mtr" -> "m", "Sq.m" or "m" -> "m2", "Cu.m" -> "m3").
3. Normalize Units: Standardize units to a uniform, clean set of units (e.g., use "nr" or "nos" for numbers, "m" for meters, "m2" for square meters, "m3" for cubic meters, "kg" for kilograms, "lot" for lot, "set" for set, "sum" for lump sum). Avoid weird capitalization or formatting.
4. Standardize descriptions: Combine multi-line descriptions (which often get split across lines/rows in PDFs/scans) into a single, cohesive, clean string. Remove stray formatting symbols, bullet prefixes, or OCR noise.
5. Detect and Group Categories: Assign a clear "category" to each line item based on its description and trade/bill context. Normalize category names so that similar items are grouped together under identical category headers.
   - Example: "SITC Copper Pipe" and "Supply & Installation Copper Tubing" are both similar MEP items and must be assigned the same category, e.g. "Copper Piping".
   - Group architectural, structural, electrical, and plumbing items into consistent, logical, standardized categories (e.g., "Conduit & Wiring", "Sanitary Fixtures", "Excavation", "Reinforcement").
6. Remove Duplicate Rows: If identical rows or duplicate headers/footers appear due to page splits, filter them out.
7. Fill fields conservatively:
   - item_no: use the source reference/item number if clearly present; otherwise null.
   - category: the clean, normalized category/trade heading for this item (e.g. "Copper Piping", "Conduit & Wiring", "Concrete Works"). Required for every valid item.
   - description: required for every valid item.
   - unit: standardized unit (string) or null if not applicable.
   - quantity: clean float number or null if missing.
   - rate: clean float number or null if missing. Do not invent/hallucinate rates.
   - amount: clean float number or null if missing. Keep amount aligned with quantity * rate if both are present.
   - remarks: optional notes or special conditions found in the raw row.

Ensure output is valid, raw JSON matches the requested schema. Be conservative: do not invent items or hallucinate numbers."""


def _get_gemini_api_key() -> str:
    return (
        os.getenv("GEMINI_API_KEY", "").strip()
        or os.getenv("GOOGLE_API_KEY", "").strip()
    )


def _gemini_ssl_verify() -> bool:
    """When false, skips TLS cert verification (dev-only workaround for proxy/AV SSL inspection)."""
    raw = os.getenv("GEMINI_SSL_VERIFY", "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _create_gemini_client(api_key: str) -> genai.Client:
    if not _gemini_ssl_verify():
        # google-genai overwrites verify=False with a default context; use a custom client.
        http_client = httpx.Client(verify=False)
        http_options = types.HttpOptions(httpx_client=http_client)
        return genai.Client(api_key=api_key, http_options=http_options)
    return genai.Client(api_key=api_key)


def _truncate_cell(value: str | None) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if len(s) > MAX_CELL_CHARS:
        return s[: MAX_CELL_CHARS - 3] + "..."
    return s


def _prepare_tables_for_prompt(tables: list[TableBlock]) -> list[dict[str, Any]]:
    """Flatten tables into a compact JSON-safe payload with row limits."""
    prepared: list[dict[str, Any]] = []
    rows_used = 0

    for block in tables:
        if rows_used >= MAX_ROWS_FOR_AI:
            break

        label = block.sheet or (f"page_{block.page}" if block.page else "table")
        rows: list[list[str | None]] = []
        for row in block.rows:
            if rows_used >= MAX_ROWS_FOR_AI:
                break
            rows.append([_truncate_cell(c) for c in row])
            rows_used += 1

        if rows:
            prepared.append({"source": label, "rows": rows})

    return prepared


def _parse_items(raw_items: list[Any]) -> list[BoqLineItem]:
    items: list[BoqLineItem] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        desc = entry.get("description")
        if not desc or not str(desc).strip():
            continue
        items.append(
            BoqLineItem(
                item_no=_str_or_none(entry.get("item_no")),
                category=_str_or_none(entry.get("category") or entry.get("section")),
                description=str(desc).strip(),
                unit=_str_or_none(entry.get("unit")),
                quantity=_num_or_none(entry.get("quantity")),
                rate=_num_or_none(entry.get("rate")),
                amount=_num_or_none(entry.get("amount")),
                remarks=_str_or_none(entry.get("remarks")),
            )
        )
    return items


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _num_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("Expected a JSON object.")
    return parsed


def structure_boq_tables(filename: str, tables: list[TableBlock]) -> StructureResponse:
    api_key = _get_gemini_api_key()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "GEMINI_API_KEY is not set on the backend. "
                "Copy backend/.env.example to backend/.env and add your Google Gemini API key."
            ),
        )

    if not tables:
        raise HTTPException(status_code=400, detail="No tables to structure.")

    prepared = _prepare_tables_for_prompt(tables)
    if not prepared:
        raise HTTPException(status_code=400, detail="No row data to structure.")

    total_rows = sum(len(t["rows"]) for t in prepared)
    truncated = total_rows >= MAX_ROWS_FOR_AI

    user_payload = {
        "filename": filename,
        "tables": prepared,
        "output_schema": {
            "items": [
                {
                    "item_no": "string or null",
                    "category": "string (the standardized group/trade heading, e.g. 'Copper Piping', 'Electrical Conduit', 'Concrete Works')",
                    "description": "string (required, cleaned and standardized)",
                    "unit": "string or null",
                    "quantity": "number or null",
                    "rate": "number or null",
                    "amount": "number or null",
                    "remarks": "string or null",
                }
            ],
            "summary": "string",
            "warnings": ["string"],
        },
    }

    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    client = _create_gemini_client(api_key)

    try:
        response = client.models.generate_content(
            model=model,
            contents=json.dumps(user_payload, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Gemini request failed: {exc}",
        ) from exc

    content = response.text
    if not content:
        raise HTTPException(status_code=502, detail="Gemini returned empty response.")

    try:
        parsed = _parse_json_response(content)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Gemini returned invalid JSON.",
        ) from exc

    raw_items = parsed.get("items") if isinstance(parsed, dict) else []
    if not isinstance(raw_items, list):
        raw_items = []

    warnings = parsed.get("warnings") if isinstance(parsed, dict) else []
    if not isinstance(warnings, list):
        warnings = []
    warnings = [str(w) for w in warnings if w]

    if truncated:
        warnings.insert(
            0,
            f"Only the first {MAX_ROWS_FOR_AI} rows were sent to AI due to size limits.",
        )

    summary = parsed.get("summary") if isinstance(parsed, dict) else None
    if summary is not None:
        summary = str(summary).strip() or None

    items = _parse_items(raw_items)
    if not items:
        warnings.append("No BOQ line items could be identified in the extracted tables.")

    return StructureResponse(
        filename=filename,
        items=items,
        summary=summary,
        warnings=warnings,
        model=model,
        rows_analyzed=total_rows,
    )
