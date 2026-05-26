import json
import os
import re
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field
from google.genai import types

from app.ai.structure import (
    _get_gemini_api_key,
    _parse_json_response,
    DEFAULT_GEMINI_MODEL,
    generate_content_with_retry
)

router = APIRouter(prefix="/takeoff", tags=["takeoff"])

class TakeoffItem(BaseModel):
    id: str = Field(..., description="The unique ID of the BOQ item")
    description: str = Field(..., description="The material/item description")
    category: Optional[str] = Field(None, description="Trade category")
    quantity: Optional[float] = Field(None, description="BOQ listed quantity")
    unit: Optional[str] = Field(None, description="BOQ measurement unit")

class TakeoffDrawing(BaseModel):
    id: str = Field(..., description="The drawing sheet ID")
    file_name: str = Field(..., description="The drawing sheet file name or title")

class TakeoffAuditRequest(BaseModel):
    items: List[TakeoffItem] = Field(..., description="List of active BOQ items")
    drawings: List[TakeoffDrawing] = Field(..., description="List of available CAD/PDF drawing sheets")

class OmittedItem(BaseModel):
    description: str = Field(..., description="Standardized description of the missing/extra item")
    category: str = Field(..., description="Trade category (exactly one of 'HVAC', 'Electrical', 'Plumbing', 'Fire Fighting', 'ELV', 'Interior', 'Civil', 'Mechanical')")
    tentative_quantity: float = Field(..., description="Calculated tentative quantity required for execution")
    unit: str = Field(..., description="Measurement unit (e.g. nos, m, m2, m3, set, lot)")
    rationale: str = Field(..., description="1-sentence mathematical rationale explaining why this is needed based on drawing layouts and takeoff ratios")

class TakeoffAuditResponse(BaseModel):
    omitted_items: List[OmittedItem] = Field(..., description="List of predicted extra items missing from BOQ")

TAKEOFF_SYSTEM_PROMPT = """You are an expert Quantity Surveyor (QS) and MEP Estimation Director specialized in the Indian and Middle Eastern construction markets.

Your job is to analyze a contractor's active list of BOQ items alongside their available project drawings, and automatically identify exactly 3 highly realistic, mandatory "extra items" or "omitted items" that are required to execute the drawing layouts but are completely missing from the client's commercial BOQ list.

For each missing item, calculate a high-accuracy, tentative quantity and unit using standard engineering quantity takeoff (QTO) ratios:
1. HVAC/Plumbing Piping: For every running meter of copper or GI pipe, require hangers/supports (approx. 1 support per 1.5m), insulation wraps, and elbows/bends (10% of pipe quantity).
2. Electrical Conduit Wires: Require conduit junction boxes (1 box per 10m run), pull boxes, wall-crossing sleeves, and structural cable-tray accessories.
3. Equipment Installation: Air Handling Units (AHUs), chillers, and pumps require concrete inertia blocks, vibration isolator pads, and puddle flanges at wall penetrations.

Strict Rules:
- Return EXACTLY 3 high-confidence omitted/extra items.
- Ensure the 'category' is exactly one of 'HVAC', 'Electrical', 'Plumbing', 'Fire Fighting', 'ELV', 'Interior', 'Civil', 'Mechanical'.
- Write a clear 'rationale' explaining the math (e.g., '1 support every 1.5m for 450m of piping = 300 nos').
- Provide standard units (nos, m, m2, kg, lot).

Return a valid JSON object matching the requested schema. Return ONLY valid JSON matching the schema."""

@router.post("/audit-drawings", response_model=TakeoffAuditResponse)
async def audit_drawings_for_takeoff(
    body: TakeoffAuditRequest,
    x_gemini_api_key: Optional[str] = Header(None)
) -> TakeoffAuditResponse:
    # 1. Retrieve the Gemini API key safely
    try:
        if isinstance(x_gemini_api_key, str) and x_gemini_api_key.strip():
            api_key = x_gemini_api_key.strip()
        else:
            api_key = _get_gemini_api_key()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Gemini API key is not configured: {exc}"
        ) from exc

    # 2. Build user prompt payload
    user_payload = {
        "boq_items": [item.model_dump() for item in body.items],
        "project_drawings": [dwg.model_dump() for dwg in body.drawings]
    }

    # 3. Choose the model
    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL

    # 4. Generate content from Gemini/Kimi with retries
    try:
        response = await generate_content_with_retry(
            model=model,
            contents=json.dumps(user_payload, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=TAKEOFF_SYSTEM_PROMPT,
                temperature=0.1,
                response_mime_type="application/json",
            ),
            api_key=api_key
        )
    except Exception as exc:
        raise HTTPException(
            status_code=520,
            detail=f"Gemini drawing takeoff audit failed: {exc}"
        ) from exc

    content = response.text
    if not content:
        raise HTTPException(
            status_code=502,
            detail="Gemini returned an empty drawing audit response."
        )

    # 5. Parse JSON
    try:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        parsed = json.loads(cleaned)
        
        if isinstance(parsed, list):
            parsed = {"omitted_items": parsed}
        elif isinstance(parsed, dict):
            if "omitted_items" not in parsed:
                lists = [v for v in parsed.values() if isinstance(v, list)]
                if lists:
                    parsed = {"omitted_items": lists[0]}
                else:
                    raise ValueError("Response does not contain a list of omitted items.")
        else:
            raise ValueError("Response is neither a JSON array nor a JSON object.")
        
        return TakeoffAuditResponse(**parsed)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to parse structured drawing takeoff response from Gemini: {exc}. Raw content: {content}"
        )
