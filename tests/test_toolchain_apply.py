"""Real ``apply`` of the toolchain role: the real pinned Node, real npm, a local npm registry.

The mirror is a real HTTP server. It serves the real Node.js release tarball (tests/artifacts.py
downloads it from nodejs.org once and verifies the pinned sha256) and a local npm registry
(tests/local_registry.py) holding tiny genuine npm packages for the two pinned CLIs. The role's
checksum-verified download, extraction, links, real ``npm install --global`` and shims all run for
real in a throwaway HOME; npm's cache and the registry setting are throwaway too. Nothing here
touches the real HOME.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from herdr_fleet.runner import Mode, run_playbook
from tests.artifacts import real_node
from tests.helpers import example_extra_vars
from tests.local_registry import registry_files
from tests.mirror import serve_files

PACKAGES = {
    "@earendil-works/pi-coding-agent": ("1.2.3", "pi"),
    "@openai/codex": ("0.9.0", "codex"),
}


@dataclass(frozen=True)
class Mirror:
    url: str
    sha256: dict[str, str]
    platform: str
    node_version: str


@pytest.fixture
def mirror() -> Iterator[Mirror]:
    node = real_node()
    payload = node.tarball.read_bytes()
    with serve_files({f"/v{node.version}/{node.archive_name}": payload}) as running:
        running.files.update(registry_files(running.url, PACKAGES))
        yield Mirror(running.url, node.sha256, node.platform, node.version)


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


def apply(home: Path, mirror: Mirror, monkeypatch: pytest.MonkeyPatch, **extra: object) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("npm_config_registry", mirror.url)
    monkeypatch.setenv("npm_config_cache", str(home.parent / "npm-cache"))
    variables = example_extra_vars()
    variables["toolchain_node_version"] = mirror.node_version
    variables["toolchain_node_dist_url"] = mirror.url
    variables["toolchain_node_sha256"] = mirror.sha256
    variables.update(extra)
    result = run_playbook(LOCAL, variables, Mode.APPLY)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr


def test_apply_installs_and_shims_run(
    tmp_path: Path, mirror: Mirror, monkeypatch: pytest.MonkeyPatch
) -> None:
    apply(tmp_path, mirror, monkeypatch)
    prefix = tmp_path / ".local" / "share" / "herdr-fleet" / "toolchain"
    node = prefix / "node" / mirror.node_version / "bin" / "node"
    real = subprocess.run([str(node), "--version"], capture_output=True, text=True)
    assert real.stdout.strip() == f"v{mirror.node_version}"  # the real Node, not a script
    out = subprocess.run(
        [str(tmp_path / ".local" / "bin" / "pi"), "--version"], capture_output=True, text=True
    )
    assert out.stdout.strip() == "1.2.3"


def test_incomplete_node_directory_is_replaced(
    tmp_path: Path, mirror: Mirror, monkeypatch: pytest.MonkeyPatch
) -> None:
    leftover = tmp_path / ".local/share/herdr-fleet/toolchain/node" / mirror.node_version
    leftover.mkdir(parents=True)
    (leftover / "half-extracted").write_text("x")
    apply(tmp_path, mirror, monkeypatch)
    assert (leftover / "bin" / "node").is_file()
    assert not (leftover / "half-extracted").exists()
    assert not (leftover / f"{mirror.node_version}.partial").exists()


def test_existing_shim_directory_keeps_its_mode(
    tmp_path: Path, mirror: Mirror, monkeypatch: pytest.MonkeyPatch
) -> None:
    shim_dir = tmp_path / ".local" / "bin"
    shim_dir.mkdir(parents=True)
    shim_dir.chmod(0o700)
    apply(tmp_path, mirror, monkeypatch)
    assert shim_dir.stat().st_mode & 0o777 == 0o700


def test_shim_survives_a_prefix_with_spaces_and_quotes(
    tmp_path: Path, mirror: Mirror, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "it's a `home`"
    home.mkdir()
    apply(home, mirror, monkeypatch)
    shim = home / ".local" / "bin" / "codex"
    out = subprocess.run([str(shim), "--version"], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "0.9.0", out.stderr
