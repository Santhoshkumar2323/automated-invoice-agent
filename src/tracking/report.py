from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.config import Settings
from src.validation.models import parse_amount, parse_date
from src.workflow.state import InvoiceState

TEXT_FIELDS = ("invoice_number", "vendor_name", "vendor_gstin", "buyer_gstin")
AMOUNT_FIELDS = ("subtotal", "cgst", "sgst", "igst", "total")
ALL_FIELDS = ("invoice_number", "invoice_date", "vendor_name", "vendor_gstin", "buyer_gstin") + AMOUNT_FIELDS + ("line_items",)
GOOD_STATUSES = ("ACCEPTED", "VALIDATED")
BAD_STATUSES = ("REJECTED", "NEEDS_REVIEW")


def _norm_text(value) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().upper()


def _same_amount(got, want) -> bool:
    try:
        left = 0.0 if got in (None, "") else parse_amount(got)
        right = 0.0 if want in (None, "") else parse_amount(want)
    except (ValueError, TypeError):
        return False
    return abs(left - right) <= 0.01


def _same_date(got, want) -> bool:
    try:
        return parse_date(got) == parse_date(want)
    except (ValueError, TypeError):
        return False


def _same_lines(got, want) -> bool:
    if not isinstance(got, list) or not isinstance(want, list) or len(got) != len(want):
        return False
    for left, right in zip(got, want):
        if not isinstance(left, dict) or not isinstance(right, dict):
            return False
        if not _same_amount(left.get("amount"), right.get("amount")):
            return False
    return True


def compare_extraction(extracted: Optional[dict], truth: dict) -> dict:
    if not isinstance(extracted, dict):
        return {"correct": 0, "total": len(ALL_FIELDS), "wrong": list(ALL_FIELDS)}
    wrong = []
    for name in ALL_FIELDS:
        got = extracted.get(name)
        want = truth.get(name)
        if name == "invoice_date":
            ok = _same_date(got, want)
        elif name in AMOUNT_FIELDS:
            ok = _same_amount(got, want)
        elif name == "line_items":
            ok = _same_lines(got, want)
        else:
            ok = got is not None and _norm_text(got) == _norm_text(want)
        if not ok:
            wrong.append(name)
    return {"correct": len(ALL_FIELDS) - len(wrong), "total": len(ALL_FIELDS), "wrong": wrong}


def judge(status: str, issue_codes: list[str], expected: Optional[str]) -> Optional[bool]:
    if expected is None or status not in GOOD_STATUSES + BAD_STATUSES:
        return None
    if expected == "ACCEPT":
        return status in GOOD_STATUSES
    return status in BAD_STATUSES and expected in issue_codes


def summarize_run(
    run_id: str,
    started: datetime,
    finished: datetime,
    settings: Settings,
    trigger_reason: str,
    dry_run: bool,
    results: list[InvoiceState],
    truth: dict[str, dict],
    usage: dict,
    tokens_today: int,
    pages_today: int,
) -> dict:
    counts: dict[str, int] = {}
    rows = []
    correct_fields = 0
    total_fields = 0
    expected_bad = 0
    caught = 0
    clean_total = 0
    clean_wrongly_stopped = 0

    for state in results:
        counts[state.status] = counts.get(state.status, 0) + 1
        record = truth.get(state.file_name)
        expected = record["expected_outcome"] if record else None
        codes = [i["code"] for i in state.issues]
        verdict = judge(state.status, codes, expected)

        extraction = None
        if record and state.extracted is not None:
            extraction = compare_extraction(state.extracted, record["invoice"])
            correct_fields += extraction["correct"]
            total_fields += extraction["total"]

        if verdict is not None:
            if expected == "ACCEPT":
                clean_total += 1
                if verdict is False:
                    clean_wrongly_stopped += 1
            else:
                expected_bad += 1
                if verdict:
                    caught += 1

        vendor = ""
        if state.invoice:
            vendor = state.invoice.get("vendor_name", "")
        elif state.extracted:
            vendor = str(state.extracted.get("vendor_name", ""))
        number = ""
        if state.invoice:
            number = state.invoice.get("invoice_number", "")
        elif state.extracted:
            number = str(state.extracted.get("invoice_number", ""))

        rows.append(
            {
                "file": state.file_name,
                "vendor": vendor,
                "invoice_number": number,
                "status": state.status,
                "reason": state.reason,
                "attempts": state.attempt,
                "issue_codes": codes,
                "registry_status": state.registry_status,
                "expected_outcome": expected,
                "defect": record["defect"] if record else None,
                "decision_correct": verdict,
                "extraction": extraction,
                "screenshot": state.screenshot,
            }
        )

    return {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "trigger_reason": trigger_reason,
        "dry_run": dry_run,
        "model": settings.llm.model,
        "counts": counts,
        "invoices": rows,
        "extraction_accuracy": round(correct_fields / total_fields, 4) if total_fields else None,
        "detection": {
            "bad_invoices": expected_bad,
            "caught": caught,
            "clean_invoices": clean_total,
            "clean_wrongly_stopped": clean_wrongly_stopped,
        },
        "usage": usage,
        "budget": {
            "tokens_today": tokens_today,
            "token_limit": settings.budget.daily_token_limit,
            "pages_today": pages_today,
            "page_limit": settings.budget.daily_page_limit,
        },
    }


def _cell(value) -> str:
    return str(value if value is not None else "").replace("|", "/").replace("\n", " ")


def render_markdown(summary: dict) -> str:
    usage = summary["usage"]
    budget = summary["budget"]
    detection = summary["detection"]
    accuracy = summary["extraction_accuracy"]
    lines = [
        f"# Run report {summary['run_id']}",
        "",
        f"- Started: {summary['started_at']}",
        f"- Finished: {summary['finished_at']}",
        f"- Trigger: {summary['trigger_reason']}",
        f"- Mode: {'dry run (no ledger writes)' if summary['dry_run'] else 'full run'}",
        f"- Model: {summary['model']}",
        "",
        "## Outcome",
        "",
        "| Status | Count |",
        "|---|---|",
    ]
    for status, count in sorted(summary["counts"].items()):
        lines.append(f"| {status} | {count} |")
    if not summary["counts"]:
        lines.append("| none | 0 |")

    lines += [
        "",
        "## Invoices",
        "",
        "| File | Vendor | Invoice no | Status | Attempts | Expected | Correct | Reason |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in summary["invoices"]:
        verdict = row["decision_correct"]
        verdict_text = "n/a" if verdict is None else ("yes" if verdict else "NO")
        lines.append(
            f"| {_cell(row['file'])} | {_cell(row['vendor'])} | {_cell(row['invoice_number'])} | {row['status']} | "
            f"{row['attempts']} | {_cell(row['expected_outcome'] or 'n/a')} | {verdict_text} | {_cell(row['reason'])} |"
        )

    lines += ["", "## Quality", ""]
    lines.append(
        "- Extraction accuracy (fields matching ground truth): "
        + ("n/a" if accuracy is None else f"{accuracy * 100:.1f}%")
    )
    lines.append(f"- Bad invoices caught: {detection['caught']} of {detection['bad_invoices']}")
    lines.append(
        f"- Clean invoices wrongly stopped: {detection['clean_wrongly_stopped']} of {detection['clean_invoices']}"
    )
    for row in summary["invoices"]:
        extraction = row["extraction"]
        if extraction and extraction["wrong"]:
            lines.append(f"- {_cell(row['file'])}: wrong fields {', '.join(extraction['wrong'])}")

    price_note = ""
    if usage["cost_usd"] == 0:
        price_note = " (free tier, or prices not set in config.yaml)"
    steps = ", ".join(f"{k} {v}" for k, v in sorted(usage["tokens_by_step"].items())) or "none"
    lines += [
        "",
        "## Usage",
        "",
        f"- Groq calls: {usage['llm_calls']}",
        f"- Tokens in / out / total: {usage['prompt_tokens']} / {usage['completion_tokens']} / {usage['total_tokens']}",
        f"- Tokens by step: {steps}",
        f"- Share of tokens spent on retries: {usage['retry_token_share'] * 100:.1f}%",
        f"- LlamaParse pages: {usage['pages']}",
        f"- Estimated cost (USD): {usage['cost_usd']:.6f}{price_note}",
        "",
        "## Daily budget (UTC day)",
        "",
        f"- Tokens today: {budget['tokens_today']} / {budget['token_limit'] or 'unlimited'}",
        f"- Pages today: {budget['pages_today']} / {budget['page_limit'] or 'unlimited'}",
        "",
    ]
    return "\n".join(lines)


def write_report(reports_dir: Path, summary: dict) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    stem = f"run_{summary['run_id']}"
    md_path = reports_dir / f"{stem}.md"
    md_path.write_text(render_markdown(summary), encoding="utf-8")
    (reports_dir / f"{stem}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return md_path