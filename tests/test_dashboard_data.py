import json
import os
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from src.config import PathSettings, Settings
from src.dashboard_data import (
    GithubSource,
    LocalSource,
    build_source,
    daily_usage,
    defect_breakdown,
    fmt_percent,
    http_fetch,
    ledger_table,
    ledger_total,
    load_reports_from_folder,
    overall_stats,
    run_invoice_rows,
    runs_table,
    time_ago,
)

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def row(status="ACCEPTED", defect="NONE", expected="ACCEPT", correct=True, extraction=(18, 18), file="a.pdf", reason="ok"):
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
        "extraction": {"correct": extraction[0], "total": extraction[1], "wrong": []} if extraction else None,
        "screenshot": "",
    }


def report(run_id, rows, started="2026-10-03T08:41:08+00:00", dry_run=False, tokens=3000, repair=0, pages=2, detection=None, accuracy=1.0):
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {
        "run_id": run_id,
        "started_at": started,
        "dry_run": dry_run,
        "counts": counts,
        "invoices": rows,
        "extraction_accuracy": accuracy,
        "detection": detection or {"bad_invoices": 0, "caught": 0, "clean_invoices": len(rows), "clean_wrongly_stopped": 0},
        "usage": {
            "llm_calls": len(rows),
            "prompt_tokens": tokens - 500,
            "completion_tokens": 500,
            "total_tokens": tokens,
            "pages": pages,
            "cost_usd": 0.5,
            "tokens_by_step": {"extract": tokens - repair, "repair": repair},
            "retry_token_share": 0.0,
        },
    }


def write_report(folder, data):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"run_{data['run_id']}.json").write_text(json.dumps(data), encoding="utf-8")


def test_reports_load_newest_first_and_respect_the_limit(tmp_path):
    for run_id in ("20261001_010000", "20261003_010000", "20261002_010000"):
        write_report(tmp_path, report(run_id, [row()]))
    assert [r["run_id"] for r in load_reports_from_folder(tmp_path, 0)] == ["20261003_010000", "20261002_010000", "20261001_010000"]
    assert [r["run_id"] for r in load_reports_from_folder(tmp_path, 2)] == ["20261003_010000", "20261002_010000"]


def test_corrupt_and_foreign_json_files_are_skipped(tmp_path):
    write_report(tmp_path, report("20261001_010000", [row()]))
    (tmp_path / "run_20261002_010000.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "run_20261003_010000.json").write_text('{"no": "invoices"}', encoding="utf-8")
    (tmp_path / "notes.json").write_text("{}", encoding="utf-8")
    assert [r["run_id"] for r in load_reports_from_folder(tmp_path, 0)] == ["20261001_010000"]
    assert load_reports_from_folder(tmp_path / "missing", 5) == []


def test_overall_stats_add_up_across_runs():
    first = report("1", [row(), row()], tokens=4000, pages=2)
    second = report(
        "2",
        [row(status="NEEDS_REVIEW", defect="WRONG_TOTAL", expected="ARITHMETIC_TOTAL", extraction=(17, 18))],
        tokens=2000,
        repair=500,
        pages=1,
        detection={"bad_invoices": 1, "caught": 1, "clean_invoices": 0, "clean_wrongly_stopped": 0},
    )
    stats = overall_stats([first, second])
    assert stats["runs"] == 2 and stats["invoices"] == 3
    assert stats["counts"] == {"ACCEPTED": 2, "NEEDS_REVIEW": 1}
    assert stats["fields_correct"] == 53 and stats["fields_total"] == 54
    assert stats["extraction_accuracy"] == pytest.approx(53 / 54)
    assert (stats["bad_invoices"], stats["caught"], stats["detection_rate"]) == (1, 1, 1.0)
    assert stats["clean_invoices"] == 2 and stats["false_stop_rate"] == 0
    assert stats["tokens"] == 6000 and stats["pages"] == 3
    assert stats["tokens_per_invoice"] == 2000
    assert stats["retry_token_share"] == pytest.approx(500 / 6000)
    assert stats["cost_usd"] == 1.0


def test_stats_of_nothing_are_empty_not_errors():
    stats = overall_stats([])
    assert stats["invoices"] == 0
    assert stats["extraction_accuracy"] is None
    assert stats["tokens_per_invoice"] is None
    assert stats["retry_token_share"] is None
    assert stats["detection_rate"] is None


def test_rows_without_extraction_do_not_count_toward_accuracy():
    stats = overall_stats([report("1", [row(extraction=None), row(extraction=(9, 18))])])
    assert stats["fields_total"] == 18 and stats["extraction_accuracy"] == 0.5


def test_defect_breakdown_groups_by_planted_problem():
    reports = [
        report(
            "1",
            [
                row(),
                row(status="REJECTED", defect="SUSPENDED_VENDOR", expected="REGISTRY_SUSPENDED"),
                row(status="REJECTED", defect="SUSPENDED_VENDOR", expected="REGISTRY_SUSPENDED", correct=False),
                row(status="ERROR", defect="WRONG_TOTAL", expected="ARITHMETIC_TOTAL", correct=None),
                row(defect=None),
            ],
        )
    ]
    table = defect_breakdown(reports)
    assert [e["defect"] for e in table] == ["NONE", "SUSPENDED_VENDOR", "WRONG_TOTAL"]
    suspended = table[1]
    assert (suspended["invoices"], suspended["decided"], suspended["correct"]) == (2, 2, 1)
    assert suspended["outcome_text"] == "REJECTED x2"
    wrong = table[2]
    assert (wrong["invoices"], wrong["decided"], wrong["correct"]) == (1, 0, 0)


def test_runs_table_shows_one_line_per_run():
    data = report(
        "1",
        [row(), row(status="REJECTED"), row(status="NEEDS_REVIEW"), row(status="ERROR"), row(status="SKIPPED")],
        dry_run=True,
        accuracy=0.5,
    )
    line = runs_table([data])[0]
    assert line["Started (UTC)"] == "2026-10-03 08:41"
    assert line["Mode"] == "dry run"
    assert (line["Invoices"], line["Accepted"], line["Rejected"], line["Needs review"], line["Errors"]) == (5, 1, 1, 1, 2)
    assert line["Accuracy"] == "50.0%"
    assert line["Tokens"] == 3000 and line["Pages"] == 2


def test_run_invoice_rows_use_readable_headings():
    rows = run_invoice_rows(report("1", [row(file="x.pdf", reason="because")]))
    assert rows == [
        {"File": "x.pdf", "Vendor": "Alpha Labs", "Invoice number": "INV-1", "Status": "ACCEPTED", "Attempts": 1, "Reason": "because"}
    ]


def test_daily_usage_groups_by_utc_day_and_ignores_bad_timestamps():
    events = [
        {"timestamp": "2026-10-01T23:59:00+00:00", "total_tokens": 100, "pages": 1},
        {"timestamp": "2026-10-02T00:01:00+00:00", "total_tokens": 50, "pages": 0},
        {"timestamp": "2026-10-02T09:00:00+00:00", "total_tokens": 25, "pages": 2},
        {"timestamp": "garbage", "total_tokens": 999},
    ]
    assert daily_usage(events) == [
        {"Day": "2026-10-01", "Tokens": 100, "Pages": 1},
        {"Day": "2026-10-02", "Tokens": 75, "Pages": 2},
    ]
    assert daily_usage(events, days=1) == [{"Day": "2026-10-02", "Tokens": 75, "Pages": 2}]


def test_ledger_table_is_newest_first_with_indian_amounts():
    rows = [
        {"timestamp": "t1", "vendor": "A", "gstin": "g", "invoice_number": "1", "amount": 1000.0, "status": "VERIFIED"},
        {"timestamp": "t2", "vendor": "B", "gstin": "g", "invoice_number": "2", "amount": 141536.98, "status": "VERIFIED"},
    ]
    table = ledger_table(rows, 10)
    assert [r["Invoice Number"] for r in table] == ["2", "1"]
    assert table[0]["Amount"] == "1,41,536.98"
    assert len(ledger_table(rows, 1)) == 1
    assert ledger_total(rows) == pytest.approx(142536.98)
    assert ledger_total([{"amount": "bad"}, {"amount": None}, {"amount": 5}]) == 5


def test_time_ago_phrases():
    def ago(**kw):
        return time_ago((NOW - timedelta(**kw)).isoformat(), NOW)

    assert ago(seconds=10) == "just now"
    assert ago(minutes=5) == "5 min ago"
    assert ago(hours=3) == "3 h ago"
    assert ago(days=4) == "4 days ago"
    assert time_ago("garbage", NOW) == "unknown"
    assert time_ago("2026-10-03T11:00:00", NOW) == "1 h ago"


def test_percent_formatting():
    assert fmt_percent(None) == "n/a"
    assert fmt_percent(1) == "100.0%"
    assert fmt_percent(0.12345) == "12.3%"


def touch(path, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def local_settings(tmp_path):
    return Settings(
        paths={
            "ledger_file": str(tmp_path / "ledger.json"),
            "usage_log_file": str(tmp_path / "usage.json"),
            "reports_dir": str(tmp_path / "reports"),
            "screenshots_dir": str(tmp_path / "shots"),
        }
    )


def test_local_source_reads_everything_from_the_configured_paths(tmp_path):
    settings = local_settings(tmp_path)
    write_report(tmp_path / "reports", report("20261003_010000", [row()]))
    (tmp_path / "ledger.json").write_text('[{"vendor": "A"}]', encoding="utf-8")
    (tmp_path / "usage.json").write_text('[{"total_tokens": 5}]', encoding="utf-8")
    source = LocalSource(settings)
    assert len(source.reports(10)) == 1
    assert source.ledger() == [{"vendor": "A"}]
    assert source.usage_events() == [{"total_tokens": 5}]
    assert source.errors == []


def test_local_source_lists_newest_screenshots_first(tmp_path):
    settings = local_settings(tmp_path)
    for name in ("20261001_000000_a.png", "20261003_000000_c.png", "20261002_000000_b.png"):
        touch(tmp_path / "shots" / name)
    touch(tmp_path / "shots" / ".gitkeep")
    touch(tmp_path / "shots" / "notes.txt")
    source = LocalSource(settings)
    assert [name for name, _ in source.screenshots(2)] == ["20261003_000000_c.png", "20261002_000000_b.png"]
    assert source.screenshots(0) == []
    assert LocalSource(local_settings(tmp_path / "nowhere")).screenshots(5) == []


class FakeGithub:
    def __init__(self, files=None, fail=None):
        self.files = files or {}
        self.fail = fail or {}
        self.asked = []

    def __call__(self, url):
        self.asked.append(url)
        for fragment, error in self.fail.items():
            if fragment in url:
                raise error
        for fragment, payload in self.files.items():
            if fragment in url:
                return payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)


def listing(*names):
    return [{"name": n, "type": "file"} for n in names]


def test_github_source_reads_reports_ledger_usage_and_screenshots():
    fetch = FakeGithub(
        {
            "contents/reports": listing("run_20261001_010000.json", "run_20261003_010000.json", "run_20261003_010000.md", "other.json"),
            "reports/run_20261003_010000.json": report("20261003_010000", [row()]),
            "reports/run_20261001_010000.json": report("20261001_010000", [row()]),
            "data/company_ledger.json": [{"vendor": "A"}, "junk"],
            "data/usage_log.json": [{"total_tokens": 1}],
            "contents/screenshots": [
                {"name": "20261001_000000_a.png", "type": "file"},
                {"name": "20261003_000000_b.png", "type": "file"},
                {"name": "sub", "type": "dir"},
                {"name": ".gitkeep", "type": "file"},
            ],
        }
    )
    source = GithubSource("me/invoice-agent", "main", PathSettings(), fetch)
    reports = source.reports(1)
    assert [r["run_id"] for r in reports] == ["20261003_010000"]
    assert source.ledger() == [{"vendor": "A"}]
    assert source.usage_events() == [{"total_tokens": 1}]
    shots = source.screenshots(5)
    assert [n for n, _ in shots] == ["20261003_000000_b.png", "20261001_000000_a.png"]
    assert shots[0][1] == "https://raw.githubusercontent.com/me/invoice-agent/main/screenshots/20261003_000000_b.png"
    assert source.errors == []
    assert any(u.startswith("https://api.github.com/repos/me/invoice-agent/contents/reports?ref=main") for u in fetch.asked)
    assert "invoice-agent (main)" in source.label


def test_github_missing_files_are_normal_but_real_failures_are_reported():
    quiet = GithubSource("me/r", "main", PathSettings(), FakeGithub())
    assert quiet.reports(5) == [] and quiet.ledger() == [] and quiet.screenshots(3) == []
    assert quiet.errors == []
    broken = GithubSource("me/r", "main", PathSettings(), FakeGithub(fail={"": OSError("offline")}))
    assert broken.reports(5) == []
    assert broken.errors and "offline" in broken.errors[0]
    limited = GithubSource(
        "me/r", "main", PathSettings(), FakeGithub(fail={"api.github.com": urllib.error.HTTPError("u", 403, "rate limit", {}, None)})
    )
    limited.reports(5)
    assert any("HTTP 403" in e for e in limited.errors)
    garbage = GithubSource("me/r", "main", PathSettings(), FakeGithub({"data/company_ledger.json": b"not json"}))
    assert garbage.ledger() == []
    assert garbage.errors


def test_build_source_picks_local_or_github(tmp_path):
    assert isinstance(build_source(local_settings(tmp_path)), LocalSource)
    github = Settings(dashboard={"github_repo": "me/repo", "branch": "dev"})
    source = build_source(github, FakeGithub())
    assert isinstance(source, GithubSource) and source.branch == "dev"


def test_repository_name_must_look_like_owner_slash_name():
    for bad in ("justone", "a/b/c", "has space/repo"):
        with pytest.raises(Exception):
            Settings(dashboard={"github_repo": bad})
    assert Settings(dashboard={"github_repo": "owner/repo-name.v2"}).dashboard.github_repo == "owner/repo-name.v2"
    assert Settings(dashboard={"github_repo": ""}).dashboard.github_repo == ""


def test_http_fetch_sends_a_user_agent(monkeypatch):
    seen = {}

    class Response:
        def read(self):
            return b"data"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        seen["agent"] = request.get_header("User-agent")
        seen["url"] = request.full_url
        return Response()

    monkeypatch.setattr("src.dashboard_data.urllib.request.urlopen", fake_urlopen)
    assert http_fetch("https://example.com/x") == b"data"
    assert seen == {"agent": "invoice-agent-dashboard", "url": "https://example.com/x"}