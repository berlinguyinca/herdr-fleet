"""Inventory and extra-vars generation from the real example config and lock."""

from __future__ import annotations

import pytest

from herdr_fleet.config import load_config, load_lock
from herdr_fleet.inventory import InventoryError, build_extra_vars, build_inventory
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
    assert hosts["host-b"]["ansible_ssh_common_args"] == "-o ProxyJump=fleet@host-a.example.com"


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
