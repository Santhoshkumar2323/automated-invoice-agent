import json
from datetime import datetime, timedelta, timezone

from src.config import Settings
from src.scheduler import (
    budget_exceeded,
    decide,
    read_last_run,
    write_last_run,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def make(**overrides):
    return Settings(**overrides)


def test_disabled_pipeline_skips():
    s = make(pipeline={"enabled": False})
    d = decide(s, None, 0, 0, now=NOW)
    assert d.run is False
    assert "disabled" in d.reason


def test_force_bypasses_disabled():
    s = make(pipeline={"enabled": False})
    d = decide(s, None, 0, 0, now=NOW, force=True)
    assert d.run is True


def test_first_run_runs():
    d = decide(make(), None, 0, 0, now=NOW)
    assert d.run is True
    assert d.reason == "first run"


def test_interval_not_elapsed_skips():
    last = NOW - timedelta(hours=1)
    d = decide(make(), last, 0, 0, now=NOW)
    assert d.run is False
    assert "next run" in d.reason


def test_interval_elapsed_runs():
    last = NOW - timedelta(hours=3, minutes=1)
    d = decide(make(), last, 0, 0, now=NOW)
    assert d.run is True


def test_tolerance_allows_slightly_early_wake():
    last = NOW - timedelta(hours=2, minutes=55)
    d = decide(make(), last, 0, 0, now=NOW)
    assert d.run is True


def test_outside_tolerance_skips():
    last = NOW - timedelta(hours=2, minutes=40)
    d = decide(make(), last, 0, 0, now=NOW)
    assert d.run is False


def test_custom_interval():
    s = make(pipeline={"interval_hours": 1})
    last = NOW - timedelta(minutes=65)
    assert decide(s, last, 0, 0, now=NOW).run is True


def test_token_budget_blocks_even_with_force():
    s = make(budget={"daily_token_limit": 1000})
    d = decide(s, None, 1000, 0, now=NOW, force=True)
    assert d.run is False
    assert "token" in d.reason


def test_page_budget_blocks():
    s = make(budget={"daily_page_limit": 10})
    d = decide(s, None, 0, 10, now=NOW)
    assert d.run is False
    assert "page" in d.reason


def test_zero_budget_means_unlimited():
    s = make(budget={"daily_token_limit": 0, "daily_page_limit": 0})
    assert budget_exceeded(s, 10**9, 10**9) is None


def test_read_last_run_missing_file(tmp_path):
    assert read_last_run(tmp_path / "none.json") is None


def test_read_last_run_corrupt_file(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_last_run(p) is None


def test_read_last_run_naive_timestamp_treated_as_utc(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"last_run_at": "2026-10-02T09:00:00"}), encoding="utf-8")
    result = read_last_run(p)
    assert result.tzinfo is not None
    assert result.hour == 9


def test_write_then_read_roundtrip(tmp_path):
    p = tmp_path / "nested" / "state.json"
    write_last_run(p, NOW, "ok")
    assert read_last_run(p) == NOW
    assert json.loads(p.read_text(encoding="utf-8"))["last_status"] == "ok"