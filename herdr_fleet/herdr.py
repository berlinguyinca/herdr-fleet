"""The ``herdr`` role: pins from ``fleet.lock``, its fingerprint section and its drift messages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from herdr_fleet import sections
from herdr_fleet.config import FleetLock
from herdr_fleet.fingerprint import Difference, Fingerprint

SECTION = "herdr"

# Lock platform key -> the release asset name on the herdr GitHub release. The role receives the
# resolved table as a variable and never maps names itself.
HERDR_ASSETS: Mapping[str, str] = {
    "linux-x64": "herdr-linux-x86_64",
    "linux-arm64": "herdr-linux-aarch64",
    "darwin-x64": "herdr-macos-x86_64",
    "darwin-arm64": "herdr-macos-aarch64",
}


@dataclass(frozen=True)
class HerdrPins:
    version: str
    sha256: Mapping[str, str]


def herdr_pins(lock: FleetLock) -> HerdrPins:
    herdr = cast(Mapping[str, object], lock.data["herdr"])
    return HerdrPins(
        version=cast(str, herdr["version"]),
        sha256=dict(cast(Mapping[str, str], herdr["sha256"])),
    )


@dataclass(frozen=True)
class HerdrFingerprint:
    path: str
    present: bool
    version: str | None
    sha256: str | None
    resolved_path: str | None
    server_version: str | None

    @classmethod
    def from_json(cls, raw: sections.Section) -> HerdrFingerprint:
        return cls(
            path=sections.text(SECTION, raw, "path"),
            present=sections.flag(SECTION, raw, "present"),
            version=sections.optional_text(SECTION, raw, "version"),
            sha256=sections.optional_text(SECTION, raw, "sha256"),
            resolved_path=sections.optional_text(SECTION, raw, "resolved_path"),
            server_version=sections.optional_text(SECTION, raw, "server_version"),
        )

    @property
    def resolves_to_installed(self) -> bool:
        return self.resolved_path == self.path

    def server_is_stale(self, pinned_version: str) -> bool:
        """A running server keeps its old build until restarted; informational only."""
        return self.server_version is not None and self.server_version != pinned_version


def compare_herdr(fp: Fingerprint, pins: HerdrPins) -> list[Difference]:
    """Every way the host's herdr differs from the lock; a stale running server is not one."""
    platform = fp.platform
    if platform is None:
        return [Difference("platform", "linux/darwin on x64/arm64", f"{fp.system} {fp.machine}")]
    if platform not in pins.sha256:
        return [Difference("platform", f"a herdr sha256 for {platform} in fleet.lock", "none")]
    raw = fp.sections.get(SECTION)
    if raw is None:
        return [Difference("herdr", pins.version, "not reported")]
    found = HerdrFingerprint.from_json(raw)
    differences: list[Difference] = []
    if found.version != pins.version:
        differences.append(Difference("herdr", pins.version, found.version or "missing"))
    expected_sha = pins.sha256[platform]
    if found.present and found.sha256 != expected_sha:
        differences.append(
            Difference("herdr binary", f"sha256 {expected_sha}", found.sha256 or "unreadable")
        )
    if found.present and not found.resolves_to_installed:
        differences.append(Difference("path herdr", found.path, found.resolved_path or "not found"))
    return differences
