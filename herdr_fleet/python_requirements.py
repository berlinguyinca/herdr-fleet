"""Hashed Python requirements shipped next to ``fleet.lock``.

The optional ``python_requirements.<name>`` lock field names a requirements file (relative to the
lock's directory, produced by ``uv pip compile --generate-hashes`` or pip-tools) and the sha256 of
that file. The file is read here, checked against the lock, and its hashes are enforced on the
host by ``pip install --require-hashes``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from herdr_fleet.config import ConfigError, FleetLock

HASH_MARKER = "--hash=sha256:"


@dataclass(frozen=True)
class PythonRequirements:
    path: Path
    sha256: str


def python_requirements(lock: FleetLock, name: str) -> PythonRequirements | None:
    """The verified requirements for ``name``, None when the lock does not set the field."""
    table = cast("dict[str, dict[str, str]]", lock.data.get("python_requirements") or {})
    entry = table.get(name)
    if entry is None:
        return None
    where = f"fleet.lock: python_requirements.{name}"
    if lock.directory is None:
        raise ConfigError(
            [f"{where}: the lock was not loaded from a file, so {entry['file']} cannot be located"]
        )
    path = lock.directory / entry["file"]
    resolved = path.resolve()
    if not resolved.is_relative_to(lock.directory):
        raise ConfigError(
            [
                f"{where}.file: {entry['file']} resolves outside the lock's directory; ship the "
                "requirements file next to fleet.lock"
            ]
        )
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise ConfigError(
            [
                f"{where}.file: cannot read {path}: {exc.strerror}; generate it with "
                "'uv pip compile --generate-hashes' and commit it next to fleet.lock"
            ]
        ) from exc
    digest = hashlib.sha256(raw).hexdigest()
    if digest != entry["sha256"]:
        raise ConfigError(
            [
                f"{where}.sha256: {path.name} has sha256 {digest}, "
                f"the lock says {entry['sha256']}; "
                "update the lock's sha256 after reviewing the file"
            ]
        )
    problem = _requirements_problem(raw.decode("utf-8", errors="replace"))
    if problem is not None:
        raise ConfigError(
            [
                f"{where}.file: {path.name}: {problem}; it must be generated with "
                "--generate-hashes so pip --require-hashes can verify every package"
            ]
        )
    return PythonRequirements(path=path, sha256=digest)


# Options that make pip read other files or install something unhashed from a path.
_FORBIDDEN_OPTIONS = ("-r", "--requirement", "-c", "--constraint", "-e", "--editable")


def _logical_lines(text: str) -> list[str]:
    """Requirement lines with continuations joined and comments and blank lines dropped."""
    lines: list[str] = []
    pending = ""
    for physical in text.splitlines():
        stripped = re.sub(r"(^|\s)#.*$", "", physical).rstrip()
        joined = pending + stripped
        if joined.endswith("\\"):
            pending = joined[:-1] + " "
            continue
        pending = ""
        if joined.strip():
            lines.append(joined.strip())
    if pending.strip():
        lines.append(pending.strip())
    return lines


def _requirements_problem(text: str) -> str | None:
    requirements = 0
    for line in _logical_lines(text):
        if line.startswith("-"):
            if line.split()[0] in _FORBIDDEN_OPTIONS or line.startswith(("-r", "-c", "-e")):
                return f"{line.split()[0]} is not allowed ({line})"
            continue  # an index or find-links option
        requirements += 1
        if HASH_MARKER not in line:
            return f"requirement without {HASH_MARKER}: {line}"
    return None if requirements else "no requirement lines (only comments, blanks or options)"
