import json
from datetime import datetime, timezone

from src.config import PricingSettings
from src.tracking.usage import (
    UsageTracker,
    event_cost,
    load_events,
    summarize,
    totals_for_day,
)

PRICING = PricingSettings(
    groq_input_per_million_usd=0.5,
    groq_output_per_million_usd=1.0,
    llamaparse_per_page_usd=0.01,
)


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


def test_events_are_saved_and_reloaded(tmp_path):
    path = tmp_path / "usage.json"
    tracker = UsageTracker(path, PRICING, "r1", clock=lambda: at(2, 10))
    tracker.record_parse(2, invoice="a.pdf")
    tracker.record_llm("extract", "m", 1000, 200, invoice="a.pdf")
    events = load_events(path)
    assert len(events) == 2
    assert events[0]["kind"] == "parse" and events[0]["pages"] == 2
    assert events[1]["total_tokens"] == 1200
    assert events[1]["run_id"] == "r1"


def test_new_tracker_appends_to_existing_file(tmp_path):
    path = tmp_path / "usage.json"
    first = UsageTracker(path, PRICING, "r1", clock=lambda: at(2, 10))
    first.record_llm("extract", "m", 100, 50)
    second = UsageTracker(path, PRICING, "r2", clock=lambda: at(2, 11))
    second.record_llm("extract", "m", 10, 5)
    assert len(load_events(path)) == 2
    assert len(second.events) == 1
    assert second.run_summary()["total_tokens"] == 15


def test_today_totals_only_count_the_current_utc_day(tmp_path):
    path = tmp_path / "usage.json"
    clock_value = {"now": at(1, 23, 59)}
    tracker = UsageTracker(path, PRICING, "r1", clock=lambda: clock_value["now"])
    tracker.record_llm("extract", "m", 1000, 0)
    clock_value["now"] = at(2, 0, 1)
    tracker.record_llm("extract", "m", 300, 0)
    tracker.record_parse(3)
    assert tracker.today_totals() == (300, 3)
    assert tracker.today_totals(at(1, 12)) == (1000, 0)


def test_totals_for_day_ignores_bad_timestamps():
    events = [
        {"timestamp": "garbage", "total_tokens": 999},
        {"total_tokens": 999},
        {"timestamp": "2026-10-02T09:00:00", "total_tokens": 10},
        {"timestamp": "2026-10-02T09:00:00+00:00", "total_tokens": 5, "pages": 2},
    ]
    assert totals_for_day(events, at(2, 0).date()) == (15, 2)


def test_cost_calculation():
    llm = {"kind": "llm", "prompt_tokens": 1_000_000, "completion_tokens": 500_000}
    parse = {"kind": "parse", "pages": 10}
    assert event_cost(llm, PRICING) == 1.0
    assert round(event_cost(parse, PRICING), 6) == 0.1
    assert event_cost({"kind": "other"}, PRICING) == 0.0
    assert event_cost(llm, PricingSettings()) == 0.0


def test_summary_splits_steps_and_retry_share():
    events = [
        {"kind": "llm", "step": "extract", "prompt_tokens": 800, "completion_tokens": 200, "total_tokens": 1000},
        {"kind": "llm", "step": "repair", "prompt_tokens": 900, "completion_tokens": 100, "total_tokens": 1000},
        {"kind": "parse", "step": "parse", "pages": 2, "total_tokens": 0},
    ]
    summary = summarize(events, PRICING)
    assert summary["llm_calls"] == 2
    assert summary["prompt_tokens"] == 1700
    assert summary["completion_tokens"] == 300
    assert summary["total_tokens"] == 2000
    assert summary["pages"] == 2
    assert summary["tokens_by_step"] == {"extract": 1000, "repair": 1000}
    assert summary["retry_token_share"] == 0.5


def test_summary_of_nothing_is_zero():
    summary = summarize([], PRICING)
    assert summary["total_tokens"] == 0
    assert summary["retry_token_share"] == 0.0
    assert summary["cost_usd"] == 0.0


def test_corrupt_usage_file_is_treated_as_empty(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text("{broken", encoding="utf-8")
    assert load_events(path) == []
    tracker = UsageTracker(path, PRICING, "r1", clock=lambda: at(2, 10))
    tracker.record_parse(1)
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1