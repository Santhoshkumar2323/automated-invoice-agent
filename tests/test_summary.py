import json

from src.summary import format_summary, main


def row(status="ACCEPTED", defect="NONE", expected="ACCEPT", correct=True):
    return {
        "file": "a.pdf",
        "status": status,
        "defect": defect,
        "expected_outcome": expected,
        "decision_correct": correct,
        "extraction": {"correct": 18, "total": 18, "wrong": []},
    }


def report(run_id, rows, started, dry_run=False, tokens=4000, repair=0):
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    bad = [r for r in rows if r["defect"] != "NONE"]
    return {
        "run_id": run_id,
        "started_at": started,
        "dry_run": dry_run,
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
            "prompt_tokens": tokens - 600,
            "completion_tokens": 600,
            "total_tokens": tokens,
            "pages": len(rows),
            "cost_usd": 0.0,
            "tokens_by_step": {"extract": tokens - repair, "repair": repair},
        },
    }


REPORTS = [
    report(
        "2",
        [row(status="REJECTED", defect="SUSPENDED_VENDOR", expected="REGISTRY_SUSPENDED"), row(status="NEEDS_REVIEW", defect="WRONG_TOTAL", expected="ARITHMETIC_TOTAL")],
        "2026-10-03T09:00:00+00:00",
        tokens=4000,
        repair=1000,
    ),
    report("1", [row(), row()], "2026-10-03T08:00:00+00:00", dry_run=True, tokens=4000),
]


def test_summary_contains_every_headline_figure():
    text = format_summary(REPORTS)
    assert "Runs covered: 2 (2026-10-03 08:00 to 2026-10-03 09:00 UTC)" in text
    assert "Invoices processed: 4" in text
    assert "ACCEPTED" in text and "REJECTED" in text and "NEEDS_REVIEW" in text
    assert "Extraction accuracy: 100.0% (72 of 72 fields)" in text
    assert "Bad invoices caught: 2 of 2 (100.0%)" in text
    assert "Clean invoices wrongly stopped: 0 of 2 (0.0%)" in text
    assert "Tokens per invoice: 2000" in text
    assert "Share of tokens spent on retries: 12.5%" in text
    assert "LlamaParse pages: 4" in text


def test_summary_lists_each_planted_problem():
    text = format_summary(REPORTS)
    assert "NONE (clean)" in text
    assert "SUSPENDED_VENDOR" in text and "WRONG_TOTAL" in text
    assert "REJECTED x1" in text and "NEEDS_REVIEW x1" in text


def test_summary_of_nothing_says_so():
    assert format_summary([]) == "No run reports found."


def write_config(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(f"paths:\n  reports_dir: {(tmp_path / 'reports').as_posix()}\n", encoding="utf-8")
    (tmp_path / "reports").mkdir()
    for data in REPORTS:
        (tmp_path / "reports" / f"run_2026100{data['run_id']}_000000.json").write_text(json.dumps(data), encoding="utf-8")
    return config


def test_command_line_prints_the_summary(tmp_path, capsys):
    assert main(["--config", str(write_config(tmp_path))]) == 0
    assert "Invoices processed: 4" in capsys.readouterr().out


def test_command_line_can_limit_runs_and_skip_dry_runs(tmp_path, capsys):
    config = write_config(tmp_path)
    main(["--config", str(config), "--runs", "1"])
    assert "Runs covered: 1" in capsys.readouterr().out
    main(["--config", str(config), "--full-only"])
    out = capsys.readouterr().out
    assert "Runs covered: 1" in out and "Invoices processed: 2" in out


def test_command_line_reports_a_bad_config(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "missing.yaml")]) == 2
    assert "Error" in capsys.readouterr().out