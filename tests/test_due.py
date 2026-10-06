import json
from datetime import datetime, timedelta, timezone

from src.due import main


def write_config(tmp_path, pipeline=""):
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "pipeline:",
                "  interval_hours: 3",
                *[f"  {line}" for line in pipeline.splitlines()],
                "budget:",
                "  daily_token_limit: 1000",
                "paths:",
                f"  usage_log_file: {(tmp_path / 'usage.json').as_posix()}",
                f"  run_state_file: {(tmp_path / 'state.json').as_posix()}",
            ]
        ),
        encoding="utf-8",
    )
    return config


def run(tmp_path, monkeypatch, capsys, pipeline=""):
    output = tmp_path / "github_output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    code = main(["--config", str(write_config(tmp_path, pipeline))])
    printed = capsys.readouterr().out
    return code, printed, (output.read_text(encoding="utf-8") if output.exists() else "")


def test_first_run_is_due(tmp_path, monkeypatch, capsys):
    code, printed, output = run(tmp_path, monkeypatch, capsys)
    assert code == 0
    assert "due=true (first run)" in printed
    assert output == "due=true\n"


def test_recent_run_means_not_due(tmp_path, monkeypatch, capsys):
    last = datetime.now(timezone.utc) - timedelta(minutes=10)
    (tmp_path / "state.json").write_text(json.dumps({"last_run_at": last.isoformat(), "last_status": "ok"}), encoding="utf-8")
    code, printed, output = run(tmp_path, monkeypatch, capsys)
    assert code == 0
    assert "due=false (not due yet" in printed
    assert output == "due=false\n"


def test_old_run_means_due_again(tmp_path, monkeypatch, capsys):
    last = datetime.now(timezone.utc) - timedelta(hours=4)
    (tmp_path / "state.json").write_text(json.dumps({"last_run_at": last.isoformat(), "last_status": "ok"}), encoding="utf-8")
    assert run(tmp_path, monkeypatch, capsys)[2] == "due=true\n"


def test_switched_off_pipeline_is_not_due(tmp_path, monkeypatch, capsys):
    _, printed, output = run(tmp_path, monkeypatch, capsys, "enabled: false")
    assert "disabled" in printed and output == "due=false\n"


def test_finished_window_is_not_due(tmp_path, monkeypatch, capsys):
    _, printed, output = run(tmp_path, monkeypatch, capsys, 'run_until: "2020-01-01T00:00:00+00:00"')
    assert "active window ended" in printed and output == "due=false\n"


def test_open_window_is_due(tmp_path, monkeypatch, capsys):
    until = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
    assert run(tmp_path, monkeypatch, capsys, f'run_until: "{until}"')[2] == "due=true\n"


def test_used_up_budget_is_not_due(tmp_path, monkeypatch, capsys):
    (tmp_path / "usage.json").write_text(
        json.dumps([{"timestamp": datetime.now(timezone.utc).isoformat(), "total_tokens": 1000, "pages": 0}]), encoding="utf-8"
    )
    _, printed, output = run(tmp_path, monkeypatch, capsys)
    assert "token budget" in printed and output == "due=false\n"


def test_works_without_the_github_output_variable(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert main(["--config", str(write_config(tmp_path))]) == 0
    assert "due=true" in capsys.readouterr().out


def test_a_broken_config_fails_loudly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out.txt"))
    assert main(["--config", str(tmp_path / "missing.yaml")]) == 2
    assert "Error" in capsys.readouterr().out
    assert not (tmp_path / "out.txt").exists()