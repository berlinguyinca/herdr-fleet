"""Expand hosts x roles into the ordered list of planned role applications."""

from __future__ import annotations

from dataclasses import dataclass

from herdr_fleet.config import FleetConfig
from herdr_fleet.roles import ROLE_ORDER

NO_CHANGES_NOTICE = "no changes made, check-mode execution not implemented yet (slice 2)"


@dataclass(frozen=True)
class RoleApplication:
    step: int
    host: str
    role: str


def build_plan(config: FleetConfig) -> list[RoleApplication]:
    pairs = [
        (host.name, role)
        for host in config.hosts
        for role in sorted(host.roles, key=ROLE_ORDER.index)
    ]
    return [RoleApplication(i, host, role) for i, (host, role) in enumerate(pairs, start=1)]
