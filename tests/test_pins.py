"""The pins bundle: every role's pins, read from the real example lock and config."""

from __future__ import annotations

from herdr_fleet.config import load_config, load_lock
from herdr_fleet.pins import FleetPins, fleet_pins
from herdr_fleet.toolchain import toolchain_pins
from tests.helpers import CONFIG_EXAMPLE, LOCK_EXAMPLE


def test_bundle_contains_the_toolchain_pins() -> None:
    lock, config = load_lock(LOCK_EXAMPLE), load_config(CONFIG_EXAMPLE)
    pins = fleet_pins(lock, config)
    assert isinstance(pins, FleetPins)
    assert pins.toolchain == toolchain_pins(lock)
