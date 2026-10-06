from __future__ import annotations

import re

from src.formatting import format_inr

GSTIN_CODES = {"GSTIN_FORMAT", "GSTIN_STATE", "GSTIN_CHECKSUM"}


def _amount_forms(value: float) -> set[str]:
    plain = f"{value:.2f}"
    indian = format_inr(value)
    western = f"{value:,.2f}"
    forms = {plain, indian, western}
    for form in (plain, indian, western):
        trimmed = form.rstrip("0").rstrip(".")
        if trimmed:
            forms.add(trimmed)
    return forms


def _present(value: float, text: str) -> bool:
    for form in _amount_forms(value):
        pattern = r"(?<![\d,])(?<!\d\.)" + re.escape(form) + r"(?!\d|[.,]\d)"
        if re.search(pattern, text):
            return True
    return False


def issues_confirmed_by_source(issues: list[dict], invoice: dict, markdown: str) -> bool:
    if not issues or not invoice or not markdown:
        return False

    gstins: list[str] = []
    amounts: list[float] = []
    lines = invoice.get("line_items") or []

    for issue in issues:
        code = issue.get("code")
        if code in GSTIN_CODES:
            gstins.append(str(invoice.get("vendor_gstin", "")))
        elif code == "ARITHMETIC_TOTAL":
            amounts += [invoice.get(k, 0) for k in ("subtotal", "cgst", "sgst", "igst", "total")]
        elif code == "ARITHMETIC_SUBTOTAL":
            amounts.append(invoice.get("subtotal", 0))
            amounts += [line.get("amount", 0) for line in lines]
        elif code == "ARITHMETIC_LINE":
            for line in lines:
                amounts += [line.get("quantity", 0), line.get("rate", 0), line.get("amount", 0)]
        elif code == "TAX_TYPE_MISMATCH":
            amounts += [invoice.get(k, 0) for k in ("cgst", "sgst", "igst")]
        else:
            return False

    compact = re.sub(r"\s+", "", markdown).upper()
    checked = 0
    for gstin in gstins:
        if not gstin or gstin.upper() not in compact:
            return False
        checked += 1
    for amount in amounts:
        value = float(amount or 0)
        if value == 0:
            continue
        if not _present(value, markdown):
            return False
        checked += 1
    return checked > 0