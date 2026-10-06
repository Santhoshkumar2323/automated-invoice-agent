from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from src.config import ConfigError, load_config
from src.dashboard_data import LocalSource, defect_breakdown, fmt_percent, overall_stats


def _count(text: str, value: int) -> str:
    return f"{text:<34}{value}"


def format_summary(reports: list[dict]) -> str:
    if not reports:
        return "No run reports found."
    stats = overall_stats(reports)
    first = str(reports[-1].get("started_at", ""))[:16].replace("T", " ")
    last = str(reports[0].get("started_at", ""))[:16].replace("T", " ")
    lines = [
        "Invoice Agent: evaluation summary",
        f"Runs covered: {stats['runs']} ({first} to {last} UTC)",
        f"Invoices processed: {stats['invoices']}",
        "",
        "Outcomes",
    ]
    for status, count in sorted(stats["counts"].items()):
        lines.append("  " + _count(status, count))
    lines += [
        "",
        "Quality",
        f"  Extraction accuracy: {fmt_percent(stats['extraction_accuracy'])} "
        f"({stats['fields_correct']} of {stats['fields_total']} fields)",
        f"  Bad invoices caught: {stats['caught']} of {stats['bad_invoices']} ({fmt_percent(stats['detection_rate'])})",
        f"  Clean invoices wrongly stopped: {stats['clean_wrongly_stopped']} of {stats['clean_invoices']} "
        f"({fmt_percent(stats['false_stop_rate'])})",
        "",
        "Decisions by test case",
        f"  {'Planted problem':<22}{'Invoices':<10}{'Right decision':<16}Outcomes",
    ]
    for entry in defect_breakdown(reports):
        label = "NONE (clean)" if entry["defect"] == "NONE" else entry["defect"]
        right = f"{entry['correct']} of {entry['decided']}"
        lines.append(f"  {label:<22}{entry['invoices']:<10}{right:<16}{entry['outcome_text']}")
    per_invoice = stats["tokens_per_invoice"]
    lines += [
        "",
        "Cost",
        f"  Invoices that reached the AI: {stats['invoices_extracted']}",
        f"  Groq calls: {stats['llm_calls']}",
        f"  Tokens in / out / total: {stats['prompt_tokens']} / {stats['completion_tokens']} / {stats['tokens']}",
        f"  Tokens per invoice: {'n/a' if per_invoice is None else f'{per_invoice:.0f}'}",
        f"  Share of tokens spent on retries: {fmt_percent(stats['retry_token_share'])}",
        f"  LlamaParse pages: {stats['pages']}",
        f"  Estimated cost (USD): {stats['cost_usd']:.6f}",
    ]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Summarise all saved run reports")
    parser.add_argument("--runs", type=int, default=0, help="only the newest N runs (0 means all)")
    parser.add_argument("--full-only", action="store_true", help="ignore dry runs")
    parser.add_argument("--config", type=Path, help="use a different config.yaml")
    args = parser.parse_args(argv)
    try:
        settings = load_config(args.config)
    except ConfigError as exc:
        print(f"Error: {exc}")
        return 2
    reports = LocalSource(settings).reports(args.runs, include_dry_runs=not args.full_only)
    print(format_summary(reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())