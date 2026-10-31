"""baseline role: pins, fingerprint section and drift messages."""

from __future__ import annotations

from pathlib import Path

import pytest

from herdr_fleet.baseline import (
    DIRS,
    MACOS_COMMANDS,
    REQUIRED_PACKAGES,
    BaselineFingerprint,
    BaselinePins,
    baseline_pins,
    compare_baseline,
)
from herdr_fleet.config import load_config, load_lock
from herdr_fleet.fingerprint import Difference, Fingerprint, FingerprintError
from herdr_fleet.pins import fleet_pins
from tests.helpers import (
    CONFIG_EXAMPLE,
    LOCK_EXAMPLE,
    example_config,
    example_extra_vars,
    write_yaml,
)

CONFIG_DIR = "~/.config/herdr-engineering"


@pytest.fixture
def pins() -> BaselinePins:
    return BaselinePins(packages=("git", "curl", "vim"), macos_commands=MACOS_COMMANDS, dirs=DIRS)


def section(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "user": "fleet",
        "packages": {"git": "installed", "curl": "installed", "vim": "installed"},
        "dirs": {
            "~/.config/herdr-engineering": "0700",
            "~/.local/share/herdr-engineering": "0700",
            "~/.local/share/herdr-engineering/state": "0750",
            "~/.local/share/herdr-engineering/artifacts": "0750",
            "~/.local/bin": "0755",
        },
        "tailscale": "up",
        "python3_version": "3.11.2",
        "linger": "unmanaged",
    }
    return {**base, **over}


def fingerprint(body: dict[str, object] | None, system: str = "Linux") -> Fingerprint:
    raw: dict[str, object] = {"system": system, "machine": "x86_64"}
    if body is not None:
        raw["baseline"] = body
    return Fingerprint.from_json(raw)


def only(pins: BaselinePins, system: str = "Linux", **over: object) -> list[str]:
    return [d.message for d in compare_baseline(fingerprint(section(**over), system), pins)]


def test_pins_are_the_fixed_list_plus_the_configured_extras_without_duplicates() -> None:
    config = load_config(CONFIG_EXAMPLE)
    pins = baseline_pins(config)
    assert pins.packages == (*REQUIRED_PACKAGES, "vim", "mc", "btop")
    assert fleet_pins(load_lock(LOCK_EXAMPLE), config).baseline == pins


def test_an_extra_package_that_is_already_required_is_not_repeated(tmp_path: Path) -> None:
    doc = example_config()
    doc["baseline"] = {"extra_packages": ["git", "vim"]}
    pins = baseline_pins(load_config(write_yaml(tmp_path / "c.yaml", doc)))
    assert pins.packages == (*REQUIRED_PACKAGES, "vim")


def test_the_fixed_list_covers_what_the_other_roles_need() -> None:
    assert {"git", "curl", "ca-certificates", "python3", "python3-venv", "xz-utils"} == set(
        REQUIRED_PACKAGES
    )
    assert [d.path for d in DIRS] == [
        "~/.config/herdr-engineering",
        "~/.local/share/herdr-engineering",
        "~/.local/share/herdr-engineering/state",
        "~/.local/share/herdr-engineering/artifacts",
        "~/.local/bin",
    ]
    assert [d.mode for d in DIRS] == ["0700", "0700", "0750", "0750", None]


def test_extra_vars_carry_the_package_and_directory_tables() -> None:
    extra = example_extra_vars()
    assert extra["baseline_packages"] == [*REQUIRED_PACKAGES, "vim", "mc", "btop"]
    assert extra["baseline_macos_commands"] == list(MACOS_COMMANDS)
    assert {"path": "~/.local/bin", "mode": None} in extra["baseline_dirs"]  # type: ignore[operator]
    assert {"path": CONFIG_DIR, "mode": "0700"} in extra["baseline_dirs"]  # type: ignore[operator]


def test_matching_host_has_no_differences(pins: BaselinePins) -> None:
    assert compare_baseline(fingerprint(section()), pins) == []


def test_missing_section_is_not_reported(pins: BaselinePins) -> None:
    assert compare_baseline(fingerprint(None), pins) == [
        Difference("baseline", "reported", "not reported")
    ]


def test_missing_packages_are_named_one_by_one(pins: BaselinePins) -> None:
    assert only(pins, packages={"git": "missing", "curl": "installed", "vim": "missing"}) == [
        "package git: expected installed, found missing",
        "package vim: expected installed, found missing",
    ]


def test_a_package_the_host_did_not_report_is_named(pins: BaselinePins) -> None:
    assert only(pins, packages={"git": "installed", "curl": "installed"}) == [
        "package vim: expected installed, found not reported"
    ]


def test_directory_drift_messages(pins: BaselinePins) -> None:
    dirs = dict(section()["dirs"])  # type: ignore[call-overload]
    dirs[CONFIG_DIR] = "missing"
    dirs["~/.local/share/herdr-engineering"] = "0755"
    dirs["~/.local/bin"] = "missing"
    assert only(pins, dirs=dirs) == [
        f"dir {CONFIG_DIR}: expected mode 0700, found missing",
        "dir ~/.local/share/herdr-engineering: expected mode 0700, found 0755",
        "dir ~/.local/bin: expected present, found missing",
    ]


def test_the_binary_directory_mode_is_never_drift(pins: BaselinePins) -> None:
    dirs = dict(section()["dirs"])  # type: ignore[call-overload]
    dirs["~/.local/bin"] = "0700"
    assert only(pins, dirs=dirs) == []


def test_linger_is_drift_only_when_off_on_a_managed_host(pins: BaselinePins) -> None:
    assert only(pins, linger="no") == ["linger fleet: expected yes, found no"]
    assert only(pins, linger="yes") == [] and only(pins, linger="unmanaged") == []


def test_tailscale_is_never_drift(pins: BaselinePins) -> None:
    for state in ("up", "down", "absent"):
        assert only(pins, tailscale=state) == []


def test_macos_checks_commands_not_apt_packages(pins: BaselinePins) -> None:
    found = only(
        pins,
        system="Darwin",
        packages={"git": "installed", "curl": "missing", "python3": "installed"},
    )
    assert found == ["package curl: expected installed, found missing"]


def test_section_round_trip_and_rejection() -> None:
    parsed = BaselineFingerprint.from_json(section())
    assert parsed.user == "fleet" and parsed.tailscale == "up" and parsed.python3_version
    assert BaselineFingerprint.from_json(section(python3_version=None)).python3_version is None
    with pytest.raises(FingerprintError, match=r"baseline\.linger"):
        BaselineFingerprint.from_json(section(linger="maybe"))
    with pytest.raises(FingerprintError, match=r"baseline\.tailscale"):
        BaselineFingerprint.from_json(section(tailscale="sideways"))
    with pytest.raises(FingerprintError, match=r"baseline\.packages"):
        BaselineFingerprint.from_json(section(packages={"git": "present"}))
    with pytest.raises(FingerprintError, match=r"baseline\.dirs"):
        BaselineFingerprint.from_json(section(dirs=["x"]))


@pytest.mark.parametrize("name", ["openssh-server-", "x+", "-x", "a b", "vim\n", "--purge", "A"])
def test_pins_refuse_a_package_name_the_schema_would_also_refuse(name: str) -> None:
    from dataclasses import replace

    from herdr_fleet.config import ConfigError

    config = replace(load_config(CONFIG_EXAMPLE), baseline_extra_packages=(name,))
    with pytest.raises(ConfigError, match="baseline.extra_packages"):
        baseline_pins(config)


def test_the_package_pattern_in_the_schema_is_the_one_the_pins_enforce() -> None:
    import json
    from pathlib import Path

    from herdr_fleet.baseline import PACKAGE_PATTERN

    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "herdr_fleet/schemas/fleet-config.schema.json")
        .read_text()
    )
    assert schema["properties"]["baseline"]["properties"]["extra_packages"]["items"]["pattern"] == (
        PACKAGE_PATTERN
    )
