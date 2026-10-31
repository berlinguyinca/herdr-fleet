"""Ansible inventory and extra-vars, generated from fleet-config and fleet.lock."""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence

from herdr_fleet.config import FleetLock, Host
from herdr_fleet.toolchain import CLI_SPECS, toolchain_pins

# Roles that have an Ansible implementation in this release; the rest are planned, not run.
IMPLEMENTED_ROLES: tuple[str, ...] = ("toolchain",)

JsonObject = dict[str, object]

# Same patterns as fleet-config.schema.json (a test keeps them equal). Enforced again here because
# these values end up on an ssh command line: a leading "-" or a space would be read as an option.
ADDRESS_PATTERN = r"^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?|\[[0-9A-Fa-f:.]+\])$"
USER_PATTERN = r"^[a-z_][a-z0-9._-]*$"
HOST_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class InventoryError(Exception):
    """The config cannot be turned into an inventory (unknown limit or jump host)."""


def _check_connection(host: Host) -> None:
    if not re.fullmatch(ADDRESS_PATTERN, host.address):
        raise InventoryError(
            f"hosts.{host.name}.connection.address {host.address!r} is not a hostname, IPv4 "
            "address or bracketed IPv6 address"
        )
    if not re.fullmatch(USER_PATTERN, host.user):
        raise InventoryError(
            f"hosts.{host.name}.connection.user {host.user!r} must match {USER_PATTERN}"
        )


def _jump_host(host: Host, by_name: dict[str, Host]) -> Host:
    assert host.jump_host is not None
    jump = by_name.get(host.jump_host)
    if jump is None:
        raise InventoryError(
            f"hosts.{host.name}.connection.jump_host '{host.jump_host}' is not a host in "
            f"fleet-config.yaml (known: {', '.join(by_name)})"
        )
    if jump.jump_host is not None:
        raise InventoryError(
            f"hosts.{host.name}.connection.jump_host '{jump.name}' itself has a jump_host "
            f"('{jump.jump_host}'); chained jump hosts are not supported"
        )
    _check_connection(jump)
    return jump


def _ssh_address(host: Host) -> str:
    """Bare address for ansible_host; ssh wants IPv6 without the brackets."""
    return host.address.removeprefix("[").removesuffix("]")


def _host_vars(host: Host, by_name: dict[str, Host], local: bool) -> JsonObject:
    entry: JsonObject = {
        "ansible_connection": "local" if local else "ssh",
        "ansible_python_interpreter": "auto_silent",
        "fleet_roles": [r for r in host.roles if r in IMPLEMENTED_ROLES],
        "fleet_os": host.os,
    }
    if not local:
        _check_connection(host)
        entry["ansible_host"] = _ssh_address(host)
        entry["ansible_user"] = host.user
        if host.jump_host is not None:
            jump = _jump_host(host, by_name)
            entry["ansible_ssh_common_args"] = "-J " + shlex.quote(f"{jump.user}@{jump.address}")
    return entry


def build_inventory(hosts: Sequence[Host], *, local: bool, limit: str | None) -> JsonObject:
    by_name = {h.name: h for h in hosts}
    if limit is not None and limit not in by_name:
        raise InventoryError(f"--limit '{limit}' is not a host; known hosts: {', '.join(by_name)}")
    selected = [h for h in hosts if limit is None or h.name == limit]
    return {"all": {"hosts": {h.name: _host_vars(h, by_name, local) for h in selected}}}


def build_extra_vars(lock: FleetLock) -> JsonObject:
    pins = toolchain_pins(lock)
    return {
        "toolchain_node_version": pins.node_version,
        "toolchain_node_sha256": dict(pins.node_sha256),
        "toolchain_clis": [
            {
                "name": spec.name,
                "package": spec.package,
                "version": pins.clis[spec.name],
                "command": spec.command,
            }
            for spec in CLI_SPECS
        ],
    }
