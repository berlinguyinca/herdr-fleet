"""Turn the playbook's event stream into per-host, per-role results and exit codes."""

from __future__ import annotations

import difflib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

from herdr_fleet.fingerprint import Difference, Fingerprint, FingerprintError, compare
from herdr_fleet.inventory import IMPLEMENTED_ROLES
from herdr_fleet.toolchain import ToolchainPins

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_DRIFT = 2

_CHANGING = {"changed", "would_change"}
_BROKEN = {"failed", "unreachable"}
_MAX_DIFF_PARTS = 8


class RoleStatus(StrEnum):
    OK = "ok"
    DRIFT = "drift"
    FAILED = "failed"
    NOT_IMPLEMENTED = "not-implemented"


@dataclass(frozen=True)
class TaskEvent:
    host: str
    role: str | None
    task: str
    status: str
    msg: str
    diff: tuple[object, ...]


@dataclass(frozen=True)
class ParsedEvents:
    tasks: tuple[TaskEvent, ...]
    fingerprints: Mapping[str, Fingerprint]
    noise: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PendingChange:
    task: str
    detail: str


@dataclass(frozen=True)
class RoleResult:
    role: str
    status: RoleStatus
    differences: tuple[Difference, ...] = ()
    pending_changes: tuple[PendingChange, ...] = ()
    errors: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "role": self.role,
            "status": self.status.value,
            "differences": [
                {"field": d.field, "expected": d.expected, "actual": d.actual, "message": d.message}
                for d in self.differences
            ],
            "pending_changes": [{"task": c.task, "detail": c.detail} for c in self.pending_changes],
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class HostReport:
    host: str
    roles: tuple[RoleResult, ...]
    fingerprint: Fingerprint | None
    errors: tuple[str, ...]
    changed_count: int = 0

    @property
    def status(self) -> str:
        if self.errors or any(r.status is RoleStatus.FAILED for r in self.roles):
            return "failed"
        return "drift" if any(r.status is RoleStatus.DRIFT for r in self.roles) else "ok"

    def to_json(self) -> dict[str, object]:
        return {
            "host": self.host,
            "status": self.status,
            "changed": self.changed_count,
            "errors": list(self.errors),
            "roles": [r.to_json() for r in self.roles],
            "fingerprint": self.fingerprint.to_json() if self.fingerprint else None,
        }


def summarize_diff(diffs: Sequence[object]) -> str:
    """One line describing what Ansible's ``--diff`` said would change."""
    parts: list[str] = []
    for item in diffs:
        if not isinstance(item, dict):
            continue
        before, after = item.get("before"), item.get("after")
        if isinstance(before, dict) and isinstance(after, dict):
            path = after.get("path", "")
            parts.extend(
                f"{path}: {key} {before.get(key, '-')} -> {value}"
                for key, value in after.items()
                if key != "path" and before.get(key) != value
            )
        elif isinstance(before, str) and isinstance(after, str):
            lines = difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
            parts.extend(ln for ln in lines if ln[:1] in "+-" and ln[:3] not in ("+++", "---"))
    return "; ".join(parts[:_MAX_DIFF_PARTS])


def parse_events(stdout: str) -> ParsedEvents:
    tasks: list[TaskEvent] = []
    fingerprints: dict[str, Fingerprint] = {}
    noise: list[str] = []
    for line in stdout.splitlines():
        event = _decode(line)
        if event is None:
            if line.strip():
                noise.append(line.strip())
        elif event.get("event") == "task":
            tasks.append(_task_event(event))
        elif event.get("event") == "fingerprint":
            try:
                fingerprints[str(event["host"])] = Fingerprint.from_json(
                    cast(Mapping[str, object], event["fingerprint"])
                )
            except FingerprintError as exc:
                noise.append(str(exc))
    return ParsedEvents(tuple(tasks), fingerprints, noise)


def _decode(line: str) -> dict[str, object] | None:
    try:
        decoded = json.loads(line)
    except json.JSONDecodeError:
        return None
    return cast("dict[str, object]", decoded) if isinstance(decoded, dict) else None


def _task_event(event: Mapping[str, object]) -> TaskEvent:
    role = event.get("role")
    return TaskEvent(
        host=str(event["host"]),
        role=str(role) if role else None,
        task=str(event["task"]),
        status=str(event["status"]),
        msg=str(event.get("msg", "")),
        diff=tuple(cast("list[object]", event.get("diff") or [])),
    )


def _role_result(
    role: str, events: Sequence[TaskEvent], differences: Sequence[Difference]
) -> RoleResult:
    errors = tuple(f"{e.task}: {e.msg}" for e in events if e.status in _BROKEN)
    pending = tuple(
        PendingChange(e.task, summarize_diff(e.diff)) for e in events if e.status in _CHANGING
    )
    if errors:
        status = RoleStatus.FAILED
    elif pending or differences:
        status = RoleStatus.DRIFT
    else:
        status = RoleStatus.OK
    return RoleResult(role, status, tuple(differences), pending, errors)


def _host_report(
    host: str,
    roles: Sequence[str],
    events: Sequence[TaskEvent],
    fingerprint: Fingerprint | None,
    pins: ToolchainPins,
    expect_fingerprint: bool,
) -> HostReport:
    errors = [f"{e.task}: {e.msg}" for e in events if e.role is None and e.status in _BROKEN]
    results: list[RoleResult] = []
    for role in roles:
        if role not in IMPLEMENTED_ROLES:
            results.append(RoleResult(role, RoleStatus.NOT_IMPLEMENTED))
            continue
        differences = compare(fingerprint, pins) if fingerprint and role == "toolchain" else []
        results.append(_role_result(role, [e for e in events if e.role == role], differences))
    broken = errors or any(r.status is RoleStatus.FAILED for r in results)
    if expect_fingerprint and fingerprint is None and not broken and "toolchain" in roles:
        errors.append("no fingerprint was reported for this host")
    return HostReport(
        host,
        tuple(results),
        fingerprint,
        tuple(errors),
        changed_count=sum(1 for e in events if e.status == "changed"),
    )


def build_reports(
    parsed: ParsedEvents,
    host_roles: Mapping[str, Sequence[str]],
    pins: ToolchainPins,
    *,
    expect_fingerprint: bool = True,
) -> list[HostReport]:
    return [
        _host_report(
            host,
            roles,
            [e for e in parsed.tasks if e.host == host],
            parsed.fingerprints.get(host),
            pins,
            expect_fingerprint,
        )
        for host, roles in host_roles.items()
    ]


def check_exit_code(reports: Sequence[HostReport]) -> int:
    """0 no drift, 2 drift, 1 error (errors outrank drift)."""
    if any(r.status == "failed" for r in reports):
        return EXIT_ERROR
    return EXIT_DRIFT if any(r.status == "drift" for r in reports) else EXIT_OK


def apply_exit_code(reports: Sequence[HostReport]) -> int:
    """After apply the verification run must show no drift: any mismatch is a failure."""
    return EXIT_OK if all(r.status == "ok" for r in reports) else EXIT_ERROR
