from __future__ import annotations

import argparse
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.config import DEFAULT_CONFIG_PATH, ConfigError, load_config


def _replace_line(text: str, key: str, value: str) -> Optional[str]:
    pattern = re.compile(rf"^(\s+){re.escape(key)}:.*$", re.MULTILINE)
    if not pattern.search(text):
        return None
    return pattern.sub(lambda match: f"{match.group(1)}{key}: {value}", text, count=1)


def apply_window(text: str, until: Optional[datetime], interval: Optional[float]) -> str:
    value = f'"{until.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")}"' if until else '""'
    updated = _replace_line(text, "run_until", value)
    if updated is None:
        inserted = re.sub(
            r"^(\s+)enabled:.*$",
            lambda match: f"{match.group(0)}\n{match.group(1)}run_until: {value}",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        if inserted == text:
            raise ConfigError("could not find the pipeline section in the config file")
        updated = inserted
    if interval is not None:
        replaced = _replace_line(updated, "interval_hours", f"{interval:g}")
        if replaced is None:
            raise ConfigError("could not find interval_hours in the config file")
        updated = replaced
    return updated


def main(argv: Optional[list[str]] = None, now: Optional[datetime] = None) -> int:
    parser = argparse.ArgumentParser(description="Open or close a time window for the automatic runs")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--hours", type=float, help="allow runs for this many hours from now")
    group.add_argument("--clear", action="store_true", help="remove the time limit")
    parser.add_argument("--interval", type=float, help="hours between runs while the window is open")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="use a different config.yaml")
    args = parser.parse_args(argv)

    if args.hours is not None and not 0 < args.hours <= 168:
        print("Error: --hours must be more than 0 and at most 168")
        return 2
    if args.interval is not None and not 0 < args.interval <= 168:
        print("Error: --interval must be more than 0 and at most 168")
        return 2
    if not args.config.exists():
        print(f"Error: config file not found: {args.config}")
        return 2

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    until = None
    if args.hours is not None:
        until = (current + timedelta(hours=args.hours)).replace(second=0, microsecond=0)

    original = args.config.read_text(encoding="utf-8")
    try:
        updated = apply_window(original, until, args.interval)
        args.config.write_text(updated, encoding="utf-8")
        settings = load_config(args.config)
    except ConfigError as exc:
        args.config.write_text(original, encoding="utf-8")
        print(f"Error: {exc}")
        return 2

    if until is None:
        print("The time limit was removed. Runs continue whenever they are due.")
    else:
        local = until.astimezone()
        interval = settings.pipeline.interval_hours
        runs = max(math.ceil(args.hours / interval), 1) if args.hours else 0
        print(f"Runs are allowed until {until:%Y-%m-%d %H:%M} UTC ({local:%Y-%m-%d %H:%M} on this computer's clock).")
        print(f"Interval: every {interval:g} hour(s), so about {runs} run(s) in the window.")
    print("To make the cloud use this, commit and push config.yaml.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())