"""Every role's pins, gathered once from ``fleet.lock`` and ``fleet-config.yaml``.

``FleetPins`` is what the report layer compares a host's fingerprint against and what
``inventory.build_extra_vars`` turns into Ansible variables. A role contributes one field and one
module holding its pins dataclass, mirroring ``toolchain.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from herdr_fleet.config import FleetConfig, FleetLock
from herdr_fleet.herdr import HerdrPins, herdr_pins
from herdr_fleet.toolchain import ToolchainPins, toolchain_pins


@dataclass(frozen=True)
class FleetPins:
    toolchain: ToolchainPins
    herdr: HerdrPins


def fleet_pins(lock: FleetLock, config: FleetConfig) -> FleetPins:
    return FleetPins(toolchain=toolchain_pins(lock), herdr=herdr_pins(lock))
