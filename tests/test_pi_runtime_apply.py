"""Real ``apply``, ``check`` and ``verify`` of the pi-runtime role in a throwaway HOME.

The Pi release comes from a real git repository at a ``file://`` URL. ``npm`` is the host's real npm
and node, linked into the throwaway HOME's managed toolchain ``bin`` directory (as the toolchain
role would link them) and run by a real ``npm ci`` on a tiny offline package (tests/real_npm.py).
Pi sessions are stood in for by a real long-running process and real session/mission files,
which must come through untouched.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from herdr_fleet.fingerprint import Fingerprint
from herdr_fleet.pi_runtime import PiRuntimeFingerprint, PiRuntimePins, compare_pi_runtime
from herdr_fleet.report import ParsedEvents, parse_events
from herdr_fleet.runner import Mode, run_playbook
from tests.eng_repo import Source, make_source
from tests.helpers import example_extra_vars
from tests.real_npm import PI_FILES, isolate_npm, link_real_npm

LOCAL = {
    "all": {
        "hosts": {
            "h": {
                "ansible_connection": "local",
                "ansible_python_interpreter": "auto_silent",
                "fleet_roles": ["pi-runtime"],
            }
        }
    }
}


@pytest.fixture
def source(tmp_path_factory: pytest.TempPathFactory) -> Source:
    return make_source(tmp_path_factory.mktemp("src") / "pi-engineering", PI_FILES)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway HOME whose managed toolchain has the host's real npm and node linked in."""
    directory = tmp_path / "home"
    isolate_npm(monkeypatch, directory, tmp_path / "npm.log")
    link_real_npm(directory / ".local/share/herdr-fleet/toolchain/bin")
    return directory


def npm_calls(home: Path) -> list[list[str]]:
    log = home.parent / "npm.log"
    return [line.split("|") for line in log.read_text().splitlines()] if log.exists() else []


def pins_for(source: Source) -> PiRuntimePins:
    return PiRuntimePins(revision=source.revision, repository=source.url)


def variables(source: Source, **over: object) -> dict[str, object]:
    extra = example_extra_vars()
    extra.update(
        pi_runtime_revision=source.revision,
        pi_runtime_repository=source.url,
        pi_runtime_python=sys.executable,
    )
    extra.update(over)
    return extra


def converge(
    home: Path, source: Source, mode: Mode, *, check_ok: bool = True, **over: object
) -> ParsedEvents:
    result = run_playbook(LOCAL, variables(source, **over), mode)
    if check_ok:
        assert result.returncode == 0, result.stdout[-3000:] + result.stderr
    return parse_events(result.stdout)


def changed(events: ParsedEvents) -> list[str]:
    return [t.task for t in events.tasks if t.status == "changed"]


def verify(home: Path, source: Source) -> Fingerprint:
    events = converge(home, source, Mode.VERIFY)
    assert not events.noise, events.noise
    return events.fingerprints["h"]


def messages(home: Path, source: Source) -> list[str]:
    return [d.message for d in compare_pi_runtime(verify(home, source), pins_for(source))]


def releases(home: Path) -> Path:
    return home / ".local/share/herdr-engineering/pi/releases"


def settings(home: Path) -> Path:
    return home / ".pi/agent/settings.json"


def test_apply_stages_registers_and_verify_is_clean(home: Path, source: Source) -> None:
    converge(home, source, Mode.APPLY)
    release = releases(home) / source.revision
    assert (release / "package.json").is_file() and (release / "node_modules/dep").is_dir()
    root = home / ".local/share/herdr-engineering/pi"
    installed = json.loads((root / "installed.json").read_text())
    assert installed == {"revision": source.revision, "sourceRoot": str(release)}
    assert (root / "receipts" / f"{source.revision}.json").is_file()
    assert json.loads(settings(home).read_text())["packages"] == [str(release)]
    fp = verify(home, source)
    assert compare_pi_runtime(fp, pins_for(source)) == []
    found = PiRuntimeFingerprint.from_json(fp.sections["pi_runtime"])
    assert found.revision == source.revision and found.registered and found.node_modules_present
    assert found.release_path == str(release) and found.source_root == str(release)


def test_npm_ci_runs_with_the_managed_toolchain_first_on_path(home: Path, source: Source) -> None:
    converge(home, source, Mode.APPLY)
    toolchain_bin = str(home / ".local/share/herdr-fleet/toolchain/bin")
    ((path, cwd),) = npm_calls(home)  # written by the package's real `prepare` script
    entries = path.split(":")
    host_npm_dir = str(Path(shutil.which("npm") or "").parent)
    assert toolchain_bin in entries
    assert host_npm_dir not in entries or entries.index(toolchain_bin) < entries.index(host_npm_dir)
    assert cwd == str(releases(home) / source.revision)
    assert (releases(home) / source.revision / "node_modules/dep/package.json").is_file()


def test_second_apply_changes_nothing_and_runs_no_npm(home: Path, source: Source) -> None:
    converge(home, source, Mode.APPLY)
    assert changed(converge(home, source, Mode.APPLY)) == []
    assert len(npm_calls(home)) == 1


def test_check_mode_on_a_fresh_home_reports_the_stage_and_runs_nothing(
    home: Path, source: Source
) -> None:
    events = converge(home, source, Mode.CHECK)
    assert f"stage pi-runtime {source.revision[:12]} (pending)" in changed(events)
    assert not (home / ".local/share/herdr-engineering").exists()
    assert not settings(home).exists()
    assert npm_calls(home) == []


def test_check_mode_after_apply_has_nothing_pending(home: Path, source: Source) -> None:
    converge(home, source, Mode.APPLY)
    assert changed(converge(home, source, Mode.CHECK)) == []


def test_unrelated_pi_settings_and_packages_survive(home: Path, source: Source) -> None:
    settings(home).parent.mkdir(parents=True)
    settings(home).write_text(
        json.dumps({"theme": "dark", "packages": ["npm:other", {"source": "npm:more"}]})
    )
    converge(home, source, Mode.APPLY)
    merged = json.loads(settings(home).read_text())
    release = str(releases(home) / source.revision)
    assert merged == {"theme": "dark", "packages": ["npm:other", {"source": "npm:more"}, release]}


def test_a_new_pin_stages_a_second_release_and_keeps_the_first(home: Path, source: Source) -> None:
    converge(home, source, Mode.APPLY)
    old_release = releases(home) / source.revision
    marker = old_release / "node_modules/keep"
    marker.write_text("mine")
    newer = source.commit({"NEWS": "second\n"})
    pending = converge(home, newer, Mode.CHECK)
    assert f"stage pi-runtime {newer.revision[:12]} (pending)" in changed(pending)
    converge(home, newer, Mode.APPLY)
    assert marker.read_text() == "mine"
    assert (releases(home) / newer.revision / "NEWS").is_file()
    assert sorted(p.name for p in releases(home).iterdir()) == sorted(
        [source.revision, newer.revision]
    )
    assert json.loads(settings(home).read_text())["packages"] == [
        str(releases(home) / newer.revision)
    ]
    assert messages(home, newer) == []
    assert messages(home, source)[0] == (
        f"pi-runtime revision: expected {source.revision}, found {newer.revision}"
    )


def test_a_removed_registration_is_named_and_apply_restores_it(home: Path, source: Source) -> None:
    converge(home, source, Mode.APPLY)
    settings(home).write_text(json.dumps({"packages": []}))
    release = releases(home) / source.revision
    found = messages(home, source)
    assert (
        f"pi-runtime registration: expected {settings(home)} to list {release}, "
        "found not registered" in found
    )
    converge(home, source, Mode.APPLY)
    assert messages(home, source) == []
    assert len(npm_calls(home)) == 1  # re-registering needs no second npm ci


def test_removed_dependencies_are_named_and_apply_restores_them(home: Path, source: Source) -> None:
    converge(home, source, Mode.APPLY)
    release = releases(home) / source.revision
    subprocess.run(["rm", "-r", str(release / "node_modules")], check=True)
    assert f"pi-runtime dependencies: expected {release}/node_modules, found missing" in messages(
        home, source
    )
    converge(home, source, Mode.APPLY)
    assert messages(home, source) == []


def test_a_modified_release_is_reported_as_a_missing_revision(home: Path, source: Source) -> None:
    converge(home, source, Mode.APPLY)
    (releases(home) / source.revision / "README").write_text("edited\n")
    assert f"pi-runtime revision: expected {source.revision}, found missing" in messages(
        home, source
    )


@pytest.fixture
def live_session(home: Path) -> Iterator[subprocess.Popen[bytes]]:
    """A real running process standing in for a Pi session, plus session and mission files."""
    sessions = home / ".pi/agent/sessions"
    sessions.mkdir(parents=True)
    (sessions / "s1.jsonl").write_text('{"turn": 1}\n')
    missions = home / ".local/share/herdr-engineering/missions"
    missions.mkdir(parents=True)
    (missions / "m1.json").write_text('{"state": "running"}\n')
    process = subprocess.Popen(["sleep", "300"])
    try:
        yield process
    finally:
        process.kill()
        process.wait()


def tree(home: Path) -> dict[str, bytes]:
    roots = (home / ".pi/agent/sessions", home / ".local/share/herdr-engineering/missions")
    return {str(p): p.read_bytes() for r in roots for p in sorted(r.rglob("*")) if p.is_file()}


def test_apply_never_touches_a_running_session_or_a_mission(
    home: Path, source: Source, live_session: subprocess.Popen[bytes]
) -> None:
    before = tree(home)
    converge(home, source, Mode.APPLY)
    newer = source.commit({"NEWS": "second\n"})
    converge(home, newer, Mode.APPLY)
    assert live_session.poll() is None  # still running, never signalled
    assert tree(home) == before


def test_the_role_contains_no_activation_or_deletion_task() -> None:
    """Parse the role's tasks (comments excluded): only the stage helper may do real work."""
    role = Path(__file__).resolve().parent.parent / "herdr_fleet/ansible/roles/pi-runtime"
    modules: set[str] = set()
    commands: list[str] = []
    for path in role.rglob("*.yml"):
        if path.parent.name == "defaults":
            continue
        for task in yaml.safe_load(path.read_text()):
            modules.update(k for k in task if k.startswith("ansible.builtin."))
            body = task.get("ansible.builtin.command")
            if body:
                commands.append(" ".join(str(a) for a in body["argv"]))
            if task.get("ansible.builtin.file", {}).get("state") == "absent":
                raise AssertionError(f"{path}: deletes something")
    assert (
        not {"ansible.builtin.service", "ansible.builtin.systemd", "ansible.builtin.shell"}
        & modules
    )
    forbidden = ("rollout", "herdr-eng", "kill", "reload", "restart", "rm ")
    for command in commands:
        assert not any(word in command for word in forbidden), command
    # exactly one command does real work: the stage helper (the other two are probes)
    staging = [c for c in commands if "--revision" in c]
    assert len(staging) == 1 and "--kind pi" in staging[0]


def test_apply_without_the_toolchain_npm_fails_naming_the_remedy(
    home: Path, source: Source
) -> None:
    (home / ".local/share/herdr-fleet/toolchain/bin/npm").unlink()
    result = run_playbook(LOCAL, variables(source), Mode.APPLY)
    assert result.returncode != 0
    assert "toolchain role" in result.stdout
    assert not (home / ".local/share/herdr-engineering").exists()
    assert not settings(home).exists()


def test_check_mode_does_not_need_the_toolchain_yet(home: Path, source: Source) -> None:
    (home / ".local/share/herdr-fleet/toolchain/bin/npm").unlink()
    events = converge(home, source, Mode.CHECK)
    assert f"stage pi-runtime {source.revision[:12]} (pending)" in changed(events)


def test_a_missing_pin_variable_fails_instead_of_guessing(home: Path, source: Source) -> None:
    extra = variables(source)
    del extra["pi_runtime_repository"]
    result = run_playbook(LOCAL, extra, Mode.APPLY)
    assert result.returncode != 0
    assert "do not run this role by hand" in result.stdout


def test_a_short_revision_is_refused(home: Path, source: Source) -> None:
    result = run_playbook(
        LOCAL, variables(source, pi_runtime_revision=source.revision[:12]), Mode.APPLY
    )
    assert result.returncode != 0
    assert "repos.pi-engineering" in result.stdout
