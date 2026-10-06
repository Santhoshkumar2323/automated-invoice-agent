import pytest

from src.config import ConfigError, Secrets, load_config


def write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_default_config_file_loads():
    s = load_config()
    assert s.pipeline.interval_hours == 3
    assert s.pipeline.invoices_per_run == 2


def test_empty_file_uses_defaults(tmp_path):
    s = load_config(write(tmp_path, ""))
    assert s.pipeline.enabled is True


def test_unknown_key_is_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, "pipeline:\n  intervall_hours: 3\n"))


def test_out_of_range_value_is_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, "pipeline:\n  bad_invoice_ratio: 1.5\n"))


def test_invalid_yaml_is_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, "pipeline: [unclosed"))


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


def test_secrets_require_reports_missing():
    with pytest.raises(ConfigError) as exc:
        Secrets(groq_api_key="x").require("groq_api_key", "llama_cloud_api_key")
    assert "llama_cloud_api_key" in str(exc.value)


def test_path_resolves_under_root():
    s = load_config()
    assert s.path("ledger_file").name == "company_ledger.json"



def test_parser_tier_is_validated(tmp_path):
    assert load_config(write(tmp_path, "parser:\n  tier: fast\n")).parser.tier == "fast"
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, "parser:\n  tier: turbo\n"))




def test_reasoning_effort_is_validated(tmp_path):
    assert load_config(write(tmp_path, "llm:\n  reasoning_effort: high\n")).llm.reasoning_effort == "high"
    assert load_config(write(tmp_path, "llm:\n  reasoning_effort: null\n")).llm.reasoning_effort is None
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, "llm:\n  reasoning_effort: extreme\n"))


def test_default_model_is_a_current_groq_model():
    assert load_config().llm.model == "openai/gpt-oss-20b"        
