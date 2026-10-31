"""The fingerprint probes against a real (throwaway) HOME, through the real ansible-playbook."""

from __future__ import annotations

from pathlib import Path

import pytest

from herdr_fleet.config import load_lock
from herdr_fleet.fingerprint import Fingerprint, compare
from herdr_fleet.inventory import build_extra_vars
from herdr_fleet.report import parse_events
from herdr_fleet.runner import Mode, run_playbook
from herdr_fleet.toolchain import toolchain_pins
from tests.helpers import LOCK_EXAMPLE

LOCAL = {
    "all": {
        "hosts": {
            "h": {
                "ansible_connection": "local",
                "ansible_python_interpreter": "auto_silent",
                "fleet_roles": ["toolchain"],
            }
        }
    }
}


def fingerprint_in(home: Path, monkeypatch: pytest.MonkeyPatch) -> Fingerprint:
    monkeypatch.setenv("HOME", str(home))
    result = run_playbook(LOCAL, build_extra_vars(load_lock(LOCK_EXAMPLE)), Mode.VERIFY)
    parsed = parse_events(result.stdout)
    assert not parsed.noise, parsed.noise
    return parsed.fingerprints["h"]


def test_profile_noise_does_not_break_the_resolved_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shim_dir = tmp_path / ".local" / "bin"
    shim_dir.mkdir(parents=True)
    shim = shim_dir / "pi"
    shim.write_text("#!/bin/sh\nexit 0\n")
    shim.chmod(0o755)
    (tmp_path / ".bash_profile").write_text(
        'echo "Welcome! last login: never"\nexport PATH="$HOME/.local/bin:$PATH"\n'
    )
    fp = fingerprint_in(tmp_path, monkeypatch)
    assert fp.clis["pi-cli"].resolved_path == str(shim)
    assert fp.clis["pi-cli"].resolves_to_shim


def test_banner_is_never_taken_for_a_resolved_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".bash_profile").write_text('echo "banner line"\n')
    fp = fingerprint_in(tmp_path, monkeypatch)
    resolved = fp.clis["pi-cli"].resolved_path
    # the host may have its own pi; either way the banner must never be taken for a path
    assert resolved is None or resolved.startswith("/")


def install_pi(home: Path, *, target: str | None, shim_exit: int = 0) -> None:
    """Lay out ~/.local/share/herdr-fleet/toolchain and ~/.local/bin/pi like the role does."""
    prefix = home / ".local" / "share" / "herdr-fleet" / "toolchain"
    package = prefix / "lib" / "node_modules" / "@earendil-works" / "pi-coding-agent"
    package.mkdir(parents=True)
    (package / "package.json").write_text('{"version": "1.2.3"}')
    (prefix / "bin").mkdir()
    if target is not None:
        real = prefix / "bin" / "pi"
        real.symlink_to(target)
    (home / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    shim = home / ".local" / "bin" / "pi"
    shim.write_text(f"#!/bin/sh\nexit {shim_exit}\n")
    shim.chmod(0o755)


def health_fields(home: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    fp = fingerprint_in(home, monkeypatch)
    pins = toolchain_pins(load_lock(LOCK_EXAMPLE))
    return [
        d.field for d in compare(fp, pins) if d.field.endswith("pi-cli") and d.field != "cli pi-cli"
    ]


def test_dangling_bin_link_is_reported_although_package_and_shim_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_pi(tmp_path, target=str(tmp_path / "nowhere"))
    assert "bin pi-cli" in health_fields(tmp_path, monkeypatch)


def test_shim_that_exits_nonzero_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real-pi"
    real.write_text("#!/bin/sh\nexit 0\n")
    install_pi(tmp_path, target=str(real), shim_exit=3)
    assert "shim pi-cli" in health_fields(tmp_path, monkeypatch)


def test_healthy_shim_has_no_health_differences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real-pi"
    real.write_text("#!/bin/sh\nexit 0\n")
    install_pi(tmp_path, target=str(real))
    (tmp_path / ".bash_profile").write_text('export PATH="$HOME/.local/bin:$PATH"\n')
    assert health_fields(tmp_path, monkeypatch) == []
