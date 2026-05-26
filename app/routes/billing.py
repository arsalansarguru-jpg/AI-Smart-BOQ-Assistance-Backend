from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/billing", tags=["billing"])

class BillingItem(BaseModel):
    id: str = Field(..., description="Standard unique BOQ item ID")
    item_no: str = Field(..., description="BOQ item reference number")
    description: str = Field(..., description="Standardized item description")
    category: str = Field(..., description="Construction trade category")
    boq_qty: float = Field(..., description="Initial tender BOQ quantity limit")
    unit: str = Field(..., description="Measurement unit")
    rate: float = Field(..., description="Contract agreed unit rate")
    prev_qty: float = Field(..., description="Quantity executed in previous months")
    current_qty: float = Field(..., description="Quantity executed in the current monthly cycle")
    remarks: Optional[str] = Field(None, description="Site engineer remarks or annotations")

class RaBillRequest(BaseModel):
    project_name: str = Field(..., description="Contract title")
    bill_number: str = Field(..., description="Running Account invoice billing serial (e.g. RA-01, RA-02)")
    items: List[BillingItem] = Field(..., description="List of items with logged progress measurements")

class RaBillResponse(BaseModel):
    project_name: str
    bill_number: str
    total_contract_sum: float
    total_prev_amount: float
    total_current_amount: float # Gross Valuation of Work Done
    total_cum_amount: float
    retention_deduction: float # 5% Contract Retention Cash Withheld
    net_receivable_before_tax: float
    tax_amount: float # 18% GST (CGST 9% + SGST 9%)
    net_payable_amount: float # Final payment due to the contractor
    overall_progress_percent: float
    certified_status: str

@router.post("/generate-ra-bill", response_model=RaBillResponse)
async def generate_ra_bill_invoice(body: RaBillRequest) -> RaBillResponse:
    if not body.items:
        raise HTTPException(
            status_code=400,
            detail="Cannot compile progress invoice with an empty measurements sheet."
        )
    
    total_contract_sum = 0.0
    total_prev_amount = 0.0
    total_current_amount = 0.0
    total_cum_amount = 0.0
    
    for item in body.items:
        total_contract_sum += item.boq_qty * item.rate
        total_prev_amount += item.prev_qty * item.rate
        total_current_amount += item.current_qty * item.rate
        total_cum_amount += (item.prev_qty + item.current_qty) * item.rate

    # Dynamic QS financial structures calculations
    retention_deduction = round(total_current_amount * 0.05, 2)
    net_receivable_before_tax = round(total_current_amount - retention_deduction, 2)
    tax_amount = round(net_receivable_before_tax * 0.18, 2)
    net_payable_amount = round(net_receivable_before_tax + tax_amount, 2)
    
    overall_progress_percent = 0.0
    if total_contract_sum > 0:
        overall_progress_percent = round((total_cum_amount / total_contract_sum) * 100, 2)
        
    return RaBillResponse(
        project_name=body.project_name,
        bill_number=body.bill_number,
        total_contract_sum=round(total_contract_sum, 2),
        total_prev_amount=round(total_prev_amount, 2),
        total_current_amount=round(total_current_amount, 2),
        total_cum_amount=round(total_cum_amount, 2),
        retention_deduction=retention_deduction,
        net_receivable_before_tax=net_receivable_before_tax,
        tax_amount=tax_amount,
        net_payable_amount=net_payable_amount,
        overall_progress_percent=overall_progress_percent,
        certified_status="Certified JMR"
    )
