"""Toolchain pins: what the ``toolchain`` role installs, read from ``fleet.lock``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from herdr_fleet.config import FleetLock

_SYSTEMS: Mapping[str, str] = {"Linux": "linux", "Darwin": "darwin"}
_MACHINES: Mapping[str, str] = {
    "x86_64": "x64",
    "amd64": "x64",
    "aarch64": "arm64",
    "arm64": "arm64",
}


@dataclass(frozen=True)
class CliSpec:
    """An npm-distributed global CLI: the lock key, the npm package and the command it provides."""

    name: str
    package: str
    command: str


# gitleaks and woodpecker-cli are binary releases; their lock keys are unused until a later slice.
CLI_SPECS: tuple[CliSpec, ...] = (
    CliSpec("pi-cli", "@earendil-works/pi-coding-agent", "pi"),
    CliSpec("codex", "@openai/codex", "codex"),
)


@dataclass(frozen=True)
class ToolchainPins:
    node_version: str
    node_sha256: Mapping[str, str]
    clis: Mapping[str, str]


def platform_key(system: str, machine: str) -> str | None:
    """Map ``uname -s`` / ``uname -m`` to the lock's ``linux-x64`` style key, or None."""
    os_name = _SYSTEMS.get(system)
    arch = _MACHINES.get(machine)
    return f"{os_name}-{arch}" if os_name and arch else None


def toolchain_pins(lock: FleetLock) -> ToolchainPins:
    node = cast(Mapping[str, object], lock.data["node"])
    clis = cast(Mapping[str, str], lock.data["clis"])
    return ToolchainPins(
        node_version=cast(str, node["version"]),
        node_sha256=dict(cast(Mapping[str, str], node["sha256"])),
        clis={spec.name: clis[spec.name] for spec in CLI_SPECS},
    )
