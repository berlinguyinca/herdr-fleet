"""A real git repository shaped like a herdr-engineering release, for role tests.

It holds a tiny PEP 517 package (in-tree backend, no PyPI access) that installs a real
``herdr-eng`` console script, the fabric agent unit template and the Pi build-gate extension, so
the whole role runs against real ``git``, ``venv`` and ``pip`` in a throwaway HOME.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

BACKEND = """\
import base64, hashlib, zipfile

NAME, VERSION = "herdr_engineering", "1.0.0"
MODULE = '''
def main():
    import sys
    if sys.argv[1:] == ["--version"]:
        print("herdr-eng 1.0.0")
'''


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    dist = f"{NAME}-{VERSION}.dist-info"
    files = {
        f"{dist}/METADATA": "Metadata-Version: 2.1\\nName: herdr-engineering\\n"
        f"Version: {VERSION}\\n",
        f"{dist}/WHEEL": "Wheel-Version: 1.0\\nGenerator: tiny\\n"
        "Root-Is-Purelib: true\\nTag: py3-none-any\\n",
        f"{dist}/entry_points.txt": "[console_scripts]\\nherdr-eng = herdr_engineering:main\\n",
        f"{NAME}.py": MODULE,
    }
    record = []
    for name, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=")
        record.append(f"{name},sha256={digest.decode()},{len(text.encode())}")
    record.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = "\\n".join(record) + "\\n"
    wheel = f"{NAME}-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(f"{wheel_directory}/{wheel}", "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return wheel
"""
PYPROJECT = """\
[build-system]
requires = []
build-backend = "backend"
backend-path = ["."]
"""
UNIT = """\
[Unit]
Description=Example fabric agent

[Service]
EnvironmentFile=-%h/.config/herdr-engineering/fabric.env
ExecStart=__HERDR_ENG__ devfabric agent
Restart=on-failure

[Install]
WantedBy=default.target
"""
BUILD_GATE = "export default function buildGate() { return 'gate v1'; }\n"

RELEASE_FILES = {
    "pyproject.toml": PYPROJECT,
    "backend.py": BACKEND,
    ".gitignore": "__pycache__/\nbuild/\n*.egg-info/\n",
    "scripts/herdr-eng-fabric-agent.service": UNIT,
    "deploy/pi-build-gate/build-gate.ts": BUILD_GATE,
}


def git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    done = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True, env=env
    )
    return done.stdout.strip()


@dataclass(frozen=True)
class Source:
    path: Path
    url: str
    revision: str

    def commit(self, files: dict[str, str], message: str = "next") -> Source:
        """A new commit on top; returns the repository at the new revision."""
        for name, text in files.items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        git(self.path, "add", "-A")
        git(self.path, "commit", "-q", "-m", message)
        return Source(self.path, self.url, git(self.path, "rev-parse", "HEAD"))


def make_source(path: Path, files: dict[str, str] | None = None) -> Source:
    """A real repository with one commit, addressed by ``file://`` URL."""
    path.mkdir(parents=True)
    git(path, "init", "-q")
    for name, text in (files or RELEASE_FILES).items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "release")
    git(path, "config", "uploadpack.allowAnySHA1InWant", "true")
    return Source(path, path.as_uri(), git(path, "rev-parse", "HEAD"))
