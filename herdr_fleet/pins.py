"""Every role's pins, gathered once from ``fleet.lock`` and ``fleet-config.yaml``.

``FleetPins`` is what the report layer compares a host's fingerprint against and what
``inventory.build_extra_vars`` turns into Ansible variables. A role contributes one field and one
module holding its pins dataclass, mirroring ``toolchain.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from herdr_fleet.baseline import BaselinePins, baseline_pins
from herdr_fleet.config import FleetConfig, FleetLock
from herdr_fleet.herdr import HerdrPins, herdr_pins
from herdr_fleet.herdr_engineering import (
    ROLE as HERDR_ENGINEERING_ROLE,
)
from herdr_fleet.herdr_engineering import (
    HerdrEngineeringPins,
    herdr_engineering_pins,
    unpinned_python_note,
)
from herdr_fleet.pi_runtime import ROLE as PI_RUNTIME_ROLE
from herdr_fleet.pi_runtime import PiRuntimePins, pi_runtime_pins
from herdr_fleet.toolchain import ToolchainPins, toolchain_pins


@dataclass(frozen=True)
class FleetPins:
    baseline: BaselinePins
    toolchain: ToolchainPins
    herdr: HerdrPins
    herdr_engineering: HerdrEngineeringPins
    pi_runtime: PiRuntimePins


def _on_a_host(config: FleetConfig, role: str) -> bool:
    return any(role in host.roles for host in config.hosts)


def fleet_pins(lock: FleetLock, config: FleetConfig) -> FleetPins:
    needed = _on_a_host(config, HERDR_ENGINEERING_ROLE)
    return FleetPins(
        baseline=baseline_pins(config),
        toolchain=toolchain_pins(lock),
        herdr=herdr_pins(lock),
        herdr_engineering=herdr_engineering_pins(lock, config, required=needed),
        pi_runtime=pi_runtime_pins(lock, config, required=_on_a_host(config, PI_RUNTIME_ROLE)),
    )


def fleet_notes(pins: FleetPins) -> list[str]:
    """Informational findings that are not drift: they never change an exit code."""
    notes = [unpinned_python_note(pins.herdr_engineering)]
    return [note for note in notes if note is not None]
