from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from src.config import Settings
from src.scheduler import utc_now
from src.tracking.usage import _event_day, load_events

_NAME_STAMP = re.compile(r"^(?:run_)?(\d{8}_\d{6})")


def _stamp(path: Path) -> str:
    match = _NAME_STAMP.match(path.name)
    if match:
        return match.group(1)
    try:
        moment = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return "00000000_000000"
    return moment.strftime("%Y%m%d_%H%M%S")


def _delete(path: Path) -> bool:
    try:
        path.unlink()
    except OSError:
        return False
    return True


def prune_oldest(folder: Path, pattern: str, keep: int) -> int:
    if keep <= 0 or not folder.exists():
        return 0
    files = sorted((p for p in folder.glob(pattern) if p.is_file()), key=lambda p: (_stamp(p), p.name))
    surplus = files[: max(len(files) - keep, 0)]
    return sum(1 for path in surplus if _delete(path))


def prune_reports(folder: Path, keep: int) -> int:
    if keep <= 0 or not folder.exists():
        return 0
    stems = sorted({p.stem for p in folder.glob("run_*") if p.suffix in (".md", ".json")})
    removed = 0
    for stem in stems[: max(len(stems) - keep, 0)]:
        results = [_delete(folder / f"{stem}{suffix}") for suffix in (".md", ".json") if (folder / f"{stem}{suffix}").exists()]
        if any(results):
            removed += 1
    return removed


def _write_json(path: Path, data: list) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, path)


def trim_json_list(path: Path, keep: int) -> int:
    if keep <= 0 or not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(data, list) or len(data) <= keep:
        return 0
    removed = len(data) - keep
    _write_json(path, data[-keep:])
    return removed


def prune_usage(path: Path, days: int, now: datetime) -> int:
    if days <= 0 or not path.exists():
        return 0
    events = load_events(path)
    cutoff = (now.astimezone(timezone.utc) - timedelta(days=days)).date()
    kept = [event for event in events if (_event_day(event) or cutoff) >= cutoff]
    if len(kept) == len(events):
        return 0
    _write_json(path, kept)
    return len(events) - len(kept)


def _guard(action: Callable[[], int]) -> int:
    try:
        return action()
    except Exception:
        return 0


def apply_retention(settings: Settings, now: Optional[datetime] = None) -> dict[str, int]:
    config = settings.retention
    moment = now or utc_now()
    return {
        "screenshots": _guard(lambda: prune_oldest(settings.path("screenshots_dir"), "*.png", config.max_screenshots)),
        "pdfs": _guard(lambda: prune_oldest(settings.path("incoming_dir"), "*.pdf", config.max_incoming_pdfs)),
        "reports": _guard(lambda: prune_reports(settings.path("reports_dir"), config.max_reports)),
        "ground_truth": _guard(lambda: trim_json_list(settings.path("ground_truth_file"), config.max_ground_truth)),
        "usage_events": _guard(lambda: prune_usage(settings.path("usage_log_file"), config.usage_days, moment)),
    }