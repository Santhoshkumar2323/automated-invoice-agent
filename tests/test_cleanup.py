from pathlib import Path

import pytest

from src.cleanup import CleanupError, Target, execute, format_plan, main, plan
from src.config import Settings


def make_settings(tmp_path):
    return Settings(
        paths={
            "incoming_dir": str(tmp_path / "incoming"),
            "ledger_file": str(tmp_path / "data" / "ledger.json"),
            "ground_truth_file": str(tmp_path / "data" / "truth.json"),
            "usage_log_file": str(tmp_path / "data" / "usage.json"),
            "run_state_file": str(tmp_path / "data" / "state.json"),
            "reports_dir": str(tmp_path / "reports"),
            "screenshots_dir": str(tmp_path / "shots"),
        }
    )


def touch(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def populate(tmp_path):
    for name in ("ledger.json", "truth.json", "usage.json", "state.json"):
        touch(tmp_path / "data" / name)
    touch(tmp_path / "incoming" / "a.pdf")
    touch(tmp_path / "incoming" / ".gitkeep")
    touch(tmp_path / "shots" / "20261003_000000_a.png")
    touch(tmp_path / "shots" / ".gitkeep")
    touch(tmp_path / "reports" / "run_20261003_000000.md")
    touch(tmp_path / "reports" / "run_20261003_000000.json")
    touch(tmp_path / "reports" / ".gitkeep")
    touch(tmp_path / "reports" / "notes.txt")


def names(targets):
    return sorted(t.path.name for t in targets)


def test_plan_lists_test_data_but_keeps_the_usage_log_by_default(tmp_path):
    populate(tmp_path)
    targets = plan(make_settings(tmp_path), root=tmp_path)
    assert names(targets) == [
        "20261003_000000_a.png",
        "a.pdf",
        "ledger.json",
        "run_20261003_000000.json",
        "run_20261003_000000.md",
        "state.json",
        "truth.json",
    ]


def test_plan_includes_the_usage_log_only_when_asked(tmp_path):
    populate(tmp_path)
    targets = plan(make_settings(tmp_path), include_usage=True, root=tmp_path)
    assert "usage.json" in names(targets)


def test_placeholders_and_unrelated_files_are_never_listed(tmp_path):
    populate(tmp_path)
    listed = names(plan(make_settings(tmp_path), include_usage=True, root=tmp_path))
    assert ".gitkeep" not in listed
    assert "notes.txt" not in listed


def test_missing_files_and_folders_are_fine(tmp_path):
    assert plan(make_settings(tmp_path), root=tmp_path) == []


def test_anything_outside_the_project_folder_is_refused(tmp_path):
    inside = tmp_path / "project"
    inside.mkdir()
    outside = tmp_path / "elsewhere"
    touch(outside / "ledger.json")
    settings = Settings(paths={"ledger_file": str(outside / "ledger.json")})
    with pytest.raises(CleanupError):
        plan(settings, root=inside)
    assert (outside / "ledger.json").exists()


def test_execute_deletes_files_and_reports_failures(tmp_path, monkeypatch):
    first = touch(tmp_path / "a.png")
    second = touch(tmp_path / "b.png")
    targets = [Target("screenshot", first, 1), Target("screenshot", second, 1)]
    real_unlink = Path.unlink

    def flaky(self, *args, **kwargs):
        if self.name == "b.png":
            raise PermissionError("in use")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky)
    deleted, failed = execute(targets)
    assert deleted == 1
    assert [t.path.name for t in failed] == ["b.png"]
    assert not first.exists() and second.exists()


def test_plan_text_groups_files_and_shows_sizes(tmp_path):
    populate(tmp_path)
    text = format_plan(plan(make_settings(tmp_path), root=tmp_path))
    assert "These files would be deleted:" in text
    assert "screenshot" in text and "report" in text and "ledger" in text
    assert "total" in text and "B" in text
    assert format_plan([]) == "Nothing to clean."


def write_config(tmp_path):
    populate(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "paths:",
                f"  incoming_dir: {(tmp_path / 'incoming').as_posix()}",
                f"  ledger_file: {(tmp_path / 'data' / 'ledger.json').as_posix()}",
                f"  ground_truth_file: {(tmp_path / 'data' / 'truth.json').as_posix()}",
                f"  usage_log_file: {(tmp_path / 'data' / 'usage.json').as_posix()}",
                f"  run_state_file: {(tmp_path / 'data' / 'state.json').as_posix()}",
                f"  reports_dir: {(tmp_path / 'reports').as_posix()}",
                f"  screenshots_dir: {(tmp_path / 'shots').as_posix()}",
            ]
        ),
        encoding="utf-8",
    )
    return config


def test_without_yes_nothing_is_deleted(tmp_path, capsys, monkeypatch):
    config = write_config(tmp_path)
    monkeypatch.setattr("src.cleanup.ROOT", tmp_path)
    assert main(["--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "Nothing was deleted" in out
    assert (tmp_path / "data" / "ledger.json").exists()


def test_with_yes_the_files_are_deleted_and_usage_is_kept(tmp_path, capsys, monkeypatch):
    config = write_config(tmp_path)
    monkeypatch.setattr("src.cleanup.ROOT", tmp_path)
    assert main(["--config", str(config), "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Deleted 7 file(s)." in out
    assert "usage log was kept" in out
    assert not (tmp_path / "data" / "ledger.json").exists()
    assert (tmp_path / "data" / "usage.json").exists()
    assert (tmp_path / "shots" / ".gitkeep").exists()
    assert (tmp_path / "reports" / "notes.txt").exists()


def test_reset_usage_also_removes_the_usage_log(tmp_path, capsys, monkeypatch):
    config = write_config(tmp_path)
    monkeypatch.setattr("src.cleanup.ROOT", tmp_path)
    assert main(["--config", str(config), "--yes", "--reset-usage"]) == 0
    assert "Deleted 8 file(s)." in capsys.readouterr().out
    assert not (tmp_path / "data" / "usage.json").exists()


def test_a_second_run_finds_nothing_to_do(tmp_path, capsys, monkeypatch):
    config = write_config(tmp_path)
    monkeypatch.setattr("src.cleanup.ROOT", tmp_path)
    main(["--config", str(config), "--yes"])
    capsys.readouterr()
    assert main(["--config", str(config), "--yes"]) == 0
    assert "Nothing to clean." in capsys.readouterr().out


def test_bad_config_is_reported(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "missing.yaml")]) == 2
    assert "Error" in capsys.readouterr().out