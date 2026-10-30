"""herdr-fleet command line: ``validate``, ``plan`` (optionally ``--check``) and ``apply``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, replace
from pathlib import Path

from herdr_fleet.config import ConfigError, FleetConfig, FleetLock, load_config, load_lock
from herdr_fleet.inventory import InventoryError, build_extra_vars, build_inventory
from herdr_fleet.plan import NO_CHANGES_NOTICE, build_plan
from herdr_fleet.render import render_hosts
from herdr_fleet.report import (
    EXIT_ERROR,
    EXIT_OK,
    HostReport,
    apply_exit_code,
    build_reports,
    check_exit_code,
    parse_events,
)
from herdr_fleet.runner import Mode, RunResult, run_playbook
from herdr_fleet.toolchain import toolchain_pins

APPLY_LIMITS = (
    "NOT implemented yet (slice 4): staggering by host activity, session-awareness, rollback "
    "and notifications. apply converges hosts one at a time and verifies each against fleet.lock."
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="herdr-fleet")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("validate", "validate fleet.lock and fleet-config.yaml"),
        (
            "plan",
            "list the planned role applications; with --check, report drift on every host "
            "(exit 0 no drift, 2 drift, 1 error)",
        ),
        ("apply", f"converge hosts to fleet.lock, then verify. {APPLY_LIMITS}"),
    ):
        cmd = sub.add_parser(name, help=help_text, description=help_text)
        cmd.add_argument("--lock", type=Path, required=True, help="path to fleet.lock")
        cmd.add_argument("--config", type=Path, required=True, help="path to fleet-config.yaml")
        cmd.add_argument("--json", action="store_true", help="machine-readable output")
        if name != "validate":
            cmd.add_argument(
                "--connection",
                choices=("ssh", "local"),
                default="ssh",
                help="local: run every targeted host on this machine (container tests, "
                "single-host use); only with --check or apply",
            )
            cmd.add_argument("--limit", metavar="HOST", help="only this host (--check or apply)")
        if name == "plan":
            cmd.add_argument(
                "--check", action="store_true", help="run roles in check mode and report drift"
            )
        if name == "apply":
            cmd.add_argument(
                "--yes",
                action="store_true",
                help="do not ask for confirmation (required without a TTY)",
            )
    return parser


def _load_inputs(
    lock: Path, config: Path
) -> tuple[FleetLock | None, FleetConfig | None, list[str]]:
    errors: list[str] = []
    loaded_lock: FleetLock | None = None
    loaded: FleetConfig | None = None
    try:
        loaded_lock = load_lock(lock)
    except ConfigError as exc:
        errors.extend(exc.errors)
    try:
        loaded = load_config(config)
    except ConfigError as exc:
        errors.extend(exc.errors)
    return loaded_lock, loaded, errors


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


def _host_roles(config: FleetConfig, limit: str | None) -> dict[str, tuple[str, ...]]:
    return {h.name: h.roles for h in config.hosts if limit is None or h.name == limit}


def _runner_errors(result: RunResult, reports: list[HostReport]) -> list[str]:
    """ansible-playbook failed but no host reported why (bad ssh config, missing binary, ...)."""
    if result.returncode == 0 or any(r.status == "failed" for r in reports):
        return []
    tail = (result.stderr.strip() or result.stdout.strip()).splitlines()[-5:]
    return [f"ansible-playbook exited {result.returncode}: " + " | ".join(tail)]


def _emit_reports(
    mode: str, reports: list[HostReport], errors: list[str], code: int, as_json: bool, note: str
) -> None:
    payload: dict[str, object] = {
        "mode": mode,
        "exit_code": code,
        "errors": errors,
        "changes_made": sum(r.changed_count for r in reports),
        "hosts": [r.to_json() for r in reports],
    }
    text = "\n".join([render_hosts(reports), *(f"error: {e}" for e in errors), "", note]).strip()
    _emit(payload, text, as_json)


def _prepare(
    args: argparse.Namespace, lock: FleetLock, config: FleetConfig
) -> tuple[dict[str, object], dict[str, object]]:
    inventory = build_inventory(config.hosts, local=args.connection == "local", limit=args.limit)
    return inventory, build_extra_vars(lock)


def _run_check(args: argparse.Namespace, lock: FleetLock, config: FleetConfig) -> int:
    try:
        inventory, extra_vars = _prepare(args, lock, config)
    except InventoryError as exc:
        return _failure([str(exc)], args.json)
    result = run_playbook(inventory, extra_vars, Mode.CHECK)
    reports = build_reports(
        parse_events(result.stdout), _host_roles(config, args.limit), toolchain_pins(lock)
    )
    errors = _runner_errors(result, reports)
    code = EXIT_ERROR if errors else check_exit_code(reports)
    verdict = {0: "no drift", 2: "drift found", 1: "errors"}[code]
    _emit_reports(
        "check", reports, errors, code, args.json, f"plan --check: {verdict} (exit {code})"
    )
    return code


def _confirmed(args: argparse.Namespace) -> bool:
    if args.yes:
        return True
    if not sys.stdin.isatty():
        return False
    return input("Apply fleet.lock to the selected hosts? Type 'yes' to continue: ") == "yes"


def _run_apply(args: argparse.Namespace, lock: FleetLock, config: FleetConfig) -> int:
    if not _confirmed(args):
        return _failure(
            ["apply changes software on hosts; pass --yes (or answer the prompt on a TTY)"],
            args.json,
        )
    try:
        inventory, extra_vars = _prepare(args, lock, config)
    except InventoryError as exc:
        return _failure([str(exc)], args.json)
    roles, pins = _host_roles(config, args.limit), toolchain_pins(lock)
    applied = run_playbook(inventory, extra_vars, Mode.APPLY)
    applied_reports = build_reports(
        parse_events(applied.stdout), roles, pins, expect_fingerprint=False
    )
    errors = _runner_errors(applied, applied_reports)
    if errors or any(r.status == "failed" for r in applied_reports):
        _emit_reports(
            "apply", applied_reports, errors, EXIT_ERROR, args.json, "apply failed; not verified"
        )
        return EXIT_ERROR
    verified = run_playbook(inventory, extra_vars, Mode.VERIFY)
    reports = build_reports(parse_events(verified.stdout), roles, pins)
    changed = {r.host: r.changed_count for r in applied_reports}
    reports = [replace(r, changed_count=changed[r.host]) for r in reports]
    errors = _runner_errors(verified, reports)
    code = EXIT_ERROR if errors else apply_exit_code(reports)
    total = sum(changed.values())
    note = (
        f"apply: {total} change(s) made; verification {'passed' if code == EXIT_OK else 'FAILED'}"
    )
    _emit_reports("apply", reports, errors, code, args.json, f"{note}\n{APPLY_LIMITS}")
    return code


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    lock, config, errors = _load_inputs(args.lock, args.config)
    if args.command == "validate":
        return _run_validate(errors, args.json)
    if args.command == "plan" and not args.check:
        if args.connection != "ssh" or args.limit:
            return _failure(["--connection and --limit only apply with --check"], args.json)
        return _run_plan(config, errors, args.json)
    if errors or config is None or lock is None:
        return _failure(errors, args.json)
    return (
        _run_check(args, lock, config) if args.command == "plan" else _run_apply(args, lock, config)
    )


if __name__ == "__main__":
    sys.exit(main())
