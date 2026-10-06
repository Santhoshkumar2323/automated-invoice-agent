import json
from datetime import datetime, timedelta, timezone

import pytest

from src.config import ConfigError, Secrets, Settings
from src.main import build_plugins, run_cycle
from src.workflow.state import EntryResult, ParseResult, ExtractResult

START = datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc)


def make_settings(tmp_path, pipeline=None, budget=None):
    return Settings(
        pipeline={"bad_invoice_ratio": 0.0, "invoices_per_run": 3, "seed": 1, **(pipeline or {})},
        budget=budget or {},
        paths={
            "incoming_dir": str(tmp_path / "incoming"),
            "ledger_file": str(tmp_path / "ledger.json"),
            "ground_truth_file": str(tmp_path / "truth.json"),
            "usage_log_file": str(tmp_path / "usage.json"),
            "run_state_file": str(tmp_path / "state.json"),
            "reports_dir": str(tmp_path / "reports"),
            "screenshots_dir": str(tmp_path / "shots"),
        },
    )


class Plugins:
    def __init__(self, settings, parse_error=None):
        self.settings = settings
        self.parse_error = parse_error
        self.factory_calls = 0
        self.enter_calls = 0

    def factory(self):
        self.factory_calls += 1
        return self.parse, self.extract, self.enter

    def parse(self, path):
        if self.parse_error:
            raise self.parse_error
        return ParseResult(markdown=path.name, pages=1)

    def extract(self, markdown, feedback, previous):
        truth = json.loads(self.settings.path("ground_truth_file").read_text(encoding="utf-8"))
        record = next(r for r in truth if r["file"] == markdown)
        return ExtractResult(record["invoice"], prompt_tokens=1000, completion_tokens=200, model="fake")

    def enter(self, invoice, file_name):
        self.enter_calls += 1
        path = self.settings.path("ledger_file")
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        rows.append(
            {
                "vendor": invoice["vendor_name"],
                "gstin": invoice["vendor_gstin"],
                "invoice_number": invoice["invoice_number"],
                "amount": invoice["total"],
                "status": "VERIFIED",
            }
        )
        path.write_text(json.dumps(rows), encoding="utf-8")
        return EntryResult(True, screenshot="shot.png")


def test_disabled_pipeline_skips_without_loading_plugins(tmp_path):
    settings = make_settings(tmp_path, pipeline={"enabled": False})
    plugins = Plugins(settings)
    outcome = run_cycle(settings, plugins.factory, now=START)
    assert outcome.ran is False
    assert "disabled" in outcome.reason
    assert plugins.factory_calls == 0
    assert not settings.path("run_state_file").exists()
    assert not settings.path("incoming_dir").exists()


def test_force_runs_even_when_disabled(tmp_path):
    settings = make_settings(tmp_path, pipeline={"enabled": False})
    outcome = run_cycle(settings, Plugins(settings).factory, force=True, now=START)
    assert outcome.ran is True


def test_full_cycle_with_clean_invoices(tmp_path):
    settings = make_settings(tmp_path)
    plugins = Plugins(settings)
    outcome = run_cycle(settings, plugins.factory, now=START)
    assert outcome.ran is True
    assert [r.status for r in outcome.results] == ["ACCEPTED"] * 3
    assert len(json.loads(settings.path("ledger_file").read_text(encoding="utf-8"))) == 3
    assert len(json.loads(settings.path("ground_truth_file").read_text(encoding="utf-8"))) == 3
    assert outcome.report_path.exists()
    assert outcome.report_path.with_suffix(".json").exists()
    assert json.loads(settings.path("run_state_file").read_text(encoding="utf-8"))["last_status"] == "ok"
    usage = json.loads(settings.path("usage_log_file").read_text(encoding="utf-8"))
    assert len([e for e in usage if e["kind"] == "parse"]) == 3
    assert len([e for e in usage if e["kind"] == "llm"]) == 3
    assert outcome.summary["extraction_accuracy"] == 1.0
    assert outcome.summary["usage"]["total_tokens"] == 3600


def test_second_run_waits_for_the_interval(tmp_path):
    settings = make_settings(tmp_path)
    plugins = Plugins(settings)
    run_cycle(settings, plugins.factory, now=START)
    too_soon = run_cycle(settings, plugins.factory, now=START + timedelta(hours=1))
    assert too_soon.ran is False
    assert "not due" in too_soon.reason
    due = run_cycle(settings, plugins.factory, now=START + timedelta(hours=3))
    assert due.ran is True


def test_duplicate_numbers_never_repeat_across_runs(tmp_path):
    settings = make_settings(tmp_path)
    plugins = Plugins(settings)
    run_cycle(settings, plugins.factory, now=START)
    run_cycle(settings, plugins.factory, force=True, now=START + timedelta(hours=3))
    rows = json.loads(settings.path("ledger_file").read_text(encoding="utf-8"))
    keys = [(r["gstin"], r["invoice_number"]) for r in rows]
    assert len(keys) == len(set(keys)) == 6


def test_dry_run_never_touches_the_ledger(tmp_path):
    settings = make_settings(tmp_path)
    plugins = Plugins(settings)
    outcome = run_cycle(settings, plugins.factory, dry_run=True, now=START)
    assert [r.status for r in outcome.results] == ["VALIDATED"] * 3
    assert plugins.enter_calls == 0
    assert not settings.path("ledger_file").exists()
    assert outcome.summary["dry_run"] is True


def test_bad_invoices_are_all_stopped(tmp_path):
    settings = make_settings(tmp_path, pipeline={"bad_invoice_ratio": 1.0, "invoices_per_run": 12})
    plugins = Plugins(settings)
    outcome = run_cycle(settings, plugins.factory, now=START)
    statuses = {r.status for r in outcome.results}
    assert statuses <= {"REJECTED", "NEEDS_REVIEW"}
    assert plugins.enter_calls == 0
    assert not settings.path("ledger_file").exists()
    detection = outcome.summary["detection"]
    assert detection["caught"] == detection["bad_invoices"] == 12


def test_budget_stops_the_run_midway(tmp_path):
    settings = make_settings(tmp_path, pipeline={"invoices_per_run": 5}, budget={"daily_token_limit": 2500})
    outcome = run_cycle(settings, Plugins(settings).factory, now=START)
    assert [r.status for r in outcome.results] == ["ACCEPTED", "ACCEPTED", "ACCEPTED", "SKIPPED", "SKIPPED"]


def test_run_is_refused_when_budget_already_used(tmp_path):
    settings = make_settings(tmp_path, budget={"daily_token_limit": 1000})
    settings.path("usage_log_file").write_text(
        json.dumps([{"timestamp": START.isoformat(), "kind": "llm", "total_tokens": 1000, "pages": 0}]),
        encoding="utf-8",
    )
    outcome = run_cycle(settings, Plugins(settings).factory, now=START + timedelta(hours=1))
    assert outcome.ran is False
    assert "token budget" in outcome.reason


def test_budget_resets_on_a_new_day(tmp_path):
    settings = make_settings(tmp_path, budget={"daily_token_limit": 1000})
    settings.path("usage_log_file").write_text(
        json.dumps([{"timestamp": START.isoformat(), "kind": "llm", "total_tokens": 1000, "pages": 0}]),
        encoding="utf-8",
    )
    outcome = run_cycle(settings, Plugins(settings).factory, now=START + timedelta(days=1))
    assert outcome.ran is True


def test_errors_are_recorded_in_run_state(tmp_path):
    settings = make_settings(tmp_path)
    plugins = Plugins(settings, parse_error=RuntimeError("402 payment required"))
    outcome = run_cycle(settings, plugins.factory, now=START)
    assert {r.status for r in outcome.results} == {"ERROR"}
    state = json.loads(settings.path("run_state_file").read_text(encoding="utf-8"))
    assert state["last_status"] == "completed_with_errors"


def test_custom_count_overrides_config(tmp_path):
    settings = make_settings(tmp_path)
    outcome = run_cycle(settings, Plugins(settings).factory, count=1, now=START)
    assert len(outcome.results) == 1


def test_build_plugins_requires_both_keys():
    with pytest.raises(ConfigError) as exc:
        build_plugins(Settings(), Secrets(groq_api_key="x"), dry_run=True)
    assert "llama_cloud_api_key" in str(exc.value)