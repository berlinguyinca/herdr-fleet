"""Ansible inventory and extra-vars, generated from fleet-config and fleet.lock."""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from importlib import resources

from herdr_fleet.baseline import BaselinePins
from herdr_fleet.config import FleetConfig, FleetLock, Host
from herdr_fleet.herdr import HERDR_ASSETS, HerdrPins
from herdr_fleet.herdr_engineering import HerdrEngineeringPins
from herdr_fleet.pi_runtime import PiRuntimePins
from herdr_fleet.pins import fleet_pins
from herdr_fleet.toolchain import CLI_SPECS, ToolchainPins

# Roles that have an Ansible implementation in this release; the rest are planned, not run.
IMPLEMENTED_ROLES: tuple[str, ...] = (
    "baseline",
    "toolchain",
    "herdr",
    "herdr-engineering",
    "pi-runtime",
)

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


def _toolchain_vars(pins: ToolchainPins) -> JsonObject:
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


def _herdr_vars(pins: HerdrPins) -> JsonObject:
    return {
        "herdr_version": pins.version,
        "herdr_sha256": dict(pins.sha256),
        "herdr_assets": dict(HERDR_ASSETS),
    }


def _herdr_engineering_vars(pins: HerdrEngineeringPins) -> JsonObject:
    if pins.repository is None:  # no host has the role: nothing will read these
        return {}
    return {
        "herdr_engineering_revision": pins.revision,
        "herdr_engineering_repository": pins.repository,
        "herdr_engineering_gateway_url": pins.gateway_url,
        "herdr_engineering_control_plane_url": pins.control_plane_url,
        "fleet_stage_release_src": str(resources.files("herdr_fleet").joinpath("stage_release.py")),
    }


def _pi_runtime_vars(pins: PiRuntimePins) -> JsonObject:
    if pins.repository is None:  # no host has the role: nothing will read these
        return {}
    return {
        "pi_runtime_revision": pins.revision,
        "pi_runtime_repository": pins.repository,
        "fleet_stage_release_src": str(resources.files("herdr_fleet").joinpath("stage_release.py")),
    }


def _baseline_vars(pins: BaselinePins) -> JsonObject:
    return {
        "baseline_packages": list(pins.packages),
        "baseline_macos_commands": list(pins.macos_commands),
        "baseline_dirs": [{"path": d.path, "mode": d.mode} for d in pins.dirs],
    }


def build_extra_vars(lock: FleetLock, config: FleetConfig) -> JsonObject:
    """Every role's Ansible variables; roles never read the lock or the config themselves."""
    pins = fleet_pins(lock, config)
    return {
        **_baseline_vars(pins.baseline),
        **_toolchain_vars(pins.toolchain),
        **_herdr_vars(pins.herdr),
        **_herdr_engineering_vars(pins.herdr_engineering),
        **_pi_runtime_vars(pins.pi_runtime),
    }
