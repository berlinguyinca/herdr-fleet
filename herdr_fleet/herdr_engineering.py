"""The ``herdr-engineering`` role: pins, its fingerprint section and its drift messages."""

from __future__ import annotations

from dataclasses import dataclass

from herdr_fleet import sections
from herdr_fleet.config import FleetConfig, FleetLock
from herdr_fleet.fingerprint import Difference, Fingerprint, FingerprintError
from herdr_fleet.python_requirements import python_requirements
from herdr_fleet.repos import repo_url

ROLE = "herdr-engineering"
SECTION = "herdr_engineering"


@dataclass(frozen=True)
class HerdrEngineeringPins:
    revision: str
    repository: str | None  # None when no host has the role, so the lock needs no repo_urls
    gateway_url: str | None
    control_plane_url: str | None
    requirements_path: str | None = None  # hashed requirements shipped beside the lock
    requirements_sha256: str | None = None


def herdr_engineering_pins(
    lock: FleetLock, config: FleetConfig, *, required: bool
) -> HerdrEngineeringPins:
    """``required``: some host has the role, so the lock must say where to clone from."""
    repos = lock.data["repos"]
    assert isinstance(repos, dict)
    requirements = python_requirements(lock, ROLE) if required else None
    return HerdrEngineeringPins(
        revision=str(repos[ROLE]),
        repository=repo_url(lock, ROLE) if required else None,
        gateway_url=config.gateway_url,
        control_plane_url=config.control_plane_url,
        requirements_path=str(requirements.path) if requirements else None,
        requirements_sha256=requirements.sha256 if requirements else None,
    )


def unpinned_python_note(pins: HerdrEngineeringPins) -> str | None:
    """Informational: the role is used but its Python dependencies are not hash-pinned."""
    if pins.repository is None or pins.requirements_sha256 is not None:
        return None
    return (
        "unpinned python dependencies: herdr-engineering's venv resolves its Python dependencies "
        "from PyPI at install time; add python_requirements.herdr-engineering (a hashed "
        "requirements file and its sha256) to fleet.lock to pin them"
    )


SERVICE_STATES: tuple[str, ...] = ("active", "inactive", "unmanaged")
ENV_MODE = "0600"
FABRIC_URL_KEY = "HERDR_ENGINEERING_FABRIC_URL"
CONTROL_PLANE_URL_KEY = "HERDR_ENGINEERING_CONTROL_PLANE_URL"


def _service_state(raw: sections.Section) -> str:
    state = sections.text(SECTION, raw, "fabric_service")
    if state not in SERVICE_STATES:
        raise FingerprintError(
            f"fingerprint: {SECTION}.fabric_service: expected one of {', '.join(SERVICE_STATES)}, "
            f"got {state!r}"
        )
    return state


@dataclass(frozen=True)
class HerdrEngineeringFingerprint:
    revision: str | None  # the stage helper's attestation; None when invalid or absent
    source_root: str | None
    herdr_eng_version: str | None
    venv_entry_present: bool
    shim_path: str
    resolved_path: str | None
    fabric_env_url: str | None
    fabric_env_control_plane_url: str | None
    fabric_env_mode: str | None
    fabric_unit_present: bool
    fabric_unit_matches_release: bool
    fabric_service: str  # "active" | "inactive" | "unmanaged"
    build_gate_sha256: str | None
    build_gate_release_sha256: str | None
    python_requirements_sha256: str | None = None  # what the venv was installed from, if pinned

    @classmethod
    def from_json(cls, raw: sections.Section) -> HerdrEngineeringFingerprint:
        return cls(
            revision=sections.optional_text(SECTION, raw, "revision"),
            source_root=sections.optional_text(SECTION, raw, "source_root"),
            herdr_eng_version=sections.optional_text(SECTION, raw, "herdr_eng_version"),
            venv_entry_present=sections.flag(SECTION, raw, "venv_entry_present"),
            shim_path=sections.text(SECTION, raw, "shim_path"),
            resolved_path=sections.optional_text(SECTION, raw, "resolved_path"),
            fabric_env_url=sections.optional_text(SECTION, raw, "fabric_env_url"),
            fabric_env_control_plane_url=sections.optional_text(
                SECTION, raw, "fabric_env_control_plane_url"
            ),
            fabric_env_mode=sections.optional_text(SECTION, raw, "fabric_env_mode"),
            fabric_unit_present=sections.flag(SECTION, raw, "fabric_unit_present"),
            fabric_unit_matches_release=sections.flag(SECTION, raw, "fabric_unit_matches_release"),
            fabric_service=_service_state(raw),
            build_gate_sha256=sections.optional_text(SECTION, raw, "build_gate_sha256"),
            build_gate_release_sha256=sections.optional_text(
                SECTION, raw, "build_gate_release_sha256"
            ),
            python_requirements_sha256=sections.optional_text(
                SECTION, raw, "python_requirements_sha256"
            ),
        )

    @property
    def shim_resolves(self) -> bool:
        return self.resolved_path == self.shim_path

    @property
    def build_gate_matches_release(self) -> bool:
        return (
            self.build_gate_sha256 is not None
            and self.build_gate_sha256 == self.build_gate_release_sha256
        )


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
    if (
        pins.requirements_sha256 is not None
        and found.python_requirements_sha256 != pins.requirements_sha256
    ):
        differences.append(
            Difference(
                "python requirements",
                f"sha256 {pins.requirements_sha256}",
                found.python_requirements_sha256 or "missing",
            )
        )
    if not found.venv_entry_present:
        differences.append(Difference("herdr-eng", "the venv entry point", "missing"))
    if not found.shim_resolves:
        differences.append(
            Difference("path herdr-eng", found.shim_path, found.resolved_path or "not found")
        )
    differences.extend(_fabric_env_differences(found, pins))
    short = pins.revision[:12]
    # macOS has no systemd: apply renders no unit there, so a missing unit is not drift.
    if fp.system != "Darwin" and not found.fabric_unit_matches_release:
        state = "differs" if found.fabric_unit_present else "missing"
        differences.append(Difference("fabric agent unit", f"rendered from release {short}", state))
    if found.fabric_service == "inactive":
        differences.append(Difference("fabric agent", "active", "inactive"))
    # A release that ships no build gate has nothing to install (apply skips it): not drift.
    if found.build_gate_release_sha256 is not None and not found.build_gate_matches_release:
        differences.append(
            Difference(
                "build-gate", f"sha256 of release {short}", found.build_gate_sha256 or "missing"
            )
        )
    return differences


def _env_line(key: str, value: str | None) -> str:
    return "missing" if value is None else f"{key}={value}"


def _fabric_env_differences(
    found: HerdrEngineeringFingerprint, pins: HerdrEngineeringPins
) -> list[Difference]:
    out: list[Difference] = []
    expected = (
        (FABRIC_URL_KEY, pins.gateway_url, found.fabric_env_url),
        (CONTROL_PLANE_URL_KEY, pins.control_plane_url, found.fabric_env_control_plane_url),
    )
    for key, want, have in expected:
        if want is not None and have != want:
            out.append(Difference("fabric.env", f"{key}={want}", _env_line(key, have)))
        elif want is None and key == CONTROL_PLANE_URL_KEY and have is not None:
            # Removed from the config: apply deletes the line, so a leftover one is drift.
            out.append(Difference("fabric.env", f"no {key}", _env_line(key, have)))
    if found.fabric_env_mode is not None and found.fabric_env_mode != ENV_MODE:
        out.append(Difference("fabric.env mode", ENV_MODE, found.fabric_env_mode))
    return out
