from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.config import ROOT, ConfigError, Settings, load_config


class CleanupError(Exception):
    pass


@dataclass(frozen=True)
class Target:
    label: str
    path: Path
    size: int


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def plan(settings: Settings, include_usage: bool = False, root: Optional[Path] = None) -> list[Target]:
    project = root or ROOT
    candidates: list[tuple[str, Path]] = [
        ("ledger", settings.path("ledger_file")),
        ("ground truth", settings.path("ground_truth_file")),
        ("run state", settings.path("run_state_file")),
    ]
    if include_usage:
        candidates.append(("usage log", settings.path("usage_log_file")))

    incoming = settings.path("incoming_dir")
    if incoming.exists():
        candidates += [("generated pdf", p) for p in sorted(incoming.glob("*.pdf"))]
    shots = settings.path("screenshots_dir")
    if shots.exists():
        candidates += [("screenshot", p) for p in sorted(shots.glob("*.png"))]
    reports = settings.path("reports_dir")
    if reports.exists():
        candidates += [("report", p) for p in sorted(reports.glob("run_*")) if p.suffix in (".md", ".json")]

    targets = []
    for label, path in candidates:
        if not path.is_file():
            continue
        if not _inside(path, project):
            raise CleanupError(f"refusing to touch {path} because it is outside the project folder")
        targets.append(Target(label, path, path.stat().st_size))
    return targets


def execute(targets: list[Target]) -> tuple[int, list[Target]]:
    deleted = 0
    failed = []
    for target in targets:
        try:
            target.path.unlink()
            deleted += 1
        except OSError:
            failed.append(target)
    return deleted, failed


def _size(total: int) -> str:
    if total < 1024:
        return f"{total} B"
    if total < 1024 * 1024:
        return f"{total / 1024:.1f} KB"
    return f"{total / (1024 * 1024):.1f} MB"


def format_plan(targets: list[Target]) -> str:
    if not targets:
        return "Nothing to clean."
    groups: dict[str, list[Target]] = {}
    for target in targets:
        groups.setdefault(target.label, []).append(target)
    lines = ["These files would be deleted:"]
    for label, items in groups.items():
        lines.append(f"  {label:<14}{len(items):>4} file(s)  {_size(sum(t.size for t in items))}")
    lines.append(f"  {'total':<14}{len(targets):>4} file(s)  {_size(sum(t.size for t in targets))}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Remove test data before publishing")
    parser.add_argument("--yes", action="store_true", help="really delete (without this, only shows the plan)")
    parser.add_argument("--reset-usage", action="store_true", help="also delete the token and page usage log")
    parser.add_argument("--config", type=Path, help="use a different config.yaml")
    args = parser.parse_args(argv)
    try:
        settings = load_config(args.config)
        targets = plan(settings, include_usage=args.reset_usage)
    except (ConfigError, CleanupError) as exc:
        print(f"Error: {exc}")
        return 2

    print(format_plan(targets))
    if not targets:
        return 0
    if not args.yes:
        print("Nothing was deleted. Run again with --yes to delete these files.")
        return 0

    deleted, failed = execute(targets)
    print(f"Deleted {deleted} file(s).")
    for target in failed:
        print(f"Could not delete (in use?): {target.path}")
    if not args.reset_usage:
        print("The usage log was kept, so today's token and page budget still counts.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())