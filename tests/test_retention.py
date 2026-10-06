import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.config import ConfigError, Settings, load_config
from src.main import run_cycle
from src.retention import (
    apply_retention,
    prune_oldest,
    prune_reports,
    prune_usage,
    trim_json_list,
)
from src.workflow.state import EntryResult, ExtractResult, ParseResult

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def touch(path, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_defaults_are_sensible_and_adjustable(tmp_path):
    defaults = Settings().retention
    assert (defaults.max_screenshots, defaults.max_reports, defaults.max_ground_truth) == (40, 100, 500)
    assert (defaults.max_incoming_pdfs, defaults.usage_days) == (20, 30)
    file = tmp_path / "c.yaml"
    file.write_text("retention:\n  max_screenshots: 5\n  usage_days: 0\n", encoding="utf-8")
    custom = load_config(file).retention
    assert custom.max_screenshots == 5 and custom.usage_days == 0


def test_negative_limits_are_rejected(tmp_path):
    file = tmp_path / "c.yaml"
    file.write_text("retention:\n  max_screenshots: -1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(file)


def test_newest_files_are_kept_by_timestamp_in_the_name_not_by_file_date(tmp_path):
    names = [f"20261001_00000{i}_a.png" for i in range(5)]
    for index, name in enumerate(names):
        touch(tmp_path / name, mtime=1_000_000 + (4 - index) * 1000)
    assert prune_oldest(tmp_path, "*.png", 2) == 3
    assert sorted(p.name for p in tmp_path.iterdir()) == names[-2:]


def test_files_without_a_timestamp_fall_back_to_file_date(tmp_path):
    touch(tmp_path / "old.png", mtime=1_000_000)
    touch(tmp_path / "mid.png", mtime=2_000_000)
    touch(tmp_path / "new.png", mtime=3_000_000)
    assert prune_oldest(tmp_path, "*.png", 1) == 2
    assert [p.name for p in tmp_path.iterdir()] == ["new.png"]


def test_zero_means_unlimited_and_missing_folder_is_fine(tmp_path):
    touch(tmp_path / "20261001_000000_a.png")
    assert prune_oldest(tmp_path, "*.png", 0) == 0
    assert prune_oldest(tmp_path / "missing", "*.png", 5) == 0
    assert len(list(tmp_path.iterdir())) == 1


def test_other_files_are_never_touched(tmp_path):
    touch(tmp_path / ".gitkeep")
    touch(tmp_path / "notes.txt")
    for i in range(3):
        touch(tmp_path / f"2026100{i + 1}_000000_a.png")
    prune_oldest(tmp_path, "*.png", 1)
    assert {p.name for p in tmp_path.iterdir()} == {".gitkeep", "notes.txt", "20261003_000000_a.png"}


def test_a_file_that_cannot_be_deleted_is_skipped_not_fatal(tmp_path, monkeypatch):
    for i in range(3):
        touch(tmp_path / f"2026100{i + 1}_000000_a.png")
    real_unlink = Path.unlink

    def flaky(self, *args, **kwargs):
        if self.name.startswith("20261001"):
            raise PermissionError("in use")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky)
    assert prune_oldest(tmp_path, "*.png", 1) == 1


def test_reports_are_removed_as_pairs_oldest_first(tmp_path):
    for stamp in ("20261001_010000", "20261002_010000", "20261003_010000"):
        touch(tmp_path / f"run_{stamp}.md")
        touch(tmp_path / f"run_{stamp}.json")
    touch(tmp_path / ".gitkeep")
    touch(tmp_path / "other.md")
    assert prune_reports(tmp_path, 2) == 1
    names = {p.name for p in tmp_path.iterdir()}
    assert "run_20261001_010000.md" not in names and "run_20261001_010000.json" not in names
    assert {"run_20261002_010000.md", "run_20261003_010000.json", ".gitkeep", "other.md"} <= names


def test_report_pruning_handles_unpaired_files_and_unlimited(tmp_path):
    touch(tmp_path / "run_20261001_010000.md")
    touch(tmp_path / "run_20261002_010000.json")
    assert prune_reports(tmp_path, 0) == 0
    assert prune_reports(tmp_path, 1) == 1
    assert [p.name for p in tmp_path.iterdir()] == ["run_20261002_010000.json"]


def test_json_list_keeps_only_the_newest_records(tmp_path):
    path = tmp_path / "truth.json"
    path.write_text(json.dumps(list(range(10))), encoding="utf-8")
    assert trim_json_list(path, 4) == 6
    assert json.loads(path.read_text(encoding="utf-8")) == [6, 7, 8, 9]
    assert [p.name for p in tmp_path.iterdir()] == ["truth.json"]


def test_json_list_is_left_alone_when_small_corrupt_or_unlimited(tmp_path):
    small = tmp_path / "small.json"
    small.write_text("[1, 2]", encoding="utf-8")
    assert trim_json_list(small, 5) == 0
    assert trim_json_list(small, 0) == 0
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{broken", encoding="utf-8")
    assert trim_json_list(corrupt, 1) == 0
    assert corrupt.read_text(encoding="utf-8") == "{broken"
    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"a": 1}', encoding="utf-8")
    assert trim_json_list(wrong, 1) == 0
    assert trim_json_list(tmp_path / "missing.json", 1) == 0


def event(days_ago, tokens=100):
    return {"timestamp": (NOW - timedelta(days=days_ago)).isoformat(), "kind": "llm", "total_tokens": tokens, "pages": 0}


def test_old_usage_events_are_dropped_and_recent_ones_kept(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text(json.dumps([event(45), event(31), event(30), event(1), event(0)]), encoding="utf-8")
    assert prune_usage(path, 30, NOW) == 2
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 3


def test_usage_with_unreadable_timestamps_is_kept(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text(json.dumps([{"timestamp": "garbage"}, event(90)]), encoding="utf-8")
    assert prune_usage(path, 30, NOW) == 1
    assert json.loads(path.read_text(encoding="utf-8")) == [{"timestamp": "garbage"}]


def test_usage_pruning_is_off_for_zero_and_safe_for_corrupt_files(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text(json.dumps([event(400)]), encoding="utf-8")
    assert prune_usage(path, 0, NOW) == 0
    corrupt = tmp_path / "bad.json"
    corrupt.write_text("{broken", encoding="utf-8")
    assert prune_usage(corrupt, 30, NOW) == 0
    assert corrupt.read_text(encoding="utf-8") == "{broken"


def settings_for(tmp_path, **retention):
    return Settings(
        retention=retention,
        pipeline={"bad_invoice_ratio": 0.0, "invoices_per_run": 2, "seed": 1},
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


def test_apply_retention_cleans_every_area_and_reports_counts(tmp_path):
    settings = settings_for(tmp_path, max_screenshots=2, max_incoming_pdfs=1, max_reports=1, max_ground_truth=3, usage_days=5)
    for i in range(4):
        touch(tmp_path / "shots" / f"2026100{i + 1}_000000_a.png")
        touch(tmp_path / "incoming" / f"inv{i}.pdf", mtime=1_000_000 + i)
        touch(tmp_path / "reports" / f"run_2026100{i + 1}_000000.md")
        touch(tmp_path / "reports" / f"run_2026100{i + 1}_000000.json")
    (tmp_path / "truth.json").write_text(json.dumps(list(range(10))), encoding="utf-8")
    (tmp_path / "usage.json").write_text(json.dumps([event(20), event(1)]), encoding="utf-8")
    assert apply_retention(settings, now=NOW) == {
        "screenshots": 2,
        "pdfs": 3,
        "reports": 3,
        "ground_truth": 7,
        "usage_events": 1,
    }


def test_apply_retention_on_an_empty_project_does_nothing(tmp_path):
    assert set(apply_retention(settings_for(tmp_path), now=NOW).values()) == {0}


def test_everything_can_be_switched_off_with_zeros(tmp_path):
    settings = settings_for(tmp_path, max_screenshots=0, max_incoming_pdfs=0, max_reports=0, max_ground_truth=0, usage_days=0)
    for i in range(5):
        touch(tmp_path / "shots" / f"2026100{i + 1}_000000_a.png")
    assert set(apply_retention(settings, now=NOW).values()) == {0}
    assert len(list((tmp_path / "shots").iterdir())) == 5


class Plugins:
    def __init__(self, settings):
        self.settings = settings

    def factory(self):
        return self.parse, self.extract, self.enter

    def parse(self, path):
        return ParseResult(markdown=path.name, pages=1)

    def extract(self, markdown, feedback, previous):
        truth = json.loads(self.settings.path("ground_truth_file").read_text(encoding="utf-8"))
        record = next(r for r in truth if r["file"] == markdown)
        return ExtractResult(record["invoice"], prompt_tokens=1000, completion_tokens=200, model="fake")

    def enter(self, invoice, file_name):
        return EntryResult(True, screenshot="shot.png")


def test_a_run_cleans_up_after_itself(tmp_path):
    settings = settings_for(tmp_path, max_screenshots=2)
    for i in range(5):
        touch(tmp_path / "shots" / f"2026100{i + 1}_000000_a.png")
    outcome = run_cycle(settings, Plugins(settings).factory, now=NOW)
    assert outcome.ran is True
    assert outcome.pruned["screenshots"] == 3
    assert len(list((tmp_path / "shots").iterdir())) == 2