"""Role table: the canonical apply order (see spec 0001, "Roles")."""

from __future__ import annotations

ROLE_ORDER: tuple[str, ...] = (
    "baseline",
    "toolchain",
    "herdr",
    "herdr-engineering",
    "pi-runtime",
    "secret-scan",
    "controller",
    "gateway",
    "ci-server",
    "ci-agent",
)
