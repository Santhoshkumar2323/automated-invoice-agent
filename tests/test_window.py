from datetime import datetime, timezone

import pytest

from src.config import load_config
from src.window import apply_window, main

NOW = datetime(2026, 10, 6, 10, 7, 33, tzinfo=timezone.utc)

CONFIG = """pipeline:
  enabled: true
  run_until: ""
  interval_hours: 3
  invoices_per_run: 2

llm:
  model: openai/gpt-oss-20b
"""


def write(tmp_path, text=CONFIG, newline=None):
    path = tmp_path / "config.yaml"
    if newline:
        path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    else:
        path.write_text(text, encoding="utf-8")
    return path


def test_five_hour_window_sets_the_end_time_and_leaves_the_rest_alone(tmp_path, capsys):
    path = write(tmp_path)
    assert main(["--hours", "5", "--config", str(path)], now=NOW) == 0
    settings = load_config(path)
    assert settings.pipeline.run_until == datetime(2026, 10, 6, 15, 7, tzinfo=timezone.utc)
    assert settings.pipeline.interval_hours == 3
    assert settings.pipeline.invoices_per_run == 2
    assert settings.llm.model == "openai/gpt-oss-20b"
    out = capsys.readouterr().out
    assert "2026-10-06 15:07 UTC" in out
    assert "about 2 run(s)" in out
    assert "commit and push config.yaml" in out


def test_interval_can_be_changed_in_the_same_command(tmp_path, capsys):
    path = write(tmp_path)
    assert main(["--hours", "5", "--interval", "1", "--config", str(path)], now=NOW) == 0
    assert load_config(path).pipeline.interval_hours == 1
    assert "about 5 run(s)" in capsys.readouterr().out


def test_only_the_two_expected_lines_change(tmp_path):
    path = write(tmp_path)
    main(["--hours", "5", "--interval", "1", "--config", str(path)], now=NOW)
    before = CONFIG.splitlines()
    after = path.read_text(encoding="utf-8").splitlines()
    assert len(before) == len(after)
    changed = [line for old, line in zip(before, after) if old != line]
    assert changed == ['  run_until: "2026-10-06T15:07:00+00:00"', "  interval_hours: 1"]


def test_the_line_is_added_when_the_config_has_none(tmp_path):
    path = write(tmp_path, CONFIG.replace('  run_until: ""\n', ""))
    assert main(["--hours", "2", "--config", str(path)], now=NOW) == 0
    assert load_config(path).pipeline.run_until == datetime(2026, 10, 6, 12, 7, tzinfo=timezone.utc)
    assert path.read_text(encoding="utf-8").count("run_until") == 1


def test_clear_removes_the_limit(tmp_path, capsys):
    path = write(tmp_path)
    main(["--hours", "5", "--config", str(path)], now=NOW)
    assert main(["--clear", "--config", str(path)], now=NOW) == 0
    assert load_config(path).pipeline.run_until is None
    assert "time limit was removed" in capsys.readouterr().out


def test_clear_can_also_restore_the_interval(tmp_path):
    path = write(tmp_path)
    main(["--hours", "5", "--interval", "1", "--config", str(path)], now=NOW)
    main(["--clear", "--interval", "3", "--config", str(path)], now=NOW)
    settings = load_config(path)
    assert settings.pipeline.run_until is None and settings.pipeline.interval_hours == 3


def test_windows_line_endings_are_handled(tmp_path):
    path = write(tmp_path, newline="\r\n")
    assert main(["--hours", "5", "--interval", "1", "--config", str(path)], now=NOW) == 0
    settings = load_config(path)
    assert settings.pipeline.interval_hours == 1
    assert settings.pipeline.run_until == datetime(2026, 10, 6, 15, 7, tzinfo=timezone.utc)


@pytest.mark.parametrize("args", [["--hours", "0"], ["--hours", "-2"], ["--hours", "500"], ["--hours", "2", "--interval", "0"]])
def test_silly_numbers_are_refused_and_nothing_changes(tmp_path, capsys, args):
    path = write(tmp_path)
    assert main(args + ["--config", str(path)], now=NOW) == 2
    assert path.read_text(encoding="utf-8") == CONFIG
    assert "Error" in capsys.readouterr().out


def test_a_missing_config_file_is_reported(tmp_path, capsys):
    assert main(["--hours", "2", "--config", str(tmp_path / "nope.yaml")], now=NOW) == 2
    assert "not found" in capsys.readouterr().out


def test_a_config_without_a_pipeline_section_is_left_untouched(tmp_path, capsys):
    path = write(tmp_path, "llm:\n  model: x\n")
    assert main(["--hours", "2", "--config", str(path)], now=NOW) == 2
    assert path.read_text(encoding="utf-8") == "llm:\n  model: x\n"


def test_exactly_one_of_hours_or_clear_is_required(tmp_path):
    path = write(tmp_path)
    with pytest.raises(SystemExit):
        main(["--config", str(path)])
    with pytest.raises(SystemExit):
        main(["--hours", "2", "--clear", "--config", str(path)])


def test_apply_window_is_a_pure_text_change():
    text = apply_window(CONFIG, datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc), None)
    assert 'run_until: "2026-10-06T15:00:00+00:00"' in text
    assert "interval_hours: 3" in text