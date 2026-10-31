"""Plain-text rendering of host reports."""

from __future__ import annotations

from collections.abc import Sequence

from herdr_fleet.report import HostReport, RoleResult, RoleStatus


def _role_lines(role: RoleResult) -> list[str]:
    if role.status is RoleStatus.NOT_IMPLEMENTED:
        return [f"  {role.role}: not implemented in this release (skipped)"]
    lines = [f"  {role.role}: {role.status.value}"]
    if role.differences:
        lines.append("    differs from fleet.lock:")
        lines.extend(f"      - {d.message}" for d in role.differences)
    if role.pending_changes:
        lines.append("    pending changes:")
        lines.extend(
            f"      - {c.task}" + (f" ({c.detail})" if c.detail else "")
            for c in role.pending_changes
        )
    if role.errors:
        lines.append("    errors:")
        lines.extend(f"      - {e}" for e in role.errors)
    return lines


def render_hosts(reports: Sequence[HostReport]) -> str:
    lines: list[str] = []
    for report in reports:
        lines.append(f"{report.host}: {report.status.upper().replace('-', ' ')}")
        lines.extend(f"  error: {e}" for e in report.errors)
        for role in report.roles:
            lines.extend(_role_lines(role))
    return "\n".join(lines)
