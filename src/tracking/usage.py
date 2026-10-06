from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from src.config import PricingSettings
from src.scheduler import utc_now


def load_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict)]


def _event_day(event: dict) -> Optional[date]:
    try:
        parsed = datetime.fromisoformat(event["timestamp"])
    except (KeyError, ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date()


def totals_for_day(events: list[dict], day: date) -> tuple[int, int]:
    tokens = 0
    pages = 0
    for event in events:
        if _event_day(event) != day:
            continue
        tokens += int(event.get("total_tokens", 0) or 0)
        pages += int(event.get("pages", 0) or 0)
    return tokens, pages


def event_cost(event: dict, pricing: PricingSettings) -> float:
    kind = event.get("kind")
    if kind == "llm":
        prompt = int(event.get("prompt_tokens", 0) or 0)
        completion = int(event.get("completion_tokens", 0) or 0)
        return (
            prompt / 1_000_000 * pricing.groq_input_per_million_usd
            + completion / 1_000_000 * pricing.groq_output_per_million_usd
        )
    if kind == "parse":
        return int(event.get("pages", 0) or 0) * pricing.llamaparse_per_page_usd
    return 0.0


def summarize(events: list[dict], pricing: PricingSettings) -> dict:
    prompt = 0
    completion = 0
    pages = 0
    calls = 0
    cost = 0.0
    by_step: dict[str, int] = {}
    for event in events:
        cost += event_cost(event, pricing)
        if event.get("kind") == "llm":
            calls += 1
            prompt += int(event.get("prompt_tokens", 0) or 0)
            completion += int(event.get("completion_tokens", 0) or 0)
            step = str(event.get("step", "other"))
            by_step[step] = by_step.get(step, 0) + int(event.get("total_tokens", 0) or 0)
        elif event.get("kind") == "parse":
            pages += int(event.get("pages", 0) or 0)
    total = prompt + completion
    retry_share = round(by_step.get("repair", 0) / total, 4) if total else 0.0
    return {
        "llm_calls": calls,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "pages": pages,
        "cost_usd": round(cost, 6),
        "tokens_by_step": by_step,
        "retry_token_share": retry_share,
    }


class UsageTracker:
    def __init__(
        self,
        path: Path,
        pricing: PricingSettings,
        run_id: str,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.path = path
        self.pricing = pricing
        self.run_id = run_id
        self.clock = clock
        self._all = load_events(path)
        self.events: list[dict] = []

    def _add(self, event: dict) -> None:
        full = {"timestamp": self.clock().isoformat(), "run_id": self.run_id, **event}
        self.events.append(full)
        self._all.append(full)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._all, indent=2), encoding="utf-8")

    def record_llm(
        self,
        step: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        invoice: str = "",
    ) -> None:
        self._add(
            {
                "kind": "llm",
                "step": step,
                "model": model,
                "invoice": invoice,
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "total_tokens": int(prompt_tokens) + int(completion_tokens),
                "pages": 0,
            }
        )

    def record_parse(self, pages: int, invoice: str = "") -> None:
        self._add(
            {
                "kind": "parse",
                "step": "parse",
                "model": "llamaparse",
                "invoice": invoice,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "pages": int(pages),
            }
        )

    def today_totals(self, now: Optional[datetime] = None) -> tuple[int, int]:
        moment = (now or self.clock()).astimezone(timezone.utc)
        return totals_for_day(self._all, moment.date())

    def run_summary(self) -> dict:
        return summarize(self.events, self.pricing)