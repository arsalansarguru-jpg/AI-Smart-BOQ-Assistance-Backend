import io
from typing import Any

import pandas as pd

from app.models.schemas import TableBlock


def _normalize_cell(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text if text else None


def extract_excel_tables(excel_bytes: bytes) -> tuple[list[TableBlock], int]:
    tables: list[TableBlock] = []
    xl = pd.ExcelFile(io.BytesIO(excel_bytes))
    sheet_count = len(xl.sheet_names)

    for sheet_name in xl.sheet_names:
        df = pd.read_excel(xl, sheet_name=sheet_name, header=None, dtype=object)
        rows = [
            [_normalize_cell(c) for c in row]
            for row in df.values.tolist()
        ]
        if any(any(cell for cell in row) for row in rows):
            tables.append(TableBlock(sheet=sheet_name, rows=rows))

    return tables, sheet_count
