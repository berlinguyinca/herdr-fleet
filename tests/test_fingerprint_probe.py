"""The fingerprint probes against a real (throwaway) HOME, through the real ansible-playbook."""

from __future__ import annotations

from pathlib import Path

import pytest

from herdr_fleet.config import load_lock
from herdr_fleet.fingerprint import Fingerprint
from herdr_fleet.inventory import build_extra_vars
from herdr_fleet.report import parse_events
from herdr_fleet.runner import Mode, run_playbook
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
