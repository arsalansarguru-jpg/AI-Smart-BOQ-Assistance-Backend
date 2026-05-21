from fastapi import APIRouter, HTTPException

from app.ai.quotations import structure_quotation_tables
from app.models.schemas import QuotationStructureRequest, QuotationStructureResponse

router = APIRouter(prefix="/structure/quotation", tags=["quotation"])


@router.post("", response_model=QuotationStructureResponse)
async def structure_quotation(body: QuotationStructureRequest) -> QuotationStructureResponse:
    if not body.filename.strip():
        raise HTTPException(status_code=400, detail="Filename is required.")
    if not body.tables:
        raise HTTPException(
            status_code=400,
            detail="No tables provided. Extract tables from the file first.",
        )
    return structure_quotation_tables(body.filename.strip(), body.tables)
