import json
import os
import re
from typing import Any
from fastapi import HTTPException
from google.genai import types

from app.models.schemas import QuotationLineItem, QuotationStructureResponse, TableBlock
from app.ai.structure import (
    _get_gemini_api_key,
    _gemini_ssl_verify,
    _create_gemini_client,
    _prepare_tables_for_prompt,
    _num_or_none,
    _str_or_none,
    _parse_json_response,
    DEFAULT_GEMINI_MODEL,
    MAX_ROWS_FOR_AI
)

SYSTEM_PROMPT = """You are an expert construction estimation and procurement assistant.
You receive raw table rows extracted from vendor quotations, supplier rate sheets, or price lists.

Your job is to:
1. Detect the Vendor Name: Identify the company/supplier providing the quotation from the text or headers. Standardize the vendor name (e.g., "Havells India Ltd" -> "Havells").
2. Detect the Quotation Date: Look for any quotation, proposal, or invoice date. Standardize the date to "YYYY-MM-DD" format. If no date is found, leave it null or use the current year if context allows (be conservative, default to null if unsure).
3. Extract quotation material items: Skip blank rows, category headers, page numbers, subtotal/total rows, terms and conditions, and footer noise. For each material item, extract:
   - `item_name`: The original description of the material/item.
   - `brand`: The manufacturer or brand of the material, if specified (e.g. "Havells", "Legrand", "Finolex").
   - `unit`: The unit of measurement (e.g. "m", "m2", "nos", "set", "lot").
   - `quoted_rate`: The unit price/rate quoted by the supplier as a numeric float.
4. Normalize the item name: Generate a clean, standardized, and normalized item name (`normalized_item_name`) by:
   - Stripping installation/SITC prefix phrases (e.g., "SITC of", "Supply & Laying of", "SITC", "S & I of").
   - Standardizing abbreviations (e.g. "dia", "dia.", "Dia" -> "dia"; "thick", "thk" -> "thick").
   - Removing boilerplate suffix fluff (e.g. "complete in all respects", "as required", "as per specifications").
   - Standardizing dimensions (e.g., "25 mm", "25mm", "25  mm" -> "25mm").
   - Standardizing spelling and lowercase naming for exact cross-vendor matching (e.g. "copper piping 25mm", "pvc conduit 20mm").
   This normalized name is critical because it is used to group and match identical items across different vendors!

Ensure output is valid, raw JSON matches the requested schema. Be conservative: do not invent items or hallucinate rates."""


def structure_quotation_tables(filename: str, tables: list[TableBlock]) -> QuotationStructureResponse:
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
            "vendor_name": "string (detected vendor name, e.g. 'Havells')",
            "quotation_date": "string (YYYY-MM-DD or null)",
            "items": [
                {
                    "item_name": "string (raw original description from quotation)",
                    "brand": "string or null (e.g. 'Havells')",
                    "unit": "string or null (e.g. 'm', 'nos')",
                    "quoted_rate": "number (unit price quoted by supplier)",
                    "normalized_item_name": "string (standardized, cleaned lower-cased item name for cross-vendor grouping, e.g. 'copper pipe 25mm')"
                }
            ],
            "confidence": "number (float between 0.00 and 1.00)",
            "summary": "string or null",
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
            status_code=520,
            detail=f"Gemini quotation parsing request failed: {exc}",
        ) from exc

    content = response.text
    if not content:
        raise HTTPException(status_code=502, detail="Gemini returned empty quotation response.")

    try:
        parsed = _parse_json_response(content)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Gemini returned invalid JSON for quotation.",
        ) from exc

    vendor_name = _str_or_none(parsed.get("vendor_name")) or "Unknown Vendor"
    quotation_date = _str_or_none(parsed.get("quotation_date"))

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

    confidence = _num_or_none(parsed.get("confidence")) or 0.85
    summary = parsed.get("summary")

    items: list[QuotationLineItem] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        raw_name = _str_or_none(item.get("item_name"))
        rate = _num_or_none(item.get("quoted_rate"))
        if not raw_name or rate is None:
            continue

        normalized = _str_or_none(item.get("normalized_item_name"))
        if not normalized:
            # Fallback normalization logic if Gemini misses it
            normalized = raw_name.lower().strip()
            # remove SITC/Supply & Installation
            normalized = re.sub(r'^(sitc|supply\s*&\s*installation|supply\s*&\s*laying|s\s*&\s*i|supply\s*only)\s*(of)?\s*', '', normalized)
            # compress spaces
            normalized = re.sub(r'\s+', ' ', normalized)

        items.append(
            QuotationLineItem(
                item_name=raw_name,
                brand=_str_or_none(item.get("brand")),
                unit=_str_or_none(item.get("unit")),
                quoted_rate=rate,
                normalized_item_name=normalized,
            )
        )

    return QuotationStructureResponse(
        vendor_name=vendor_name,
        quotation_date=quotation_date,
        items=items,
        confidence=confidence,
        summary=summary,
        warnings=warnings,
    )
