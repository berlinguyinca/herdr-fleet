"""Stager failure reporting, pinned-install integrity and settings handling, with real git/pip.

pip only reads wheels from a local directory (the ``local_index`` fixture); repositories are
``file://`` remotes; every HOME is a throwaway directory.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from herdr_fleet import stage_release
from herdr_fleet.stage_release import REQUIREMENTS_MARKER, describe, run, stage
from tests.eng_repo import make_source
from tests.test_python_requirements import (
    imports,
    local_index,
    release_needing,
    sha,
    venv_python,
)
from tests.test_stage_release import (
    cli,
    npm_bin,
    pi_repo,
    root_of,
    settings_of,
    stage_pi,
)
from tests.wheels import build_wheel, hashed_requirements


def marker_of(home: Path) -> Path:
    return venv_python(home).parent.parent / REQUIREMENTS_MARKER


def pinned(home: Path, source, text: str, directory: Path) -> dict[str, str]:  # type: ignore[no-untyped-def]
    reqs = directory / f"r-{sha(text)[:8]}.txt"
    reqs.write_text(text)
    return stage(
        home,
        source.revision,
        "herdr",
        source.url,
        python=sys.executable,
        runner=run,
        requirements=(reqs, sha(text)),
    )


# --- failures are explained, not hidden -------------------------------------------------------


def test_run_failure_carries_the_stderr_of_the_command() -> None:
    with pytest.raises(subprocess.CalledProcessError) as info:
        run(["git", "-C", "/nonexistent-herdr-fleet-dir", "rev-parse", "HEAD"])
    assert "not a git repository" in str(info.value) or "cannot change to" in str(info.value)


def test_a_failing_git_fetch_prints_git_s_own_message_and_exits_one(tmp_path: Path) -> None:
    done = cli(
        "--kind", "pi", "--revision", "a" * 40, "--repository", (tmp_path / "gone").as_uri(),
        home=tmp_path / "home",
    )  # fmt: skip
    assert done.returncode == 1
    assert "Traceback" not in done.stderr
    assert "git" in done.stderr and "fetch" in done.stderr
    assert "gone" in done.stderr  # git's own "does not appear to be a git repository" detail


def test_a_pip_hash_mismatch_prints_pips_explanation_and_exits_one(
    tmp_path: Path, local_index: Path
) -> None:
    dep = build_wheel(local_index, "fleetdep", "1.0.0")
    text = hashed_requirements(local_index, dep)
    dep.unlink()
    build_wheel(local_index, "fleetdep", "1.0.0", body="VERSION = 'evil'\n")
    reqs = tmp_path / "r.txt"
    reqs.write_text(text)
    source = make_source(tmp_path / "src", release_needing("fleetdep==1.0.0"))
    done = cli(
        "--kind", "herdr", "--revision", source.revision, "--repository", source.url,
        "--python", sys.executable, "--requirements", str(reqs),
        "--requirements-sha256", sha(text),
        home=tmp_path / "home",
    )  # fmt: skip
    assert done.returncode == 1
    assert "Traceback" not in done.stderr
    assert "DO NOT MATCH THE HASHES" in done.stderr


# --- pip check ---------------------------------------------------------------------------------


def test_a_dependency_missing_from_the_file_fails_pip_check_and_leaves_no_marker(
    tmp_path: Path, local_index: Path
) -> None:
    dep = build_wheel(local_index, "fleetdep", "1.0.0")
    build_wheel(local_index, "sneaky", "1.0.0")  # available, declared, but not in the file
    text = hashed_requirements(local_index, dep)
    source = make_source(tmp_path / "src", release_needing("fleetdep==1.0.0", "sneaky==1.0.0"))
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(subprocess.CalledProcessError) as info:
        pinned(home, source, text, tmp_path)
    assert "sneaky" in str(info.value)
    assert not imports(home, "sneaky")
    assert not marker_of(home).exists()
    assert describe(home, "herdr") is None


# --- the marker never outlives a failed install; dropped packages go away ----------------------


def test_a_failed_reinstall_cannot_leave_the_previous_marker_behind(
    tmp_path: Path, local_index: Path
) -> None:
    dep = build_wheel(local_index, "fleetdep", "1.0.0")
    text = hashed_requirements(local_index, dep)
    source = make_source(tmp_path / "src", release_needing("fleetdep==1.0.0"))
    home = tmp_path / "home"
    home.mkdir()
    pinned(home, source, text, tmp_path)
    assert marker_of(home).read_text().strip() == sha(text)

    bad = text.replace(text.split("--hash=sha256:")[1].strip(), "0" * 64)
    with pytest.raises(subprocess.CalledProcessError):
        pinned(home, source.commit({"note.txt": "x"}), bad, tmp_path)
    assert not marker_of(home).exists()


def test_packages_dropped_from_the_requirements_are_gone_after_the_next_stage(
    tmp_path: Path, local_index: Path
) -> None:
    dep = build_wheel(local_index, "fleetdep", "1.0.0")
    old = build_wheel(local_index, "olddep", "1.0.0")
    both = hashed_requirements(local_index, dep, old)
    only = hashed_requirements(local_index, dep)
    source = make_source(tmp_path / "src", release_needing("fleetdep==1.0.0", "olddep==1.0.0"))
    home = tmp_path / "home"
    home.mkdir()
    pinned(home, source, both, tmp_path)
    assert imports(home, "olddep")

    newer = source.commit(release_needing("fleetdep==1.0.0"))
    result = pinned(home, newer, only, tmp_path)
    assert imports(home, "fleetdep") and not imports(home, "olddep")
    assert describe(home, "herdr") == result
    assert marker_of(home).read_text().strip() == sha(only)


def test_an_unchanged_requirements_file_keeps_the_venv(tmp_path: Path, local_index: Path) -> None:
    dep = build_wheel(local_index, "fleetdep", "1.0.0")
    text = hashed_requirements(local_index, dep)
    source = make_source(tmp_path / "src", release_needing("fleetdep==1.0.0"))
    home = tmp_path / "home"
    home.mkdir()
    pinned(home, source, text, tmp_path)
    sentinel = venv_python(home).parent.parent / "sentinel"
    sentinel.write_text("kept")
    pinned(home, source.commit({"note.txt": "x"}), text, tmp_path)
    assert sentinel.read_text() == "kept"


# --- Pi registration: only the managed release root is ours to repoint -----------------------


def package_dir(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "package.json").write_text(json.dumps({"name": "pi-engineering-runtime"}))
    return path


def test_a_local_checkout_outside_the_managed_root_is_left_alone(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    checkout = package_dir(tmp_path / "dev" / "pi-engineering")
    settings_of(home).parent.mkdir(parents=True)
    settings_of(home).write_text(json.dumps({"packages": [str(checkout)]}))
    result = stage_pi(home, pi_repo, npm_bin)
    assert json.loads(settings_of(home).read_text())["packages"] == [
        str(checkout),
        result["sourceRoot"],
    ]


def test_a_previous_managed_release_is_repointed(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    old = package_dir(root_of(home, "pi") / "releases" / ("b" * 40))
    settings_of(home).parent.mkdir(parents=True)
    settings_of(home).write_text(json.dumps({"packages": [str(old)]}))
    result = stage_pi(home, pi_repo, npm_bin)
    assert json.loads(settings_of(home).read_text())["packages"] == [result["sourceRoot"]]


# --- settings.json file mode -------------------------------------------------------------------


def test_the_mode_of_an_existing_settings_file_is_preserved(
    tmp_path: Path, pi_repo: tuple[str, str], npm_bin: Path
) -> None:
    home = tmp_path / "home"
    settings_of(home).parent.mkdir(parents=True)
    settings_of(home).write_text("{}\n")
    settings_of(home).chmod(0o640)
    stage_pi(home, pi_repo, npm_bin)
    assert settings_of(home).stat().st_mode & 0o777 == 0o640


def test_the_module_still_names_no_operator_repository() -> None:
    assert "github.com" not in Path(stage_release.__file__).read_text()
