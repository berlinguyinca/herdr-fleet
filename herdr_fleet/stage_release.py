#!/usr/bin/env python3
"""Stage exact herdr-engineering / pi-engineering releases without touching developer checkouts.

Vendored from herdr-engineering's ``ansible/files/stage-release.py`` so this fleet tool is
self-contained. The on-disk layout is unchanged (``~/.local/share/herdr-engineering/<kind>``), so
a host staged by either tool is understood by both. It is copied to target hosts and run there:
standard library only, and syntax that runs on Python 3.9.

Managed source directories are write-once. A failed install has no installation receipt and can be
retried; an existing modified directory is never reset.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

KINDS = ("pi", "herdr")

# A settings.json.lock directory older than this belongs to a writer that died; a younger one is
# waited for, for at most LOCK_WAIT_SECONDS, because a live Pi writer holds it for milliseconds.
LOCK_STALE_SECONDS = 30
LOCK_WAIT_SECONDS = 3.0
LOCK_POLL_SECONDS = 0.1

# Read-only git must not take index.lock: a check run beside a developer's git would race with it.
GIT_ENV_READ_ONLY = {"GIT_OPTIONAL_LOCKS": "0"}


def run(cmd, *, cwd=None, env=None):
    if cmd and cmd[0] == "git" and env is None:
        env = dict(os.environ, **GIT_ENV_READ_ONLY)
    result = subprocess.run(
        cmd, cwd=cwd, env=env, check=True, capture_output=True, text=True, timeout=900
    )
    return result.stdout.strip()


def atomic_json(path, data, indent=2, trailing_newline=True):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".release-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=indent)
            if trailing_newline:
                stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_revision(revision):
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision):
        raise ValueError("A full immutable commit ID is required")
    if platform.system() not in ("Linux", "Darwin"):
        raise RuntimeError("Release staging supports Linux and macOS only")


def verify_checkout(path, revision, runner):
    if runner(["git", "-C", str(path), "rev-parse", "HEAD"]) != revision:
        raise RuntimeError("Managed release has the wrong commit; refusing overwrite")
    if runner(["git", "-C", str(path), "status", "--porcelain", "--untracked-files=all"]):
        raise RuntimeError("Managed release is dirty; preserving local work")


def checkout(root, revision, repository, runner):
    release = root / "releases" / revision
    if not release.exists():
        release.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(dir=str(release.parent), prefix=".staging-") as temporary:
            source = Path(temporary) / "source"
            runner(["git", "init", str(source)])
            runner(["git", "-C", str(source), "remote", "add", "origin", repository])
            runner(["git", "-C", str(source), "fetch", "--depth", "1", "origin", revision])
            runner(["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"])
            verify_checkout(source, revision, runner)
            source.rename(release)
    verify_checkout(release, revision, runner)
    return release


def repository_pattern(repository):
    """Regex for the ways one clone URL appears as a Pi package source in settings.json."""
    location = re.sub(r"^(?:https?://|ssh://|git@)", "", repository)
    location = re.sub(r"^[^/:]+@", "", location)
    location = re.sub(r"\.git$", "", location).replace(":", "/", 1).strip("/")
    host, _, path = location.partition("/")
    return (
        r"(?:git:)?(?:https?://|ssh://git@|git@)?"
        + re.escape(host)
        + r"[/:]"
        + re.escape(path)
        + r"(?:\.git)?(?:@[^\s]+)?"
    )


def is_pi_package(entry, home, repository):
    if isinstance(entry, str):
        source = entry
    elif isinstance(entry, dict):
        source = entry.get("source")
    else:
        source = None
    if not isinstance(source, str):
        raise ValueError("Invalid Pi package entry; refusing to rewrite settings")
    if re.fullmatch(repository_pattern(repository), source):
        return True
    if source.startswith("npm:pi-engineering-runtime"):
        return source == "npm:pi-engineering-runtime" or source.startswith(
            "npm:pi-engineering-runtime@"
        )
    if source.startswith(("~/", "/", "./", "../")):
        if source.startswith("~/"):
            directory = home / source[2:]
        elif source.startswith("/"):
            directory = Path(source)
        else:
            directory = home / ".pi/agent" / source
        try:
            name = json.loads((directory / "package.json").read_text()).get("name")
        except (OSError, ValueError):
            return False
        return name == "pi-engineering-runtime"
    return False


def registration(home, release, repository):
    settings = home / ".pi/agent/settings.json"
    before = settings.read_bytes() if settings.exists() else None
    value = json.loads(before) if before is not None else {}
    if not isinstance(value, dict) or not isinstance(value.get("packages", []), list):
        raise ValueError("Invalid Pi settings/packages; refusing to overwrite")
    packages = []
    replaced = False
    for entry in value.get("packages", []):
        if is_pi_package(entry, home, repository):
            if replaced:
                raise ValueError("Multiple Pi Engineering registrations require reconciliation")
            if isinstance(entry, dict):
                packages.append(dict(entry, source=str(release)))
            else:
                packages.append(str(release))
            replaced = True
        else:
            packages.append(entry)
    if not replaced:
        packages.append(str(release))
    value["packages"] = packages
    # Settings that changed while staging are never overwritten (checked under the settings lock).
    return settings, before, value


def verify_herdr_distribution(home, release, runner):
    """Attest the package actually installed in the service interpreter.

    Receipts and a clean checkout alone cannot detect a later pip/uv reinstall. PEP 610
    direct_url.json must identify this exact immutable local release.
    """
    python = home / ".venvs/herdr-engineering/bin/python"
    origin = json.loads(
        runner(
            [
                str(python),
                "-I",
                "-c",
                "import importlib.metadata; "
                "print(importlib.metadata.distribution('herdr-engineering')"
                ".read_text('direct_url.json') or '{}')",
            ]
        )
    )
    if (
        origin.get("url") != release.as_uri()
        or not isinstance(origin.get("dir_info"), dict)
        or origin["dir_info"].get("editable", False)
    ):
        raise RuntimeError(
            "Installed HerdR distribution does not match the selected immutable release"
        )


def with_path_prefix(path_prefix):
    """(npm executable, environment) with ``path_prefix`` first on PATH, for npm and its scripts."""
    env = dict(os.environ)
    if path_prefix:
        env["PATH"] = str(path_prefix) + os.pathsep + env.get("PATH", "")
    return shutil.which("npm", path=env.get("PATH")) or "npm", env


def _install_pi(release, receipt, path_prefix, runner):
    package = json.loads((release / "package.json").read_text())
    if package.get("name") != "pi-engineering-runtime":
        raise ValueError("Selected source is not Pi Engineering")
    if not receipt.exists() or not (release / "node_modules").is_dir():
        npm, env = with_path_prefix(path_prefix)
        runner([npm, "ci", "--include=dev", "--no-audit", "--no-fund"], cwd=str(release), env=env)


def _install_herdr(home, release, python, runner):
    venv = home / ".venvs/herdr-engineering"
    if not (venv / "bin/python").exists():
        runner([python, "-m", "venv", str(venv)])
    runner([str(venv / "bin/python"), "-m", "pip", "install", "--quiet", "--upgrade", str(release)])
    verify_herdr_distribution(home, release, runner)


def detect_indent(raw):
    """The indent unit of an existing JSON document (a tab, or N spaces); 2 when it has none."""
    match = re.search(rb"^([ \t]+)\S", raw or b"", re.MULTILINE)
    if match is None:
        return 2
    unit = match.group(1)
    return "\t" if unit.startswith(b"\t") else len(unit)


def acquire_settings_lock(lock):
    """mkdir the lock, removing a stale one and waiting briefly for a live one."""
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            lock.mkdir(mode=0o700)
            return
        except FileExistsError:
            pass
        try:
            if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
                lock.rmdir()  # empty directory: a live writer's lock is never forced
                continue
        except FileNotFoundError:
            continue
        except OSError:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("Pi settings are locked; retry safely")
        time.sleep(LOCK_POLL_SECONDS)


def _write_settings(pending):
    settings, before, value = pending
    settings.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Pi's FileSettingsStorage uses proper-lockfile with realpath:false: the atomic
    # settings.json.lock mkdir is its writer exclusion protocol.
    settings_lock = settings.with_name(settings.name + ".lock")
    acquire_settings_lock(settings_lock)
    try:
        if (settings.read_bytes() if settings.exists() else None) != before:
            raise RuntimeError("Pi settings changed during staging; retry safely")
        # Replace the file a symlink points at, not the link (dotfile managers keep it elsewhere).
        atomic_json(
            Path(os.path.realpath(str(settings))),
            value,
            indent=detect_indent(before),
            trailing_newline=before is None or before.endswith(b"\n"),
        )
    finally:
        settings_lock.rmdir()


def stage(home, revision, kind, repository, *, python="python3", path_prefix=None, runner=run):
    validate_revision(revision)
    home = home.expanduser().resolve()
    root = home / ".local/share/herdr-engineering" / kind
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "stage.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        release = root / "releases" / revision
        pending_settings = registration(home, release, repository) if kind == "pi" else None
        release = checkout(root, revision, repository, runner)
        receipt = root / "receipts" / (revision + ".json")
        if kind == "pi":
            _install_pi(release, receipt, path_prefix, runner)
        else:
            _install_herdr(home, release, python, runner)
        verify_checkout(release, revision, runner)
        result = {"revision": revision, "sourceRoot": str(release)}
        atomic_json(receipt, result)
        if pending_settings:
            _write_settings(pending_settings)
        atomic_json(root / "installed.json", result)
        return result


def describe(home, kind, runner=run):
    """The staged release if it is intact and registered, else None. Offline and read-only."""
    home = home.expanduser().resolve()
    root = home / ".local/share/herdr-engineering" / kind
    try:
        result = json.loads((root / "installed.json").read_text())
        revision = result["revision"]
        validate_revision(revision)
        release = root / "releases" / revision
        if result["sourceRoot"] != str(release):
            return None
        verify_checkout(release, revision, runner)
        if json.loads((root / "receipts" / (revision + ".json")).read_text()) != result:
            return None
        if kind == "pi":
            settings = json.loads((home / ".pi/agent/settings.json").read_text())
            packages = settings.get("packages", [])
            sources = [
                e if isinstance(e, str) else e.get("source") if isinstance(e, dict) else None
                for e in packages
            ]
            if str(release) not in sources or not (release / "node_modules").is_dir():
                return None
        else:
            verify_herdr_distribution(home, release, runner)
        return result
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError):
        return None


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=KINDS, required=True)
    parser.add_argument("--revision")
    parser.add_argument("--repository", help="clone URL of the repository (required to stage)")
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--python", default="python3", help="interpreter that creates the venv")
    parser.add_argument("--path-prefix", help="directory searched first for npm and node")
    parser.add_argument("--describe", action="store_true")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.describe:
        result = describe(args.home, args.kind)
        print(json.dumps(result or {}))
        return 0 if result else 1
    if not args.revision:
        parser.error("--revision is required for staging")
    if not args.repository:
        parser.error("--repository is required for staging")
    result = stage(
        args.home,
        args.revision,
        args.kind,
        args.repository,
        python=args.python,
        path_prefix=args.path_prefix,
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
