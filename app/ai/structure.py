import json
import os
import re
import asyncio
import logging
from typing import Any

import httpx
from fastapi import HTTPException
from google import genai
from google.genai import types

from app.models.schemas import BoqLineItem, StructureResponse, TableBlock
from app.ai.kimi import get_kimi_api_key, generate_kimi_content
from app.ai.openai_client import get_openai_api_key, generate_openai_content

logger = logging.getLogger(__name__)

MAX_ROWS_FOR_AI = 150
MAX_CELL_CHARS = 500
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = """You are an expert construction estimator assistant. You receive raw table rows extracted from tender BOQ PDFs or Excel sheets.

Your job: clean, normalize, and structure these messy rows into a clean Bill of Quantities line-item list in JSON format.

Core Tasks & Rules:
1. Identify real BOQ line items. Skip blank rows, pure headers, page numbers, subtotal/total rows, and footer noise.
2. Correct OCR mistakes: Fix misread numbers (e.g., "l" -> "1", "S" -> "5", "O" -> "0" in numeric columns), misread letters, and misread unit symbols (e.g., "rn" or "Mtr" -> "m", "Sq.m" or "m" -> "m2", "Cu.m" -> "m3").
3. Normalize Units: Standardize units to a uniform, clean set of units (e.g., use "nr" or "nos" for numbers, "m" for meters, "m2" for square meters, "m3" for cubic meters, "kg" for kilograms, "lot" for lot, "set" for set, "sum" for lump sum). Avoid weird capitalization or formatting.
4. Standardize descriptions: Combine multi-line descriptions (which often get split across lines/rows in PDFs/scans) into a single, cohesive, clean string. Remove stray formatting symbols, bullet prefixes, or OCR noise. Remove fluff words such as "SITC", "Supply & Installation", "complete as reqd", "as per specs".
   - Example: "SITC Copper Tubng 25 mm complete as reqd" -> "Copper Pipe 25mm".
5. Strict Category Classification: Classify every valid line item into exactly one of these 8 construction trade categories:
   - `HVAC`
   - `Electrical`
   - `Plumbing`
   - `Fire Fighting`
   - `ELV`
   - `Interior`
   - `Civil`
   - `Mechanical`
   Do NOT use any other categories. Use the item description and context.
   Abbreviation and keyword mapping rules:
   - "VRF", "VRV", "FCU", "AHU", "Chiller", "Copper Pipe 25mm" -> `HVAC`
   - "Lighting Fixture", "Distribution Board", "DB Wiring", "DB", "MCB", "UPS", "LT Panel" -> `Electrical`
   - "GI Pipe", "Water Supply lines", "Sewerage Pipe", "GI", "PVC", "SWR" -> `Plumbing`
   - "CCTV", "PA System", "Access Control", "Security" -> `ELV`
   - "Sprinkler", "Wet Riser", "Fire Alarm", "Fire Hydrant" -> `Fire Fighting`
   - "Gypsum ceiling", "Paint", "Joinery", "Partition", "Doors" -> `Interior`
   - "PCC", "RCC", "Brickwork", "Excavation", "Concreting" -> `Civil`
   - "Pumps", "Fans", "Dampers", "Valves", "Motors" -> `Mechanical`
6. Remove Duplicate Rows: If identical rows or duplicate headers/footers appear due to page splits, filter them out.
7. Capture Original Data & Calculate Confidence:
   - For each structured item, capture the raw, uncleaned combined description row string in `original_text`.
   - Provide a `confidence` score (a float between 0.00 and 1.00) based on extraction accuracy and category classification certainty.
8. Data Integrity:
   - Keep original quantities unchanged.
   - Do NOT invent/hallucinate rates or quantities. Leave them null if they are missing in the source.

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


async def generate_content_with_retry(model, contents, config, api_key: str = None, max_attempts=4):
    # 1. Determine if we should route to OpenAI ChatGPT or Kimi (Moonshot AI)
    is_openai = False
    is_kimi = False
    custom_key = None

    if api_key and api_key.strip().startswith("sk-"):
        custom_key = api_key.strip()
        if custom_key.startswith("sk-proj-"):
            is_openai = True
        else:
            # Check if Kimi is explicitly configured on the server, otherwise default sk- keys to OpenAI ChatGPT
            if get_kimi_api_key() and not get_openai_api_key():
                is_kimi = True
            else:
                is_openai = True
    elif get_openai_api_key():
        is_openai = True
    elif get_kimi_api_key():
        is_kimi = True

    if is_openai:
        system_instruction = config.system_instruction if hasattr(config, "system_instruction") else ""
        try:
            openai_text = await generate_openai_content(
                system_instruction=system_instruction,
                user_prompt=contents,
                custom_key=custom_key
            )
            class MockResponse:
                def __init__(self, text):
                    self.text = text
            return MockResponse(openai_text)
        except Exception as exc:
            raise HTTPException(
                status_code=520,
                detail=f"OpenAI ChatGPT Engine query failed: {exc}"
            )

    if is_kimi:
        system_instruction = config.system_instruction if hasattr(config, "system_instruction") else ""
        try:
            kimi_text = await generate_kimi_content(
                system_instruction=system_instruction,
                user_prompt=contents,
                custom_key=custom_key
            )
            class MockResponse:
                def __init__(self, text):
                    self.text = text
            return MockResponse(kimi_text)
        except Exception as exc:
            raise HTTPException(
                status_code=520,
                detail=f"Kimi AI Engine query failed: {exc}"
            )

    # 2. Otherwise, fall back to standard Gemini execution...
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="No Gemini, OpenAI, or Kimi API key was configured or provided."
        )
    client = _create_gemini_client(api_key)

    attempt = 0
    while True:
        attempt += 1
        try:
            return await asyncio.to_thread(
                client.models.generate_content,
                model=model,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            exc_str = str(exc).lower()
            
            # Check if this is a daily quota limit error rather than a temporary per-minute rate limit
            is_daily_limit = any(x in exc_str for x in ["perday", "requestsperday", "daily"])
            if is_daily_limit:
                raise HTTPException(
                    status_code=429,
                    detail="Your Gemini API Daily Free Quota (20 requests/day) has been exhausted. Please add a billing card to your Google AI Studio account to upgrade to the Pay-as-you-go tier (which offers 1,500 free requests/day) or wait for the daily reset."
                )
            
            is_rate_limit = any(x in exc_str for x in ["429", "resource_exhausted", "quota", "rate limit", "exhausted"])
            if is_rate_limit and attempt < max_attempts:
                sleep_time = 3.0 * (2 ** (attempt - 1))
                logger.warning(f"Gemini API rate limited (attempt {attempt}/{max_attempts}). Retrying in {sleep_time} seconds. Error: {exc}")
                await asyncio.sleep(sleep_time)
                continue
            raise exc


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

        conf_raw = entry.get("confidence")
        confidence = _num_or_none(conf_raw) if conf_raw is not None else 0.85
        if confidence is not None:
            confidence = float(max(0.0, min(1.0, confidence)))

        orig_text = entry.get("original_text") or entry.get("original") or desc

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
                confidence=confidence,
                original_text=_str_or_none(orig_text),
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


async def structure_boq_tables(filename: str, tables: list[TableBlock], api_key: str = None) -> StructureResponse:
    if not api_key:
        api_key = _get_gemini_api_key()
    
    if not api_key and not get_kimi_api_key():
        raise HTTPException(
            status_code=503,
            detail=(
                "Neither GEMINI_API_KEY nor KIMI_API_KEY is configured on the backend. "
                "Please configure an API key to proceed."
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
                    "category": "string (exactly one of 'HVAC', 'Electrical', 'Plumbing', 'Fire Fighting', 'ELV', 'Interior', 'Civil', 'Mechanical')",
                    "description": "string (required, cleaned and standardized)",
                    "unit": "string or null",
                    "quantity": "number or null",
                    "rate": "number or null",
                    "amount": "number or null",
                    "remarks": "string or null",
                    "confidence": "number (float between 0.00 and 1.00)",
                    "original_text": "string (the raw, uncleaned combined description row string from tables)"
                }
            ],
            "summary": "string",
            "warnings": ["string"],
        },
    }

    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL

    try:
        response = await generate_content_with_retry(
            model=model,
            contents=json.dumps(user_payload, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.1,
                response_mime_type="application/json",
            ),
            api_key=api_key
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Structuring request failed: {exc}",
        ) from exc

    content = response.text
    if not content:
        raise HTTPException(status_code=502, detail="AI engine returned empty response.")

    try:
        parsed = _parse_json_response(content)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="AI engine returned invalid JSON.",
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
