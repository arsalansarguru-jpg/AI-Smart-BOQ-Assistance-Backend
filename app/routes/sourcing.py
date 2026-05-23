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


class AutoLinkItem(BaseModel):
    id: str = Field(..., description="The unique ID of the BOQ item")
    description: str = Field(..., description="The material/item description")
    category: Optional[str] = Field(None, description="Trade category")

class AutoLinkDrawing(BaseModel):
    id: str = Field(..., description="The drawing file ID or sheet number")
    file_name: str = Field(..., description="The drawing file name or sheet title")

class AutoLinkRequest(BaseModel):
    items: List[AutoLinkItem] = Field(..., description="List of BOQ items to link")
    drawings: List[AutoLinkDrawing] = Field(..., description="List of drawing files/sheets available")
    make_list_brands: List[str] = Field(..., description="List of approved brands from the make list")

class LinkedDrawing(BaseModel):
    id: str = Field(..., description="Drawing sheet ID")
    sheetNumber: str = Field(..., description="Calculated drawing sheet number, e.g. H-102, E-204, P-101")
    title: str = Field(..., description="Standardized clean drawing title")
    fileUrl: str = Field("#", description="Direct reference URL")

class LinkedMake(BaseModel):
    brand: str = Field(..., description="Brand name")
    status: str = Field("Approved", description="Approval status: Approved, Preferred, Alternative")

class AutoLinkMatch(BaseModel):
    item_id: str = Field(..., description="BOQ item ID")
    drawings: List[LinkedDrawing] = Field(default_factory=list, description="Matched drawing references")
    makes: List[LinkedMake] = Field(default_factory=list, description="Matched approved manufacturer brands")
    notes: str = Field("", description="AI coordination notes")

class AutoLinkResponse(BaseModel):
    matches: List[AutoLinkMatch] = Field(..., description="Linked item references")

AUTOLINK_SYSTEM_PROMPT = """You are an expert AI Construction Interlinker and procurement coordinator.
Your job is to analyze a list of construction BOQ (Bill of Quantities) items and intelligently pair/link them to a set of available Drawing Documents and Brand/Manufacturer names.

You are given:
1. A list of BOQ items (each has an `id`, `description`, and optional `category`).
2. A list of available Drawing Documents (each has an `id` and `file_name` e.g., 'HVAC-Piping-Layout.pdf' or 'E-204-Distribution-Block.pdf').
3. A list of approved manufacturer brand names from the Make List (e.g. ['Supreme', 'Polycab', 'Finolex', 'Astral', 'Grundfos', 'Schneider']).

For each BOQ item:
1. Match it to highly relevant drawings from the list. If a BOQ item is 'copper piping HVAC', match it to drawings like 'HVAC-Piping-Layout.pdf' or any HVAC-related drawing. If no drawings match, return an empty drawings list.
   - For matched drawings, parse/assign a simulated sheet number if none is present (e.g., 'H-102' for HVAC, 'E-204' for Electrical, 'P-101' for Plumbing, 'F-102' for Fire Fighting) and clean up the title.
2. Match it to approved brands from the make list. E.g. if the item is 'flexible cable', match it to electrical brands like 'Finolex' or 'Polycab'. Determine the status as 'Approved', 'Preferred', or 'Alternative' based on how well the brand matches the description. If no brands are provided or none match, recommend 1-2 standard brands matching the trade from the make list or standard Indian/Middle Eastern construction brands.
3. Provide a brief 1-sentence `notes` explaining why these drawings/brands were linked, or adding a quick installation/procurement note.

Return a JSON object containing the `matches` list, where each entry matches the exact requested schema (item_id, drawings list with details, makes list, notes). Return ONLY valid JSON matching the schema."""


@router.post("/auto-link", response_model=AutoLinkResponse)
async def auto_link_project_documents(body: AutoLinkRequest) -> AutoLinkResponse:
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
        "items": [item.model_dump() for item in body.items],
        "drawings": [dwg.model_dump() for dwg in body.drawings],
        "make_list_brands": body.make_list_brands
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
                system_instruction=AUTOLINK_SYSTEM_PROMPT,
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=520,
            detail=f"Gemini AI Auto-Linker query failed: {exc}"
        ) from exc

    content = response.text
    if not content:
        raise HTTPException(
            status_code=502,
            detail="Gemini returned an empty auto-linker response."
        )

    # 5. Parse JSON
    try:
        parsed = _parse_json_response(content)
        if "matches" not in parsed:
            if isinstance(parsed, list):
                parsed = {"matches": parsed}
            else:
                raise ValueError("Response does not contain a list of matches.")
        
        return AutoLinkResponse(**parsed)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to parse structured auto-linker response from Gemini: {exc}. Raw content: {content}"
        )

