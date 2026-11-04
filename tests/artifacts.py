"""Real, checksum-verified release artifacts for the tests: Node.js and the herdr binary.

The pins are the ones in ``tests/containers/fixtures/fleet.lock.yaml`` (Node from nodejs.org,
herdr from its GitHub release). A file is downloaded once into a cache directory and re-verified
on every use, so a tampered or truncated cache entry is deleted and fetched again. The cache is
``$HERDR_FLEET_TEST_CACHE`` or ``.test-cache/`` in the repository (git-ignored), never the real
home directory. If an artifact cannot be fetched (no network) the test is skipped with that
reason, it is never replaced by a fake.
"""

from __future__ import annotations

import hashlib
import os
import platform
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest

from herdr_fleet.herdr import HERDR_ASSETS
from herdr_fleet.toolchain import platform_key
from tests.helpers import read_yaml

FIXTURE_LOCK = Path(__file__).parent / "containers" / "fixtures" / "fleet.lock.yaml"
REPO = Path(__file__).resolve().parent.parent
NODE_DIST = "https://nodejs.org/dist"
HERDR_VERSION = str(read_yaml(FIXTURE_LOCK)["herdr"]["version"])
HERDR_RELEASES = "https://github.com/herdrdev/herdr/releases/download"


def cache_dir() -> Path:
    path = Path(os.environ.get("HERDR_FLEET_TEST_CACHE") or REPO / ".test-cache")
    path.mkdir(parents=True, exist_ok=True)
    return path


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(url: str, sha256: str, name: str) -> Path:
    """The file at ``url`` in the cache, verified against ``sha256``; skip when unreachable."""
    path = cache_dir() / name
    if path.is_file() and sha256_of(path) == sha256:
        return path
    path.unlink(missing_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    try:
        with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 (https pins)
            partial.write_bytes(response.read())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        partial.unlink(missing_ok=True)
        pytest.skip(f"cannot fetch the real artifact {url}: {exc}")
    if sha256_of(partial) != sha256:
        partial.unlink(missing_ok=True)
        pytest.fail(f"{url} does not match the pinned sha256 {sha256}")
    partial.replace(path)
    return path


def current_platform() -> str:
    key = platform_key(platform.system(), platform.machine())
    if key is None:
        pytest.skip("no pinned release artifacts for this platform")
    return key


@dataclass(frozen=True)
class RealNode:
    version: str
    platform: str
    tarball: Path
    sha256: dict[str, str]  # the lock's per-platform map (all platforms, as pinned)

    @property
    def archive_name(self) -> str:
        return self.tarball.name


@dataclass(frozen=True)
class RealHerdr:
    version: str
    platform: str
    binary: Path
    asset: str
    sha256: dict[str, str]


def real_node() -> RealNode:
    """The pinned Node.js release tarball for this platform, downloaded from nodejs.org."""
    key = current_platform()
    pins = read_yaml(FIXTURE_LOCK)["node"]
    version = str(pins["version"])
    ext = "tar.xz" if key.startswith("linux") else "tar.gz"
    name = f"node-v{version}-{key}.{ext}"
    tarball = fetch(f"{NODE_DIST}/v{version}/{name}", pins["sha256"][key], name)
    return RealNode(version, key, tarball, dict(pins["sha256"]))


def real_herdr() -> RealHerdr:
    """The pinned herdr release binary for this platform, downloaded from GitHub."""
    key = current_platform()
    pins = read_yaml(FIXTURE_LOCK)["herdr"]
    version = str(pins["version"])
    asset = HERDR_ASSETS[key]
    url = f"{HERDR_RELEASES}/v{version}/{asset}"
    binary = fetch(url, pins["sha256"][key], f"{asset}-{version}")
    return RealHerdr(version, key, binary, asset, dict(pins["sha256"]))


def isolate_herdr(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """Point the real herdr's sockets and config at a throwaway directory.

    herdr resolves its socket from the account's real home, not from ``$HOME``, so ``HOME`` alone
    would let ``herdr status`` talk to the operator's running server. These variables make it
    look at a path that does not exist.
    """
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(directory / "herdr.sock"))
    monkeypatch.setenv("HERDR_CLIENT_SOCKET_PATH", str(directory / "herdr-client.sock"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(directory / "config"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(directory / "run"))
    monkeypatch.setenv("XDG_STATE_HOME", str(directory / "state"))
