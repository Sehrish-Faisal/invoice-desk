from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

Money=Annotated[Decimal,Field(ge=0,le=999999999,decimal_places=2)]
Qty=Annotated[Decimal,Field(gt=0,le=999999999,decimal_places=3)]
Price=Annotated[Decimal,Field(ge=0,le=999999999,decimal_places=4)]
class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True,allow_inf_nan=False)
class Login(Model):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=False)
    username: str=Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_.-]+$')
    password: str=Field(min_length=1,max_length=200)
class Line(Model):
    description: str=Field(min_length=1,max_length=300)
    quantity: Qty
    unit_price: Price
    po_line_id: UUID
class Review(Model):
    supplier_id: UUID
    po_id: UUID
    invoice_number: str=Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9][A-Za-z0-9 /_.-]*$')
    invoice_date: date
    due_date: date
    currency: Literal['PKR','USD','EUR','GBP']
    lines: list[Line]=Field(min_length=1,max_length=100)
    tax: Money=Decimal('0')
    discount: Money=Decimal('0')
    declared_total: Money
    @model_validator(mode='after')
    def dates(self):
        if self.due_date < self.invoice_date: raise ValueError('Due date precedes invoice date.')
        return self
class ReviewRequest(Review):
    expected_version: int=Field(ge=0)
class Version(Model):
    expected_version: int=Field(ge=0)
class Decision(Version):
    decision: Literal['approve','reject']
    reason: str=Field(min_length=3,max_length=500)
class Supplier(Model):
    name: str=Field(min_length=2,max_length=150)
class POLine(Model):
    description: str=Field(min_length=1,max_length=300)
    quantity: Qty
    unit_price: Price
class PurchaseOrder(Model):
    reference: str=Field(min_length=1,max_length=80)
    supplier_id: UUID
    currency: Literal['PKR','USD','EUR','GBP']
    lines: list[POLine]=Field(min_length=1,max_length=100)
class SuggestedLine(Model):
    description: str=Field(max_length=300)
    quantity: Qty | None
    unit_price: Price | None
    page: int=Field(ge=1,le=10)
    evidence: str=Field(max_length=300)
class Suggestion(Model):
    supplier_name: str=Field(max_length=150)
    invoice_number: str=Field(max_length=80)
    invoice_date: date | None
    due_date: date | None
    currency: Literal['PKR','USD','EUR','GBP'] | None
    lines: list[SuggestedLine]=Field(max_length=100)
    tax: Money | None
    discount: Money | None
    declared_total: Money | None
    warnings: list[str]=Field(max_length=10)
def total(review):
    return sum((l.quantity*l.unit_price).quantize(Decimal('.01'),rounding=ROUND_HALF_UP) for l in review.lines)+review.tax-review.discount
