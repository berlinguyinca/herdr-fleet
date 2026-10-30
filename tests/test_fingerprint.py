"""Fingerprint model and its comparison against the lock (real lock example, real shapes)."""

from __future__ import annotations

import pytest

from herdr_fleet.config import load_lock
from herdr_fleet.fingerprint import Difference, Fingerprint, FingerprintError, compare
from herdr_fleet.toolchain import ToolchainPins, platform_key, toolchain_pins
from tests.helpers import LOCK_EXAMPLE

RawFingerprint = dict[str, object]


def good_raw(pins: ToolchainPins) -> RawFingerprint:
    return {
        "system": "Linux",
        "machine": "x86_64",
        "node_version": pins.node_version,
        "clis": {
            name: {
                "version": version,
                "shim_path": f"/home/u/.local/bin/{cmd}",
                "shim_present": True,
                "resolved_path": f"/home/u/.local/bin/{cmd}",
            }
            for (name, cmd), version in zip(
                (("pi-cli", "pi"), ("codex", "codex")),
                (pins.clis["pi-cli"], pins.clis["codex"]),
                strict=True,
            )
        },
    }


@pytest.fixture
def pins() -> ToolchainPins:
    return toolchain_pins(load_lock(LOCK_EXAMPLE))


def test_pins_come_from_lock(pins: ToolchainPins) -> None:
    assert pins.node_version == "22.11.0"
    assert pins.clis == {"pi-cli": "1.2.3", "codex": "0.9.0"}
    assert set(pins.node_sha256) == {"linux-x64", "darwin-arm64"}


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Linux", "x86_64", "linux-x64"),
        ("Linux", "aarch64", "linux-arm64"),
        ("Darwin", "arm64", "darwin-arm64"),
        ("Darwin", "x86_64", "darwin-x64"),
        ("FreeBSD", "amd64", None),
        ("Linux", "riscv64", None),
    ],
)
def test_platform_key(system: str, machine: str, expected: str | None) -> None:
    assert platform_key(system, machine) == expected


def test_matching_fingerprint_has_no_differences(pins: ToolchainPins) -> None:
    fp = Fingerprint.from_json(good_raw(pins))
    assert compare(fp, pins) == []


def test_missing_node_and_clis_are_named(pins: ToolchainPins) -> None:
    raw = good_raw(pins)
    raw["node_version"] = None
    clis = raw["clis"]
    assert isinstance(clis, dict)
    for entry in clis.values():
        entry.update(version=None, shim_present=False, resolved_path=None)
    diffs = compare(Fingerprint.from_json(raw), pins)
    fields = {d.field for d in diffs}
    assert {"node", "cli pi-cli", "cli codex", "shim pi-cli", "shim codex"} <= fields
    node = next(d for d in diffs if d.field == "node")
    assert node == Difference("node", "22.11.0", "missing")
    assert "expected 22.11.0, found missing" in node.message


def test_wrong_node_version_is_the_only_difference(pins: ToolchainPins) -> None:
    raw = good_raw(pins)
    raw["node_version"] = "20.18.0"
    assert compare(Fingerprint.from_json(raw), pins) == [Difference("node", "22.11.0", "20.18.0")]


def test_wrong_cli_version(pins: ToolchainPins) -> None:
    raw = good_raw(pins)
    clis = raw["clis"]
    assert isinstance(clis, dict)
    clis["pi-cli"]["version"] = "1.0.0"
    assert compare(Fingerprint.from_json(raw), pins) == [Difference("cli pi-cli", "1.2.3", "1.0.0")]


def test_command_resolving_elsewhere_is_reported(pins: ToolchainPins) -> None:
    raw = good_raw(pins)
    clis = raw["clis"]
    assert isinstance(clis, dict)
    clis["pi-cli"]["resolved_path"] = "/home/u/.nvm/versions/node/v20/bin/pi"
    (diff,) = compare(Fingerprint.from_json(raw), pins)
    assert diff.field == "path pi-cli"
    assert diff.expected == "/home/u/.local/bin/pi"
    assert diff.actual == "/home/u/.nvm/versions/node/v20/bin/pi"


def test_command_not_found_is_reported(pins: ToolchainPins) -> None:
    raw = good_raw(pins)
    clis = raw["clis"]
    assert isinstance(clis, dict)
    clis["codex"]["resolved_path"] = None
    (diff,) = compare(Fingerprint.from_json(raw), pins)
    assert diff == Difference("path codex", "/home/u/.local/bin/codex", "not found")


def test_unsupported_platform_is_a_difference(pins: ToolchainPins) -> None:
    raw = good_raw(pins)
    raw["machine"] = "riscv64"
    diffs = compare(Fingerprint.from_json(raw), pins)
    assert [d.field for d in diffs] == ["platform"]


def test_platform_missing_from_lock_checksums(pins: ToolchainPins) -> None:
    raw = good_raw(pins)
    raw["machine"] = "aarch64"  # linux-arm64 has no checksum in the example lock
    diffs = compare(Fingerprint.from_json(raw), pins)
    assert [d.field for d in diffs] == ["platform"]
    assert "linux-arm64" in diffs[0].message


def test_malformed_fingerprint_raises() -> None:
    with pytest.raises(FingerprintError, match="system"):
        Fingerprint.from_json({"machine": "x86_64"})
    with pytest.raises(FingerprintError, match="clis"):
        Fingerprint.from_json({"system": "Linux", "machine": "x", "node_version": None})
