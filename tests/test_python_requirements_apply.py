"""The herdr-engineering role with a hashed requirements file: real apply, check and verify.

Real git, venv and pip; pip may only read wheels from a local directory (``PIP_NO_INDEX`` and
``PIP_FIND_LINKS``), so nothing reaches the network or the real HOME.
"""

from __future__ import annotations

import dataclasses
import hashlib
import subprocess
from pathlib import Path

import pytest

from herdr_fleet.herdr_engineering import (
    HerdrEngineeringFingerprint,
    compare_herdr_engineering,
)
from herdr_fleet.runner import Mode
from tests.eng_repo import Source, make_source
from tests.test_herdr_engineering_apply import changed, converge, pins_for, verify
from tests.test_python_requirements import release_needing
from tests.wheels import build_wheel, hashed_requirements


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclasses.dataclass(frozen=True)
class Pinned:
    source: Source
    requirements: Path
    text: str
    wheels: Path

    @property
    def over(self) -> dict[str, object]:
        return {
            "herdr_engineering_requirements_src": str(self.requirements),
            "herdr_engineering_requirements_sha256": sha(self.text),
        }

    def pins(self, text: str | None = None):  # type: ignore[no-untyped-def]
        return dataclasses.replace(
            pins_for(self.source),
            requirements_path=str(self.requirements),
            requirements_sha256=sha(text or self.text),
        )


@pytest.fixture
def pinned(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Pinned:
    base = tmp_path_factory.mktemp("pinned")
    wheels = base / "wheels"
    dep = build_wheel(wheels, "fleetdep", "1.0.0")
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_FIND_LINKS", str(wheels))
    monkeypatch.setenv("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    text = hashed_requirements(wheels, dep)
    reqs = base / "he.requirements.txt"
    reqs.write_text(text)
    source = make_source(base / "src", release_needing("fleetdep==1.0.0"))
    return Pinned(source, reqs, text, wheels)


def marker(home: Path) -> Path:
    return home / ".venvs/herdr-engineering/herdr-fleet-requirements.sha256"


def imports_dep(home: Path) -> bool:
    python = home / ".venvs/herdr-engineering/bin/python"
    return (
        subprocess.run([str(python), "-I", "-c", "import fleetdep"], capture_output=True).returncode
        == 0
    )


def section(home: Path, p: Pinned, mp: pytest.MonkeyPatch) -> HerdrEngineeringFingerprint:
    fp = verify(home, p.source, mp)
    return HerdrEngineeringFingerprint.from_json(fp.sections["herdr_engineering"])


def test_apply_installs_from_the_hashed_file_and_verify_is_clean(
    tmp_path: Path, pinned: Pinned, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, pinned.source, Mode.APPLY, monkeypatch, **pinned.over)
    assert imports_dep(tmp_path)
    fp = verify(tmp_path, pinned.source, monkeypatch)
    assert compare_herdr_engineering(fp, pinned.pins()) == []
    found = HerdrEngineeringFingerprint.from_json(fp.sections["herdr_engineering"])
    assert found.python_requirements_sha256 == sha(pinned.text)


def test_second_apply_changes_nothing_and_check_has_nothing_pending(
    tmp_path: Path, pinned: Pinned, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, pinned.source, Mode.APPLY, monkeypatch, **pinned.over)
    assert changed(converge(tmp_path, pinned.source, Mode.APPLY, monkeypatch, **pinned.over)) == []
    assert changed(converge(tmp_path, pinned.source, Mode.CHECK, monkeypatch, **pinned.over)) == []


def test_a_host_staged_without_the_file_is_named_and_apply_installs_the_pinned_set(
    tmp_path: Path, pinned: Pinned, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Staged before the lock gained the field: unpinned, and dependencies resolved from the index.
    converge(tmp_path, pinned.source, Mode.APPLY, monkeypatch)
    assert marker(tmp_path).exists() is False
    fp = verify(tmp_path, pinned.source, monkeypatch)
    messages = [d.message for d in compare_herdr_engineering(fp, pinned.pins())]
    assert messages == [f"python requirements: expected sha256 {sha(pinned.text)}, found missing"]
    pending = changed(converge(tmp_path, pinned.source, Mode.CHECK, monkeypatch, **pinned.over))
    assert any("(pending)" in t for t in pending)
    converge(tmp_path, pinned.source, Mode.APPLY, monkeypatch, **pinned.over)
    assert (
        compare_herdr_engineering(verify(tmp_path, pinned.source, monkeypatch), pinned.pins()) == []
    )


def test_a_changed_requirements_file_is_named_and_apply_reinstalls(
    tmp_path: Path, pinned: Pinned, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, pinned.source, Mode.APPLY, monkeypatch, **pinned.over)
    newer = build_wheel(pinned.wheels, "fleetdep", "2.0.0")
    text = hashed_requirements(pinned.wheels, newer)
    other = pinned.requirements.with_name("he2.requirements.txt")
    other.write_text(text)
    over = {
        "herdr_engineering_requirements_src": str(other),
        "herdr_engineering_requirements_sha256": sha(text),
    }
    messages = [
        d.message
        for d in compare_herdr_engineering(
            verify(tmp_path, pinned.source, monkeypatch), pinned.pins(text)
        )
    ]
    assert messages == [
        f"python requirements: expected sha256 {sha(text)}, found {sha(pinned.text)}"
    ]
    assert any(
        "(pending)" in t
        for t in changed(converge(tmp_path, pinned.source, Mode.CHECK, monkeypatch, **over))
    )
    converge(tmp_path, pinned.source, Mode.APPLY, monkeypatch, **over)
    assert marker(tmp_path).read_text().strip() == sha(text)
    assert (
        compare_herdr_engineering(verify(tmp_path, pinned.source, monkeypatch), pinned.pins(text))
        == []
    )


def test_a_requirements_file_that_does_not_match_its_sha_fails_apply_before_pip(
    tmp_path: Path, pinned: Pinned, monkeypatch: pytest.MonkeyPatch
) -> None:
    over = {**pinned.over, "herdr_engineering_requirements_sha256": "0" * 64}
    events = converge(tmp_path, pinned.source, Mode.APPLY, monkeypatch, check_ok=False, **over)
    assert any(t.status == "failed" for t in events.tasks)
    assert not imports_dep(tmp_path) if (tmp_path / ".venvs").exists() else True
    assert not marker(tmp_path).exists()


def test_without_the_field_nothing_changes_and_nothing_is_reported(
    tmp_path: Path, pinned: Pinned, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, pinned.source, Mode.APPLY, monkeypatch)
    fp = verify(tmp_path, pinned.source, monkeypatch)
    found = HerdrEngineeringFingerprint.from_json(fp.sections["herdr_engineering"])
    assert found.python_requirements_sha256 is None
    assert compare_herdr_engineering(fp, pins_for(pinned.source)) == []
