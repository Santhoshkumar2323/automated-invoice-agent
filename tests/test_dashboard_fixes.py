import json
import urllib.error
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

import streamlit as st
from streamlit.testing.v1 import AppTest

from src.config import PathSettings, Settings, load_config
from src.dashboard_data import GithubSource, load_reports_from_folder, overall_stats, runs_table
from src.summary import main

APP = Path(__file__).resolve().parent.parent / "app" / "monitor_app.py"


def row(status="ACCEPTED", extraction=True, file="a.pdf"):
    return {
        "file": file,
        "vendor": "Alpha Labs",
        "invoice_number": "INV-1",
        "status": status,
        "reason": "ok",
        "attempts": 1,
        "defect": "NONE",
        "expected_outcome": "ACCEPT",
        "decision_correct": True,
        "extraction": {"correct": 11, "total": 11, "wrong": []} if extraction else None,
    }


def report(run_id, rows, started, dry_run=False, tokens=4000):
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {
        "run_id": run_id,
        "started_at": started,
        "dry_run": dry_run,
        "counts": counts,
        "invoices": rows,
        "extraction_accuracy": 1.0,
        "detection": {"bad_invoices": 0, "caught": 0, "clean_invoices": len(rows), "clean_wrongly_stopped": 0},
        "usage": {
            "llm_calls": len(rows),
            "prompt_tokens": tokens - 500,
            "completion_tokens": 500,
            "total_tokens": tokens,
            "pages": len(rows),
            "cost_usd": 0.0,
            "tokens_by_step": {"extract": tokens, "repair": 0},
        },
    }


def save(folder, data):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"run_{data['run_id']}.json").write_text(json.dumps(data), encoding="utf-8")


def test_tokens_per_invoice_ignores_invoices_that_never_reached_the_ai():
    data = report("1", [row(), row(), row(status="ERROR", extraction=False)], "2026-10-03T08:00:00+00:00", tokens=4000)
    stats = overall_stats([data])
    assert stats["invoices"] == 3
    assert stats["invoices_extracted"] == 2
    assert stats["tokens_per_invoice"] == 2000


def test_tokens_per_invoice_is_none_when_nothing_reached_the_ai():
    data = report("1", [row(status="ERROR", extraction=False)], "2026-10-03T08:00:00+00:00")
    assert overall_stats([data])["tokens_per_invoice"] is None


def test_runs_table_separates_dry_run_validations_from_real_acceptances():
    data = report("1", [row(), row(status="VALIDATED"), row(status="VALIDATED")], "2026-10-03T08:00:00+00:00")
    line = runs_table([data])[0]
    assert line["Accepted"] == 1
    assert line["Validated (dry run)"] == 2


def test_dry_runs_can_be_left_out_without_losing_older_real_runs(tmp_path):
    save(tmp_path, report("20261003_030000", [row()], "2026-10-03T03:00:00+00:00", dry_run=True))
    save(tmp_path, report("20261003_020000", [row()], "2026-10-03T02:00:00+00:00"))
    save(tmp_path, report("20261003_010000", [row()], "2026-10-03T01:00:00+00:00"))
    everything = load_reports_from_folder(tmp_path, 2)
    assert [r["run_id"] for r in everything] == ["20261003_030000", "20261003_020000"]
    real_only = load_reports_from_folder(tmp_path, 2, include_dry_runs=False)
    assert [r["run_id"] for r in real_only] == ["20261003_020000", "20261003_010000"]


class FakeGithub:
    def __init__(self, files):
        self.files = files

    def __call__(self, url):
        for fragment, payload in self.files.items():
            if fragment in url:
                return json.dumps(payload).encode()
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)


def test_github_source_can_also_leave_out_dry_runs():
    fetch = FakeGithub(
        {
            "contents/reports": [
                {"name": "run_20261003_030000.json", "type": "file"},
                {"name": "run_20261003_020000.json", "type": "file"},
            ],
            "run_20261003_030000.json": report("20261003_030000", [row()], "2026-10-03T03:00:00+00:00", dry_run=True),
            "run_20261003_020000.json": report("20261003_020000", [row()], "2026-10-03T02:00:00+00:00"),
        }
    )
    source = GithubSource("me/repo", "main", PathSettings(), fetch)
    assert [r["run_id"] for r in source.reports(5)] == ["20261003_030000", "20261003_020000"]
    assert [r["run_id"] for r in source.reports(5, include_dry_runs=False)] == ["20261003_020000"]


def test_dashboard_setting_defaults_to_leaving_dry_runs_out(tmp_path):
    assert Settings().dashboard.include_dry_runs is False
    file = tmp_path / "c.yaml"
    file.write_text("dashboard:\n  include_dry_runs: true\n", encoding="utf-8")
    assert load_config(file).dashboard.include_dry_runs is True


def write_config(tmp_path, include):
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "dashboard:",
                f"  include_dry_runs: {str(include).lower()}",
                "paths:",
                f"  ledger_file: {(tmp_path / 'ledger.json').as_posix()}",
                f"  usage_log_file: {(tmp_path / 'usage.json').as_posix()}",
                f"  reports_dir: {(tmp_path / 'reports').as_posix()}",
                f"  screenshots_dir: {(tmp_path / 'shots').as_posix()}",
            ]
        ),
        encoding="utf-8",
    )
    save(tmp_path / "reports", report("20261003_020000", [row(), row(file="b.pdf")], "2026-10-03T02:00:00+00:00"))
    save(
        tmp_path / "reports",
        report("20261003_030000", [row(status="VALIDATED"), row(status="VALIDATED", file="c.pdf")], "2026-10-03T03:00:00+00:00", dry_run=True),
    )
    return config


def metrics(at):
    return {m.label: m.value for m in at.metric}


def test_dashboard_hides_dry_runs_by_default(tmp_path, monkeypatch):
    st.cache_data.clear()
    monkeypatch.setenv("INVOICE_AGENT_CONFIG", str(write_config(tmp_path, False)))
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception
    m = metrics(at)
    assert m["Invoices processed"] == "2"
    assert m["Accepted"] == "2"
    assert "Dry-run validated" not in m
    assert any("Dry runs are not counted" in c.value for c in at.caption)


def test_dashboard_can_show_dry_runs_separately_from_accepted(tmp_path, monkeypatch):
    st.cache_data.clear()
    monkeypatch.setenv("INVOICE_AGENT_CONFIG", str(write_config(tmp_path, True)))
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception
    m = metrics(at)
    assert m["Invoices processed"] == "4"
    assert m["Accepted"] == "2"
    assert m["Dry-run validated"] == "2"


def test_summary_full_only_still_finds_the_newest_real_run(tmp_path, capsys):
    config = write_config(tmp_path, False)
    assert main(["--config", str(config), "--runs", "1", "--full-only"]) == 0
    out = capsys.readouterr().out
    assert "Runs covered: 1" in out
    assert "Invoices processed: 2" in out
    assert "Invoices that reached the AI: 2" in out