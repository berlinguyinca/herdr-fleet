"""herdr role: asset table, pins, fingerprint section and its drift messages."""

from __future__ import annotations

import pytest

from herdr_fleet.config import load_config, load_lock
from herdr_fleet.fingerprint import Difference, Fingerprint, FingerprintError
from herdr_fleet.herdr import (
    HERDR_ASSETS,
    HerdrFingerprint,
    HerdrPins,
    compare_herdr,
    herdr_notes,
    herdr_pins,
)
from herdr_fleet.pins import fleet_pins
from tests.helpers import CONFIG_EXAMPLE, LOCK_EXAMPLE

SHA = "2a02fed16beb651ef006e1d43f048f652ca4dc58ad053cd2d44450563d5c54b7"
OTHER_SHA = "9f3c" + "0" * 60
BIN = "/home/u/.local/bin/herdr"


@pytest.fixture
def pins() -> HerdrPins:
    return HerdrPins(version="0.9.1", sha256={"linux-x64": SHA, "darwin-arm64": "b" * 64})


def section(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "path": BIN,
        "present": True,
        "version": "0.9.1",
        "sha256": SHA,
        "resolved_path": BIN,
        "server_version": None,
    }
    return {**base, **over}


def fingerprint(herdr: dict[str, object] | None, machine: str = "x86_64") -> Fingerprint:
    raw: dict[str, object] = {"system": "Linux", "machine": machine}
    if herdr is not None:
        raw["herdr"] = herdr
    return Fingerprint.from_json(raw)


def test_asset_table_covers_all_four_platforms() -> None:
    assert HERDR_ASSETS == {
        "linux-x64": "herdr-linux-x86_64",
        "linux-arm64": "herdr-linux-aarch64",
        "darwin-x64": "herdr-macos-x86_64",
        "darwin-arm64": "herdr-macos-aarch64",
    }


def test_pins_come_from_the_lock() -> None:
    pins = herdr_pins(load_lock(LOCK_EXAMPLE))
    assert pins.version == "0.5.0" and set(pins.sha256) == {"linux-x64", "darwin-arm64"}
    bundle = fleet_pins(load_lock(LOCK_EXAMPLE), load_config(CONFIG_EXAMPLE))
    assert bundle.herdr == pins


def test_matching_host_has_no_differences(pins: HerdrPins) -> None:
    assert compare_herdr(fingerprint(section()), pins) == []


def test_missing_binary_is_named(pins: HerdrPins) -> None:
    fp = fingerprint(section(present=False, version=None, sha256=None, resolved_path=None))
    assert compare_herdr(fp, pins) == [Difference("herdr", "0.9.1", "missing")]


def test_missing_section_is_not_reported(pins: HerdrPins) -> None:
    assert compare_herdr(fingerprint(None), pins) == [Difference("herdr", "0.9.1", "not reported")]


def test_other_version_is_named(pins: HerdrPins) -> None:
    diffs = compare_herdr(fingerprint(section(version="0.9.5", sha256=OTHER_SHA)), pins)
    assert Difference("herdr", "0.9.1", "0.9.5") in diffs
    assert Difference("herdr binary", f"sha256 {SHA}", OTHER_SHA) in diffs


def test_replaced_binary_is_drift_even_when_the_version_string_matches(pins: HerdrPins) -> None:
    (diff,) = compare_herdr(fingerprint(section(sha256=OTHER_SHA)), pins)
    assert diff.message == f"herdr binary: expected sha256 {SHA}, found {OTHER_SHA}"


def test_command_resolving_elsewhere_is_reported(pins: HerdrPins) -> None:
    (diff,) = compare_herdr(fingerprint(section(resolved_path="/usr/local/bin/herdr")), pins)
    assert diff == Difference("path herdr", BIN, "/usr/local/bin/herdr")


def test_command_not_on_path_is_a_note_not_drift(pins: HerdrPins) -> None:
    fp = fingerprint(section(resolved_path=None))
    assert compare_herdr(fp, pins) == []
    (note,) = herdr_notes(fp)
    assert BIN in note and "PATH" in note
    assert herdr_notes(fingerprint(section())) == []


def test_platform_without_a_lock_checksum_is_a_difference(pins: HerdrPins) -> None:
    diffs = compare_herdr(fingerprint(section(), machine="aarch64"), pins)
    assert diffs == [Difference("platform", "a herdr sha256 for linux-arm64 in fleet.lock", "none")]


def test_unsupported_platform_is_a_difference(pins: HerdrPins) -> None:
    diffs = compare_herdr(fingerprint(section(), machine="riscv64"), pins)
    assert [d.field for d in diffs] == ["platform"]


def test_stale_server_is_informational_not_drift(pins: HerdrPins) -> None:
    fp = fingerprint(section(server_version="0.9.0"))
    assert compare_herdr(fp, pins) == []
    parsed = HerdrFingerprint.from_json(fp.sections["herdr"])
    assert parsed.server_version == "0.9.0" and parsed.server_is_stale("0.9.1")
    assert not parsed.server_is_stale("0.9.0")
    assert not HerdrFingerprint.from_json(section()).server_is_stale("0.9.1")


def test_section_round_trip() -> None:
    parsed = HerdrFingerprint.from_json(section(server_version="0.9.1"))
    assert parsed.path == BIN and parsed.present and parsed.resolves_to_installed
    assert not HerdrFingerprint.from_json(section(resolved_path="/x")).resolves_to_installed


@pytest.mark.parametrize(
    ("key", "value"),
    [("path", 3), ("present", "yes"), ("version", 1), ("sha256", []), ("server_version", 2)],
)
def test_malformed_section_raises(key: str, value: object) -> None:
    with pytest.raises(FingerprintError, match=key):
        HerdrFingerprint.from_json(section(**{key: value}))


def test_missing_required_keys_raise() -> None:
    with pytest.raises(FingerprintError, match="path"):
        HerdrFingerprint.from_json({"present": True})
