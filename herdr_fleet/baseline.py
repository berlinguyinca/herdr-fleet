"""The ``baseline`` role: pins, its fingerprint section and its drift messages.

Baseline is the only role that may escalate privileges, and only to install apt packages and to
enable linger. Everything else it reports (directories, Tailscale, the Python version) is read
without privileges. Tailscale and the Python version are informational and never drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from herdr_fleet import sections
from herdr_fleet.config import FleetConfig
from herdr_fleet.fingerprint import Difference, Fingerprint, FingerprintError

ROLE = "baseline"
SECTION = "baseline"

REQUIRED_PACKAGES: Final = ("git", "curl", "ca-certificates", "python3", "python3-venv", "xz-utils")
MACOS_COMMANDS: Final = ("git", "curl", "python3")

PACKAGE_STATES: Final = ("installed", "missing")
TAILSCALE_STATES: Final = ("up", "down", "absent")
LINGER_STATES: Final = ("yes", "no", "unmanaged")
MISSING: Final = "missing"


@dataclass(frozen=True)
class DirSpec:
    path: str  # always ``~/``-relative so the fingerprint is host-independent
    mode: str | None  # None: must exist, any mode is fine


DIRS: Final = (
    DirSpec("~/.config/herdr-engineering", "0700"),
    DirSpec("~/.local/share/herdr-engineering", "0700"),
    DirSpec("~/.local/share/herdr-engineering/state", "0750"),
    DirSpec("~/.local/share/herdr-engineering/artifacts", "0750"),
    DirSpec("~/.local/bin", None),
)


@dataclass(frozen=True)
class BaselinePins:
    packages: tuple[str, ...]
    macos_commands: tuple[str, ...]
    dirs: tuple[DirSpec, ...]


def baseline_pins(config: FleetConfig) -> BaselinePins:
    packages = tuple(dict.fromkeys((*REQUIRED_PACKAGES, *config.baseline_extra_packages)))
    return BaselinePins(packages=packages, macos_commands=MACOS_COMMANDS, dirs=DIRS)


def _choice(raw: sections.Section, key: str, allowed: tuple[str, ...]) -> str:
    value = sections.text(SECTION, raw, key)
    if value not in allowed:
        raise FingerprintError(
            f"fingerprint: {SECTION}.{key}: expected one of {', '.join(allowed)}, got {value!r}"
        )
    return value


def _table(raw: sections.Section, key: str) -> dict[str, str]:
    value = raw.get(key)
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise FingerprintError(f"fingerprint: {SECTION}.{key}: expected a string table")
    return dict(value)


@dataclass(frozen=True)
class BaselineFingerprint:
    user: str
    packages: dict[str, str]  # name -> installed | missing
    dirs: dict[str, str]  # ~/-relative path -> octal mode | missing
    tailscale: str
    python3_version: str | None
    linger: str

    @classmethod
    def from_json(cls, raw: sections.Section) -> BaselineFingerprint:
        packages = _table(raw, "packages")
        for name, state in packages.items():
            if state not in PACKAGE_STATES:
                raise FingerprintError(
                    f"fingerprint: {SECTION}.packages: {name} must be installed or missing"
                )
        return cls(
            user=sections.text(SECTION, raw, "user"),
            packages=packages,
            dirs=_table(raw, "dirs"),
            tailscale=_choice(raw, "tailscale", TAILSCALE_STATES),
            python3_version=sections.optional_text(SECTION, raw, "python3_version"),
            linger=_choice(raw, "linger", LINGER_STATES),
        )


def _package_differences(
    fp: Fingerprint, found: BaselineFingerprint, pins: BaselinePins
) -> list[Difference]:
    names = pins.macos_commands if fp.system == "Darwin" else pins.packages
    return [
        Difference(f"package {name}", "installed", found.packages.get(name, "not reported"))
        for name in names
        if found.packages.get(name) != "installed"
    ]


def _dir_differences(found: BaselineFingerprint, pins: BaselinePins) -> list[Difference]:
    out: list[Difference] = []
    for spec in pins.dirs:
        actual = found.dirs.get(spec.path, MISSING)
        if spec.mode is None:
            if actual == MISSING:
                out.append(Difference(f"dir {spec.path}", "present", MISSING))
        elif actual != spec.mode:
            out.append(Difference(f"dir {spec.path}", f"mode {spec.mode}", actual))
    return out


def compare_baseline(fp: Fingerprint, pins: BaselinePins) -> list[Difference]:
    """Drift is a missing package, a missing or wrongly moded directory, or linger off."""
    raw = fp.sections.get(SECTION)
    if raw is None:
        return [Difference(ROLE, "reported", "not reported")]
    found = BaselineFingerprint.from_json(raw)
    differences = _package_differences(fp, found, pins) + _dir_differences(found, pins)
    if found.linger == "no":
        differences.append(Difference(f"linger {found.user}", "yes", "no"))
    return differences
