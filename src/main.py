from __future__ import annotations

import argparse
import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from src.browser.browser import check_ledger_site
from src.config import ConfigError, Secrets, Settings, load_config, load_secrets
from src.factory.generate_mock_invoices import Defect, append_ground_truth, generate_batch
from src.retention import apply_retention
from src.scheduler import budget_exceeded, decide, read_last_run, utc_now, write_last_run
from src.tracking.report import summarize_run, write_report
from src.tracking.usage import UsageTracker
from src.validation.validators import load_ledger_rows
from src.workflow.graph import Deps, EntryFn, ExtractFn, ParseFn, build_graph, process_invoice
from src.workflow.state import InvoiceState

PluginFactory = Callable[[], tuple[ParseFn, ExtractFn, Optional[EntryFn]]]


@dataclass
class RunOutcome:
    ran: bool
    reason: str
    report_path: Optional[Path] = None
    results: list[InvoiceState] = field(default_factory=list)
    summary: Optional[dict] = None
    error: bool = False
    pruned: dict = field(default_factory=dict)


def run_cycle(
    settings: Settings,
    plugin_factory: PluginFactory,
    force: bool = False,
    dry_run: bool = False,
    now: Optional[datetime] = None,
    count: Optional[int] = None,
    seed: Optional[int] = None,
    force_defect: Optional[Defect] = None,
    preflight: Optional[Callable[[], Optional[str]]] = None,
) -> RunOutcome:
    started = now or utc_now()
    run_id = started.strftime("%Y%m%d_%H%M%S")
    tracker = UsageTracker(settings.path("usage_log_file"), settings.pricing, run_id)
    tokens_today, pages_today = tracker.today_totals(started)

    decision = decide(
        settings,
        read_last_run(settings.path("run_state_file")),
        tokens_today,
        pages_today,
        now=started,
        force=force,
    )
    if not decision.run:
        return RunOutcome(ran=False, reason=decision.reason)

    if preflight is not None:
        problem = preflight()
        if problem:
            return RunOutcome(ran=False, reason=problem, error=True)

    parse, extract, enter_ledger = plugin_factory()
    if dry_run:
        enter_ledger = None

    ledger_path = settings.path("ledger_file")
    rng = random.Random(seed if seed is not None else settings.pipeline.seed)
    records = generate_batch(
        count or settings.pipeline.invoices_per_run,
        settings,
        rng,
        load_ledger_rows(ledger_path),
        force_defect=force_defect,
    )
    append_ground_truth(settings.path("ground_truth_file"), records)
    truth = {record["file"]: record for record in records}

    deps = Deps(
        settings=settings,
        parse=parse,
        extract=extract,
        tracker=tracker,
        ledger_rows=lambda: load_ledger_rows(ledger_path),
        budget_check=lambda: budget_exceeded(settings, *tracker.today_totals()),
        enter_ledger=enter_ledger,
    )
    graph = build_graph(deps)

    results: list[InvoiceState] = []
    for record in records:
        path = settings.path("incoming_dir") / record["file"]
        try:
            results.append(process_invoice(graph, path))
        except Exception as exc:
            results.append(
                InvoiceState(
                    file_path=str(path),
                    file_name=record["file"],
                    status="ERROR",
                    reason=f"unexpected failure: {exc}",
                )
            )

    finished = utc_now()
    tokens_after, pages_after = tracker.today_totals()
    summary = summarize_run(
        run_id,
        started,
        finished,
        settings,
        decision.reason,
        enter_ledger is None,
        results,
        truth,
        tracker.run_summary(),
        tokens_after,
        pages_after,
    )
    report_path = write_report(settings.path("reports_dir"), summary)
    had_errors = any(r.status == "ERROR" for r in results)
    write_last_run(settings.path("run_state_file"), started, "completed_with_errors" if had_errors else "ok")
    pruned = apply_retention(settings, now=started)
    return RunOutcome(True, decision.reason, report_path, results, summary, pruned=pruned)


def build_plugins(settings: Settings, secrets: Secrets, dry_run: bool) -> tuple[ParseFn, ExtractFn, Optional[EntryFn]]:
    secrets.require("groq_api_key", "llama_cloud_api_key")
    try:
        from src.parsing.parsers import make_parser
        from src.parsing.extractor import make_extractor
    except ImportError as exc:
        raise ConfigError(f"Cannot load the parsing modules: {exc}") from exc
    parse = make_parser(settings, secrets)
    extract = make_extractor(settings, secrets)
    enter: Optional[EntryFn] = None
    if not dry_run:
        try:
            from src.browser.browser import make_ledger_entry
        except ImportError as exc:
            raise ConfigError(f"Cannot load the browser module (use --dry-run until it exists): {exc}") from exc
        enter = make_ledger_entry(settings)
    return parse, extract, enter


def preflight_for(settings: Settings, dry_run: bool) -> Optional[Callable[[], Optional[str]]]:
    if dry_run:
        return None
    return lambda: check_ledger_site(settings.browser.ledger_url)

def apply_browser_overrides(settings: Settings, show_browser: bool, slow_mo: Optional[int]) -> Settings:
    changes: dict = {}
    if show_browser:
        changes["headless"] = False
        if settings.browser.slow_mo_ms == 0:
            changes["slow_mo_ms"] = 400
    if slow_mo is not None:
        changes["slow_mo_ms"] = max(slow_mo, 0)
    if not changes:
        return settings
    return settings.model_copy(update={"browser": settings.browser.model_copy(update=changes)})

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run one invoice pipeline cycle")
    parser.add_argument("--force", action="store_true", help="run even if disabled or not due yet")
    parser.add_argument("--dry-run", action="store_true", help="skip the browser and ledger step")
    parser.add_argument("--count", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--defect", choices=[d.value for d in Defect], help="force every invoice to have this problem")
    parser.add_argument("--show-browser", action="store_true", help="show the browser window while it works")
    parser.add_argument("--slow-mo", type=int, help="milliseconds to pause between browser actions")
    args = parser.parse_args(argv)

    try:
        settings = apply_browser_overrides(load_config(), args.show_browser, args.slow_mo)
        secrets = load_secrets()
        outcome = run_cycle(
            settings,
            lambda: build_plugins(settings, secrets, args.dry_run),
            force=args.force,
            dry_run=args.dry_run,
            count=args.count,
            seed=args.seed,
            force_defect=Defect(args.defect) if args.defect else None,
            preflight=preflight_for(settings, args.dry_run),
        )
    except ConfigError as exc:
        print(f"Error: {exc}")
        return 2

    if not outcome.ran:
        if outcome.error:
            print(f"Cannot run: {outcome.reason}")
            return 3
        print(f"Skipped: {outcome.reason}")
        return 0

    for state in outcome.results:
        print(f"{state.file_name}  {state.status}  {state.reason}")
    print(f"Report: {outcome.report_path}")
    removed = {name: count for name, count in outcome.pruned.items() if count}
    if removed:
        print("Cleaned up old files: " + ", ".join(f"{count} {name}" for name, count in removed.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())