import json
from datetime import datetime, timedelta, timezone

import pytest

from src import main as main_module
from src.config import Settings
from src.factory.generate_mock_invoices import Defect
from src.main import main, preflight_for, run_cycle
from src.workflow.state import EntryResult, ExtractResult, ParseResult

START = datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc)


def make_settings(tmp_path, **pipeline):
    return Settings(
        pipeline={"bad_invoice_ratio": 0.0, "invoices_per_run": 3, "seed": 1, **pipeline},
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
    def __init__(self, settings):
        self.settings = settings
        self.factory_calls = 0

    def factory(self):
        self.factory_calls += 1
        return self.parse, self.extract, self.enter

    def parse(self, path):
        return ParseResult(markdown=path.name, pages=1)

    def extract(self, markdown, feedback, previous):
        truth = json.loads(self.settings.path("ground_truth_file").read_text(encoding="utf-8"))
        record = next(r for r in truth if r["file"] == markdown)
        return ExtractResult(record["invoice"], prompt_tokens=1000, completion_tokens=200, model="fake")

    def enter(self, invoice, file_name):
        path = self.settings.path("ledger_file")
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        rows.append({"gstin": invoice["vendor_gstin"], "invoice_number": invoice["invoice_number"]})
        path.write_text(json.dumps(rows), encoding="utf-8")
        return EntryResult(True, screenshot="shot.png")


def test_failed_preflight_stops_before_anything_is_spent(tmp_path):
    settings = make_settings(tmp_path)
    plugins = Plugins(settings)
    outcome = run_cycle(settings, plugins.factory, now=START, preflight=lambda: "ledger website is not reachable")
    assert outcome.ran is False
    assert outcome.error is True
    assert "not reachable" in outcome.reason
    assert plugins.factory_calls == 0
    assert not settings.path("incoming_dir").exists()
    assert not settings.path("usage_log_file").exists()
    assert not settings.path("run_state_file").exists()


def test_passing_preflight_lets_the_run_continue(tmp_path):
    settings = make_settings(tmp_path)
    outcome = run_cycle(settings, Plugins(settings).factory, now=START, preflight=lambda: None)
    assert outcome.ran is True
    assert outcome.error is False


def test_preflight_is_not_called_when_the_run_is_not_due(tmp_path):
    settings = make_settings(tmp_path)
    plugins = Plugins(settings)
    run_cycle(settings, plugins.factory, now=START)
    calls = []
    outcome = run_cycle(
        settings,
        plugins.factory,
        now=START + timedelta(hours=1),
        preflight=lambda: calls.append(1),
    )
    assert outcome.ran is False
    assert outcome.error is False
    assert calls == []


def test_forced_defect_makes_every_invoice_bad(tmp_path):
    settings = make_settings(tmp_path)
    outcome = run_cycle(settings, Plugins(settings).factory, now=START, force_defect=Defect.SUSPENDED_VENDOR)
    assert [r.status for r in outcome.results] == ["REJECTED"] * 3
    assert outcome.summary["detection"]["caught"] == 3
    assert not settings.path("ledger_file").exists()


def test_forced_wrong_total_ends_in_review(tmp_path):
    settings = make_settings(tmp_path)
    outcome = run_cycle(settings, Plugins(settings).factory, now=START, force_defect=Defect.WRONG_TOTAL)
    assert {r.status for r in outcome.results} == {"NEEDS_REVIEW"}
    assert all("ARITHMETIC_TOTAL" in r.reason for r in outcome.results)


def test_forced_none_overrides_a_high_bad_ratio(tmp_path):
    settings = make_settings(tmp_path, bad_invoice_ratio=1.0)
    outcome = run_cycle(settings, Plugins(settings).factory, now=START, force_defect=Defect.NONE)
    assert [r.status for r in outcome.results] == ["ACCEPTED"] * 3


def test_forced_duplicate_hits_the_ledger_rule(tmp_path):
    settings = make_settings(tmp_path)
    plugins = Plugins(settings)
    run_cycle(settings, plugins.factory, now=START)
    outcome = run_cycle(
        settings,
        plugins.factory,
        force=True,
        now=START + timedelta(hours=3),
        force_defect=Defect.DUPLICATE_INVOICE,
    )
    assert [r.status for r in outcome.results] == ["REJECTED"] * 3
    assert all("DUPLICATE_INVOICE" in r.reason for r in outcome.results)


def test_preflight_is_skipped_for_dry_runs():
    assert preflight_for(Settings(), dry_run=True) is None


def test_preflight_checks_the_configured_website(monkeypatch):
    asked = []
    monkeypatch.setattr(main_module, "check_ledger_site", lambda url: asked.append(url) or "problem")
    settings = Settings(browser={"ledger_url": "http://localhost:9999"})
    check = preflight_for(settings, dry_run=False)
    assert check() == "problem"
    assert asked == ["http://localhost:9999"]


def test_unknown_defect_name_is_rejected_by_the_command_line():
    with pytest.raises(SystemExit):
        main(["--defect", "NOT_A_DEFECT"])