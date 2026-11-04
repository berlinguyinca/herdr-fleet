"""Real wheels for a local pip index: built with the standard library, installed by real pip."""

from __future__ import annotations

import base64
import hashlib
import zipfile
from pathlib import Path


def build_wheel(directory: Path, name: str, version: str, body: str = "") -> Path:
    """A pure-Python wheel ``name``-``version`` containing one module ``name``."""
    dist = f"{name}-{version}.dist-info"
    files = {
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        f"{dist}/WHEEL": "Wheel-Version: 1.0\nGenerator: tests\nRoot-Is-Purelib: true\n"
        "Tag: py3-none-any\n",
        f"{name}.py": body or f"VERSION = {version!r}\n",
    }
    record = []
    for path, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=")
        record.append(f"{path},sha256={digest.decode()},{len(text.encode())}")
    record.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = "\n".join(record) + "\n"
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)
    return wheel


def wheel_sha256(wheel: Path) -> str:
    return hashlib.sha256(wheel.read_bytes()).hexdigest()


def hashed_requirements(wheel_dir: Path, *wheels: Path) -> str:
    """A ``pip install --require-hashes`` file pinning exactly these wheels."""
    lines = []
    for wheel in wheels:
        name, version = wheel.name.split("-")[:2]
        lines.append(f"{name}=={version} \\\n    --hash=sha256:{wheel_sha256(wheel)}\n")
    return "".join(lines)
