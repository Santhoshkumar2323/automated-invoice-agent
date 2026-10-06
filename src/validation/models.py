from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

DATE_FORMATS = (
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d-%b-%Y",
)

_CURRENCY_NOISE = re.compile(r"(?i)(rs\.?|inr|₹|\s)")


def parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date format: {text!r}")


def parse_amount(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not an amount")
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        cleaned = _CURRENCY_NOISE.sub("", str(value)).replace(",", "")
        if cleaned == "":
            raise ValueError("empty amount")
        number = float(cleaned)
    if not math.isfinite(number):
        raise ValueError("amount must be finite")
    return round(number, 2)


class LineItem(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    description: str = Field(min_length=1)
    hsn_code: str = ""
    quantity: float = Field(gt=0)
    rate: float = Field(ge=0)
    amount: float = Field(ge=0)

    @field_validator("quantity", "rate", "amount", mode="before")
    @classmethod
    def _amounts(cls, value: Any) -> float:
        return parse_amount(value)

    @field_validator("hsn_code", mode="before")
    @classmethod
    def _hsn(cls, value: Any) -> str:
        return "" if value is None else str(value).strip()


class Invoice(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    invoice_number: str = Field(min_length=1)
    invoice_date: date
    vendor_name: str = Field(min_length=1)
    vendor_gstin: str = Field(min_length=1)
    buyer_name: str = ""
    buyer_gstin: str = ""
    place_of_supply: str = ""
    line_items: list[LineItem] = Field(min_length=1)
    subtotal: float = Field(ge=0)
    cgst: float = Field(0.0, ge=0)
    sgst: float = Field(0.0, ge=0)
    igst: float = Field(0.0, ge=0)
    total: float = Field(ge=0)

    @field_validator("invoice_date", mode="before")
    @classmethod
    def _date(cls, value: Any) -> date:
        return parse_date(value)

    @field_validator("subtotal", "cgst", "sgst", "igst", "total", mode="before")
    @classmethod
    def _amounts(cls, value: Any) -> float:
        if value is None or value == "":
            return 0.0
        return parse_amount(value)

    @field_validator("vendor_gstin", "buyer_gstin", mode="before")
    @classmethod
    def _gstin(cls, value: Any) -> str:
        if value is None:
            return ""
        return re.sub(r"\s+", "", str(value)).upper()