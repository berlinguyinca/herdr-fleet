"""Host fingerprint produced by the playbook, and its comparison against ``fleet.lock``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from herdr_fleet.toolchain import CLI_SPECS, ToolchainPins, platform_key

JsonObject = Mapping[str, object]


class FingerprintError(Exception):
    """The playbook returned a fingerprint that does not have the expected shape."""


@dataclass(frozen=True)
class Difference:
    field: str
    expected: str
    actual: str

    @property
    def message(self) -> str:
        return f"{self.field}: expected {self.expected}, found {self.actual}"


@dataclass(frozen=True)
class CliFingerprint:
    version: str | None
    shim_path: str
    shim_present: bool
    resolved_path: str | None
    target_path: str
    target_present: bool  # the managed prefix's bin/<cmd> exists (symlinks followed)
    shim_runs: bool  # `<shim> --version` exited 0 with the managed PATH

    @property
    def resolves_to_shim(self) -> bool:
        return self.resolved_path == self.shim_path


@dataclass(frozen=True)
class Fingerprint:
    system: str
    machine: str
    node_version: str | None
    clis: Mapping[str, CliFingerprint]

    @property
    def platform(self) -> str | None:
        return platform_key(self.system, self.machine)

    @classmethod
    def from_json(cls, raw: JsonObject) -> Fingerprint:
        return cls(
            system=_text(raw, "system"),
            machine=_text(raw, "machine"),
            node_version=_optional_text(raw, "node_version"),
            clis={name: _cli(name, entry) for name, entry in _mapping(raw, "clis").items()},
        )

    def to_json(self) -> dict[str, object]:
        return {
            "system": self.system,
            "machine": self.machine,
            "platform": self.platform,
            "node_version": self.node_version,
            "clis": {
                name: {
                    "version": c.version,
                    "shim_path": c.shim_path,
                    "shim_present": c.shim_present,
                    "resolved_path": c.resolved_path,
                    "target_path": c.target_path,
                    "target_present": c.target_present,
                    "shim_runs": c.shim_runs,
                    "resolves_to_shim": c.resolves_to_shim,
                }
                for name, c in self.clis.items()
            },
        }


def _text(raw: JsonObject, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise FingerprintError(f"fingerprint: {key}: expected a string, got {value!r}")
    return value


def _optional_text(raw: JsonObject, key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    return _text(raw, key)


def _mapping(raw: JsonObject, key: str) -> Mapping[str, JsonObject]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise FingerprintError(f"fingerprint: {key}: expected a mapping, got {value!r}")
    return cast(Mapping[str, JsonObject], value)


def _cli(name: str, entry: JsonObject) -> CliFingerprint:
    return CliFingerprint(
        version=_optional_text(entry, "version"),
        shim_path=_text(entry, "shim_path"),
        shim_present=_flag(name, entry, "shim_present"),
        resolved_path=_optional_text(entry, "resolved_path"),
        target_path=_text(entry, "target_path"),
        target_present=_flag(name, entry, "target_present"),
        shim_runs=_flag(name, entry, "shim_runs"),
    )


def _flag(name: str, entry: JsonObject, key: str) -> bool:
    value = entry.get(key)
    if not isinstance(value, bool):
        raise FingerprintError(f"fingerprint: clis.{name}.{key}: expected a boolean")
    return value


def _platform_differences(fp: Fingerprint, pins: ToolchainPins) -> list[Difference]:
    platform = fp.platform
    if platform is None:
        return [Difference("platform", "linux/darwin on x64/arm64", f"{fp.system} {fp.machine}")]
    if platform not in pins.node_sha256:
        return [Difference("platform", f"a node sha256 for {platform} in fleet.lock", "none")]
    return []


def _cli_differences(name: str, expected: str, fp: Fingerprint) -> list[Difference]:
    cli = fp.clis.get(name)
    if cli is None:
        return [Difference(f"cli {name}", expected, "not reported")]
    found: list[Difference] = []
    if cli.version != expected:
        found.append(Difference(f"cli {name}", expected, cli.version or "missing"))
    if not cli.shim_present:
        found.append(Difference(f"shim {name}", cli.shim_path, "missing"))
    if cli.version == expected and cli.shim_present:
        found.extend(_shim_health(name, cli))
    if cli.shim_present and not cli.resolves_to_shim:
        found.append(Difference(f"path {name}", cli.shim_path, cli.resolved_path or "not found"))
    return found


def _shim_health(name: str, cli: CliFingerprint) -> list[Difference]:
    """The package is the right version, but the shim may still be unable to start it."""
    if not cli.target_present:
        return [Difference(f"bin {name}", f"{cli.target_path} present", "missing")]
    if not cli.shim_runs:
        return [Difference(f"shim {name}", "`--version` exits 0", "fails to run")]
    return []


def compare(fp: Fingerprint, pins: ToolchainPins) -> list[Difference]:
    """Every way ``fp`` differs from the lock; an empty list means the host matches."""
    found = _platform_differences(fp, pins)
    if fp.node_version != pins.node_version:
        found.append(Difference("node", pins.node_version, fp.node_version or "missing"))
    for spec in CLI_SPECS:
        found.extend(_cli_differences(spec.name, pins.clis[spec.name], fp))
    return found
