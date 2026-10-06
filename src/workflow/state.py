from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel, Field

STATUSES = ("ACCEPTED", "VALIDATED", "REJECTED", "NEEDS_REVIEW", "ERROR", "SKIPPED")


@dataclass
class ParseResult:
    markdown: str
    pages: int = 1


@dataclass
class ExtractResult:
    data: dict
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""


@dataclass
class EntryResult:
    success: bool
    screenshot: str = ""
    message: str = ""


class InvoiceState(BaseModel):
    file_path: str
    file_name: str = ""
    markdown: str = ""
    extracted: Optional[dict] = None
    invoice: Optional[dict] = None
    issues: list[dict] = Field(default_factory=list)
    registry_status: Optional[str] = None
    attempt: int = 0
    stalled: bool = False
    status: str = "PENDING"
    reason: str = ""
    ledger_confirmed: bool = False
    screenshot: str = ""
    trace: list[str] = Field(default_factory=list)