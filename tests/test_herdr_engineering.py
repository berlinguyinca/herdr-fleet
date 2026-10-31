"""herdr-engineering role: pins, fingerprint section and drift messages (staging half)."""

from __future__ import annotations

from pathlib import Path

import pytest

from herdr_fleet.config import ConfigError, load_config, load_lock
from herdr_fleet.fingerprint import Difference, Fingerprint, FingerprintError
from herdr_fleet.herdr_engineering import (
    HerdrEngineeringFingerprint,
    HerdrEngineeringPins,
    compare_herdr_engineering,
    herdr_engineering_pins,
)
from herdr_fleet.pins import fleet_pins
from tests.helpers import CONFIG_EXAMPLE, LOCK_EXAMPLE, example_config, example_lock, write_yaml

SHA = "1" * 40
OTHER = "9" * 40
HOME = "/home/u"
SHIM = f"{HOME}/.local/bin/herdr-eng"
GATEWAY = "https://gateway.example.com"
GATE = "c" * 64
SHORT = SHA[:12]


@pytest.fixture
def pins() -> HerdrEngineeringPins:
    return HerdrEngineeringPins(
        revision=SHA,
        repository="https://git.example.com/example-org/herdr-engineering.git",
        gateway_url=GATEWAY,
        control_plane_url=None,
    )


def section(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "revision": SHA,
        "source_root": f"{HOME}/.local/share/herdr-engineering/herdr/releases/{SHA}",
        "herdr_eng_version": "herdr-eng 1.0.0",
        "venv_entry_present": True,
        "shim_path": SHIM,
        "resolved_path": SHIM,
        "fabric_env_url": GATEWAY,
        "fabric_env_control_plane_url": None,
        "fabric_env_mode": "0600",
        "fabric_unit_present": True,
        "fabric_unit_matches_release": True,
        "fabric_service": "unmanaged",
        "build_gate_sha256": GATE,
        "build_gate_release_sha256": GATE,
    }
    return {**base, **over}


def fingerprint(body: dict[str, object] | None) -> Fingerprint:
    raw: dict[str, object] = {"system": "Linux", "machine": "x86_64"}
    if body is not None:
        raw["herdr_engineering"] = body
    return Fingerprint.from_json(raw)


def test_pins_come_from_the_lock_and_config() -> None:
    pins = herdr_engineering_pins(
        load_lock(LOCK_EXAMPLE), load_config(CONFIG_EXAMPLE), required=True
    )
    assert pins.revision == "1" * 40
    assert pins.repository == "https://git.example.com/example-org/herdr-engineering.git"
    assert pins.gateway_url is not None
    bundle = fleet_pins(load_lock(LOCK_EXAMPLE), load_config(CONFIG_EXAMPLE))
    assert bundle.herdr_engineering == pins


def test_a_lock_without_repo_urls_still_loads_when_no_host_needs_the_repository(
    tmp_path: Path,
) -> None:
    lock = example_lock()
    del lock["repo_urls"]
    config = example_config()
    hosts = config["hosts"]
    assert isinstance(hosts, dict)
    for body in hosts.values():
        body["roles"] = [r for r in body["roles"] if r != "herdr-engineering"]
    cfg = load_config(write_yaml(tmp_path / "c.yaml", config))
    bundle = fleet_pins(load_lock(write_yaml(tmp_path / "l.yaml", lock)), cfg)
    assert bundle.herdr_engineering.repository is None


def test_a_host_with_the_role_and_no_repo_url_fails_naming_the_remedy(
    tmp_path: Path,
) -> None:
    lock = example_lock()
    del lock["repo_urls"]
    with pytest.raises(ConfigError, match=r"repo_urls\.herdr-engineering"):
        fleet_pins(
            load_lock(write_yaml(tmp_path / "l.yaml", lock)),
            load_config(CONFIG_EXAMPLE),
        )


def test_matching_host_has_no_differences(pins: HerdrEngineeringPins) -> None:
    assert compare_herdr_engineering(fingerprint(section()), pins) == []


def test_missing_section_is_not_reported(pins: HerdrEngineeringPins) -> None:
    assert compare_herdr_engineering(fingerprint(None), pins) == [
        Difference("herdr-engineering", SHA, "not reported")
    ]


def test_unattested_revision_is_missing(pins: HerdrEngineeringPins) -> None:
    diffs = compare_herdr_engineering(fingerprint(section(revision=None, source_root=None)), pins)
    assert Difference("herdr-engineering revision", SHA, "missing") in diffs


def test_other_revision_is_named(pins: HerdrEngineeringPins) -> None:
    (diff,) = compare_herdr_engineering(fingerprint(section(revision=OTHER)), pins)
    assert diff.message == f"herdr-engineering revision: expected {SHA}, found {OTHER}"


def test_missing_entry_point_is_named(pins: HerdrEngineeringPins) -> None:
    diffs = compare_herdr_engineering(fingerprint(section(venv_entry_present=False)), pins)
    assert Difference("herdr-eng", "the venv entry point", "missing") in diffs


def test_command_resolving_elsewhere_or_nowhere_is_reported(pins: HerdrEngineeringPins) -> None:
    (elsewhere,) = compare_herdr_engineering(
        fingerprint(section(resolved_path="/usr/local/bin/herdr-eng")), pins
    )
    assert elsewhere == Difference("path herdr-eng", SHIM, "/usr/local/bin/herdr-eng")
    (nowhere,) = compare_herdr_engineering(fingerprint(section(resolved_path=None)), pins)
    assert nowhere.message == f"path herdr-eng: expected {SHIM}, found not found"


def test_section_round_trip() -> None:
    parsed = HerdrEngineeringFingerprint.from_json(section())
    assert parsed.revision == SHA and parsed.venv_entry_present and parsed.shim_resolves
    assert not HerdrEngineeringFingerprint.from_json(section(resolved_path="/x")).shim_resolves
    assert HerdrEngineeringFingerprint.from_json(section(revision=None)).revision is None


def test_malformed_section_names_the_key() -> None:
    with pytest.raises(FingerprintError, match=r"herdr_engineering\.venv_entry_present"):
        HerdrEngineeringFingerprint.from_json(section(venv_entry_present="yes"))


def test_extra_vars_carry_the_pins_and_the_packaged_helper() -> None:
    from pathlib import Path

    from tests.helpers import example_extra_vars

    extra = example_extra_vars()
    assert extra["herdr_engineering_revision"] == "1" * 40
    assert extra["herdr_engineering_repository"] == (
        "https://git.example.com/example-org/herdr-engineering.git"
    )
    assert extra["herdr_engineering_gateway_url"] is not None
    assert extra["herdr_engineering_control_plane_url"] == "https://control-plane.example.com"
    assert Path(str(extra["fleet_stage_release_src"])).name == "stage_release.py"
    assert Path(str(extra["fleet_stage_release_src"])).is_file()
