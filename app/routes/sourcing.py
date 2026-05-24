import json
import os
import asyncio
import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from google.genai import types

from app.ai.structure import (
    _get_gemini_api_key,
    _create_gemini_client,
    _parse_json_response,
    DEFAULT_GEMINI_MODEL
)

logger = logging.getLogger(__name__)

async def generate_content_with_retry(client, model, contents, config, max_attempts=4):
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
            is_rate_limit = any(x in exc_str for x in ["429", "resource_exhausted", "quota", "rate limit", "exhausted"])
            if is_rate_limit and attempt < max_attempts:
                sleep_time = 3.0 * (2 ** (attempt - 1))
                logger.warning(f"Gemini API rate limited (attempt {attempt}/{max_attempts}). Retrying in {sleep_time} seconds. Error: {exc}")
                await asyncio.sleep(sleep_time)
                continue
            raise exc

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

    # 4. Generate content from Gemini with retries
    try:
        response = await generate_content_with_retry(
            client=client,
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
    sheetNumber: str = Field(default="D-101", description="Calculated drawing sheet number, e.g. H-102, E-204, P-101")
    title: str = Field(default="Attachment Drawing", description="Standardized clean drawing title")
    fileUrl: str = Field(default="#", description="Direct reference URL")

    @model_validator(mode="before")
    @classmethod
    def map_input_fields(cls, data):
        if isinstance(data, str):
            # If Gemini returned just the drawing ID as a string, wrap it safely
            data = {"id": data, "title": "Attachment Drawing", "sheetNumber": "D-101"}
            
        elif isinstance(data, dict):
            # If the dict is a single key-value pair, and "id" is not in data,
            # treat key as "id" and value as "title" (e.g. {'0158d24a-...': 'Electrical Main SLD'})
            if len(data) == 1 and "id" not in data:
                key = list(data.keys())[0]
                val = data[key]
                if isinstance(val, str):
                    data = {"id": key, "title": val}

            # Map file_name -> title if title is missing
            if "file_name" in data and "title" not in data:
                data["title"] = data["file_name"]
            
            # Map file_name -> sheetNumber if sheetNumber is missing
            if "sheetNumber" not in data:
                import re
                title = data.get("title", data.get("file_name", ""))
                # Look for patterns like E-204, H-102, P-101, etc.
                match = re.search(r'\b([A-Z]-\d{2,4})\b', title, re.IGNORECASE)
                if match:
                    data["sheetNumber"] = match.group(1).upper()
                else:
                    data["sheetNumber"] = "D-101"
        return data

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
1. Match it to highly relevant drawings from the list. If no drawings match, return an empty drawings list.
   - VERY IMPORTANT: The drawings output list entries MUST strictly contain the following keys:
     * `id`: The matched drawing ID.
     * `sheetNumber`: A simulated sheet number parsed or assigned to this drawing (e.g., 'H-102' for HVAC, 'E-204' for Electrical, 'P-101' for Plumbing).
     * `title`: A standardized clean title for the sheet (e.g., 'HVAC Piping Plan Layout'). Do NOT return the input key `file_name`—use `title` instead.
     * `fileUrl`: Set to '#' by default.
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

    # 4. Generate content from Gemini with retries
    try:
        response = await generate_content_with_retry(
            client=client,
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


class PriceListMatchRequest(BaseModel):
    description: str = Field(..., description="The BOQ item description")
    category: Optional[str] = Field(None, description="The category/trade of the BOQ item")
    price_lists: List[str] = Field(..., description="List of price list/catalog file names available in the project")

class PriceListMatchResponse(BaseModel):
    matched: bool = Field(..., description="True if a catalog match was found")
    brand: Optional[str] = Field(None, description="The matched manufacturer/brand name")
    catalog_code: Optional[str] = Field(None, description="The catalog item code/part number if found")
    list_price: Optional[float] = Field(None, description="The catalog MSRP / list price in Indian Rupees (₹)")
    discount: Optional[float] = Field(None, description="The default catalog/trade discount percentage if specified")
    matched_description: Optional[str] = Field(None, description="The matched description from the catalog")
    notes: Optional[str] = Field(None, description="AI lookup and matching notes")

MATCH_PRICELIST_SYSTEM_PROMPT = """You are an expert construction estimator and quantity surveying consultant.
Your job is to take a BOQ item description, its trade category, and a list of available price list/catalog file names, and intelligently find/extract the list price and brand specifications.

Since actual catalog PDFs are stored in the user's Supabase repository, you are given the catalog filenames as a reference (e.g. ['Polycab-Cables-Pricelist-2026.pdf', 'Astral-Pipes-Catalog-2025.pdf']).
Based on your extensive industrial knowledge of standard construction material catalog rates in Indian and Middle Eastern markets (such as Polycab, Finolex, Havells, Anchor, Astral, Supreme, Tyco, Grundfos, Tata Steel, Jindal, Saint Gobain):
1. Determine if any of the available price lists match the BOQ item trade (e.g. matching 'cables' to 'Polycab-Cables-Pricelist-2026.pdf').
2. Locate or simulate the high-accuracy official catalog List Price (MSRP) for the item. E.g.
   - 2.5 Sq.mm wire -> list price of ~₹138/mtr (MSRP).
   - 25mm copper tubing -> list price of ~₹425/mtr (MSRP).
   - 50mm GI Water Pipe -> list price of ~₹650/mtr (MSRP).
3. If a match is found:
   - Set `matched` to true.
   - Set `brand` to the matched brand name.
   - Set `catalog_code` to a realistic catalog code (e.g. `PC-25-3C`, `AS-GI-50`).
   - Set `list_price` to the official retail list price.
   - Set `discount` to a standard trade discount percentage (e.g., 10% to 25% depending on standard trade discounts in India).
   - Set `matched_description` to the official standardized catalog specification.
   - Set `notes` to a 1-sentence note showing which catalog list was cleared.
4. If no logical match can be made, return `matched = false`.

Return only a valid JSON object matching the requested schema. Do not return extra text."""


@router.post("/match-price-list", response_model=PriceListMatchResponse)
async def match_price_list_rate(body: PriceListMatchRequest) -> PriceListMatchResponse:
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
        "boq_description": body.description,
        "category": body.category or "Construction",
        "available_catalogs": body.price_lists
    }

    # 3. Choose the model
    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    client = _create_gemini_client(api_key)

    # 4. Generate content from Gemini with retries
    try:
        response = await generate_content_with_retry(
            client=client,
            model=model,
            contents=json.dumps(user_payload, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=MATCH_PRICELIST_SYSTEM_PROMPT,
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=520,
            detail=f"Gemini AI Catalog matching failed: {exc}"
        ) from exc

    content = response.text
    if not content:
        raise HTTPException(
            status_code=502,
            detail="Gemini returned an empty catalog matching response."
        )

    # 5. Parse JSON
    try:
        parsed = _parse_json_response(content)
        return PriceListMatchResponse(**parsed)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to parse structured catalog matching response from Gemini: {exc}. Raw content: {content}"
        )


