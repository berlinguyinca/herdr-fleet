"""Real ``apply`` of the toolchain role against a local mirror that serves a tiny fake Node.

The mirror is a real HTTP server; the "Node" archive holds shell scripts, so the whole role
(checksum-verified download, extract, move, links, npm install, shims) runs hermetically in a
throwaway HOME. Nothing here touches the network or the real HOME.
"""

from __future__ import annotations

import hashlib
import io
import platform
import subprocess
import tarfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from herdr_fleet.config import load_lock
from herdr_fleet.inventory import build_extra_vars
from herdr_fleet.runner import Mode, run_playbook
from herdr_fleet.toolchain import platform_key
from tests.helpers import LOCK_EXAMPLE

NODE_VERSION = "22.11.0"
FAKE_NODE = "#!/bin/sh\necho v22.11.0\n"
FAKE_NPM = r"""#!/bin/sh
# usage: npm install --global --prefix PREFIX --no-audit --no-fund PKG@VERSION
prefix=""
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) prefix="$2"; shift 2 ;;
    install|--*) shift ;;
    *) spec="$1"; shift ;;
  esac
done
pkg="${spec%@*}"; version="${spec##*@}"
case "$pkg" in
  @earendil-works/pi-coding-agent) cmd=pi ;;
  @openai/codex) cmd=codex ;;
esac
mkdir -p "$prefix/lib/node_modules/$pkg" "$prefix/bin"
printf '{"version": "%s"}' "$version" > "$prefix/lib/node_modules/$pkg/package.json"
printf '#!/bin/sh\necho %s\n' "$version" > "$prefix/bin/$cmd"
chmod +x "$prefix/bin/$cmd"
"""


def _archive(key: str) -> tuple[str, bytes]:
    ext = "tar.xz" if key.startswith("linux") else "tar.gz"
    root = f"node-v{NODE_VERSION}-{key}"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:xz" if ext == "tar.xz" else "w:gz") as tar:
        for name, body in (("node", FAKE_NODE), ("npm", FAKE_NPM), ("npx", "#!/bin/sh\n")):
            data = body.encode()
            info = tarfile.TarInfo(f"{root}/bin/{name}")
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
    return f"{root}.{ext}", buffer.getvalue()


@dataclass(frozen=True)
class Mirror:
    url: str
    sha256: dict[str, str]
    platform: str


@pytest.fixture
def mirror() -> Iterator[Mirror]:
    key = platform_key(platform.system(), platform.machine())
    if key is None:
        pytest.skip("unsupported platform for the fake Node archive")
    name, payload = _archive(key)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.endswith(name):
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield Mirror(
            f"http://127.0.0.1:{server.server_port}",
            {key: hashlib.sha256(payload).hexdigest()},
            key,
        )
    finally:
        server.shutdown()
        server.server_close()


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
    variables = build_extra_vars(load_lock(LOCK_EXAMPLE))
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
    assert (prefix / "node" / NODE_VERSION / "bin" / "node").is_file()
    out = subprocess.run(
        [str(tmp_path / ".local" / "bin" / "pi"), "--version"], capture_output=True, text=True
    )
    assert out.stdout.strip() == "1.2.3"


def test_incomplete_node_directory_is_replaced(
    tmp_path: Path, mirror: Mirror, monkeypatch: pytest.MonkeyPatch
) -> None:
    leftover = tmp_path / ".local/share/herdr-fleet/toolchain/node" / NODE_VERSION
    leftover.mkdir(parents=True)
    (leftover / "half-extracted").write_text("x")
    apply(tmp_path, mirror, monkeypatch)
    assert (leftover / "bin" / "node").is_file()
    assert not (leftover / "half-extracted").exists()
    assert not (leftover / f"{NODE_VERSION}.partial").exists()


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
