"""The vendored release stager against real git repositories, a real npm script and real pip.

Repositories are local ``file://`` remotes in a throwaway directory, ``npm`` is a script on PATH
(like the toolchain tests), and the herdr package is a tiny in-tree PEP 517 project, so nothing
reaches the network or the real HOME.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from herdr_fleet import stage_release
from herdr_fleet.stage_release import describe, run, stage

PI_PACKAGE = {"name": "pi-engineering-runtime", "version": "1.0.0"}
BACKEND = """\
import base64, hashlib, zipfile

NAME, VERSION = "herdr_engineering", "1.0.0"


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    dist = f"{NAME}-{VERSION}.dist-info"
    files = {
        f"{dist}/METADATA": "Metadata-Version: 2.1\\nName: herdr-engineering\\n"
        f"Version: {VERSION}\\n",
        f"{dist}/WHEEL": "Wheel-Version: 1.0\\nGenerator: tiny\\n"
        "Root-Is-Purelib: true\\nTag: py3-none-any\\n",
        f"{NAME}.py": "VALUE = 1\\n",
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
FAKE_NPM = """#!/bin/sh
# usage: npm ci --include=dev --no-audit --no-fund (cwd = the release)
mkdir -p node_modules/dep
echo "$PWD" >> "$NPM_LOG"
"""


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


def make_repo(path: Path, files: dict[str, str]) -> tuple[str, str]:
    """A real repository with one commit; (file:// URL, full commit id)."""
    path.mkdir(parents=True)
    git(path, "init", "-q")
    for name, text in files.items():
        (path / name).write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "release")
    git(path, "config", "uploadpack.allowAnySHA1InWant", "true")
    return path.as_uri(), git(path, "rev-parse", "HEAD")


@pytest.fixture
def pi_repo(tmp_path: Path) -> tuple[str, str]:
    return make_repo(tmp_path / "pi-src", {"package.json": json.dumps(PI_PACKAGE)})


@pytest.fixture
def herdr_repo(tmp_path: Path) -> tuple[str, str]:
    return make_repo(
        tmp_path / "herdr-src",
        {
            "pyproject.toml": PYPROJECT,
            "backend.py": BACKEND,
            ".gitignore": "__pycache__/\nbuild/\n",
        },
    )


@pytest.fixture
def npm_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "tools"
    directory.mkdir()
    npm = directory / "npm"
    npm.write_text(FAKE_NPM)
    npm.chmod(0o755)
    monkeypatch.setenv("NPM_LOG", str(tmp_path / "npm.log"))
    return directory


def root_of(home: Path, kind: str) -> Path:
    return home / ".local/share/herdr-engineering" / kind


def settings_of(home: Path) -> Path:
    return home / ".pi/agent/settings.json"


def stage_pi(home: Path, repo: tuple[str, str], npm_bin: Path) -> dict[str, str]:
    url, revision = repo
    return stage(home, revision, "pi", url, path_prefix=str(npm_bin), runner=run)


# --- pi --------------------------------------------------------------------------------------


def test_staging_pi_creates_release_receipt_installed_and_registration(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    result = stage_pi(home, pi_repo, npm_bin)
    release = root_of(home, "pi") / "releases" / pi_repo[1]
    assert result == {"revision": pi_repo[1], "sourceRoot": str(release)}
    assert (release / "node_modules" / "dep").is_dir()
    assert (
        json.loads((root_of(home, "pi") / "receipts" / f"{pi_repo[1]}.json").read_text()) == result
    )
    assert json.loads((root_of(home, "pi") / "installed.json").read_text()) == result
    assert json.loads(settings_of(home).read_text())["packages"] == [str(release)]
    assert describe(home, "pi") == result


def test_restaging_the_same_revision_installs_nothing_again(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    first = stage_pi(home, pi_repo, npm_bin)
    second = stage_pi(home, pi_repo, npm_bin)
    assert second == first
    assert (tmp_path / "npm.log").read_text().count("\n") == 1


def test_settings_merge_keeps_unrelated_keys_and_other_packages(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    settings_of(home).parent.mkdir(parents=True)
    settings_of(home).write_text(
        json.dumps({"theme": "dark", "packages": ["npm:other", {"source": "npm:more"}]})
    )
    stage_pi(home, pi_repo, npm_bin)
    merged = json.loads(settings_of(home).read_text())
    release = str(root_of(home, "pi") / "releases" / pi_repo[1])
    assert merged["theme"] == "dark"
    assert merged["packages"] == ["npm:other", {"source": "npm:more"}, release]


def test_an_existing_registration_of_this_repository_is_replaced_not_duplicated(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    settings_of(home).parent.mkdir(parents=True)
    old = {"source": pi_repo[0] + "@main", "extensions": ["x"]}
    settings_of(home).write_text(json.dumps({"packages": [old]}))
    stage_pi(home, pi_repo, npm_bin)
    release = str(root_of(home, "pi") / "releases" / pi_repo[1])
    assert json.loads(settings_of(home).read_text())["packages"] == [
        {"source": release, "extensions": ["x"]}
    ]


def test_invalid_settings_json_is_refused_and_left_alone(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    settings_of(home).parent.mkdir(parents=True)
    settings_of(home).write_text("{not json")
    with pytest.raises(ValueError):
        stage_pi(home, pi_repo, npm_bin)
    assert settings_of(home).read_text() == "{not json"
    assert not (root_of(home, "pi") / "installed.json").exists()


def test_multiple_registrations_are_a_hard_error(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    settings_of(home).parent.mkdir(parents=True)
    settings_of(home).write_text(
        json.dumps({"packages": ["npm:pi-engineering-runtime", "npm:pi-engineering-runtime@2"]})
    )
    with pytest.raises(ValueError, match="Multiple Pi Engineering registrations"):
        stage_pi(home, pi_repo, npm_bin)


def test_a_non_pi_source_is_refused(tmp_path: Path, npm_bin: Path) -> None:
    repo = make_repo(tmp_path / "other", {"package.json": json.dumps({"name": "other"})})
    with pytest.raises(ValueError, match="not Pi Engineering"):
        stage_pi(tmp_path / "home", repo, npm_bin)


def test_a_locked_settings_file_asks_to_retry_and_stages_no_installed_marker(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    (settings_of(home).parent / "settings.json.lock").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="locked"):
        stage_pi(home, pi_repo, npm_bin)
    assert not (root_of(home, "pi") / "installed.json").exists()


def age(path: Path, seconds: int) -> None:
    moment = time.time() - seconds
    os.utime(path, (moment, moment))


def test_a_stale_settings_lock_from_a_crashed_writer_is_removed(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    lock = settings_of(home).parent / "settings.json.lock"
    lock.mkdir(parents=True)
    age(lock, 120)
    result = stage_pi(home, pi_repo, npm_bin)
    assert json.loads(settings_of(home).read_text())["packages"] == [result["sourceRoot"]]
    assert not lock.exists()


def test_a_fresh_settings_lock_is_waited_for_briefly_then_reported(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stage_release, "LOCK_WAIT_SECONDS", 0.6)
    home = tmp_path / "home"
    lock = settings_of(home).parent / "settings.json.lock"
    lock.mkdir(parents=True)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="locked"):
        stage_pi(home, pi_repo, npm_bin)
    assert time.monotonic() - started >= 0.5
    assert lock.is_dir()  # a live writer's lock is never removed


def test_a_lock_released_during_the_wait_lets_staging_finish(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    lock = settings_of(home).parent / "settings.json.lock"
    lock.mkdir(parents=True)
    releaser = threading.Timer(0.5, lock.rmdir)
    releaser.start()
    try:
        result = stage_pi(home, pi_repo, npm_bin)
    finally:
        releaser.join()
    assert describe(home, "pi") == result


def test_a_symlinked_settings_file_stays_a_symlink_and_its_target_is_updated(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    real = tmp_path / "dotfiles" / "pi-settings.json"
    real.parent.mkdir()
    real.write_text('{"theme": "dark"}\n')
    settings_of(home).parent.mkdir(parents=True)
    settings_of(home).symlink_to(real)
    result = stage_pi(home, pi_repo, npm_bin)
    assert settings_of(home).is_symlink() and settings_of(home).resolve() == real
    assert json.loads(real.read_text())["packages"] == [result["sourceRoot"]]
    assert describe(home, "pi") == result


def test_settings_keep_their_key_order_and_indentation(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    settings_of(home).parent.mkdir(parents=True)
    settings_of(home).write_text(
        '{\n    "zeta": 1,\n    "alpha": {"b": 2, "a": 1},\n    "mid": 3\n}\n'
    )
    stage_pi(home, pi_repo, npm_bin)
    text = settings_of(home).read_text()
    assert list(json.loads(text)) == ["zeta", "alpha", "mid", "packages"]
    assert text.splitlines()[1] == '    "zeta": 1,'
    assert text.endswith("}\n")
    settings_of(home).write_text('{\n\t"b": 1,\n\t"a": 2\n}')
    stage_release.stage(home, pi_repo[1], "pi", pi_repo[0], path_prefix=str(npm_bin), runner=run)
    assert settings_of(home).read_text().splitlines()[1] == '\t"b": 1,'


def test_describe_treats_a_non_string_non_object_package_as_not_attested(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    result = stage_pi(home, pi_repo, npm_bin)
    value = json.loads(settings_of(home).read_text())
    value["packages"] = [42, None, ["x"], result["sourceRoot"]]
    settings_of(home).write_text(json.dumps(value))
    assert describe(home, "pi") == result
    value["packages"] = [42, None]
    settings_of(home).write_text(json.dumps(value))
    assert describe(home, "pi") is None


def test_a_dirty_checkout_is_refused_and_preserved(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    stage_pi(home, pi_repo, npm_bin)
    release = root_of(home, "pi") / "releases" / pi_repo[1]
    (release / "package.json").write_text("{}")
    with pytest.raises(RuntimeError, match="dirty"):
        stage_pi(home, pi_repo, npm_bin)
    assert (release / "package.json").read_text() == "{}"


def test_a_checkout_at_the_wrong_commit_is_refused(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    stage_pi(home, pi_repo, npm_bin)
    release = root_of(home, "pi") / "releases" / pi_repo[1]
    (release / "x").write_text("x")
    git(release, "add", "x")
    git(release, "commit", "-q", "-m", "local")
    with pytest.raises(RuntimeError, match="wrong commit"):
        stage_pi(home, pi_repo, npm_bin)


@pytest.mark.parametrize("revision", ["main", "abc123", "G" * 40])
def test_only_a_full_commit_id_is_accepted(tmp_path: Path, revision: str) -> None:
    with pytest.raises(ValueError, match="full immutable commit"):
        stage(tmp_path / "home", revision, "pi", "file:///nowhere", runner=run)


def test_two_concurrent_stagers_hit_the_lock(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    import fcntl

    home = tmp_path / "home"
    root = root_of(home, "pi")
    root.mkdir(parents=True)
    with (root / "stage.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with pytest.raises(BlockingIOError):
            stage_pi(home, pi_repo, npm_bin)
    assert not (root / "releases").exists()


def test_describe_returns_nothing_after_the_release_is_modified(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    stage_pi(home, pi_repo, npm_bin)
    release = root_of(home, "pi") / "releases" / pi_repo[1]
    (release / "stray").write_text("x")
    assert describe(home, "pi") is None


def test_describe_returns_nothing_when_nothing_was_staged(tmp_path: Path) -> None:
    assert describe(tmp_path / "home", "pi") is None
    assert describe(tmp_path / "home", "herdr") is None


def test_describe_returns_nothing_when_the_registration_is_gone(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    stage_pi(home, pi_repo, npm_bin)
    settings_of(home).write_text(json.dumps({"packages": []}))
    assert describe(home, "pi") is None


# --- herdr -----------------------------------------------------------------------------------


def test_staging_herdr_installs_into_the_venv_and_describe_attests_it(
    tmp_path: Path, herdr_repo: tuple[str, str]
) -> None:
    home = tmp_path / "home"
    url, revision = herdr_repo
    result = stage(home, revision, "herdr", url, python=sys.executable, runner=run)
    release = root_of(home, "herdr") / "releases" / revision
    assert result == {"revision": revision, "sourceRoot": str(release)}
    assert (home / ".venvs/herdr-engineering/bin/python").exists()
    assert describe(home, "herdr") == result
    # a second stage reuses the venv and the release
    assert stage(home, revision, "herdr", url, python=sys.executable, runner=run) == result


def test_describe_herdr_fails_when_the_distribution_is_reinstalled_elsewhere(
    tmp_path: Path, herdr_repo: tuple[str, str]
) -> None:
    home = tmp_path / "home"
    url, revision = herdr_repo
    stage(home, revision, "herdr", url, python=sys.executable, runner=run)
    python = home / ".venvs/herdr-engineering/bin/python"
    subprocess.run(
        [str(python), "-m", "pip", "uninstall", "--yes", "--quiet", "herdr-engineering"],
        check=True,
        capture_output=True,
    )
    assert describe(home, "herdr") is None


# --- repository pattern and CLI -----------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "https://git.example.com/org/pi-engineering",
        "https://git.example.com/org/pi-engineering.git",
        "git.example.com/org/pi-engineering@v1",
        "git:https://git.example.com/org/pi-engineering.git",
        "git@git.example.com:org/pi-engineering.git",
    ],
)
def test_repository_pattern_matches_the_ways_a_clone_url_is_written(source: str) -> None:
    import re

    pattern = stage_release.repository_pattern("https://git.example.com/org/pi-engineering.git")
    assert re.fullmatch(pattern, source)


def test_repository_pattern_does_not_match_another_repository() -> None:
    import re

    pattern = stage_release.repository_pattern("https://git.example.com/org/pi-engineering.git")
    assert not re.fullmatch(pattern, "https://git.example.com/org/other")


def test_a_local_directory_package_named_pi_engineering_runtime_is_recognized(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "pkg"
    directory.mkdir()
    (directory / "package.json").write_text(json.dumps(PI_PACKAGE))
    assert stage_release.is_pi_package(str(directory), tmp_path, "https://example.com/a/b.git")
    assert not stage_release.is_pi_package(
        str(tmp_path / "missing"), tmp_path, "https://example.com/a/b.git"
    )


def test_a_malformed_package_entry_refuses_to_rewrite_settings(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid Pi package entry"):
        stage_release.is_pi_package(5, tmp_path, "https://example.com/a/b.git")


def cli(*args: str, home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, stage_release.__file__, "--home", str(home), *args],
        capture_output=True,
        text=True,
    )


def test_cli_stage_prints_the_result_and_describe_is_offline(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    url, revision = pi_repo
    staged = cli(
        "--kind", "pi", "--revision", revision, "--repository", url,
        "--path-prefix", str(npm_bin),
        home=home,
    )  # fmt: skip
    assert staged.returncode == 0, staged.stderr
    described = cli("--kind", "pi", "--describe", home=home)
    assert described.returncode == 0
    assert json.loads(described.stdout) == json.loads(staged.stdout)


def test_cli_describe_exits_one_with_empty_json_when_nothing_is_staged(tmp_path: Path) -> None:
    done = cli("--kind", "pi", "--describe", home=tmp_path / "home")
    assert done.returncode == 1
    assert json.loads(done.stdout) == {}


def test_cli_requires_a_revision_and_a_repository_to_stage(tmp_path: Path) -> None:
    no_revision = cli("--kind", "pi", "--repository", "file:///x", home=tmp_path)
    assert no_revision.returncode == 2 and "--revision" in no_revision.stderr
    no_repository = cli("--kind", "pi", "--revision", "a" * 40, home=tmp_path)
    assert no_repository.returncode == 2 and "--repository" in no_repository.stderr


def test_the_module_names_no_operator_repository() -> None:
    text = Path(stage_release.__file__).read_text()
    assert "github.com" not in text


def test_git_runs_with_optional_locks_disabled_so_a_check_never_races_a_developers_git() -> None:
    shown = run(["git", "-c", "alias.show-env=!printenv GIT_OPTIONAL_LOCKS", "show-env"])
    assert shown == "0"


def test_a_check_on_a_staged_release_leaves_the_git_index_untouched(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    result = stage_pi(home, pi_repo, npm_bin)
    index = Path(result["sourceRoot"]) / ".git" / "index"
    before = (index.stat().st_mtime_ns, index.read_bytes())
    time.sleep(0.05)
    assert describe(home, "pi") == result
    assert (index.stat().st_mtime_ns, index.read_bytes()) == before
