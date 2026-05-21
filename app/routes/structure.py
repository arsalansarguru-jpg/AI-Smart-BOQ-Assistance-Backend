from fastapi import APIRouter, HTTPException

from app.ai.structure import structure_boq_tables
from app.models.schemas import StructureRequest, StructureResponse

router = APIRouter(prefix="/structure", tags=["structure"])


@router.post("", response_model=StructureResponse)
async def structure_boq(body: StructureRequest) -> StructureResponse:
    if not body.filename.strip():
        raise HTTPException(status_code=400, detail="Filename is required.")
    if not body.tables:
        raise HTTPException(
            status_code=400,
            detail="No tables provided. Extract tables from the file first.",
        )
    return structure_boq_tables(body.filename.strip(), body.tables)
