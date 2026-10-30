"""Ansible inventory and extra-vars, generated from fleet-config and fleet.lock."""

from __future__ import annotations

from collections.abc import Sequence

from herdr_fleet.config import FleetLock, Host
from herdr_fleet.toolchain import CLI_SPECS, toolchain_pins

# Roles that have an Ansible implementation in this release; the rest are planned, not run.
IMPLEMENTED_ROLES: tuple[str, ...] = ("toolchain",)

JsonObject = dict[str, object]


class InventoryError(Exception):
    """The config cannot be turned into an inventory (unknown limit or jump host)."""


def _jump_args(host: Host, by_name: dict[str, Host]) -> str:
    assert host.jump_host is not None
    jump = by_name.get(host.jump_host)
    if jump is None:
        raise InventoryError(
            f"hosts.{host.name}.connection.jump_host '{host.jump_host}' is not a host in "
            f"fleet-config.yaml (known: {', '.join(by_name)})"
        )
    return f"-o ProxyJump={jump.user}@{jump.address}"


def _host_vars(host: Host, by_name: dict[str, Host], local: bool) -> JsonObject:
    entry: JsonObject = {
        "ansible_connection": "local" if local else "ssh",
        "ansible_python_interpreter": "auto_silent",
        "fleet_roles": [r for r in host.roles if r in IMPLEMENTED_ROLES],
        "fleet_os": host.os,
    }
    if not local:
        entry["ansible_host"] = host.address
        entry["ansible_user"] = host.user
        if host.jump_host is not None:
            entry["ansible_ssh_common_args"] = _jump_args(host, by_name)
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
