"""herdr-fleet command line: ``validate`` and ``plan``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from herdr_fleet.config import ConfigError, FleetConfig, load_config, load_lock
from herdr_fleet.plan import NO_CHANGES_NOTICE, build_plan


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="herdr-fleet")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("validate", "validate fleet.lock and fleet-config.yaml"),
        ("plan", "validate inputs and list the planned role applications"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--lock", type=Path, required=True, help="path to fleet.lock")
        cmd.add_argument("--config", type=Path, required=True, help="path to fleet-config.yaml")
        cmd.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


def _load_inputs(lock: Path, config: Path) -> tuple[FleetConfig | None, list[str]]:
    errors: list[str] = []
    loaded: FleetConfig | None = None
    try:
        load_lock(lock)
    except ConfigError as exc:
        errors.extend(exc.errors)
    try:
        loaded = load_config(config)
    except ConfigError as exc:
        errors.extend(exc.errors)
    return loaded, errors


def _emit(payload: dict[str, object], text: str, as_json: bool) -> None:
    print(json.dumps(payload, indent=2) if as_json else text)


def _failure(errors: list[str], as_json: bool) -> int:
    payload: dict[str, object] = {"valid": False, "errors": errors}
    _emit(payload, "INVALID\n" + "\n".join(f"  {e}" for e in errors), as_json)
    return 1


def _run_validate(errors: list[str], as_json: bool) -> int:
    if errors:
        return _failure(errors, as_json)
    _emit({"valid": True, "errors": []}, "OK: fleet.lock and fleet-config.yaml are valid", as_json)
    return 0


def _run_plan(config: FleetConfig | None, errors: list[str], as_json: bool) -> int:
    if errors or config is None:
        return _failure(errors, as_json)
    steps = build_plan(config)
    payload: dict[str, object] = {
        "valid": True,
        "changes_made": False,
        "notice": NO_CHANGES_NOTICE,
        "applications": [asdict(s) for s in steps],
    }
    lines = [f"{s.step:>3}. {s.host}: {s.role}" for s in steps]
    _emit(
        payload, "\n".join(["Planned role applications:", *lines, "", NO_CHANGES_NOTICE]), as_json
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config, errors = _load_inputs(args.lock, args.config)
    if args.command == "validate":
        return _run_validate(errors, args.json)
    return _run_plan(config, errors, args.json)


if __name__ == "__main__":
    sys.exit(main())
