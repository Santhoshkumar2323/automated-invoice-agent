from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.config import CheckSettings
from src.validation import gst_registry
from src.validation.models import Invoice

CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_PATTERN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
AMOUNT_TOLERANCE = 0.05

STATE_CODES = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
}


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    retryable: bool


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)
    registry_status: Optional[str] = None

    @property
    def passed(self) -> bool:
        return not self.issues

    @property
    def codes(self) -> list[str]:
        return [i.code for i in self.issues]

    @property
    def should_retry(self) -> bool:
        return bool(self.issues) and all(i.retryable for i in self.issues)

    def summary(self) -> str:
        if self.passed:
            return "all checks passed"
        return "; ".join(f"{i.code}: {i.message}" for i in self.issues)


def gstin_check_char(first14: str) -> str:
    factor = 2
    total = 0
    for ch in reversed(first14):
        value = CHARSET.index(ch) * factor
        factor = 1 if factor == 2 else 2
        total += value // 36 + value % 36
    return CHARSET[(36 - total % 36) % 36]


def build_gstin(state_code: str, pan: str, entity: str = "1") -> str:
    base = f"{state_code}{pan}{entity}Z"
    return base + gstin_check_char(base)


def check_gstin(gstin: str, checks: CheckSettings) -> list[Issue]:
    value = (gstin or "").strip().upper()
    if not GSTIN_PATTERN.match(value):
        if checks.gstin_format:
            return [Issue("GSTIN_FORMAT", f"GSTIN '{value}' does not match the 15-character GSTIN pattern", True)]
        return []
    issues: list[Issue] = []
    if checks.state_code and value[:2] not in STATE_CODES:
        issues.append(Issue("GSTIN_STATE", f"GSTIN state code '{value[:2]}' is not a valid state code", True))
    if checks.gstin_checksum and gstin_check_char(value[:14]) != value[14]:
        issues.append(Issue("GSTIN_CHECKSUM", f"GSTIN '{value}' fails the checksum digit test", True))
    return issues


def check_arithmetic(invoice: Invoice) -> list[Issue]:
    issues: list[Issue] = []

    for index, item in enumerate(invoice.line_items, start=1):
        if abs(item.quantity * item.rate - item.amount) > AMOUNT_TOLERANCE:
            issues.append(
                Issue("ARITHMETIC_LINE", f"line {index}: quantity x rate does not equal amount", True)
            )

    line_sum = round(sum(i.amount for i in invoice.line_items), 2)
    if abs(line_sum - invoice.subtotal) > AMOUNT_TOLERANCE:
        issues.append(
            Issue("ARITHMETIC_SUBTOTAL", f"line items add up to {line_sum} but subtotal is {invoice.subtotal}", True)
        )

    expected_total = round(invoice.subtotal + invoice.cgst + invoice.sgst + invoice.igst, 2)
    if abs(expected_total - invoice.total) > AMOUNT_TOLERANCE:
        issues.append(
            Issue("ARITHMETIC_TOTAL", f"subtotal plus taxes is {expected_total} but total is {invoice.total}", True)
        )

    vendor_state = invoice.vendor_gstin[:2]
    buyer_state = invoice.buyer_gstin[:2]
    if vendor_state.isdigit() and buyer_state.isdigit():
        if vendor_state == buyer_state:
            if invoice.igst > 0 or invoice.cgst <= 0 or invoice.sgst <= 0:
                issues.append(Issue("TAX_TYPE_MISMATCH", "same-state supply must charge CGST and SGST only", True))
        else:
            if invoice.cgst > 0 or invoice.sgst > 0 or invoice.igst <= 0:
                issues.append(Issue("TAX_TYPE_MISMATCH", "interstate supply must charge IGST only", True))
    return issues


def check_duplicate(invoice: Invoice, ledger_rows: list[dict]) -> list[Issue]:
    for row in ledger_rows:
        if row.get("gstin") == invoice.vendor_gstin and row.get("invoice_number") == invoice.invoice_number:
            return [
                Issue(
                    "DUPLICATE_INVOICE",
                    f"invoice {invoice.invoice_number} from {invoice.vendor_gstin} is already in the ledger",
                    False,
                )
            ]
    return []


def check_registry(gstin: str) -> tuple[str, list[Issue]]:
    status = gst_registry.get_status(gstin)
    if status == "ACTIVE":
        return status, []
    messages = {
        "SUSPENDED": "vendor GST registration is suspended",
        "REVOKED": "vendor GST registration is revoked",
        "NOT_FOUND": "vendor GSTIN is not in the registry",
    }
    return status, [Issue(f"REGISTRY_{status}", messages.get(status, "vendor is not active"), False)]


def validate_invoice(invoice: Invoice, checks: CheckSettings, ledger_rows: list[dict]) -> ValidationResult:
    result = ValidationResult()
    gstin_issues = check_gstin(invoice.vendor_gstin, checks)
    result.issues.extend(gstin_issues)

    if checks.arithmetic:
        result.issues.extend(check_arithmetic(invoice))

    if checks.duplicate_invoice:
        result.issues.extend(check_duplicate(invoice, ledger_rows))

    if checks.registry_status and not gstin_issues:
        status, issues = check_registry(invoice.vendor_gstin)
        result.registry_status = status
        result.issues.extend(issues)

    return result


def load_ledger_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]