from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.config import CheckSettings
from src.validation.models import parse_amount
from src.validation.validators import check_gstin, load_ledger_rows

SUCCESS_TOKEN = "Ledger Entry Verified"
STATUS_VERIFIED = "VERIFIED"


class LedgerError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def read_rows(path: Path) -> list[dict]:
    return load_ledger_rows(path)


def _read_strict(path: Path) -> list:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (UnicodeDecodeError, OSError) as exc:
        raise LedgerError("CORRUPT", "ledger file cannot be read as text, so it was left untouched") from exc
    if not text.strip():
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LedgerError(
            "CORRUPT",
            f"ledger file is not valid JSON ({exc.msg}, line {exc.lineno}), so it was left untouched",
        ) from exc
    if not isinstance(data, list):
        raise LedgerError("CORRUPT", "ledger file has an unexpected format, so it was left untouched")
    return data


def _write_rows(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    os.replace(temp, path)


def add_entry(
    path: Path,
    vendor: str,
    gstin: str,
    invoice_number: str,
    amount,
    now: Optional[datetime] = None,
) -> dict:
    vendor = (vendor or "").strip()
    gstin = re.sub(r"\s+", "", gstin or "").upper()
    invoice_number = (invoice_number or "").strip()
    amount_text = "" if amount is None else str(amount).strip()

    missing = [
        label
        for label, value in (
            ("vendor name", vendor),
            ("GSTIN", gstin),
            ("invoice number", invoice_number),
            ("amount", amount_text),
        )
        if not value
    ]
    if missing:
        raise LedgerError("MISSING_FIELD", "missing " + ", ".join(missing))

    gstin_issues = check_gstin(gstin, CheckSettings())
    if gstin_issues:
        raise LedgerError("BAD_GSTIN", gstin_issues[0].message)

    try:
        value = parse_amount(amount_text)
    except (ValueError, TypeError) as exc:
        raise LedgerError("BAD_AMOUNT", f"amount '{amount_text}' is not a valid number") from exc
    if value <= 0:
        raise LedgerError("BAD_AMOUNT", "amount must be greater than zero")

    rows = _read_strict(path)
    for row in rows:
        if isinstance(row, dict) and row.get("gstin") == gstin and row.get("invoice_number") == invoice_number:
            raise LedgerError("DUPLICATE", f"invoice {invoice_number} from {gstin} already exists in the ledger")

    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")
    entry = {
        "timestamp": stamp,
        "vendor": vendor,
        "gstin": gstin,
        "invoice_number": invoice_number,
        "amount": value,
        "status": STATUS_VERIFIED,
    }
    rows.append(entry)
    _write_rows(path, rows)
    return entry