from typing import Optional
from fastapi import APIRouter, HTTPException, Header

from app.ai.structure import structure_boq_tables
from app.models.schemas import StructureRequest, StructureResponse

router = APIRouter(prefix="/structure", tags=["structure"])


@router.post("", response_model=StructureResponse)
async def structure_boq(
    body: StructureRequest,
    x_gemini_api_key: Optional[str] = Header(None)
) -> StructureResponse:
    if not body.filename.strip():
        raise HTTPException(status_code=400, detail="Filename is required.")
    if not body.tables:
        raise HTTPException(
            status_code=400,
            detail="No tables provided. Extract tables from the file first.",
        )
    
    api_key = None
    if isinstance(x_gemini_api_key, str) and x_gemini_api_key.strip():
        api_key = x_gemini_api_key.strip()
        
    return await structure_boq_tables(body.filename.strip(), body.tables, api_key=api_key)
