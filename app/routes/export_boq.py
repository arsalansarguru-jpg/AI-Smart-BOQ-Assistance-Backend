from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.export.excel import build_boq_workbook, safe_export_filename
from app.models.schemas import ExportRequest

router = APIRouter(prefix="/export", tags=["export"])


@router.post("/excel")
async def export_boq_excel(body: ExportRequest) -> Response:
    if not body.filename.strip():
        raise HTTPException(status_code=400, detail="Filename is required.")
    if not body.items:
        raise HTTPException(
            status_code=400,
            detail="No BOQ items to export. Structure the BOQ with AI first.",
        )

    content = build_boq_workbook(
        body.items,
        source_filename=body.filename.strip(),
        project_name=body.project_name.strip() if body.project_name else None,
        summary=body.summary,
    )
    download_name = safe_export_filename(body.filename)

    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{download_name}"',
        },
    )
