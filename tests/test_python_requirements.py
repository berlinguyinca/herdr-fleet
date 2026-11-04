"""The optional ``python_requirements`` lock field: schema, loader, pins and extra-vars.

Real files in a real directory next to a real lock; no mocks.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from herdr_fleet.config import ConfigError, load_config, load_lock
from herdr_fleet.herdr_engineering import unpinned_python_note
from herdr_fleet.inventory import build_extra_vars
from herdr_fleet.pins import fleet_pins
from herdr_fleet.python_requirements import python_requirements
from tests.helpers import CONFIG_EXAMPLE, example_lock, write_yaml

HASHED = "fleetdep==1.0.0 \\\n    --hash=sha256:" + "a" * 64 + "\n"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def lock_with(directory: Path, text: str = HASHED, *, file: str = "he.requirements.txt") -> Path:
    (directory / file).write_text(text)
    doc = example_lock()
    doc["python_requirements"] = {"herdr-engineering": {"file": file, "sha256": sha(text)}}
    return write_yaml(directory / "fleet.lock", doc)


def test_the_field_is_optional_and_absent_means_none(tmp_path: Path) -> None:
    lock = load_lock(write_yaml(tmp_path / "fleet.lock", example_lock()))
    assert python_requirements(lock, "herdr-engineering") is None


def test_a_valid_field_resolves_next_to_the_lock(tmp_path: Path) -> None:
    lock = load_lock(lock_with(tmp_path))
    found = python_requirements(lock, "herdr-engineering")
    assert found is not None
    assert found.path == tmp_path / "he.requirements.txt"
    assert found.sha256 == sha(HASHED)


@pytest.mark.parametrize(
    "file", ["../escape.txt", "/etc/passwd", "a/../b.txt", ".hidden", "sp ace.txt", ""]
)
def test_schema_rejects_paths_that_leave_the_lock_directory(tmp_path: Path, file: str) -> None:
    doc = example_lock()
    doc["python_requirements"] = {"herdr-engineering": {"file": file, "sha256": "a" * 64}}
    with pytest.raises(ConfigError) as info:
        load_lock(write_yaml(tmp_path / "fleet.lock", doc))
    assert "python_requirements.herdr-engineering.file" in str(info.value)


def test_schema_rejects_a_short_sha_and_unknown_keys(tmp_path: Path) -> None:
    doc = example_lock()
    doc["python_requirements"] = {"herdr-engineering": {"file": "x.txt", "sha256": "abc"}}
    with pytest.raises(ConfigError, match="python_requirements.herdr-engineering.sha256"):
        load_lock(write_yaml(tmp_path / "fleet.lock", doc))
    doc["python_requirements"] = {"other": {"file": "x.txt", "sha256": "a" * 64}}
    with pytest.raises(ConfigError, match="python_requirements"):
        load_lock(write_yaml(tmp_path / "fleet.lock", doc))


def test_a_missing_file_names_the_key_and_the_remedy(tmp_path: Path) -> None:
    path = lock_with(tmp_path)
    (tmp_path / "he.requirements.txt").unlink()
    with pytest.raises(ConfigError) as info:
        python_requirements(load_lock(path), "herdr-engineering")
    text = str(info.value)
    assert "python_requirements.herdr-engineering.file" in text and "he.requirements.txt" in text


def test_a_file_that_does_not_match_the_lock_sha256_is_refused(tmp_path: Path) -> None:
    path = lock_with(tmp_path)
    (tmp_path / "he.requirements.txt").write_text(HASHED + "evil==1 --hash=sha256:" + "b" * 64)
    with pytest.raises(ConfigError) as info:
        python_requirements(load_lock(path), "herdr-engineering")
    text = str(info.value)
    assert "sha256" in text and sha(HASHED) in text


def test_a_file_without_any_hash_is_refused(tmp_path: Path) -> None:
    path = lock_with(tmp_path, "fleetdep==1.0.0\n")
    with pytest.raises(ConfigError, match="--hash=sha256"):
        python_requirements(load_lock(path), "herdr-engineering")


def test_a_symlink_out_of_the_lock_directory_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "real.txt").write_text(HASHED)
    inside = tmp_path / "inside"
    inside.mkdir()
    (inside / "he.requirements.txt").symlink_to(outside / "real.txt")
    doc = example_lock()
    doc["python_requirements"] = {
        "herdr-engineering": {"file": "he.requirements.txt", "sha256": sha(HASHED)}
    }
    lock = load_lock(write_yaml(inside / "fleet.lock", doc))
    with pytest.raises(ConfigError, match="outside"):
        python_requirements(lock, "herdr-engineering")


def test_extra_vars_carry_the_file_and_sha_only_when_the_field_is_set(tmp_path: Path) -> None:
    config = load_config(CONFIG_EXAMPLE)
    plain = build_extra_vars(load_lock(write_yaml(tmp_path / "plain.lock", example_lock())), config)
    assert "herdr_engineering_requirements_sha256" not in plain
    pinned = build_extra_vars(load_lock(lock_with(tmp_path)), config)
    assert pinned["herdr_engineering_requirements_sha256"] == sha(HASHED)
    assert pinned["herdr_engineering_requirements_src"] == str(tmp_path / "he.requirements.txt")


def test_pins_report_the_requirements_sha256(tmp_path: Path) -> None:
    config = load_config(CONFIG_EXAMPLE)
    pins = fleet_pins(load_lock(lock_with(tmp_path)), config)
    assert pins.herdr_engineering.requirements_sha256 == sha(HASHED)
    bare = fleet_pins(load_lock(write_yaml(tmp_path / "b.lock", example_lock())), config)
    assert bare.herdr_engineering.requirements_sha256 is None


def test_the_unpinned_note_appears_only_without_the_field(tmp_path: Path) -> None:
    config = load_config(CONFIG_EXAMPLE)
    bare = fleet_pins(load_lock(write_yaml(tmp_path / "b.lock", example_lock())), config)
    note = unpinned_python_note(bare.herdr_engineering)
    assert note is not None and "unpinned python dependencies" in note
    assert "python_requirements" in note
    pinned = fleet_pins(load_lock(lock_with(tmp_path)), config)
    assert unpinned_python_note(pinned.herdr_engineering) is None


# --- the stager installs from the hashed file with real pip and a local wheel directory ----------

import subprocess  # noqa: E402
import sys  # noqa: E402

from herdr_fleet.stage_release import REQUIREMENTS_MARKER, describe, run, stage  # noqa: E402
from tests.eng_repo import RELEASE_FILES, make_source  # noqa: E402
from tests.wheels import build_wheel, hashed_requirements  # noqa: E402


def release_needing(*requires: str) -> dict[str, str]:
    """The tiny herdr-engineering release, declaring ``requires`` in its wheel METADATA."""
    metadata = "".join(f"Requires-Dist: {r}\\n" for r in requires)
    needle = r'f"Version: {VERSION}\n",'
    backend = RELEASE_FILES["backend.py"]
    assert needle in backend
    backend = backend.replace(needle, needle[:-2] + metadata + '",')
    return {**RELEASE_FILES, "backend.py": backend}


@pytest.fixture
def local_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """pip may only use the wheels in this directory: no PyPI, no network."""
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_FIND_LINKS", str(wheels))
    monkeypatch.setenv("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    return wheels


def venv_python(home: Path) -> Path:
    return home / ".venvs/herdr-engineering/bin/python"


def imports(home: Path, module: str) -> bool:
    done = subprocess.run(
        [str(venv_python(home)), "-I", "-c", f"import {module}"], capture_output=True
    )
    return done.returncode == 0


def test_pinned_install_uses_the_hashed_file_and_leaves_a_marker(
    tmp_path: Path, local_index: Path
) -> None:
    dep = build_wheel(local_index, "fleetdep", "1.0.0")
    text = hashed_requirements(local_index, dep)
    reqs = tmp_path / "r.txt"
    reqs.write_text(text)
    source = make_source(tmp_path / "src", release_needing("fleetdep==1.0.0"))
    home = tmp_path / "home"
    home.mkdir()
    result = stage(
        home,
        source.revision,
        "herdr",
        source.url,
        python=sys.executable,
        runner=run,
        requirements=(reqs, sha(text)),
    )
    assert imports(home, "fleetdep")
    assert (venv_python(home).parent.parent / REQUIREMENTS_MARKER).read_text().strip() == sha(text)
    assert describe(home, "herdr") == result


def test_a_dependency_missing_from_the_file_is_not_resolved_behind_its_back(
    tmp_path: Path, local_index: Path
) -> None:
    dep = build_wheel(local_index, "fleetdep", "1.0.0")
    build_wheel(local_index, "sneaky", "1.0.0")  # on the "index", declared, but not in the file
    text = hashed_requirements(local_index, dep)
    reqs = tmp_path / "r.txt"
    reqs.write_text(text)
    source = make_source(tmp_path / "src", release_needing("fleetdep==1.0.0", "sneaky==1.0.0"))
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(subprocess.CalledProcessError, match="sneaky"):  # pip check
        stage(
            home,
            source.revision,
            "herdr",
            source.url,
            python=sys.executable,
            runner=run,
            requirements=(reqs, sha(text)),
        )
    assert imports(home, "fleetdep") and not imports(home, "sneaky")


def test_a_wheel_that_does_not_match_its_hash_installs_nothing(
    tmp_path: Path, local_index: Path
) -> None:
    dep = build_wheel(local_index, "fleetdep", "1.0.0")
    text = hashed_requirements(local_index, dep)
    # the index is tampered with after the file was generated
    dep.unlink()
    build_wheel(local_index, "fleetdep", "1.0.0", body="VERSION = 'evil'\n")
    reqs = tmp_path / "r.txt"
    reqs.write_text(text)
    source = make_source(tmp_path / "src", release_needing("fleetdep==1.0.0"))
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(subprocess.CalledProcessError):
        stage(
            home,
            source.revision,
            "herdr",
            source.url,
            python=sys.executable,
            runner=run,
            requirements=(reqs, sha(text)),
        )
    assert not imports(home, "fleetdep")
    assert describe(home, "herdr") is None


def test_a_requirements_file_that_changed_since_the_lock_is_refused_before_pip_runs(
    tmp_path: Path, local_index: Path
) -> None:
    dep = build_wheel(local_index, "fleetdep", "1.0.0")
    text = hashed_requirements(local_index, dep)
    reqs = tmp_path / "r.txt"
    reqs.write_text(text + "# edited\n")
    source = make_source(tmp_path / "src", release_needing())
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(RuntimeError, match="sha256"):
        stage(
            home,
            source.revision,
            "herdr",
            source.url,
            python=sys.executable,
            runner=run,
            requirements=(reqs, sha(text)),
        )
    assert not (home / ".venvs").exists()  # not even the venv was created


def test_the_command_line_takes_both_flags_or_neither(tmp_path: Path) -> None:
    helper = Path(sys.modules["herdr_fleet.stage_release"].__file__)  # type: ignore[arg-type]
    done = subprocess.run(
        [
            sys.executable,
            str(helper),
            "--kind",
            "herdr",
            "--revision",
            "a" * 40,
            "--repository",
            "file:///x",
            "--home",
            str(tmp_path),
            "--requirements",
            str(tmp_path / "r.txt"),
        ],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 2 and "go together" in done.stderr
    done = subprocess.run(
        [
            sys.executable,
            str(helper),
            "--kind",
            "pi",
            "--revision",
            "a" * 40,
            "--repository",
            "file:///x",
            "--home",
            str(tmp_path),
            "--requirements",
            str(tmp_path / "r.txt"),
            "--requirements-sha256",
            "a" * 64,
        ],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 2 and "--kind herdr only" in done.stderr


def test_unpinned_install_is_unchanged_and_clears_a_stale_marker(
    tmp_path: Path, local_index: Path
) -> None:
    source = make_source(tmp_path / "src", release_needing())
    home = tmp_path / "home"
    home.mkdir()
    stage(home, source.revision, "herdr", source.url, python=sys.executable, runner=run)
    marker = venv_python(home).parent.parent / REQUIREMENTS_MARKER
    assert not marker.exists()
    marker.write_text("stale\n")
    second = source.commit({"VERSION.txt": "2\n"})
    stage(home, second.revision, "herdr", second.url, python=sys.executable, runner=run)
    assert not marker.exists()


# --- the informational note on the CLI ----------------------------------------------------------


def _plan_notes(lock_path: Path, capsys: pytest.CaptureFixture[str]) -> list[str]:
    import json

    from herdr_fleet.cli import main

    code = main(["plan", "--lock", str(lock_path), "--config", str(CONFIG_EXAMPLE), "--json"])
    assert code == 0
    return list(json.loads(capsys.readouterr().out)["notes"])


def test_plan_reports_an_unpinned_python_dependencies_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = _plan_notes(write_yaml(tmp_path / "fleet.lock", example_lock()), capsys)
    assert any("unpinned python dependencies" in n for n in notes)


def test_plan_has_no_note_when_requirements_are_pinned(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _plan_notes(lock_with(tmp_path), capsys) == []
