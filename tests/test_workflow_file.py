import re
from pathlib import Path

import pytest
import yaml

from src.config import ROOT, Settings
from src.factory.generate_mock_invoices import Defect

WORKFLOW = ROOT / ".github" / "workflows" / "pipeline.yml"
GITIGNORE = ROOT / ".gitignore"


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def steps(workflow):
    return workflow["jobs"]["run"]["steps"]


def triggers(workflow):
    return workflow.get("on", workflow.get(True))


def step(steps, name_part):
    return next(s for s in steps if name_part.lower() in s["name"].lower())


def test_it_wakes_up_every_hour(workflow):
    schedule = triggers(workflow)["schedule"]
    assert len(schedule) == 1
    assert re.fullmatch(r"\d{1,2} \* \* \* \*", schedule[0]["cron"])


def test_manual_runs_offer_count_defect_and_force(workflow):
    inputs = triggers(workflow)["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"count", "defect", "force"}
    assert inputs["force"]["type"] == "boolean" and inputs["force"]["default"] is True
    options = set(inputs["defect"]["options"])
    assert options == {d.value for d in Defect} | {"MIXED"}
    assert inputs["defect"]["default"] == "MIXED"
    assert "" not in inputs["defect"]["options"]


def test_the_run_script_accepts_every_defect_name(steps):
    script = step(steps, "Run the pipeline")["run"]
    for defect in Defect:
        assert defect.value in script


def test_only_the_permission_it_needs_is_requested(workflow):
    assert workflow["permissions"] == {"contents": "write"}


def test_two_runs_can_never_overlap(workflow):
    assert workflow["concurrency"]["group"]
    assert workflow["concurrency"]["cancel-in-progress"] is False


def test_the_job_has_a_time_limit(workflow):
    assert 5 <= workflow["jobs"]["run"]["timeout-minutes"] <= 60


def test_python_version_matches_the_one_you_develop_on(steps):
    assert step(steps, "Set up Python")["with"]["python-version"] == "3.13"


def test_the_due_check_comes_first_and_everything_expensive_waits_for_it(steps):
    names = [s["name"] for s in steps]
    due_index = next(i for i, s in enumerate(steps) if s.get("id") == "due")
    for later in steps[due_index + 1 :]:
        assert "steps.due.outputs.due" in later["if"], later["name"]
    for early in steps[:due_index]:
        assert "if" not in early, early["name"]
    assert names.index("Install all packages") > due_index
    assert names.index("Install the browser") > due_index


def test_the_schedule_check_installs_only_light_packages(steps):
    command = step(steps, "schedule check")["run"]
    assert "playwright" not in command and "streamlit" not in command and "groq" not in command


def test_cleanup_and_saving_still_happen_after_a_failure(steps):
    for name in ("Show the latest report", "Stop the ledger website", "Save results"):
        assert "always()" in step(steps, name)["if"]


def test_secrets_appear_only_in_the_pipeline_step_environment(steps):
    holders = [s["name"] for s in steps if "secrets." in yaml.safe_dump(s.get("env", {}))]
    assert holders == ["Run the pipeline"]
    env = step(steps, "Run the pipeline")["env"]
    assert {"GROQ_API_KEY", "LLAMA_CLOUD_API_KEY", "LANGSMITH_API_KEY"} <= set(env)
    for current in steps:
        assert "secrets." not in current.get("run", ""), current["name"]
        assert "secrets." not in yaml.safe_dump(current.get("with", {})), current["name"]


def test_inputs_reach_scripts_only_through_environment_variables(steps):
    for current in steps:
        assert "inputs." not in current.get("run", ""), current["name"]


def test_the_script_never_prints_environment_or_keys(steps):
    script = step(steps, "Run the pipeline")["run"]
    for forbidden in ("printenv", "env |", "set -x", "echo $GROQ", "echo $LLAMA", "echo $LANGSMITH", '"$GROQ', '"$LLAMA'):
        assert forbidden not in script


def test_langsmith_tracing_follows_whether_a_key_exists(steps):
    env = step(steps, "Run the pipeline")["env"]
    assert "LANGSMITH_API_KEY != ''" in env["LANGSMITH_TRACING"]


def test_the_ledger_website_uses_the_port_the_robot_expects(steps):
    script = step(steps, "Start the ledger website")["run"]
    port = Settings().browser.ledger_url.rsplit(":", 1)[1]
    assert f"--server.port {port}" in script
    assert f"localhost:{port}/_stcore/health" in script
    assert "--server.headless true" in script


def test_results_saved_are_exactly_the_generated_folders(steps):
    script = step(steps, "Save results")["run"]
    assert "git add -A data reports screenshots" in script
    assert "git diff --cached --quiet" in script
    assert "git pull --rebase" in script


def test_the_bot_commit_identity_is_not_a_real_person(steps):
    script = step(steps, "Save results")["run"]
    assert "invoice-agent-bot" in script


def test_gitignore_blocks_secrets_and_clutter():
    lines = [line.strip() for line in GITIGNORE.read_text(encoding="utf-8").splitlines() if line.strip()]
    for needed in (".env", ".venv/", "__pycache__/", "data/incoming_invoices/*.pdf"):
        assert needed in lines


def test_gitignore_does_not_hide_what_the_cloud_needs_to_commit():
    lines = [line.strip() for line in GITIGNORE.read_text(encoding="utf-8").splitlines() if line.strip()]
    for forbidden in ("reports/", "screenshots/", "data/", "*.json", "*.png", "*.md", ".env*", ".env.example", ".gitkeep"):
        assert forbidden not in lines


SECRET_PATTERNS = [
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    re.compile(r"llx-[A-Za-z0-9]{20,}"),
    re.compile(r"lsv2_(?:sk|pt)_[A-Za-z0-9]{20,}"),
]
SKIP_DIRS = {".venv", "venv", ".git", "__pycache__", ".pytest_cache", "node_modules", ".vscode"}
TEXT_SUFFIXES = {".py", ".yaml", ".yml", ".md", ".txt", ".json", ".toml", ".ini", ".example", ".cfg"}


def test_no_api_key_is_written_into_any_file_that_would_be_published():
    leaks = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.name == ".env" or path.suffix not in TEXT_SUFFIXES or path.stat().st_size > 2_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            leaks.append(str(path.relative_to(ROOT)))
    assert leaks == []