"""A tiny but real Node package for tests, installed by the host's real ``npm ci``.

The package has one dependency that is a ``file:`` directory inside the package itself, so
``npm ci`` needs no registry and no network. ``package-lock.json`` was written by a real
``npm install --package-lock-only --offline``. Its ``prepare`` script runs for real during
``npm ci`` and appends ``PATH|PWD`` to ``$NPM_LOG``, which tells a test which npm and which PATH
the install really ran with. Real npm needs a real node: tests skip when the host has none.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

PACKAGE = {
    "name": "pi-engineering-runtime",
    "version": "1.0.0",
    "private": True,
    "dependencies": {"dep": "file:./dep"},
    "scripts": {"prepare": 'printf \'%s|%s\\n\' "$PATH" "$PWD" >> "$NPM_LOG"'},
}
LOCK = {
    "name": "pi-engineering-runtime",
    "version": "1.0.0",
    "lockfileVersion": 3,
    "requires": True,
    "packages": {
        "": {
            "name": "pi-engineering-runtime",
            "version": "1.0.0",
            "dependencies": {"dep": "file:./dep"},
        },
        "dep": {"version": "1.0.0"},
        "node_modules/dep": {"resolved": "dep", "link": True},
    },
}
PI_FILES = {
    "package.json": json.dumps(PACKAGE),
    "package-lock.json": json.dumps(LOCK),
    "dep/package.json": json.dumps({"name": "dep", "version": "1.0.0"}),
    "README": "pi\n",
    ".gitignore": "node_modules/\n",
}


def real_node_and_npm() -> tuple[Path, Path]:
    """The host's real node binary and npm CLI script, fully resolved."""
    node, npm = shutil.which("node"), shutil.which("npm")
    if node is None or npm is None:
        pytest.skip("real npm tests need node and npm on PATH")
    return Path(node).resolve(), Path(npm).resolve()


def real_npm_dir() -> Path:
    """A directory whose ``npm`` and ``node`` are the host's real ones."""
    return Path(shutil.which("npm") or "").parent


def link_real_npm(directory: Path) -> None:
    """Make ``directory/npm`` and ``directory/node`` the real thing (symlinks, no wrapper)."""
    node, npm = real_node_and_npm()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "npm").symlink_to(npm)
    (directory / "node").symlink_to(node)


def isolate_npm(monkeypatch: pytest.MonkeyPatch, home: Path, log: Path) -> None:
    """Keep npm's cache and config out of the real HOME and tell the lifecycle script its log."""
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("npm_config_cache", str(home / ".npm"))
    monkeypatch.setenv("NPM_LOG", str(log))
    monkeypatch.delenv("NODE_OPTIONS", raising=False)
    assert os.environ["HOME"] == str(home)
