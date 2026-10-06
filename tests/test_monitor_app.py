import base64
import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

import streamlit as st
from streamlit.testing.v1 import AppTest

from src.scheduler import utc_now

APP = Path(__file__).resolve().parent.parent / "app" / "monitor_app.py"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def row(status="ACCEPTED", defect="NONE", expected="ACCEPT", correct=True, file="a.pdf", reason="ledger entry verified"):
    return {
        "file": file,
        "vendor": "Alpha Labs",
        "invoice_number": "INV-1",
        "status": status,
        "reason": reason,
        "attempts": 1,
        "issue_codes": [],
        "registry_status": None,
        "expected_outcome": expected,
        "defect": defect,
        "decision_correct": correct,
        "extraction": {"correct": 18, "total": 18, "wrong": []},
        "screenshot": "",
    }


def make_report(run_id, rows, started, tokens=3000):
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    bad = [r for r in rows if r["defect"] != "NONE"]
    return {
        "run_id": run_id,
        "started_at": started,
        "dry_run": False,
        "counts": counts,
        "invoices": rows,
        "extraction_accuracy": 1.0,
        "detection": {
            "bad_invoices": len(bad),
            "caught": len([r for r in bad if r["decision_correct"]]),
            "clean_invoices": len(rows) - len(bad),
            "clean_wrongly_stopped": 0,
        },
        "usage": {
            "llm_calls": len(rows),
            "prompt_tokens": tokens - 500,
            "completion_tokens": 500,
            "total_tokens": tokens,
            "pages": len(rows),
            "cost_usd": 0.0,
            "tokens_by_step": {"extract": tokens, "repair": 0},
            "retry_token_share": 0.0,
        },
    }


@pytest.fixture
def project(tmp_path, monkeypatch):
    st.cache_data.clear()
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "budget:",
                "  daily_token_limit: 10000",
                "  daily_page_limit: 20",
                "paths:",
                f"  ledger_file: {(tmp_path / 'ledger.json').as_posix()}",
                f"  usage_log_file: {(tmp_path / 'usage.json').as_posix()}",
                f"  reports_dir: {(tmp_path / 'reports').as_posix()}",
                f"  screenshots_dir: {(tmp_path / 'shots').as_posix()}",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("INVOICE_AGENT_CONFIG", str(config))
    return tmp_path


def open_app():
    return AppTest.from_file(str(APP), default_timeout=30).run()


def metrics(at):
    return {m.label: m.value for m in at.metric}


def fill(project):
    reports = project / "reports"
    reports.mkdir()
    first = make_report("20261003_080000", [row(), row(file="b.pdf")], "2026-10-03T08:00:00+00:00")
    second = make_report(
        "20261003_090000",
        [
            row(status="REJECTED", defect="SUSPENDED_VENDOR", expected="REGISTRY_SUSPENDED", file="c.pdf", reason="REGISTRY_SUSPENDED: x"),
            row(status="NEEDS_REVIEW", defect="WRONG_TOTAL", expected="ARITHMETIC_TOTAL", file="d.pdf", reason="ARITHMETIC_TOTAL: y"),
        ],
        "2026-10-03T09:00:00+00:00",
    )
    for data in (first, second):
        (reports / f"run_{data['run_id']}.json").write_text(json.dumps(data), encoding="utf-8")
    (project / "ledger.json").write_text(
        json.dumps(
            [
                {"timestamp": "t", "vendor": "A", "gstin": "g", "invoice_number": "1", "amount": 141536.98, "status": "VERIFIED"},
                {"timestamp": "t", "vendor": "B", "gstin": "g", "invoice_number": "2", "amount": 1000.0, "status": "VERIFIED"},
            ]
        ),
        encoding="utf-8",
    )
    (project / "usage.json").write_text(
        json.dumps([{"timestamp": utc_now().isoformat(), "total_tokens": 2500, "pages": 5}]), encoding="utf-8"
    )
    (project / "shots").mkdir()
    (project / "shots" / "20261003_080000_a.png").write_bytes(PNG)


def test_empty_project_shows_friendly_messages_not_errors(project):
    at = open_app()
    assert not at.exception
    assert metrics(at)["Invoices processed"] == "0"
    assert metrics(at)["Extraction accuracy"] == "n/a"
    assert any("No runs recorded yet" in i.value for i in at.info)
    assert any("ledger is empty" in i.value for i in at.info)


def test_headline_numbers_add_up_across_runs(project):
    fill(project)
    at = open_app()
    assert not at.exception
    m = metrics(at)
    assert m["Invoices processed"] == "4"
    assert m["Accepted"] == "2"
    assert m["Rejected"] == "1"
    assert m["Needs review"] == "1"
    assert m["Errors"] == "0"
    assert m["Extraction accuracy"] == "100.0%"
    assert m["Bad invoices caught"] == "2 of 2"
    assert m["Clean wrongly stopped"] == "0 of 2"
    assert m["Tokens per invoice"] == "1,500"


def test_latest_run_table_shows_the_newest_run(project):
    fill(project)
    at = open_app()
    latest = at.dataframe[0].value
    assert list(latest["File"]) == ["c.pdf", "d.pdf"]
    assert list(latest["Status"]) == ["REJECTED", "NEEDS_REVIEW"]
    assert any("20261003_090000" in s.value for s in at.subheader)


def test_ledger_tab_uses_indian_formatting(project):
    fill(project)
    at = open_app()
    assert metrics(at)["Ledger entries"] == "2"
    assert metrics(at)["Ledger total (INR)"] == "1,42,536.98"
    ledger = next(d.value for d in at.dataframe if "Invoice Number" in d.value.columns)
    assert list(ledger["Amount"]) == ["1,000.00", "1,41,536.98"]


def test_quality_and_runs_tables_are_filled(project):
    fill(project)
    at = open_app()
    tables = [d.value for d in at.dataframe]
    runs = next(t for t in tables if "Started (UTC)" in t.columns)
    assert len(runs) == 2
    quality = next(t for t in tables if "Planted problem" in t.columns)
    assert set(quality["Planted problem"]) == {"none (clean invoice)", "SUSPENDED_VENDOR", "WRONG_TOTAL"}


def test_usage_tab_shows_todays_budget(project):
    fill(project)
    at = open_app()
    texts = [w.value for w in at.markdown]
    assert any("Tokens today (UTC): 2,500 of 10,000" in t for t in texts)
    assert any("pages today (UTC): 5 of 20" in t for t in texts)


def test_screenshots_tab_renders_images(project):
    fill(project)
    at = open_app()
    assert not at.exception
    assert len(at.get("image")) == 1


def test_screenshots_tab_copes_with_no_images(project):
    at = open_app()
    assert any("No screenshots" in i.value for i in at.info)


def test_a_corrupt_report_does_not_break_the_page(project):
    fill(project)
    (project / "reports" / "run_20261003_100000.json").write_text("{broken", encoding="utf-8")
    at = open_app()
    assert not at.exception
    assert metrics(at)["Invoices processed"] == "4"


def test_page_never_offers_any_way_to_change_data(project):
    fill(project)
    at = open_app()
    assert len(at.button) == 0
    assert len(at.text_input) == 0


def github_project(tmp_path, monkeypatch, fetch):
    st.cache_data.clear()
    config = tmp_path / "config.yaml"
    config.write_text("dashboard:\n  github_repo: me/invoice-agent\n  branch: main\n", encoding="utf-8")
    monkeypatch.setenv("INVOICE_AGENT_CONFIG", str(config))
    monkeypatch.setattr("src.dashboard_data.http_fetch", fetch)


def test_github_mode_loads_data_through_the_fetcher(tmp_path, monkeypatch):
    data = make_report("20261003_080000", [row(), row(file="b.pdf")], "2026-10-03T08:00:00+00:00")

    def fetch(url):
        if "contents/reports" in url:
            return json.dumps([{"name": "run_20261003_080000.json", "type": "file"}]).encode()
        if url.endswith("reports/run_20261003_080000.json"):
            return json.dumps(data).encode()
        if "contents/screenshots" in url:
            return json.dumps([]).encode()
        raise OSError("not found")

    github_project(tmp_path, monkeypatch, fetch)
    at = open_app()
    assert not at.exception
    assert metrics(at)["Invoices processed"] == "2"
    assert any("GitHub repository me/invoice-agent" in c.value for c in at.caption)


def test_github_mode_shows_a_warning_when_github_is_unreachable(tmp_path, monkeypatch):
    def fetch(url):
        raise OSError("offline")

    github_project(tmp_path, monkeypatch, fetch)
    at = open_app()
    assert not at.exception
    assert any("could not be loaded" in w.value for w in at.warning)
    assert metrics(at)["Invoices processed"] == "0"