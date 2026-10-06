from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional

from src.config import ConfigError, load_config
from src.scheduler import decide, read_last_run, utc_now
from src.tracking.usage import load_events, totals_for_day


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Say whether a pipeline run is due, without spending anything")
    parser.add_argument("--config", type=Path, help="use a different config.yaml")
    args = parser.parse_args(argv)
    try:
        settings = load_config(args.config)
    except ConfigError as exc:
        print(f"Error: {exc}")
        return 2

    now = utc_now()
    tokens, pages = totals_for_day(load_events(settings.path("usage_log_file")), now.date())
    decision = decide(settings, read_last_run(settings.path("run_state_file")), tokens, pages, now=now)
    print(f"due={'true' if decision.run else 'false'} ({decision.reason})")

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"due={'true' if decision.run else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())