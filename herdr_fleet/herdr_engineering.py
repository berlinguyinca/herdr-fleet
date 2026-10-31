"""The ``herdr-engineering`` role: pins, its fingerprint section and its drift messages."""

from __future__ import annotations

from dataclasses import dataclass

from herdr_fleet import sections
from herdr_fleet.config import FleetConfig, FleetLock
from herdr_fleet.fingerprint import Difference, Fingerprint
from herdr_fleet.repos import repo_url

ROLE = "herdr-engineering"
SECTION = "herdr_engineering"


@dataclass(frozen=True)
class HerdrEngineeringPins:
    revision: str
    repository: str | None  # None when no host has the role, so the lock needs no repo_urls
    gateway_url: str | None
    control_plane_url: str | None


def herdr_engineering_pins(
    lock: FleetLock, config: FleetConfig, *, required: bool
) -> HerdrEngineeringPins:
    """``required``: some host has the role, so the lock must say where to clone from."""
    repos = lock.data["repos"]
    assert isinstance(repos, dict)
    return HerdrEngineeringPins(
        revision=str(repos[ROLE]),
        repository=repo_url(lock, ROLE) if required else None,
        gateway_url=config.gateway_url,
        control_plane_url=config.control_plane_url,
    )


@dataclass(frozen=True)
class HerdrEngineeringFingerprint:
    revision: str | None  # the stage helper's attestation; None when invalid or absent
    source_root: str | None
    herdr_eng_version: str | None
    venv_entry_present: bool
    shim_path: str
    resolved_path: str | None

    @classmethod
    def from_json(cls, raw: sections.Section) -> HerdrEngineeringFingerprint:
        return cls(
            revision=sections.optional_text(SECTION, raw, "revision"),
            source_root=sections.optional_text(SECTION, raw, "source_root"),
            herdr_eng_version=sections.optional_text(SECTION, raw, "herdr_eng_version"),
            venv_entry_present=sections.flag(SECTION, raw, "venv_entry_present"),
            shim_path=sections.text(SECTION, raw, "shim_path"),
            resolved_path=sections.optional_text(SECTION, raw, "resolved_path"),
        )

    @property
    def shim_resolves(self) -> bool:
        return self.resolved_path == self.shim_path


def compare_herdr_engineering(fp: Fingerprint, pins: HerdrEngineeringPins) -> list[Difference]:
    """Every way the host's herdr-engineering differs from the lock."""
    raw = fp.sections.get(SECTION)
    if raw is None:
        return [Difference(ROLE, pins.revision, "not reported")]
    found = HerdrEngineeringFingerprint.from_json(raw)
    differences: list[Difference] = []
    if found.revision != pins.revision:
        differences.append(
            Difference(f"{ROLE} revision", pins.revision, found.revision or "missing")
        )
    if not found.venv_entry_present:
        differences.append(Difference("herdr-eng", "the venv entry point", "missing"))
    if not found.shim_resolves:
        differences.append(
            Difference("path herdr-eng", found.shim_path, found.resolved_path or "not found")
        )
    return differences
