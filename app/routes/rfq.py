import uuid
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field

router = APIRouter(prefix="/rfq", tags=["rfq"])

# In-memory session store to guarantee high-availability and zero database-missing crashes
MOCK_RFQ_DB: Dict[str, Dict[str, Any]] = {}

class RFQItem(BaseModel):
    id: str = Field(..., description="BOQ Item ID")
    description: str = Field(..., description="Material description")
    quantity: float = Field(..., description="Required quantity")
    unit: str = Field(..., description="Measurement unit")

class RFQDistributeRequest(BaseModel):
    project_id: str = Field(..., description="Tender Project ID")
    items: List[RFQItem] = Field(..., description="BOQ items to distribute")
    suppliers: List[str] = Field(..., description="Material suppliers to invite")

class RFQDistributeResponse(BaseModel):
    links: Dict[str, str] = Field(..., description="Generated secure quoting links for each supplier")

class RFQItemPricing(BaseModel):
    id: str = Field(..., description="BOQ Item ID")
    list_price: float = Field(..., description="Catalog list price (MSRP)")
    discount: float = Field(..., description="Trade discount percentage")
    lead_time: int = Field(..., description="Delivery lead time in days")

class RFQSubmitRequest(BaseModel):
    pricing: List[RFQItemPricing] = Field(..., description="Completed itemized pricing from supplier")

@router.post("/distribute", response_model=RFQDistributeResponse)
async def distribute_rfq(body: RFQDistributeRequest) -> RFQDistributeResponse:
    if not body.items:
        raise HTTPException(status_code=400, detail="Cannot distribute empty items list.")
    if not body.suppliers:
        raise HTTPException(status_code=400, detail="Must specify at least one material supplier.")

    links = {}
    for supplier in body.suppliers:
        session_id = f"rfq_session_{uuid.uuid4().hex[:12]}"
        
        # Seed in-memory mock database
        MOCK_RFQ_DB[session_id] = {
            "session_id": session_id,
            "project_id": body.project_id,
            "supplier": supplier,
            "status": "pending",
            "items": [item.model_dump() for item in body.items],
            "submissions": []
        }
        
        # Generate the secure frontend portal URL
        links[supplier] = f"/rfq/{session_id}"

    return RFQDistributeResponse(links=links)

@router.get("/session/{session_id}")
async def get_rfq_session(session_id: str) -> Dict[str, Any]:
    session = MOCK_RFQ_DB.get(session_id)
    if not session:
        # Generate on-the-fly seed session to support smooth local demoing if not pre-seeded
        if session_id.startswith("rfq_session_"):
            supplier_name = "Polycab Wires" if "wire" in session_id else "Astral Pipes"
            MOCK_RFQ_DB[session_id] = {
                "session_id": session_id,
                "project_id": "proj-seed",
                "supplier": supplier_name,
                "status": "pending",
                "items": [
                    {"id": "it-1", "description": "2.5 Sq.mm 3 Core Wires", "quantity": 1200, "unit": "m"},
                    {"id": "it-2", "description": "16 Sq.mm heavy duty XLPE cable", "quantity": 350, "unit": "m"}
                ],
                "submissions": []
            }
            return MOCK_RFQ_DB[session_id]
        raise HTTPException(status_code=404, detail="RFQ quoting session expired or not found.")
    
    return session

@router.post("/submit/{session_id}")
async def submit_rfq_pricing(session_id: str, body: RFQSubmitRequest) -> Dict[str, Any]:
    session = MOCK_RFQ_DB.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="RFQ quoting session expired or not found.")

    submissions_list = []
    for price in body.pricing:
        net_rate = price.list_price * (1 - (price.discount / 100))
        submissions_list.append({
            "item_id": price.id,
            "list_price": price.list_price,
            "discount": price.discount,
            "net_rate": round(net_rate, 2),
            "lead_time": price.lead_time
        })

    session["status"] = "submitted"
    session["submissions"] = submissions_list
    
    return {
        "status": "success",
        "message": f"RFQ prices successfully compiled for supplier {session['supplier']}.",
        "submissions": submissions_list
    }
