"""Drift-report parsing and exit codes, from event lines in the callback's real format."""

from __future__ import annotations

import json

import pytest

from herdr_fleet.config import load_lock
from herdr_fleet.fingerprint import Difference, Fingerprint
from herdr_fleet.herdr import herdr_pins
from herdr_fleet.herdr_engineering import HerdrEngineeringPins
from herdr_fleet.inventory import IMPLEMENTED_ROLES
from herdr_fleet.pi_runtime import PiRuntimePins
from herdr_fleet.pins import FleetPins
from herdr_fleet.report import (
    EXIT_DRIFT,
    EXIT_ERROR,
    EXIT_OK,
    NOT_ATTEMPTED,
    ROLE_COMPARATORS,
    HostReport,
    RoleStatus,
    apply_exit_code,
    build_reports,
    check_exit_code,
    expects_fingerprint,
    parse_events,
    summarize_diff,
)
from herdr_fleet.toolchain import ToolchainPins, toolchain_pins
from tests.helpers import LOCK_EXAMPLE


def fleet_pins_of(pins: ToolchainPins) -> FleetPins:
    return FleetPins(
        toolchain=pins,
        herdr=herdr_pins(load_lock(LOCK_EXAMPLE)),
        herdr_engineering=HerdrEngineeringPins(
            revision="1" * 40, repository=None, gateway_url=None, control_plane_url=None
        ),
        pi_runtime=PiRuntimePins(revision="2" * 40, repository=None),
    )


HOST_ROLES = {"host-a": ("baseline", "toolchain")}


@pytest.fixture
def pins() -> ToolchainPins:
    return toolchain_pins(load_lock(LOCK_EXAMPLE))


def task(status: str, name: str, *, host: str = "host-a", role: str | None = "toolchain") -> str:
    return json.dumps(
        {
            "event": "task",
            "host": host,
            "role": role,
            "task": name,
            "status": status,
            "msg": "",
            "diff": [],
        }
    )


def fingerprint(pins: ToolchainPins, *, node: str | None, pi: str | None) -> str:
    def cli(version: str | None, cmd: str) -> dict[str, object]:
        shim = f"/h/.local/bin/{cmd}"
        return {
            "version": version,
            "shim_path": shim,
            "shim_present": version is not None,
            "resolved_path": shim if version else None,
            "target_path": f"/h/toolchain/bin/{cmd}",
            "target_present": version is not None,
            "shim_runs": version is not None,
        }

    return json.dumps(
        {
            "event": "fingerprint",
            "host": "host-a",
            "fingerprint": {
                "system": "Linux",
                "machine": "x86_64",
                "node_version": node,
                "clis": {"pi-cli": cli(pi, "pi"), "codex": cli(pins.clis["codex"], "codex")},
            },
        }
    )


def reports(lines: list[str], pins: ToolchainPins) -> list[HostReport]:
    return build_reports(parse_events("\n".join(lines)), HOST_ROLES, fleet_pins_of(pins))


def test_parse_ignores_non_event_lines() -> None:
    parsed = parse_events("[WARNING]: something\n" + task("ok", "x") + "\nnot json {\n")
    assert len(parsed.tasks) == 1 and parsed.noise == ["[WARNING]: something", "not json {"]


def test_clean_host_is_ok_and_exit_zero(pins: ToolchainPins) -> None:
    (report,) = reports([task("ok", "probe"), fingerprint(pins, node="22.11.0", pi="1.2.3")], pins)
    toolchain = next(r for r in report.roles if r.role == "toolchain")
    assert toolchain.status is RoleStatus.OK
    assert check_exit_code([report]) == EXIT_OK
    assert apply_exit_code([report]) == EXIT_OK


def test_fresh_host_is_drift_naming_node_and_cli(pins: ToolchainPins) -> None:
    (report,) = reports(
        [
            task("changed", "directories exist"),
            task("would_change", "move node 22.11.0 into place"),
            fingerprint(pins, node=None, pi=None),
        ],
        pins,
    )
    toolchain = next(r for r in report.roles if r.role == "toolchain")
    assert toolchain.status is RoleStatus.DRIFT
    assert [d.field for d in toolchain.differences] == [
        "node",
        "cli pi-cli",
        "shim pi-cli",
    ]
    assert [c.task for c in toolchain.pending_changes] == [
        "directories exist",
        "move node 22.11.0 into place",
    ]
    assert check_exit_code([report]) == EXIT_DRIFT
    assert apply_exit_code([report]) == EXIT_ERROR


def test_fingerprint_difference_alone_is_drift(pins: ToolchainPins) -> None:
    (report,) = reports([fingerprint(pins, node="20.1.0", pi="1.2.3")], pins)
    assert check_exit_code([report]) == EXIT_DRIFT


def test_unimplemented_roles_are_reported_not_hidden(pins: ToolchainPins) -> None:
    (report,) = reports([fingerprint(pins, node="22.11.0", pi="1.2.3")], pins)
    baseline = next(r for r in report.roles if r.role == "baseline")
    assert baseline.status is RoleStatus.NOT_IMPLEMENTED
    assert check_exit_code([report]) == EXIT_OK


def test_failed_task_is_an_error_not_drift(pins: ToolchainPins) -> None:
    failed = json.loads(task("failed", "download node"))
    failed["msg"] = "The checksum did not match"
    (report,) = reports([json.dumps(failed)], pins)
    toolchain = next(r for r in report.roles if r.role == "toolchain")
    assert toolchain.status is RoleStatus.FAILED
    assert toolchain.errors == ("download node: The checksum did not match",)
    assert check_exit_code([report]) == EXIT_ERROR
    assert apply_exit_code([report]) == EXIT_ERROR


def test_unreachable_host_is_a_host_error(pins: ToolchainPins) -> None:
    unreachable = json.loads(task("unreachable", "Gather facts", role=None))
    unreachable["msg"] = "ssh: connect to host host-a port 22: timed out"
    (report,) = reports([json.dumps(unreachable)], pins)
    assert report.errors == ("Gather facts: ssh: connect to host host-a port 22: timed out",)
    assert check_exit_code([report]) == EXIT_ERROR


def test_host_without_fingerprint_or_failure_is_an_error(pins: ToolchainPins) -> None:
    (report,) = reports([task("ok", "probe")], pins)
    assert report.errors == ("no fingerprint was reported for this host",)
    assert check_exit_code([report]) == EXIT_ERROR


def test_changed_count_counts_real_changes_only(pins: ToolchainPins) -> None:
    (report,) = reports(
        [task("changed", "a"), task("ok", "b"), fingerprint(pins, node="22.11.0", pi="1.2.3")],
        pins,
    )
    assert report.changed_count == 1


def test_error_outranks_drift(pins: ToolchainPins) -> None:
    (drifting,) = reports([fingerprint(pins, node=None, pi=None)], pins)
    broken = HostReport("host-b", (), None, ("boom",))
    assert check_exit_code([drifting, broken]) == EXIT_ERROR


def test_json_payload_shape(pins: ToolchainPins) -> None:
    (report,) = reports([fingerprint(pins, node=None, pi="1.2.3")], pins)
    payload = report.to_json()
    assert payload["host"] == "host-a" and payload["status"] == "drift"
    roles = payload["roles"]
    assert isinstance(roles, list)
    toolchain = next(r for r in roles if r["role"] == "toolchain")
    assert toolchain["differences"][0] == {
        "field": "node",
        "expected": "22.11.0",
        "actual": "missing",
        "message": "node: expected 22.11.0, found missing",
    }
    json.dumps(payload)


def test_summarize_state_diff() -> None:
    diff = [
        {
            "before": {"path": "/h/bin", "state": "absent"},
            "after": {"path": "/h/bin", "state": "directory"},
        }
    ]
    assert summarize_diff(diff) == "/h/bin: state absent -> directory"


def test_summarize_text_diff() -> None:
    diff = [{"before": "a\nb\n", "after": "a\nc\n", "before_header": "x", "after_header": "x"}]
    assert summarize_diff(diff) == "-b; +c"


def test_summarize_empty() -> None:
    assert summarize_diff([]) == ""


def test_host_with_no_events_is_not_attempted_not_missing_fingerprint(pins: ToolchainPins) -> None:
    ran, skipped = build_reports(
        parse_events(task("failed", "directories exist", host="host-a")),
        {"host-a": ("toolchain",), "host-b": ("toolchain",)},
        fleet_pins_of(pins),
    )
    assert ran.status == "failed"
    assert skipped.status == "not-attempted" and not skipped.attempted
    assert skipped.errors == (NOT_ATTEMPTED,)
    assert "no fingerprint" not in " ".join(skipped.errors)
    assert skipped.to_json()["attempted"] is False
    assert check_exit_code([skipped]) == EXIT_ERROR
    assert apply_exit_code([skipped]) == EXIT_ERROR


def test_toolchain_comparator_is_registered_for_every_implemented_role_that_needs_one() -> None:
    assert "toolchain" in ROLE_COMPARATORS
    assert set(ROLE_COMPARATORS) <= set(IMPLEMENTED_ROLES)
    assert set(IMPLEMENTED_ROLES) == set(ROLE_COMPARATORS)


def test_differences_come_from_the_registered_comparator(pins: ToolchainPins) -> None:
    def always_differs(fp: Fingerprint, fleet: FleetPins) -> list[Difference]:
        return [Difference("registry", "dispatch", "used")]

    parsed = parse_events(fingerprint(pins, node="22.11.0", pi="1.2.3"))
    (report,) = build_reports(
        parsed, HOST_ROLES, fleet_pins_of(pins), comparators={"toolchain": always_differs}
    )
    toolchain = next(r for r in report.roles if r.role == "toolchain")
    assert [d.field for d in toolchain.differences] == ["registry"]


def test_missing_fingerprint_is_an_error_whenever_any_implemented_role_is_on_the_host(
    pins: ToolchainPins,
) -> None:
    only_unimplemented = build_reports(
        parse_events(task("ok", "probe")), {"host-a": ("baseline",)}, fleet_pins_of(pins)
    )
    assert only_unimplemented[0].errors == ()
    assert expects_fingerprint(("secret-scan", "toolchain")) is True
    assert expects_fingerprint(("secret-scan",)) is False


def test_fingerprint_event_with_extra_sections_still_compares_toolchain(
    pins: ToolchainPins,
) -> None:
    event = json.loads(fingerprint(pins, node="22.11.0", pi="1.2.3"))
    event["fingerprint"]["herdr"] = {"version": "0.9.1"}
    (report,) = build_reports(parse_events(json.dumps(event)), HOST_ROLES, fleet_pins_of(pins))
    assert check_exit_code([report]) == EXIT_OK
    assert report.to_json()["fingerprint"]["herdr"] == {"version": "0.9.1"}  # type: ignore[index]


def test_herdr_role_is_compared_through_the_registry(pins: ToolchainPins) -> None:
    event = json.dumps(
        {
            "event": "fingerprint",
            "host": "host-a",
            "fingerprint": {
                "system": "Linux",
                "machine": "x86_64",
                "herdr": {
                    "path": "/h/.local/bin/herdr",
                    "present": False,
                    "version": None,
                    "sha256": None,
                    "resolved_path": None,
                    "server_version": None,
                },
            },
        }
    )
    (report,) = build_reports(parse_events(event), {"host-a": ("herdr",)}, fleet_pins_of(pins))
    (herdr,) = report.roles
    assert herdr.role == "herdr" and herdr.status is RoleStatus.DRIFT
    assert [d.field for d in herdr.differences] == ["herdr"]
    assert "found missing" in herdr.differences[0].message
