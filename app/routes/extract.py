from fastapi import APIRouter, File, HTTPException, UploadFile

from app.extraction.excel import extract_excel_tables
from app.extraction.pdf import extract_pdf_tables
from app.models.schemas import ExtractResponse

router = APIRouter(prefix="/extract", tags=["extract"])

PDF_TYPES = {"application/pdf"}
EXCEL_TYPES = {
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _guess_type(filename: str, content_type: str | None) -> str:
    lower = filename.lower()
    if content_type in PDF_TYPES or lower.endswith(".pdf"):
        return "pdf"
    if content_type in EXCEL_TYPES or lower.endswith((".xls", ".xlsx")):
        return "excel"
    return "unknown"


@router.post("", response_model=ExtractResponse)
async def extract_tables(file: UploadFile = File(...)) -> ExtractResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required.")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="File is empty.")

    file_type = _guess_type(file.filename, file.content_type)
    if file_type == "unknown":
        raise HTTPException(
            status_code=400,
            detail="Only PDF and Excel (.xls, .xlsx) files are supported.",
        )

    if file_type == "pdf":
        tables, page_count = extract_pdf_tables(content)
        if not tables:
            return ExtractResponse(
                filename=file.filename,
                file_type=file_type,
                page_count=page_count,
                tables=[],
                message="No tables detected. The PDF may be scanned or use non-standard layout.",
            )
        return ExtractResponse(
            filename=file.filename,
            file_type=file_type,
            page_count=page_count,
            tables=tables,
        )

    tables, sheet_count = extract_excel_tables(content)
    if not tables:
        return ExtractResponse(
            filename=file.filename,
            file_type=file_type,
            sheet_count=sheet_count,
            tables=[],
            message="No data found in Excel sheets.",
        )
    return ExtractResponse(
        filename=file.filename,
        file_type=file_type,
        sheet_count=sheet_count,
        tables=tables,
    )
