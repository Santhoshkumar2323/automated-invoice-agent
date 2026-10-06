from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from src.config import Settings
from src.formatting import format_inr
from src.retention import _stamp
from src.tracking.usage import _event_day, load_events
from src.validation.validators import load_ledger_rows

Fetch = Callable[[str], bytes]

_REPORT_NAME = re.compile(r"^run_\d{8}_\d{6}\.json$")
_NAME_STAMP = re.compile(r"^(\d{8}_\d{6})")

GOOD_STATUSES = ("ACCEPTED", "VALIDATED")


def http_fetch(url: str, timeout: float = 10.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "invoice-agent-dashboard"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _is_report(data) -> bool:
    return isinstance(data, dict) and "invoices" in data


def load_reports_from_folder(folder: Path, limit: int, include_dry_runs: bool = True) -> list[dict]:
    if not folder.exists():
        return []
    files = sorted((p for p in folder.glob("run_*.json") if _REPORT_NAME.match(p.name)), reverse=True)
    reports = []
    for path in files:
        if limit > 0 and len(reports) >= limit:
            break
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not _is_report(data):
            continue
        if not include_dry_runs and data.get("dry_run"):
            continue
        reports.append(data)
    return reports


class LocalSource:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.label = "files in this repository"
        self.errors: list[str] = []

    def reports(self, limit: int, include_dry_runs: bool = True) -> list[dict]:
        return load_reports_from_folder(self.settings.path("reports_dir"), limit, include_dry_runs)

    def ledger(self) -> list[dict]:
        return load_ledger_rows(self.settings.path("ledger_file"))

    def usage_events(self) -> list[dict]:
        return load_events(self.settings.path("usage_log_file"))

    def screenshots(self, limit: int) -> list[tuple[str, str]]:
        folder = self.settings.path("screenshots_dir")
        if limit <= 0 or not folder.exists():
            return []
        files = sorted((p for p in folder.glob("*.png") if p.is_file()), key=lambda p: (_stamp(p), p.name), reverse=True)
        return [(p.name, str(p)) for p in files[:limit]]


class GithubSource:
    def __init__(self, repo: str, branch: str, paths, fetch: Optional[Fetch] = None) -> None:
        self.repo = repo
        self.branch = branch
        self.paths = paths
        self.fetch = fetch or http_fetch
        self.label = f"GitHub repository {repo} ({branch})"
        self.errors: list[str] = []

    def _raw_url(self, relative: str) -> str:
        return f"https://raw.githubusercontent.com/{self.repo}/{self.branch}/{relative.strip('/')}"

    def _json(self, url: str):
        try:
            raw = self.fetch(url)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                self.errors.append(f"{url}: HTTP {exc.code}")
            return None
        except Exception as exc:
            self.errors.append(f"{url}: {exc}")
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            self.errors.append(f"{url}: not valid JSON")
            return None

    def _names(self, folder: str) -> list[str]:
        url = f"https://api.github.com/repos/{self.repo}/contents/{folder.strip('/')}?ref={self.branch}"
        data = self._json(url)
        if not isinstance(data, list):
            return []
        return [i["name"] for i in data if isinstance(i, dict) and i.get("type") == "file" and "name" in i]

    def reports(self, limit: int, include_dry_runs: bool = True) -> list[dict]:
        names = sorted((n for n in self._names(self.paths.reports_dir) if _REPORT_NAME.match(n)), reverse=True)
        reports = []
        for name in names:
            if limit > 0 and len(reports) >= limit:
                break
            data = self._json(self._raw_url(f"{self.paths.reports_dir}/{name}"))
            if not _is_report(data):
                continue
            if not include_dry_runs and data.get("dry_run"):
                continue
            reports.append(data)
        return reports

    def _rows(self, relative: str) -> list[dict]:
        data = self._json(self._raw_url(relative))
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    def ledger(self) -> list[dict]:
        return self._rows(self.paths.ledger_file)

    def usage_events(self) -> list[dict]:
        return self._rows(self.paths.usage_log_file)

    def screenshots(self, limit: int) -> list[tuple[str, str]]:
        if limit <= 0:
            return []
        names = [n for n in self._names(self.paths.screenshots_dir) if n.lower().endswith(".png")]

        def key(name: str):
            match = _NAME_STAMP.match(name)
            return (match.group(1) if match else "", name)

        names = sorted(names, key=key, reverse=True)[:limit]
        return [(n, self._raw_url(f"{self.paths.screenshots_dir}/{n}")) for n in names]


def build_source(settings: Settings, fetch: Optional[Fetch] = None):
    repo = settings.dashboard.github_repo.strip()
    if repo:
        return GithubSource(repo, settings.dashboard.branch, settings.paths, fetch)
    return LocalSource(settings)


def fmt_percent(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def time_ago(iso: str, now: Optional[datetime] = None) -> str:
    try:
        moment = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return "unknown"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    seconds = int(((now or datetime.now(timezone.utc)) - moment).total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h ago"
    return f"{hours // 24} days ago"


def overall_stats(reports: list[dict]) -> dict:
    counts: dict[str, int] = {}
    invoices = extracted = fields_ok = fields_total = 0
    bad = caught = clean = wrongly = 0
    tokens = repair = pages = calls = 0
    prompt = completion = 0
    cost = 0.0
    for report in reports:
        for row in report.get("invoices", []):
            invoices += 1
            status = row.get("status", "UNKNOWN")
            counts[status] = counts.get(status, 0) + 1
            extraction = row.get("extraction")
            if extraction:
                extracted += 1
                fields_ok += extraction.get("correct", 0)
                fields_total += extraction.get("total", 0)
        detection = report.get("detection", {})
        bad += detection.get("bad_invoices", 0)
        caught += detection.get("caught", 0)
        clean += detection.get("clean_invoices", 0)
        wrongly += detection.get("clean_wrongly_stopped", 0)
        usage = report.get("usage", {})
        tokens += usage.get("total_tokens", 0)
        prompt += usage.get("prompt_tokens", 0)
        completion += usage.get("completion_tokens", 0)
        repair += usage.get("tokens_by_step", {}).get("repair", 0)
        pages += usage.get("pages", 0)
        calls += usage.get("llm_calls", 0)
        cost += usage.get("cost_usd", 0.0)
    return {
        "runs": len(reports),
        "invoices": invoices,
        "invoices_extracted": extracted,
        "counts": counts,
        "fields_correct": fields_ok,
        "fields_total": fields_total,
        "extraction_accuracy": fields_ok / fields_total if fields_total else None,
        "bad_invoices": bad,
        "caught": caught,
        "detection_rate": caught / bad if bad else None,
        "clean_invoices": clean,
        "clean_wrongly_stopped": wrongly,
        "false_stop_rate": wrongly / clean if clean else None,
        "tokens": tokens,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "llm_calls": calls,
        "pages": pages,
        "cost_usd": round(cost, 6),
        "tokens_per_invoice": tokens / extracted if extracted else None,
        "retry_token_share": repair / tokens if tokens else None,
    }


def defect_breakdown(reports: list[dict]) -> list[dict]:
    table: dict[str, dict] = {}
    for report in reports:
        for row in report.get("invoices", []):
            defect = row.get("defect")
            if defect is None:
                continue
            entry = table.setdefault(defect, {"defect": defect, "invoices": 0, "decided": 0, "correct": 0, "outcomes": {}})
            entry["invoices"] += 1
            verdict = row.get("decision_correct")
            if verdict is not None:
                entry["decided"] += 1
                entry["correct"] += 1 if verdict else 0
            status = row.get("status", "UNKNOWN")
            entry["outcomes"][status] = entry["outcomes"].get(status, 0) + 1
    ordered = sorted(table.values(), key=lambda e: (e["defect"] != "NONE", e["defect"]))
    for entry in ordered:
        entry["outcome_text"] = ", ".join(f"{status} x{n}" for status, n in sorted(entry["outcomes"].items()))
    return ordered


def runs_table(reports: list[dict]) -> list[dict]:
    rows = []
    for report in reports:
        counts = report.get("counts", {})
        usage = report.get("usage", {})
        started = str(report.get("started_at", ""))[:16].replace("T", " ")
        rows.append(
            {
                "Started (UTC)": started,
                "Mode": "dry run" if report.get("dry_run") else "full run",
                "Invoices": sum(counts.values()),
                "Accepted": counts.get("ACCEPTED", 0),
                "Validated (dry run)": counts.get("VALIDATED", 0),
                "Rejected": counts.get("REJECTED", 0),
                "Needs review": counts.get("NEEDS_REVIEW", 0),
                "Errors": counts.get("ERROR", 0) + counts.get("SKIPPED", 0),
                "Accuracy": fmt_percent(report.get("extraction_accuracy")),
                "Tokens": usage.get("total_tokens", 0),
                "Pages": usage.get("pages", 0),
            }
        )
    return rows


def run_invoice_rows(report: dict) -> list[dict]:
    return [
        {
            "File": row.get("file", ""),
            "Vendor": row.get("vendor", ""),
            "Invoice number": row.get("invoice_number", ""),
            "Status": row.get("status", ""),
            "Attempts": row.get("attempts", 0),
            "Reason": row.get("reason", ""),
        }
        for row in report.get("invoices", [])
    ]


def daily_usage(events: list[dict], days: int = 14) -> list[dict]:
    totals: dict[str, dict] = {}
    for event in events:
        day = _event_day(event)
        if day is None:
            continue
        entry = totals.setdefault(day.isoformat(), {"Day": day.isoformat(), "Tokens": 0, "Pages": 0})
        entry["Tokens"] += int(event.get("total_tokens", 0) or 0)
        entry["Pages"] += int(event.get("pages", 0) or 0)
    ordered = [totals[key] for key in sorted(totals)]
    return ordered[-days:] if days > 0 else ordered


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def ledger_total(rows: list[dict]) -> float:
    return sum(_number(row.get("amount")) for row in rows)


def ledger_table(rows: list[dict], limit: int) -> list[dict]:
    ordered = list(reversed(rows))
    if limit > 0:
        ordered = ordered[:limit]
    return [
        {
            "Timestamp": row.get("timestamp", ""),
            "Vendor": row.get("vendor", ""),
            "GSTIN": row.get("gstin", ""),
            "Invoice Number": row.get("invoice_number", ""),
            "Amount": format_inr(_number(row.get("amount"))),
            "Status": row.get("status", ""),
        }
        for row in ordered
    ]