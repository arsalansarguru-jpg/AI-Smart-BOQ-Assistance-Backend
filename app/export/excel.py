import re
from datetime import datetime, timezone
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.models.schemas import BoqLineItem

HEADERS = [
    "Item No",
    "Category",
    "Description",
    "Unit",
    "Quantity",
    "Rate",
    "Amount",
    "Remarks",
]

HEADER_FILL = PatternFill("solid", fgColor="1F2937")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
TITLE_FONT = Font(bold=True, size=16)
META_FONT = Font(size=10, color="4B5563")
NUM_ALIGN = Alignment(horizontal="right", vertical="top")
TEXT_ALIGN = Alignment(vertical="top", wrap_text=True)

COLUMN_WIDTHS = [12, 20, 52, 10, 12, 14, 14, 28]


def safe_export_filename(source_filename: str) -> str:
    base = re.sub(r"\.[^.]+$", "", source_filename.strip() or "boq")
    base = re.sub(r'[<>:"/\\|?*]', "_", base)[:80]
    return f"{base}_BOQ_export.xlsx"


def build_boq_workbook(
    items: list[BoqLineItem],
    *,
    source_filename: str,
    project_name: str | None = None,
    summary: str | None = None,
) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "BOQ"

    row = 1
    ws.cell(row=row, column=1, value="Bill of Quantities").font = TITLE_FONT
    row += 1

    if project_name:
        cell = ws.cell(row=row, column=1, value=f"Project: {project_name}")
        cell.font = META_FONT
        row += 1

    ws.cell(row=row, column=1, value=f"Source file: {source_filename}").font = (
        META_FONT
    )
    row += 1

    exported_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ws.cell(row=row, column=1, value=f"Exported: {exported_at}").font = META_FONT
    row += 1

    if summary:
        cell = ws.cell(row=row, column=1, value=summary)
        cell.font = META_FONT
        cell.alignment = Alignment(wrap_text=True)
        row += 1

    row += 1
    header_row = row

    for col, title in enumerate(HEADERS, start=1):
        cell = ws.cell(row=header_row, column=col, value=title)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")

    data_start = header_row + 1
    total_amount = 0.0
    has_amount = False

    for offset, item in enumerate(items):
        r = data_start + offset
        values = [
            item.item_no,
            item.category,
            item.description,
            item.unit,
            item.quantity,
            item.rate,
            item.amount,
            item.remarks,
        ]
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=r, column=col, value=value)
            cell.alignment = (
                NUM_ALIGN if col in (5, 6, 7) else TEXT_ALIGN
            )
            if col in (5, 6, 7) and isinstance(value, (int, float)):
                cell.number_format = "#,##0.00"

        if item.amount is not None:
            has_amount = True
            total_amount += item.amount

    if has_amount:
        total_row = data_start + len(items) + 1
        label_cell = ws.cell(row=total_row, column=6, value="Total")
        label_cell.font = Font(bold=True)
        label_cell.alignment = Alignment(horizontal="right")
        total_cell = ws.cell(row=total_row, column=7, value=total_amount)
        total_cell.font = Font(bold=True)
        total_cell.number_format = "#,##0.00"
        total_cell.alignment = NUM_ALIGN

    for idx, width in enumerate(COLUMN_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width

    ws.freeze_panes = ws.cell(row=data_start, column=1)

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
