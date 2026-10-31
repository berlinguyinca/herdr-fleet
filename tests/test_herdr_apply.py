"""Real ``apply``, ``check`` and ``verify`` of the herdr role against a local HTTP mirror.

The mirror serves a tiny executable script named like the real release asset, so the whole role
(checksum-verified download, cache, install, fingerprint probes) runs hermetically in a throwaway
HOME. The real herdr binary is never run: it talks to the real home's socket.
"""

from __future__ import annotations

import hashlib
import platform
import stat
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from herdr_fleet.fingerprint import Fingerprint
from herdr_fleet.herdr import HERDR_ASSETS, HerdrPins, compare_herdr
from herdr_fleet.report import ParsedEvents, parse_events
from herdr_fleet.runner import Mode, run_playbook
from herdr_fleet.toolchain import platform_key
from tests.helpers import example_extra_vars
from tests.mirror import RunningMirror, serve_files

VERSION = "0.9.1"
FAKE_HERDR = f"""#!/bin/sh
case "$1" in
  --version) echo "herdr {VERSION}" ;;
  status)
    printf 'client:\\n  version: {VERSION}\\nserver:\\n  pid: 1\\n  version: 0.9.0\\n' ;;
esac
"""

LOCAL = {
    "all": {
        "hosts": {
            "h": {
                "ansible_connection": "local",
                "ansible_python_interpreter": "auto_silent",
                "fleet_roles": ["herdr"],
            }
        }
    }
}


@dataclass(frozen=True)
class Release:
    mirror: RunningMirror
    platform: str
    sha256: str

    @property
    def pins(self) -> HerdrPins:
        return HerdrPins(version=VERSION, sha256={self.platform: self.sha256})


@pytest.fixture
def release() -> Iterator[Release]:
    key = platform_key(platform.system(), platform.machine())
    if key is None:
        pytest.skip("unsupported platform for the fake herdr release")
    payload = FAKE_HERDR.encode()
    path = f"/v{VERSION}/{HERDR_ASSETS[key]}"
    with serve_files({path: payload}) as running:
        yield Release(running, key, hashlib.sha256(payload).hexdigest())


def variables(release: Release, sha256: str | None = None) -> dict[str, object]:
    extra = example_extra_vars()
    extra["herdr_version"] = VERSION
    extra["herdr_sha256"] = {release.platform: sha256 or release.sha256}
    extra["herdr_release_base_url"] = release.mirror.url
    return extra


def converge(
    home: Path,
    release: Release,
    mode: Mode,
    monkeypatch: pytest.MonkeyPatch,
    *,
    sha256: str | None = None,
    check_ok: bool = True,
) -> ParsedEvents:
    monkeypatch.setenv("HOME", str(home))
    result = run_playbook(LOCAL, variables(release, sha256), mode)
    if check_ok:
        assert result.returncode == 0, result.stdout[-2000:] + result.stderr
    return parse_events(result.stdout)


def changed(events: ParsedEvents) -> list[str]:
    return [t.task for t in events.tasks if t.status == "changed"]


def installed(home: Path) -> Path:
    return home / ".local" / "bin" / "herdr"


def verify(home: Path, release: Release, monkeypatch: pytest.MonkeyPatch) -> Fingerprint:
    (home / ".bash_profile").write_text('export PATH="$HOME/.local/bin:$PATH"\n')
    events = converge(home, release, Mode.VERIFY, monkeypatch)
    assert not events.noise, events.noise
    return events.fingerprints["h"]


def test_apply_installs_the_pinned_binary_and_verify_is_clean(
    tmp_path: Path, release: Release, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, release, Mode.APPLY, monkeypatch)
    binary = installed(tmp_path)
    assert binary.read_text() == FAKE_HERDR
    assert stat.S_IMODE(binary.stat().st_mode) == 0o755
    assert not binary.is_symlink()
    fp = verify(tmp_path, release, monkeypatch)
    assert compare_herdr(fp, release.pins) == []
    section = fp.sections["herdr"]
    assert section["version"] == VERSION
    assert section["server_version"] == "0.9.0"  # running server is older: reported, not drift


def test_second_apply_changes_nothing_and_downloads_nothing(
    tmp_path: Path, release: Release, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, release, Mode.APPLY, monkeypatch)
    before = len(release.mirror.requests)
    events = converge(tmp_path, release, Mode.APPLY, monkeypatch)
    assert changed(events) == []
    assert len(release.mirror.requests) == before


def test_wrong_sha256_fails_and_leaves_nothing_installed(
    tmp_path: Path, release: Release, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = converge(tmp_path, release, Mode.APPLY, monkeypatch, sha256="0" * 64, check_ok=False)
    assert any(t.status == "failed" for t in events.tasks)
    assert not installed(tmp_path).exists()


def test_wrong_sha256_leaves_an_existing_binary_untouched(
    tmp_path: Path, release: Release, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = installed(tmp_path)
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\necho old\n")
    binary.chmod(0o755)
    converge(tmp_path, release, Mode.APPLY, monkeypatch, sha256="0" * 64, check_ok=False)
    assert binary.read_text() == "#!/bin/sh\necho old\n"


def test_hand_replaced_binary_is_reported_as_drift(
    tmp_path: Path, release: Release, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, release, Mode.APPLY, monkeypatch)
    binary = installed(tmp_path)
    binary.write_text(FAKE_HERDR + "# edited\n")
    fp = verify(tmp_path, release, monkeypatch)
    messages = [d.message for d in compare_herdr(fp, release.pins)]
    assert any(m.startswith("herdr binary: expected sha256 ") for m in messages)


def test_apply_repairs_a_hand_replaced_binary_from_the_cache(
    tmp_path: Path, release: Release, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, release, Mode.APPLY, monkeypatch)
    installed(tmp_path).write_text("#!/bin/sh\necho tampered\n")
    before = len(release.mirror.requests)
    events = converge(tmp_path, release, Mode.APPLY, monkeypatch)
    assert installed(tmp_path).read_text() == FAKE_HERDR
    assert "put the verified herdr 0.9.1 in place" in changed(events)
    assert len(release.mirror.requests) == before  # the checksum-verified cache was enough


def test_check_mode_on_a_fresh_home_lists_the_install_and_never_touches_the_network(
    tmp_path: Path, release: Release, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = converge(tmp_path, release, Mode.CHECK, monkeypatch)
    assert "install herdr 0.9.1 (pending)" in changed(events)
    assert release.mirror.requests == []
    assert not installed(tmp_path).exists()


def test_check_mode_after_apply_has_nothing_pending(
    tmp_path: Path, release: Release, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, release, Mode.APPLY, monkeypatch)
    events = converge(tmp_path, release, Mode.CHECK, monkeypatch)
    assert changed(events) == []


def test_existing_bin_directory_keeps_its_mode(
    tmp_path: Path, release: Release, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    bin_dir.chmod(0o700)
    converge(tmp_path, release, Mode.APPLY, monkeypatch)
    assert stat.S_IMODE(bin_dir.stat().st_mode) == 0o700


def test_rolling_back_to_a_cached_version_needs_no_network(
    tmp_path: Path, release: Release, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, release, Mode.APPLY, monkeypatch)
    installed(tmp_path).unlink()
    before = len(release.mirror.requests)
    converge(tmp_path, release, Mode.APPLY, monkeypatch)
    assert installed(tmp_path).read_text() == FAKE_HERDR
    assert len(release.mirror.requests) == before


def test_platform_without_a_pinned_checksum_fails_naming_the_remedy(
    tmp_path: Path, release: Release, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    extra = variables(release)
    extra["herdr_sha256"] = {}
    result = run_playbook(LOCAL, extra, Mode.APPLY)
    assert result.returncode != 0
    assert f"fleet.lock has no herdr.sha256 for {release.platform}" in result.stdout
    assert not installed(tmp_path).exists()


def test_the_asset_is_executable_by_the_user_shell(
    tmp_path: Path, release: Release, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, release, Mode.APPLY, monkeypatch)
    out = subprocess.run([str(installed(tmp_path)), "--version"], capture_output=True, text=True)
    assert out.stdout.strip() == f"herdr {VERSION}"
