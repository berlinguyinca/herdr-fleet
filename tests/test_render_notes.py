"""Informational role notes are rendered and serialized but never change the status."""

from __future__ import annotations

from herdr_fleet.render import render_hosts
from herdr_fleet.report import HostReport, RoleResult, RoleStatus


def test_notes_are_rendered_and_in_json_without_drift() -> None:
    role = RoleResult("herdr", RoleStatus.OK, notes=("herdr is not on PATH",))
    report = HostReport("h", (role,), None, ())
    assert report.status == "ok"
    text = render_hosts([report])
    assert "    notes:\n      - herdr is not on PATH" in text
    assert role.to_json()["notes"] == ["herdr is not on PATH"]
