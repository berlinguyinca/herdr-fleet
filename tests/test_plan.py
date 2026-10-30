from __future__ import annotations

from herdr_fleet.config import FleetConfig, Host
from herdr_fleet.plan import NO_CHANGES_NOTICE, build_plan
from herdr_fleet.roles import ROLE_ORDER


def _host(name: str, roles: tuple[str, ...]) -> Host:
    return Host(name, "debian", roles, f"{name}.example.com", "fleet", None)


def _config(*hosts: Host) -> FleetConfig:
    return FleetConfig(hosts, None, "tunnel", None, (), None)


def test_roles_sorted_into_spec_order_per_host() -> None:
    shuffled = ("ci-agent", "herdr", "baseline", "toolchain", "ci-server")
    plan = build_plan(_config(_host("host-a", shuffled)))
    assert [a.role for a in plan] == ["baseline", "toolchain", "herdr", "ci-server", "ci-agent"]


def test_full_role_table_order_matches_spec() -> None:
    plan = build_plan(_config(_host("host-a", tuple(reversed(ROLE_ORDER)))))
    assert [a.role for a in plan] == [
        "baseline",
        "toolchain",
        "herdr",
        "herdr-engineering",
        "pi-runtime",
        "secret-scan",
        "controller",
        "gateway",
        "ci-server",
        "ci-agent",
    ]


def test_hosts_keep_config_order_and_steps_are_numbered() -> None:
    plan = build_plan(
        _config(_host("host-b", ("herdr", "baseline")), _host("host-a", ("baseline",)))
    )
    assert [(a.host, a.role) for a in plan] == [
        ("host-b", "baseline"),
        ("host-b", "herdr"),
        ("host-a", "baseline"),
    ]
    assert [a.step for a in plan] == [1, 2, 3]


def test_notice_is_plain() -> None:
    assert "no changes made" in NO_CHANGES_NOTICE
    assert "check-mode execution not implemented yet (slice 2)" in NO_CHANGES_NOTICE
