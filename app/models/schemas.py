from pydantic import BaseModel, Field


class TableBlock(BaseModel):
    page: int | None = None
    sheet: str | None = None
    rows: list[list[str | None]]


class ExtractResponse(BaseModel):
    filename: str
    file_type: str
    page_count: int | None = None
    sheet_count: int | None = None
    tables: list[TableBlock] = Field(default_factory=list)
    message: str | None = None


class BoqLineItem(BaseModel):
    item_no: str | None = None
    category: str | None = None
    description: str
    unit: str | None = None
    quantity: float | None = None
    rate: float | None = None
    amount: float | None = None
    remarks: str | None = None
    confidence: float | None = None
    original_text: str | None = None


class StructureRequest(BaseModel):
    filename: str
    tables: list[TableBlock] = Field(default_factory=list)


class StructureResponse(BaseModel):
    filename: str
    items: list[BoqLineItem] = Field(default_factory=list)
    summary: str | None = None
    warnings: list[str] = Field(default_factory=list)
    model: str | None = None
    rows_analyzed: int | None = None


class ExportRequest(BaseModel):
    filename: str
    items: list[BoqLineItem] = Field(default_factory=list)
    project_name: str | None = None
    summary: str | None = None


class QuotationLineItem(BaseModel):
    item_name: str
    brand: str | None = None
    unit: str | None = None
    quoted_rate: float
    normalized_item_name: str | None = None


class QuotationStructureRequest(BaseModel):
    filename: str
    tables: list[TableBlock] = Field(default_factory=list)


class QuotationStructureResponse(BaseModel):
    vendor_name: str
    quotation_date: str | None = None
    items: list[QuotationLineItem] = Field(default_factory=list)
    confidence: float
    summary: str | None = None
    warnings: list[str] = Field(default_factory=list)

