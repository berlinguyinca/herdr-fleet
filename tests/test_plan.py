from __future__ import annotations

from herdr_fleet.config import FleetConfig, Host, load_config
from herdr_fleet.plan import NO_CHANGES_NOTICE, build_plan
from herdr_fleet.roles import ROLE_ORDER
from tests.helpers import CONFIG_EXAMPLE


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


def test_example_config_hosts_plan_the_slice_three_roles_in_order() -> None:
    plan = build_plan(load_config(CONFIG_EXAMPLE))
    host_a = [a.role for a in plan if a.host == "host-a"]
    assert host_a[:5] == ["baseline", "toolchain", "herdr", "herdr-engineering", "pi-runtime"]
    host_b = [a.role for a in plan if a.host == "host-b"]
    assert host_b == [
        "baseline",
        "toolchain",
        "herdr",
        "herdr-engineering",
        "secret-scan",
        "ci-agent",
    ]
