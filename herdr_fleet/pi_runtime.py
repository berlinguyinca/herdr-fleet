"""The ``pi-runtime`` role: pins, its fingerprint section and its drift messages.

The role only stages and registers a Pi release. Whether a running Pi session has loaded it is
activation, owned by the rollout coordinator, so the fingerprint deliberately holds nothing about
sessions or processes.
"""

from __future__ import annotations

from dataclasses import dataclass

from herdr_fleet import sections
from herdr_fleet.config import FleetConfig, FleetLock
from herdr_fleet.fingerprint import Difference, Fingerprint
from herdr_fleet.repos import repo_url

ROLE = "pi-runtime"
SECTION = "pi_runtime"
REPO = "pi-engineering"


@dataclass(frozen=True)
class PiRuntimePins:
    revision: str
    repository: str | None  # None when no host has the role, so the lock needs no repo_urls


def pi_runtime_pins(lock: FleetLock, config: FleetConfig, *, required: bool) -> PiRuntimePins:
    """``required``: some host has the role, so the lock must say where to clone from."""
    del config  # the role has no operator-config input; kept for a uniform pins signature
    repos = lock.data["repos"]
    assert isinstance(repos, dict)
    return PiRuntimePins(
        revision=str(repos[REPO]),
        repository=repo_url(lock, REPO) if required else None,
    )


@dataclass(frozen=True)
class PiRuntimeFingerprint:
    revision: str | None  # the stage helper's attestation; None when invalid or absent
    source_root: str | None
    release_path: str  # where the pinned release lives (or would live)
    settings_path: str
    registered: bool  # settings.json lists release_path
    node_modules_present: bool
    settings_valid: bool = True  # settings.json is absent or a JSON object with a packages list

    @classmethod
    def from_json(cls, raw: sections.Section) -> PiRuntimeFingerprint:
        return cls(
            revision=sections.optional_text(SECTION, raw, "revision"),
            source_root=sections.optional_text(SECTION, raw, "source_root"),
            release_path=sections.text(SECTION, raw, "release_path"),
            settings_path=sections.text(SECTION, raw, "settings_path"),
            registered=sections.flag(SECTION, raw, "registered"),
            node_modules_present=sections.flag(SECTION, raw, "node_modules_present"),
            settings_valid=(
                sections.flag(SECTION, raw, "settings_valid") if "settings_valid" in raw else True
            ),
        )


def compare_pi_runtime(fp: Fingerprint, pins: PiRuntimePins) -> list[Difference]:
    """Every way the staged Pi release differs from the lock (not what a session has loaded)."""
    raw = fp.sections.get(SECTION)
    if raw is None:
        return [Difference(ROLE, pins.revision, "not reported")]
    found = PiRuntimeFingerprint.from_json(raw)
    differences: list[Difference] = []
    if found.revision != pins.revision:
        differences.append(
            Difference(f"{ROLE} revision", pins.revision, found.revision or "missing")
        )
    if not found.settings_valid:
        differences.append(
            Difference(
                f"{ROLE} settings",
                f"{found.settings_path} as a JSON object with a packages list",
                "malformed (fix or remove it; apply will not overwrite it)",
            )
        )
    elif not found.registered:
        differences.append(
            Difference(
                f"{ROLE} registration",
                f"{found.settings_path} to list {found.release_path}",
                "not registered",
            )
        )
    if not found.node_modules_present:
        differences.append(
            Difference(f"{ROLE} dependencies", f"{found.release_path}/node_modules", "missing")
        )
    return differences
