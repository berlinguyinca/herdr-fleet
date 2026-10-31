"""Real ``apply``, ``check`` and ``verify`` of the herdr-engineering role in a throwaway HOME.

The release comes from a real git repository at a ``file://`` URL; ``venv`` and ``pip`` are real
(the package is a tiny in-tree PEP 517 project). Nothing reaches the network or the real HOME.
"""

from __future__ import annotations

import hashlib
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from herdr_fleet.fingerprint import Fingerprint
from herdr_fleet.herdr_engineering import (
    HerdrEngineeringFingerprint,
    HerdrEngineeringPins,
    compare_herdr_engineering,
)
from herdr_fleet.report import ParsedEvents, parse_events
from herdr_fleet.runner import Mode, run_playbook
from tests.eng_repo import BUILD_GATE, UNIT, Source, make_source
from tests.helpers import example_extra_vars

GATEWAY = "https://gateway.example.com"

LOCAL = {
    "all": {
        "hosts": {
            "h": {
                "ansible_connection": "local",
                "ansible_python_interpreter": "auto_silent",
                "fleet_roles": ["herdr-engineering"],
            }
        }
    }
}


@pytest.fixture
def source(tmp_path_factory: pytest.TempPathFactory) -> Source:
    return make_source(tmp_path_factory.mktemp("src") / "herdr-engineering")


def pins_for(source: Source) -> HerdrEngineeringPins:
    return HerdrEngineeringPins(
        revision=source.revision,
        repository=source.url,
        gateway_url=GATEWAY,
        control_plane_url=None,
    )


def variables(source: Source, **over: object) -> dict[str, object]:
    extra = example_extra_vars()
    extra.update(
        herdr_engineering_revision=source.revision,
        herdr_engineering_repository=source.url,
        herdr_engineering_python=sys.executable,
        herdr_engineering_gateway_url=GATEWAY,
        herdr_engineering_control_plane_url=None,
    )
    extra.update(over)
    return extra


def converge(
    home: Path,
    source: Source,
    mode: Mode,
    monkeypatch: pytest.MonkeyPatch,
    *,
    check_ok: bool = True,
    **over: object,
) -> ParsedEvents:
    monkeypatch.setenv("HOME", str(home))
    # Point the role at a runtime directory with no user manager: it must never reach (let alone
    # start units in) the systemd of the machine running the tests.
    over.setdefault("herdr_engineering_runtime_dir", str(home / "no-systemd-here"))
    result = run_playbook(LOCAL, variables(source, **over), mode)
    if check_ok:
        assert result.returncode == 0, result.stdout[-3000:] + result.stderr
    return parse_events(result.stdout)


def changed(events: ParsedEvents) -> list[str]:
    return [t.task for t in events.tasks if t.status == "changed"]


def verify(home: Path, source: Source, monkeypatch: pytest.MonkeyPatch) -> Fingerprint:
    (home / ".bash_profile").write_text('export PATH="$HOME/.local/bin:$PATH"\n')
    events = converge(home, source, Mode.VERIFY, monkeypatch)
    assert not events.noise, events.noise
    return events.fingerprints["h"]


def releases(home: Path) -> Path:
    return home / ".local/share/herdr-engineering/herdr/releases"


def shim(home: Path) -> Path:
    return home / ".local/bin/herdr-eng"


def test_apply_stages_the_pinned_revision_and_verify_is_clean(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    assert (releases(tmp_path) / source.revision / "pyproject.toml").is_file()
    venv_entry = tmp_path / ".venvs/herdr-engineering/bin/herdr-eng"
    assert venv_entry.is_file()
    assert shim(tmp_path).is_symlink() and shim(tmp_path).resolve() == venv_entry.resolve()
    helper = tmp_path / ".local/share/herdr-fleet/helpers/stage_release.py"
    assert oct(helper.stat().st_mode & 0o777) == "0o700"
    fp = verify(tmp_path, source, monkeypatch)
    assert compare_herdr_engineering(fp, pins_for(source)) == []
    section = HerdrEngineeringFingerprint.from_json(fp.sections["herdr_engineering"])
    assert section.revision == source.revision
    assert section.source_root == str(releases(tmp_path) / source.revision)
    assert section.herdr_eng_version == "herdr-eng 1.0.0"


def test_shim_runs(tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    out = subprocess.run([str(shim(tmp_path)), "--version"], capture_output=True, text=True)
    assert out.stdout.strip() == "herdr-eng 1.0.0"


def test_second_apply_changes_nothing(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    events = converge(tmp_path, source, Mode.APPLY, monkeypatch)
    assert changed(events) == []


def test_check_mode_on_a_fresh_home_reports_the_stage_and_runs_no_git_or_pip(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = converge(tmp_path, source, Mode.CHECK, monkeypatch)
    assert f"stage herdr-engineering {source.revision[:12]} (pending)" in changed(events)
    assert not (tmp_path / ".local/share/herdr-engineering").exists()
    assert not (tmp_path / ".venvs").exists()
    assert not shim(tmp_path).exists()


def test_check_mode_after_apply_has_nothing_pending(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    assert changed(converge(tmp_path, source, Mode.CHECK, monkeypatch)) == []


def test_a_new_pin_stages_a_second_release_and_keeps_the_first(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    newer = source.commit({"NEWS": "second\n"})
    pending = converge(tmp_path, newer, Mode.CHECK, monkeypatch)
    assert f"stage herdr-engineering {newer.revision[:12]} (pending)" in changed(pending)
    converge(tmp_path, newer, Mode.APPLY, monkeypatch)
    assert (releases(tmp_path) / source.revision).is_dir()
    assert (releases(tmp_path) / newer.revision).is_dir()
    fp = verify(tmp_path, newer, monkeypatch)
    assert compare_herdr_engineering(fp, pins_for(newer)) == []
    old = compare_herdr_engineering(fp, pins_for(source))
    assert [d.field for d in old] == ["herdr-engineering revision"]


def test_a_modified_release_is_reported_as_a_missing_revision(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    (releases(tmp_path) / source.revision / "pyproject.toml").write_text("# edited\n")
    fp = verify(tmp_path, source, monkeypatch)
    section = HerdrEngineeringFingerprint.from_json(fp.sections["herdr_engineering"])
    assert section.revision is None
    messages = [d.message for d in compare_herdr_engineering(fp, pins_for(source))]
    assert f"herdr-engineering revision: expected {source.revision}, found missing" in messages


def test_a_removed_shim_is_named_and_apply_restores_it(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    shim(tmp_path).unlink()
    messages = [
        d.message
        for d in compare_herdr_engineering(verify(tmp_path, source, monkeypatch), pins_for(source))
    ]
    assert f"path herdr-eng: expected {shim(tmp_path)}, found not found" in messages
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    assert compare_herdr_engineering(verify(tmp_path, source, monkeypatch), pins_for(source)) == []


def test_a_removed_venv_entry_is_named(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    (tmp_path / ".venvs/herdr-engineering/bin/herdr-eng").unlink()
    messages = [
        d.message
        for d in compare_herdr_engineering(verify(tmp_path, source, monkeypatch), pins_for(source))
    ]
    assert "herdr-eng: expected the venv entry point, found missing" in messages


def test_an_interpreter_older_than_the_minimum_fails_naming_the_remedy(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = run_playbook(LOCAL, variables(source, herdr_engineering_min_python="99.0"), Mode.APPLY)
    assert result.returncode != 0
    assert "install python 99.0 or newer, or set `herdr_engineering_python`" in result.stdout
    assert not (tmp_path / ".local/share/herdr-engineering").exists()


def test_a_missing_pin_variable_fails_instead_of_guessing(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    extra = variables(source)
    del extra["herdr_engineering_repository"]
    result = run_playbook(LOCAL, extra, Mode.APPLY)
    assert result.returncode != 0
    assert "do not run this role by hand" in result.stdout


def test_no_role_uses_privilege_escalation_except_baseline() -> None:
    roles = Path(__file__).resolve().parent.parent / "herdr_fleet/ansible/roles"
    offenders = [
        str(p.relative_to(roles))
        for p in roles.rglob("*.yml")
        if p.relative_to(roles).parts[0] != "baseline" and "become" in p.read_text()
    ]
    assert offenders == []


# --- part B: fabric.env, agent unit, build gate ----------------------------------------------


def env_file(home: Path) -> Path:
    return home / ".config/herdr-engineering/fabric.env"


def unit_file(home: Path) -> Path:
    return home / ".config/systemd/user/herdr-eng-fabric-agent.service"


def gate_file(home: Path) -> Path:
    return home / ".pi/agent/extensions/build-gate.ts"


def section_of(fp: Fingerprint) -> HerdrEngineeringFingerprint:
    return HerdrEngineeringFingerprint.from_json(fp.sections["herdr_engineering"])


def messages(fp: Fingerprint, source: Source, **over: object) -> list[str]:
    pins = pins_for(source)
    pins = HerdrEngineeringPins(**{**pins.__dict__, **over})
    return [d.message for d in compare_herdr_engineering(fp, pins)]


def test_fabric_env_is_written_0600_and_other_lines_survive(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file(tmp_path).parent.mkdir(parents=True)
    env_file(tmp_path).write_text(
        "OTHER=kept\nHERDR_ENGINEERING_FABRIC_URL=https://old.example.com\n"
    )
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    text = env_file(tmp_path).read_text()
    assert f"HERDR_ENGINEERING_FABRIC_URL={GATEWAY}\n" in text
    assert "OTHER=kept\n" in text and "old.example.com" not in text
    assert stat.S_IMODE(env_file(tmp_path).stat().st_mode) == 0o600
    assert "HERDR_ENGINEERING_CONTROL_PLANE_URL" not in text  # never invented


def test_control_plane_line_is_written_only_when_configured(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(
        tmp_path,
        source,
        Mode.APPLY,
        monkeypatch,
        herdr_engineering_control_plane_url="https://control.example.com",
    )
    assert "HERDR_ENGINEERING_CONTROL_PLANE_URL=https://control.example.com\n" in (
        env_file(tmp_path).read_text()
    )


def test_unit_is_the_release_template_with_the_placeholder_replaced(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    entry = tmp_path / ".venvs/herdr-engineering/bin/herdr-eng"
    assert unit_file(tmp_path).read_text() == UNIT.replace("__HERDR_ENG__", str(entry))
    assert "__HERDR_ENG__" not in unit_file(tmp_path).read_text()


def test_build_gate_is_copied_from_the_release_0644(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    assert gate_file(tmp_path).read_text() == BUILD_GATE
    assert stat.S_IMODE(gate_file(tmp_path).stat().st_mode) == 0o644


def test_without_a_user_systemd_the_service_is_unmanaged_and_verify_is_clean(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    fp = verify(tmp_path, source, monkeypatch)
    found = section_of(fp)
    assert found.fabric_service == "unmanaged"
    assert found.fabric_env_url == GATEWAY and found.fabric_unit_matches_release
    assert found.build_gate_sha256 == hashlib.sha256(BUILD_GATE.encode()).hexdigest()
    assert messages(fp, source) == []


def test_second_apply_still_changes_nothing(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    assert changed(converge(tmp_path, source, Mode.APPLY, monkeypatch)) == []


def test_check_mode_after_apply_has_nothing_pending_for_the_new_files(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    assert changed(converge(tmp_path, source, Mode.CHECK, monkeypatch)) == []


def test_check_mode_on_a_fresh_home_writes_none_of_the_files(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.CHECK, monkeypatch)
    assert not unit_file(tmp_path).exists() and not gate_file(tmp_path).exists()
    assert not env_file(tmp_path).exists()


def test_a_wrong_fabric_url_is_named_and_apply_repairs_it(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    env_file(tmp_path).write_text("HERDR_ENGINEERING_FABRIC_URL=https://elsewhere.example.com\n")
    found = messages(verify(tmp_path, source, monkeypatch), source)
    assert (
        f"fabric.env: expected HERDR_ENGINEERING_FABRIC_URL={GATEWAY}, found "
        "HERDR_ENGINEERING_FABRIC_URL=https://elsewhere.example.com" in found
    )
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    assert messages(verify(tmp_path, source, monkeypatch), source) == []


def test_a_missing_fabric_env_is_named(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    env_file(tmp_path).unlink()
    found = messages(verify(tmp_path, source, monkeypatch), source)
    assert f"fabric.env: expected HERDR_ENGINEERING_FABRIC_URL={GATEWAY}, found missing" in found


def test_a_loosened_fabric_env_mode_is_named(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    env_file(tmp_path).chmod(0o644)
    found = messages(verify(tmp_path, source, monkeypatch), source)
    assert "fabric.env mode: expected 0600, found 0644" in found
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    assert stat.S_IMODE(env_file(tmp_path).stat().st_mode) == 0o600


def test_edited_and_deleted_units_are_named_differently(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    short = source.revision[:12]
    unit_file(tmp_path).write_text("[Service]\nExecStart=/bin/true\n")
    edited = messages(verify(tmp_path, source, monkeypatch), source)
    assert f"fabric agent unit: expected rendered from release {short}, found differs" in edited
    unit_file(tmp_path).unlink()
    gone = messages(verify(tmp_path, source, monkeypatch), source)
    assert f"fabric agent unit: expected rendered from release {short}, found missing" in gone
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    assert messages(verify(tmp_path, source, monkeypatch), source) == []


def test_a_modified_build_gate_is_named_by_sha256(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    gate_file(tmp_path).write_text("tampered\n")
    digest = hashlib.sha256(b"tampered\n").hexdigest()
    found = messages(verify(tmp_path, source, monkeypatch), source)
    assert f"build-gate: expected sha256 of release {source.revision[:12]}, found {digest}" in found
    gate_file(tmp_path).unlink()
    gone = messages(verify(tmp_path, source, monkeypatch), source)
    assert f"build-gate: expected sha256 of release {source.revision[:12]}, found missing" in gone
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    assert gate_file(tmp_path).read_text() == BUILD_GATE


def test_a_new_pin_rewrites_the_build_gate_and_unit_from_the_new_release(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    newer = source.commit({"deploy/pi-build-gate/build-gate.ts": "// gate v2\n"})
    converge(tmp_path, newer, Mode.APPLY, monkeypatch)
    assert gate_file(tmp_path).read_text() == "// gate v2\n"
    assert messages(verify(tmp_path, newer, monkeypatch), newer) == []


def test_the_rendered_unit_is_accepted_by_systemd_analyze(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    converge(tmp_path, source, Mode.APPLY, monkeypatch)
    assert unit_file(tmp_path).is_file()
    # Parsing only: systemd-analyze reads the file, it starts and enables nothing.
    done = subprocess.run(
        ["systemd-analyze", "--user", "verify", str(unit_file(tmp_path))],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert "Unknown key" not in done.stderr and "Unknown section" not in done.stderr
    assert "Failed to parse" not in done.stderr


def test_a_missing_gateway_url_fails_naming_the_remedy(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = run_playbook(LOCAL, variables(source, herdr_engineering_gateway_url=None), Mode.APPLY)
    assert result.returncode != 0
    assert "services.gateway.url" in result.stdout


# --- operator strings are data, never templates ------------------------------------------------


def test_a_jinja_lookup_in_the_gateway_url_is_written_literally_and_never_runs(
    tmp_path: Path, source: Source, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "INJECTED"
    evil = "https://x.example.com/{{ lookup('pipe', 'touch " + str(marker) + "') }}"
    converge(tmp_path, source, Mode.APPLY, monkeypatch, herdr_engineering_gateway_url=evil)
    assert not marker.exists(), "an extra-var string was evaluated as a template"
    assert f"HERDR_ENGINEERING_FABRIC_URL={evil}\n" in env_file(tmp_path).read_text()
