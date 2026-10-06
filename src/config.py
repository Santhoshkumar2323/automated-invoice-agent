from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"


class ConfigError(Exception):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PipelineSettings(_Strict):
    enabled: bool = True
    interval_hours: float = Field(3.0, gt=0)
    wake_tolerance_minutes: float = Field(10.0, ge=0)
    invoices_per_run: int = Field(2, ge=1, le=20)
    bad_invoice_ratio: float = Field(0.25, ge=0, le=1)
    max_retries: int = Field(2, ge=0, le=5)
    skip_pointless_retries: bool = True
    run_until: Optional[datetime] = None
    seed: Optional[int] = None

    @field_validator("run_until", mode="before")
    @classmethod
    def _blank_means_no_limit(cls, value):
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            raise ValueError(
                "run_until must be a date and time such as 2026-10-06T14:00:00+00:00 "
                "(or use: python -m src.window --hours 5)"
            )
        return value

    @field_validator("run_until")
    @classmethod
    def _assume_utc(cls, value):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

class LLMSettings(_Strict):
    model: str = "openai/gpt-oss-20b"
    temperature: float = Field(0.0, ge=0, le=1)
    max_tokens: int = Field(4000, ge=100, le=8000)
    reasoning_effort: Optional[Literal["low", "medium", "high"]] = "low"


class ParserSettings(_Strict):
    tier: Literal["fast", "cost_effective", "agentic", "agentic_plus"] = "cost_effective"
    timeout_seconds: int = Field(300, ge=30, le=1800)


class BudgetSettings(_Strict):
    daily_token_limit: int = Field(100000, ge=0)
    daily_page_limit: int = Field(50, ge=0)


class PricingSettings(_Strict):
    groq_input_per_million_usd: float = Field(0.0, ge=0)
    groq_output_per_million_usd: float = Field(0.0, ge=0)
    llamaparse_per_page_usd: float = Field(0.0, ge=0)


class BrowserSettings(_Strict):
    headless: bool = True
    slow_mo_ms: int = Field(0, ge=0)
    timeout_ms: int = Field(15000, ge=1000)
    ledger_url: str = "http://localhost:8501"
    viewport_width: int = Field(1280, ge=320, le=3840)
    viewport_height: int = Field(1400, ge=400, le=4000)
    dismiss_popups: bool = True


class CheckSettings(_Strict):
    gstin_format: bool = True
    gstin_checksum: bool = True
    state_code: bool = True
    arithmetic: bool = True
    duplicate_invoice: bool = True
    registry_status: bool = True


class RetentionSettings(_Strict):
    max_screenshots: int = Field(40, ge=0)
    max_reports: int = Field(100, ge=0)
    max_ground_truth: int = Field(500, ge=0)
    max_incoming_pdfs: int = Field(20, ge=0)
    usage_days: int = Field(30, ge=0)

class DashboardSettings(_Strict):
    github_repo: str = Field("", pattern=r"^([\w.-]+/[\w.-]+)?$")
    branch: str = "main"
    refresh_seconds: int = Field(300, ge=10)
    recent_runs: int = Field(30, ge=1, le=200)
    recent_ledger_rows: int = Field(50, ge=1, le=1000)
    recent_screenshots: int = Field(6, ge=0, le=60)
    include_dry_runs: bool = False

class PathSettings(_Strict):
    incoming_dir: str = "data/incoming_invoices"
    ledger_file: str = "data/company_ledger.json"
    ground_truth_file: str = "data/ground_truth.json"
    usage_log_file: str = "data/usage_log.json"
    run_state_file: str = "data/run_state.json"
    reports_dir: str = "reports"
    screenshots_dir: str = "screenshots"


class Settings(_Strict):
    pipeline: PipelineSettings = PipelineSettings()
    llm: LLMSettings = LLMSettings()
    parser: ParserSettings = ParserSettings()
    budget: BudgetSettings = BudgetSettings()
    pricing: PricingSettings = PricingSettings()
    browser: BrowserSettings = BrowserSettings()
    checks: CheckSettings = CheckSettings()
    retention: RetentionSettings = RetentionSettings()
    dashboard: DashboardSettings = DashboardSettings()
    paths: PathSettings = PathSettings()

    def path(self, name: str) -> Path:
        value = getattr(self.paths, name, None)
        if value is None:
            raise ConfigError(f"Unknown path setting: {name}")
        return ROOT / value


class Secrets(BaseModel):
    groq_api_key: str = ""
    llama_cloud_api_key: str = ""
    langsmith_api_key: str = ""

    def require(self, *names: str) -> None:
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise ConfigError("Missing secrets: " + ", ".join(missing))


def load_config(path: Optional[Path] = None) -> Settings:
    target = Path(path) if path else DEFAULT_CONFIG_PATH
    if not target.exists():
        raise ConfigError(f"Config file not found: {target}")
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {target}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Config root must be a mapping: {target}")
    try:
        return Settings(**raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise ConfigError(f"Invalid config: {problems}") from exc


def load_secrets() -> Secrets:
    load_dotenv(ROOT / ".env")
    return Secrets(
        groq_api_key=os.environ.get("GROQ_API_KEY", ""),
        llama_cloud_api_key=os.environ.get("LLAMA_CLOUD_API_KEY", ""),
        langsmith_api_key=os.environ.get("LANGSMITH_API_KEY", ""),
    )