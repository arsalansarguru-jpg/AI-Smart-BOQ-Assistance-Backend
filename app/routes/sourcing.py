import json
import os
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from google.genai import types

from app.ai.structure import (
    _get_gemini_api_key,
    _create_gemini_client,
    _parse_json_response,
    DEFAULT_GEMINI_MODEL
)

router = APIRouter(prefix="/sourcing", tags=["sourcing"])

class SourcingRequest(BaseModel):
    description: str = Field(..., description="The material/item description from the BOQ")
    region: str = Field(..., description="The project geographic region (e.g. Mumbai, Delhi, Bangalore, Chennai, Dubai)")
    category: Optional[str] = Field(None, description="The category/trade of the BOQ item (e.g. HVAC, Electrical, Plumbing)")

class SourcingVendor(BaseModel):
    name: str = Field(..., description="The supplier or manufacturer company name")
    type: str = Field(..., description="The relationship/business model (e.g., Manufacturer, Authorized Distributor, Local Stockist)")
    presence: str = Field(..., description="Brief details about their presence and logistics support in the requested region")
    contact_info: str = Field(..., description="Generic corporate sales email address (e.g. sales@polycab.com)")
    sourcing_rating: str = Field(..., description="Procurement rating (e.g., A+, A, B+ based on reliable delivery)")
    description: str = Field(..., description="Brief 1-sentence summary of their product range and specialization")

class SourcingResponse(BaseModel):
    vendors: List[SourcingVendor] = Field(..., description="Discovered local suppliers matching the criteria")

SYSTEM_PROMPT = """You are an expert procurement intelligence and construction supply chain consultant specialized in the Indian and Middle Eastern construction markets (specifically covering Metro Mumbai, Delhi NCR, Bangalore IT, Chennai Port, and Dubai UAE).

Your job is to recommend exactly 3 highly reputable, real, and active material manufacturers, authorized distributors, or local stockists/suppliers who have a strong physical logistics presence in the specified target region and specialize in the requested BOQ item material.

For the requested item, perform the following:
1. Identify the core material needed (e.g. electrical cables, copper HVAC piping, fire sprinklers).
2. Recommend exactly 3 suppliers who can supply this material in the requested region (e.g., recommend Polycab/Finolex for Mumbai cables, Danagrip/Al Halabi for Dubai kitchen/HVAC, Astral/Supreme for Delhi plumbing, etc.).
3. For each vendor, provide:
   - `name`: The standardized company name.
   - `type`: Whether they are a "Manufacturer", "Authorized Distributor", or "Local Stockist".
   - `presence`: 1-sentence outlining their specific regional warehouse, factory, or distributor presence in that target area.
   - `contact_info`: A generic, clean corporate email (e.g. `sales@company.com` or `info@company.com`).
   - `sourcing_rating`: Sourcing reliability rating (A+, A, or B+).
   - `description`: 1-sentence summary of their specialized MEP/construction materials.

Ensure your output is a valid JSON object matching the requested schema. Return only highly reliable, active suppliers."""

@router.post("/discover", response_model=SourcingResponse)
async def discover_regional_vendors(body: SourcingRequest) -> SourcingResponse:
    # 1. Retrieve the Gemini API key
    try:
        api_key = _get_gemini_api_key()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Gemini API key is not configured: {exc}"
        ) from exc

    # 2. Build user prompt payload
    user_payload = {
        "material_description": body.description,
        "category": body.category or "Construction",
        "target_region": body.region
    }

    # 3. Choose the model
    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    client = _create_gemini_client(api_key)

    # 4. Generate content from Gemini
    try:
        response = client.models.generate_content(
            model=model,
            contents=json.dumps(user_payload, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.2,
                response_mime_type="application/json",
            ),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=520,
            detail=f"Gemini AI Sourcing query failed: {exc}"
        ) from exc

    content = response.text
    if not content:
        raise HTTPException(
            status_code=502,
            detail="Gemini returned an empty vendor sourcing response."
        )

    # 5. Parse JSON
    try:
        parsed = _parse_json_response(content)
        # Ensure 'vendors' list exists in the parsed payload
        if "vendors" not in parsed:
            # Try to recover if Gemini returned a raw list instead of an object wrapping 'vendors'
            if isinstance(parsed, list):
                parsed = {"vendors": parsed}
            else:
                raise ValueError("Response does not contain a list of vendors.")
        
        # Validate against the Pydantic SourcingResponse model
        return SourcingResponse(**parsed)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to parse structured sourcing response from Gemini: {exc}. Raw content: {content}"
        )
