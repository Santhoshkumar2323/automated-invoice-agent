from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.config import Settings


@dataclass(frozen=True)
class Decision:
    run: bool
    reason: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def read_last_run(path: Path) -> Optional[datetime]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("last_run_at")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def write_last_run(path: Path, when: datetime, status: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"last_run_at": when.isoformat(), "last_status": status}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def budget_exceeded(settings: Settings, tokens_today: int, pages_today: int) -> Optional[str]:
    token_limit = settings.budget.daily_token_limit
    page_limit = settings.budget.daily_page_limit
    if token_limit > 0 and tokens_today >= token_limit:
        return f"daily token budget reached ({tokens_today}/{token_limit})"
    if page_limit > 0 and pages_today >= page_limit:
        return f"daily page budget reached ({pages_today}/{page_limit})"
    return None


def _format_wait(delta: timedelta) -> str:
    total_minutes = max(int(delta.total_seconds() // 60), 0)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes}m"


def decide(
    settings: Settings,
    last_run: Optional[datetime],
    tokens_today: int,
    pages_today: int,
    now: Optional[datetime] = None,
    force: bool = False,
) -> Decision:
    current = now or utc_now()

    if not force and not settings.pipeline.enabled:
        return Decision(False, "pipeline is disabled in config")

    until = settings.pipeline.run_until
    if not force and until is not None and current >= until:
        ended = until.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
        return Decision(False, f"the active window ended at {ended} UTC")
    blocked = budget_exceeded(settings, tokens_today, pages_today)
    
    if blocked:
        return Decision(False, blocked)

    if force:
        return Decision(True, "manual run")

    if last_run is None:
        return Decision(True, "first run")

    interval = timedelta(hours=settings.pipeline.interval_hours)
    tolerance = timedelta(minutes=settings.pipeline.wake_tolerance_minutes)
    elapsed = current - last_run
    if elapsed >= interval - tolerance:
        return Decision(True, "interval elapsed")

    remaining = interval - tolerance - elapsed
    return Decision(False, f"not due yet, next run in about {_format_wait(remaining)}")