from datetime import datetime, timedelta, timezone

import pytest

from src.config import ConfigError, Settings, load_config
from src.scheduler import decide

NOW = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)


def make(**pipeline):
    return Settings(pipeline=pipeline)


def load(tmp_path, line):
    file = tmp_path / "c.yaml"
    file.write_text(f"pipeline:\n  {line}\n", encoding="utf-8")
    return load_config(file)


def test_there_is_no_time_limit_by_default():
    assert Settings().pipeline.run_until is None
    assert decide(Settings(), None, 0, 0, now=NOW).run is True


def test_runs_are_allowed_before_the_end_of_the_window():
    settings = make(run_until=NOW + timedelta(hours=5))
    assert decide(settings, None, 0, 0, now=NOW).run is True


def test_runs_stop_after_the_window_ends():
    settings = make(run_until=NOW - timedelta(minutes=1))
    decision = decide(settings, None, 0, 0, now=NOW)
    assert decision.run is False
    assert decision.reason == "the active window ended at 2026-10-06 09:59 UTC"


def test_the_exact_end_time_is_already_closed():
    assert decide(make(run_until=NOW), None, 0, 0, now=NOW).run is False


def test_a_manual_forced_run_ignores_the_window():
    settings = make(run_until=NOW - timedelta(hours=1))
    assert decide(settings, None, 0, 0, now=NOW, force=True).run is True


def test_the_disabled_message_comes_before_the_window_message():
    settings = make(enabled=False, run_until=NOW - timedelta(hours=1))
    assert "disabled" in decide(settings, None, 0, 0, now=NOW).reason


def test_budgets_still_apply_inside_the_window():
    settings = Settings(pipeline={"run_until": NOW + timedelta(hours=5)}, budget={"daily_token_limit": 100})
    decision = decide(settings, None, 100, 0, now=NOW)
    assert decision.run is False and "token budget" in decision.reason


def test_the_interval_still_applies_inside_the_window():
    settings = make(run_until=NOW + timedelta(hours=5), interval_hours=1)
    assert decide(settings, NOW - timedelta(minutes=20), 0, 0, now=NOW).run is False
    assert decide(settings, NOW - timedelta(minutes=55), 0, 0, now=NOW).run is True


def test_quoted_iso_time_with_an_offset_is_read_correctly(tmp_path):
    settings = load(tmp_path, 'run_until: "2026-10-06T15:30:00+05:30"')
    assert settings.pipeline.run_until == NOW
    assert decide(settings, None, 0, 0, now=NOW).run is False


def test_time_without_an_offset_is_treated_as_utc(tmp_path):
    settings = load(tmp_path, 'run_until: "2026-10-06T12:00:00"')
    assert settings.pipeline.run_until == datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def test_unquoted_yaml_timestamps_work_too(tmp_path):
    settings = load(tmp_path, "run_until: 2026-10-06T12:00:00+00:00")
    assert settings.pipeline.run_until == datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("line", ['run_until: ""', "run_until: null", 'run_until: "   "', "run_until:"])
def test_blank_values_mean_no_limit(tmp_path, line):
    assert load(tmp_path, line).pipeline.run_until is None


@pytest.mark.parametrize("line", ['run_until: "tomorrow"', "run_until: 12", 'run_until: "2026-13-45T00:00:00"'])
def test_nonsense_values_are_rejected(tmp_path, line):
    with pytest.raises(ConfigError):
        load(tmp_path, line)