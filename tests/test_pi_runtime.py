"""pi-runtime role: pins, fingerprint section and drift messages (staging only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from herdr_fleet.config import ConfigError, load_config, load_lock
from herdr_fleet.fingerprint import Difference, Fingerprint, FingerprintError
from herdr_fleet.pi_runtime import (
    PiRuntimeFingerprint,
    PiRuntimePins,
    compare_pi_runtime,
    pi_runtime_pins,
)
from herdr_fleet.pins import fleet_pins
from tests.helpers import (
    CONFIG_EXAMPLE,
    LOCK_EXAMPLE,
    example_config,
    example_extra_vars,
    example_lock,
    write_yaml,
)

SHA = "2" * 40
OTHER = "8" * 40
HOME = "/home/u"
SETTINGS = f"{HOME}/.pi/agent/settings.json"
RELEASE = f"{HOME}/.local/share/herdr-engineering/pi/releases/{SHA}"
URL = "https://git.example.com/example-org/pi-engineering.git"


@pytest.fixture
def pins() -> PiRuntimePins:
    return PiRuntimePins(revision=SHA, repository=URL)


def section(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "revision": SHA,
        "source_root": RELEASE,
        "release_path": RELEASE,
        "settings_path": SETTINGS,
        "registered": True,
        "node_modules_present": True,
    }
    return {**base, **over}


def fingerprint(body: dict[str, object] | None) -> Fingerprint:
    raw: dict[str, object] = {"system": "Linux", "machine": "x86_64"}
    if body is not None:
        raw["pi_runtime"] = body
    return Fingerprint.from_json(raw)


def only(pins: PiRuntimePins, **over: object) -> list[str]:
    return [d.message for d in compare_pi_runtime(fingerprint(section(**over)), pins)]


def test_pins_come_from_the_lock() -> None:
    lock, config = load_lock(LOCK_EXAMPLE), load_config(CONFIG_EXAMPLE)
    pins = pi_runtime_pins(lock, config, required=True)
    assert pins.revision == "2" * 40
    assert pins.repository == "https://git.example.com/example-org/pi-engineering.git"
    assert fleet_pins(lock, config).pi_runtime == pins


def test_a_lock_without_repo_urls_loads_when_no_host_has_the_role(tmp_path: Path) -> None:
    lock = example_lock()
    del lock["repo_urls"]
    config = example_config()
    hosts = config["hosts"]
    assert isinstance(hosts, dict)
    for body in hosts.values():
        body["roles"] = [r for r in body["roles"] if r not in ("pi-runtime", "herdr-engineering")]
    cfg = load_config(write_yaml(tmp_path / "c.yaml", config))
    bundle = fleet_pins(load_lock(write_yaml(tmp_path / "l.yaml", lock)), cfg)
    assert bundle.pi_runtime.repository is None


def test_a_host_with_the_role_and_no_repo_url_fails_naming_the_remedy(tmp_path: Path) -> None:
    lock = example_lock()
    urls = lock["repo_urls"]
    assert isinstance(urls, dict)
    del urls["pi-engineering"]
    with pytest.raises(ConfigError, match=r"repo_urls\.pi-engineering"):
        fleet_pins(load_lock(write_yaml(tmp_path / "l.yaml", lock)), load_config(CONFIG_EXAMPLE))


def test_extra_vars_carry_the_pins_and_the_packaged_helper() -> None:
    extra = example_extra_vars()
    assert extra["pi_runtime_revision"] == "2" * 40
    assert extra["pi_runtime_repository"] == (
        "https://git.example.com/example-org/pi-engineering.git"
    )
    assert Path(str(extra["fleet_stage_release_src"])).is_file()


def test_matching_host_has_no_differences(pins: PiRuntimePins) -> None:
    assert compare_pi_runtime(fingerprint(section()), pins) == []


def test_missing_section_is_not_reported(pins: PiRuntimePins) -> None:
    assert compare_pi_runtime(fingerprint(None), pins) == [
        Difference("pi-runtime", SHA, "not reported")
    ]


def test_unattested_and_other_revisions_are_named(pins: PiRuntimePins) -> None:
    assert only(pins, revision=None, source_root=None) == [
        f"pi-runtime revision: expected {SHA}, found missing"
    ]
    assert only(pins, revision=OTHER) == [f"pi-runtime revision: expected {SHA}, found {OTHER}"]


def test_a_missing_registration_names_the_settings_file_and_release(pins: PiRuntimePins) -> None:
    assert only(pins, registered=False) == [
        f"pi-runtime registration: expected {SETTINGS} to list {RELEASE}, found not registered"
    ]


def test_missing_dependencies_name_the_directory(pins: PiRuntimePins) -> None:
    assert only(pins, node_modules_present=False) == [
        f"pi-runtime dependencies: expected {RELEASE}/node_modules, found missing"
    ]


def test_a_running_session_is_never_part_of_the_fingerprint() -> None:
    names = set(PiRuntimeFingerprint.__dataclass_fields__)
    assert not any("session" in n or "loaded" in n or "process" in n for n in names)


def test_section_round_trip_and_rejection() -> None:
    parsed = PiRuntimeFingerprint.from_json(section())
    assert parsed.revision == SHA and parsed.registered and parsed.node_modules_present
    assert PiRuntimeFingerprint.from_json(section(revision=None)).revision is None
    with pytest.raises(FingerprintError, match=r"pi_runtime\.registered"):
        PiRuntimeFingerprint.from_json(section(registered="yes"))
    with pytest.raises(FingerprintError, match=r"pi_runtime\.settings_path"):
        PiRuntimeFingerprint.from_json(section(settings_path=None))
