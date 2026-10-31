"""Inventory and extra-vars generation from the real example config and lock."""

from __future__ import annotations

import shlex

import pytest

from herdr_fleet.config import Host, load_config, load_lock
from herdr_fleet.inventory import (
    ADDRESS_PATTERN,
    HOST_NAME_PATTERN,
    USER_PATTERN,
    InventoryError,
    build_extra_vars,
    build_inventory,
)
from tests.helpers import CONFIG_EXAMPLE, LOCK_EXAMPLE


def hosts_of(inventory: dict[str, object]) -> dict[str, dict[str, object]]:
    all_group = inventory["all"]
    assert isinstance(all_group, dict)
    hosts = all_group["hosts"]
    assert isinstance(hosts, dict)
    return hosts


def test_ssh_inventory_maps_address_user_and_roles() -> None:
    config = load_config(CONFIG_EXAMPLE)
    hosts = hosts_of(build_inventory(config.hosts, local=False, limit=None))
    host_a = hosts["host-a"]
    assert host_a["ansible_host"] == "host-a.example.com"
    assert host_a["ansible_user"] == "fleet"
    assert host_a["ansible_connection"] == "ssh"
    assert "ansible_ssh_common_args" not in host_a
    # only roles that exist in this slice are handed to the playbook
    assert host_a["fleet_roles"] == ["toolchain"]
    assert host_a["fleet_os"] == "debian"


def test_jump_host_becomes_proxyjump() -> None:
    config = load_config(CONFIG_EXAMPLE)
    hosts = hosts_of(build_inventory(config.hosts, local=False, limit=None))
    assert hosts["host-b"]["ansible_ssh_common_args"] == "-J fleet@host-a.example.com"


def test_local_connection_drops_ssh_details() -> None:
    config = load_config(CONFIG_EXAMPLE)
    hosts = hosts_of(build_inventory(config.hosts, local=True, limit=None))
    assert {h["ansible_connection"] for h in hosts.values()} == {"local"}
    assert all("ansible_host" not in h and "ansible_user" not in h for h in hosts.values())
    assert all("ansible_ssh_common_args" not in h for h in hosts.values())


def test_limit_keeps_one_host_but_still_resolves_its_jump_host() -> None:
    config = load_config(CONFIG_EXAMPLE)
    hosts = hosts_of(build_inventory(config.hosts, local=False, limit="host-b"))
    assert list(hosts) == ["host-b"]
    assert "host-a.example.com" in str(hosts["host-b"]["ansible_ssh_common_args"])


def test_unknown_limit_names_the_known_hosts() -> None:
    config = load_config(CONFIG_EXAMPLE)
    with pytest.raises(InventoryError, match=r"host-z.*host-a, host-b"):
        build_inventory(config.hosts, local=False, limit="host-z")


def test_unknown_jump_host_is_an_error() -> None:
    config = load_config(CONFIG_EXAMPLE)
    broken = tuple(
        h if h.jump_host is None else type(h)(**{**h.__dict__, "jump_host": "ghost"})
        for h in config.hosts
    )
    with pytest.raises(InventoryError, match="jump_host 'ghost'"):
        build_inventory(broken, local=False, limit=None)


def test_extra_vars_come_from_the_lock() -> None:
    extra = build_extra_vars(load_lock(LOCK_EXAMPLE))
    assert extra["toolchain_node_version"] == "22.11.0"
    sha = extra["toolchain_node_sha256"]
    assert isinstance(sha, dict) and set(sha) == {"linux-x64", "darwin-arm64"}
    assert extra["toolchain_clis"] == [
        {
            "name": "pi-cli",
            "package": "@earendil-works/pi-coding-agent",
            "version": "1.2.3",
            "command": "pi",
        },
        {"name": "codex", "package": "@openai/codex", "version": "0.9.0", "command": "codex"},
    ]


def test_unused_lock_pins_are_not_passed() -> None:
    extra = build_extra_vars(load_lock(LOCK_EXAMPLE))
    assert "gitleaks" not in str(extra) and "woodpecker" not in str(extra)


def host(name: str, **over: object) -> Host:
    fields: dict[str, object] = {
        "name": name,
        "os": "debian",
        "roles": ("toolchain",),
        "address": f"{name}.example.com",
        "user": "fleet",
        "jump_host": None,
    }
    return Host(**{**fields, **over})  # type: ignore[arg-type]


@pytest.mark.parametrize("address", ["x -o ProxyCommand=id", "-oProxyCommand=id", "a;b", ""])
def test_inventory_refuses_option_injection_in_address(address: str) -> None:
    with pytest.raises(InventoryError, match="address"):
        build_inventory([host("a", address=address)], local=False, limit=None)


@pytest.mark.parametrize("user", ["-oProxyCommand=id", "a b", "A;b"])
def test_inventory_refuses_option_injection_in_user(user: str) -> None:
    with pytest.raises(InventoryError, match="user"):
        build_inventory([host("a", user=user)], local=False, limit=None)


def test_jump_spec_is_shell_quoted_and_cannot_start_with_a_dash() -> None:
    jump = host("j", address="[fe80::1]")
    inv = hosts_of(build_inventory([jump, host("t", jump_host="j")], local=False, limit=None))
    args = str(inv["t"]["ansible_ssh_common_args"])
    assert args == "-J fleet@[fe80::1]" or args == "-J 'fleet@[fe80::1]'"
    assert shlex.split(args) == ["-J", "fleet@[fe80::1]"]
    assert inv["j"]["ansible_host"] == "fe80::1"


def test_chained_jump_hosts_are_refused() -> None:
    hosts = [host("a", jump_host="b"), host("b", jump_host="c"), host("c")]
    with pytest.raises(InventoryError, match="chain"):
        build_inventory(hosts, local=False, limit="a")


def test_local_connection_ignores_connection_details() -> None:
    hosts_ = build_inventory([host("a", address="-bad")], local=True, limit=None)
    assert "a" in hosts_of(hosts_)


def test_schema_and_inventory_agree_on_the_patterns() -> None:
    from herdr_fleet.config import _schema

    conn = _schema("fleet-config.schema.json")["$defs"]["host"]["properties"]["connection"]  # type: ignore[index]
    props = conn["properties"]  # type: ignore[index]
    assert props["address"]["pattern"] == ADDRESS_PATTERN  # type: ignore[index]
    assert props["user"]["pattern"] == USER_PATTERN  # type: ignore[index]
    assert props["jump_host"]["pattern"] == HOST_NAME_PATTERN  # type: ignore[index]
