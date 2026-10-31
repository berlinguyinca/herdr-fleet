"""Every role's pins, gathered once from ``fleet.lock`` and ``fleet-config.yaml``.

``FleetPins`` is what the report layer compares a host's fingerprint against and what
``inventory.build_extra_vars`` turns into Ansible variables. A role contributes one field and one
module holding its pins dataclass, mirroring ``toolchain.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from herdr_fleet.config import FleetConfig, FleetLock
from herdr_fleet.herdr import HerdrPins, herdr_pins
from herdr_fleet.herdr_engineering import (
    ROLE as HERDR_ENGINEERING_ROLE,
)
from herdr_fleet.herdr_engineering import (
    HerdrEngineeringPins,
    herdr_engineering_pins,
)
from herdr_fleet.toolchain import ToolchainPins, toolchain_pins


@dataclass(frozen=True)
class FleetPins:
    toolchain: ToolchainPins
    herdr: HerdrPins
    herdr_engineering: HerdrEngineeringPins


def fleet_pins(lock: FleetLock, config: FleetConfig) -> FleetPins:
    needed = any(HERDR_ENGINEERING_ROLE in host.roles for host in config.hosts)
    return FleetPins(
        toolchain=toolchain_pins(lock),
        herdr=herdr_pins(lock),
        herdr_engineering=herdr_engineering_pins(lock, config, required=needed),
    )
