"""A local npm registry for the tests: real packument JSON and real ``.tgz`` packages.

Real ``npm install --global`` talks to it over HTTP (``npm_config_registry``). The packages are
tiny but genuine npm packages (a ``package.json`` and a ``bin`` entry), packed in the same
``package/`` tarball layout ``npm pack`` writes, with sha512 integrity and sha1 shasum values the
real npm verifies. Nothing about npm itself is replaced.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
from collections.abc import Mapping
from urllib.parse import quote

BIN_SCRIPT = "#!/bin/sh\necho {version}\n"


def package_tarball(name: str, version: str, command: str) -> bytes:
    """A real npm package tarball whose ``bin`` ``command`` prints ``version``."""
    manifest = {"name": name, "version": version, "bin": {command: f"bin/{command}"}}
    files = {
        "package/package.json": (json.dumps(manifest).encode(), 0o644),
        f"package/bin/{command}": (BIN_SCRIPT.format(version=version).encode(), 0o755),
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path, (data, mode) in files.items():
            info = tarfile.TarInfo(path)
            info.size, info.mode, info.mtime = len(data), mode, 0
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def registry_files(base_url: str, packages: Mapping[str, tuple[str, str]]) -> dict[str, bytes]:
    """URL path -> bytes for ``{package name: (version, command)}``: packument and tarball."""
    files: dict[str, bytes] = {}
    for name, (version, command) in packages.items():
        tarball = package_tarball(name, version, command)
        tgz_path = f"/{name}/-/{name.rsplit('/', 1)[-1]}-{version}.tgz"
        integrity = "sha512-" + base64.b64encode(hashlib.sha512(tarball).digest()).decode()
        packument = {
            "name": name,
            "dist-tags": {"latest": version},
            "versions": {
                version: {
                    "name": name,
                    "version": version,
                    "bin": {command: f"bin/{command}"},
                    "dist": {
                        "tarball": f"{base_url}{tgz_path}",
                        "integrity": integrity,
                        "shasum": hashlib.sha1(tarball).hexdigest(),  # noqa: S324 (npm's field)
                    },
                }
            },
        }
        body = json.dumps(packument).encode()
        files[f"/{quote(name, safe='@')}"] = body
        files[f"/{name.replace('/', '%2f')}"] = body
        files[f"/{name.replace('/', '%2F')}"] = body
        files[tgz_path] = tarball
    return files
