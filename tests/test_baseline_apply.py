"""Real ``apply``, ``check`` and ``verify`` of the baseline role in a throwaway HOME.

Everything unprivileged runs here with real ansible. The privileged path (apt-get, linger) is
never exercised on this host: these tests only ever use packages that are already installed, or
a package that cannot exist, and point the privilege probe at a path that has no sudo. The apt
path itself is proven inside a real container (tests/containers/test_baseline_scenario.py).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from herdr_fleet.baseline import BaselineFingerprint, baseline_pins, compare_baseline
from herdr_fleet.config import load_config
from herdr_fleet.fingerprint import Fingerprint
from herdr_fleet.report import ParsedEvents, parse_events
from herdr_fleet.runner import Mode, ansible_dir, run_playbook
from tests.helpers import CONFIG_EXAMPLE, example_extra_vars

INSTALLED = ["dpkg", "bash"]  # Debian essentials: present wherever these tests can run
ABSENT = "herdr-fleet-test-absent-pkg"
NO_SUDO = "/nonexistent/sudo"
ROLE = ansible_dir() / "roles" / "baseline"

LOCAL = {
    "all": {
        "hosts": {
            "h": {
                "ansible_connection": "local",
                "ansible_python_interpreter": "auto_silent",
                "fleet_roles": ["baseline"],
            }
        }
    }
}


@pytest.fixture(autouse=True)
def never_root() -> None:
    assert os.geteuid() != 0, "the baseline tests must never run as root on the host"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "home"
    directory.mkdir()
    monkeypatch.setenv("HOME", str(directory))
    return directory


def variables(**over: object) -> dict[str, object]:
    extra = example_extra_vars()
    extra.update(baseline_packages=INSTALLED, baseline_sudo=NO_SUDO, baseline_tailscale=NO_SUDO)
    extra.update(over)
    return extra


def converge(mode: Mode, *, ok: bool = True, **over: object) -> ParsedEvents:
    result = run_playbook(LOCAL, variables(**over), mode)
    if ok:
        assert result.returncode == 0, result.stdout[-3000:] + result.stderr
    return parse_events(result.stdout)


def changed(events: ParsedEvents) -> list[str]:
    return [t.task for t in events.tasks if t.status == "changed"]


def verify(**over: object) -> Fingerprint:
    events = converge(Mode.VERIFY, **over)
    assert not events.noise, events.noise
    return events.fingerprints["h"]


def section(**over: object) -> BaselineFingerprint:
    return BaselineFingerprint.from_json(verify(**over).sections["baseline"])


def pins_with(packages: list[str]):  # type: ignore[no-untyped-def]
    from dataclasses import replace

    return replace(baseline_pins(load_config(CONFIG_EXAMPLE)), packages=tuple(packages))


def messages(**over: object) -> list[str]:
    packages = over.get("baseline_packages", INSTALLED)
    return [d.message for d in compare_baseline(verify(**over), pins_with(packages))]  # type: ignore[arg-type]


def untouched(home: Path) -> bool:
    """Nothing but ansible's own scratch directory exists in the throwaway HOME."""
    return [p.name for p in home.iterdir() if p.name != ".ansible"] == []


def mode_of(path: Path) -> str:
    return format(path.stat().st_mode & 0o7777, "04o")


def failure(**over: object) -> str:
    result = run_playbook(LOCAL, variables(**over), Mode.APPLY)
    assert result.returncode != 0
    return result.stdout + result.stderr


def test_apply_creates_the_directories_with_their_modes(home: Path) -> None:
    converge(Mode.APPLY)
    assert mode_of(home / ".config/herdr-engineering") == "0700"
    assert mode_of(home / ".local/share/herdr-engineering") == "0700"
    assert mode_of(home / ".local/share/herdr-engineering/state") == "0750"
    assert mode_of(home / ".local/share/herdr-engineering/artifacts") == "0750"
    assert (home / ".local/bin").is_dir()
    assert messages() == []


def test_second_apply_changes_nothing(home: Path) -> None:
    converge(Mode.APPLY)
    assert changed(converge(Mode.APPLY)) == []
    assert changed(converge(Mode.CHECK)) == []


def test_check_on_a_fresh_home_reports_directories_and_writes_nothing(home: Path) -> None:
    events = converge(Mode.CHECK)
    assert changed(events) != []
    assert untouched(home)
    assert messages() == [
        "dir ~/.config/herdr-engineering: expected mode 0700, found missing",
        "dir ~/.local/share/herdr-engineering: expected mode 0700, found missing",
        "dir ~/.local/share/herdr-engineering/state: expected mode 0750, found missing",
        "dir ~/.local/share/herdr-engineering/artifacts: expected mode 0750, found missing",
        "dir ~/.local/bin: expected present, found missing",
    ]


def test_wrong_modes_and_removed_directories_are_named_and_repaired(home: Path) -> None:
    converge(Mode.APPLY)
    (home / ".config/herdr-engineering").chmod(0o755)
    (home / ".local/bin").rmdir()
    assert messages() == [
        "dir ~/.config/herdr-engineering: expected mode 0700, found 0755",
        "dir ~/.local/bin: expected present, found missing",
    ]
    converge(Mode.APPLY)
    assert messages() == []
    assert mode_of(home / ".config/herdr-engineering") == "0700"


def test_the_binary_directory_keeps_the_mode_it_has(home: Path) -> None:
    (home / ".local/bin").mkdir(parents=True)
    (home / ".local/bin").chmod(0o711)
    converge(Mode.APPLY)
    assert mode_of(home / ".local/bin") == "0711"


def test_installed_packages_are_reported_installed(home: Path) -> None:
    found = section()
    assert found.packages == {"dpkg": "installed", "bash": "installed"}
    assert found.user == os.environ.get("USER", found.user)


def test_a_missing_package_is_pending_in_check_mode_and_nothing_is_written(home: Path) -> None:
    events = converge(Mode.CHECK, baseline_packages=[*INSTALLED, ABSENT])
    assert f"install packages {ABSENT} (pending)" in changed(events)
    assert not any(t.status == "failed" for t in events.tasks)
    assert untouched(home)
    assert (
        messages(baseline_packages=[*INSTALLED, ABSENT]).count(
            f"package {ABSENT}: expected installed, found missing"
        )
        == 1
    )


def test_apply_without_root_names_the_exact_command_and_changes_nothing(home: Path) -> None:
    other = "herdr-fleet-test-absent-two"
    text = failure(baseline_packages=[*INSTALLED, ABSENT, other])
    assert f"sudo apt-get install -y {ABSENT} {other}" in text
    assert untouched(home)


def test_no_privilege_probe_when_nothing_privileged_is_pending(home: Path) -> None:
    # The sudo path does not exist; applying with everything installed must not need it.
    converge(Mode.APPLY)
    assert messages() == []


@pytest.mark.parametrize(
    ("script", "expected"),
    [("true", "up"), ("false", "down")],
)
def test_tailscale_up_and_down_are_reported_and_never_drift(
    home: Path, tmp_path: Path, script: str, expected: str
) -> None:
    tool = tmp_path / "tailscale"
    tool.symlink_to(shutil.which(script) or f"/usr/bin/{script}")  # the real true / false
    assert section(baseline_tailscale=str(tool)).tailscale == expected
    assert [m for m in messages(baseline_tailscale=str(tool)) if "tailscale" in m.lower()] == []


def test_tailscale_absent_is_reported_and_never_drift(home: Path) -> None:
    assert section().tailscale == "absent"
    assert all("tailscale" not in m.lower() for m in messages())


def test_the_python_version_is_reported_from_the_hosts_python3(home: Path) -> None:
    out = subprocess.run(["python3", "--version"], capture_output=True, text=True, check=True)
    assert section().python3_version == out.stdout.strip().removeprefix("Python ")


def linger_on_this_host() -> str:
    user = (
        os.environ.get("USER")
        or subprocess.run(["id", "-un"], capture_output=True, text=True, check=True).stdout.strip()
    )
    try:
        done = subprocess.run(
            ["loginctl", "show-user", user, "--property=Linger", "--value"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return "unmanaged"
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else "unmanaged"


def test_linger_is_unmanaged_unless_herdr_engineering_is_on_the_host(home: Path) -> None:
    assert section().linger == "unmanaged"


def test_linger_matches_what_loginctl_reports_and_check_never_enables_it(home: Path) -> None:
    real = linger_on_this_host()
    events = converge(Mode.CHECK, baseline_manage_linger=True)
    pending = [t for t in changed(events) if t.startswith("enable linger")]
    found = section(baseline_manage_linger=True)
    assert found.linger == real
    assert (len(pending) == 1) == (real == "no")
    assert linger_on_this_host() == real  # check mode left it alone


def tasks_of(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(items: list[dict[str, Any]]) -> None:
        for item in items:
            out.append(item)
            for key in ("block", "rescue", "always"):
                walk(item.get(key, []))

    walk(yaml.safe_load(path.read_text()) or [])
    return out


def test_no_task_uses_become_and_only_three_commands_escalate_through_sudo() -> None:
    # `become` runs `sudo -H -S -n -u root /bin/sh -c ...`, which a restricted sudoers refuses even
    # when the probed command is allowed. The privileged commands carry an explicit
    # `sudo -n --` prefix instead, so the probe and the executed command are the same argv.
    escalated: dict[str, str] = {}
    for path in sorted((ROLE / "tasks").glob("*.yml")):
        for task in tasks_of(path):
            assert not any(key.startswith("become") for key in task), task["name"]
            command = task.get("ansible.builtin.command")
            if isinstance(command, dict) and "baseline_priv" in str(command.get("argv")):
                escalated[str(task["name"])] = str(command["argv"])
    assert len(escalated) == 3
    joined = " ".join(escalated.values())
    assert "apt-get" in joined and "update" in joined and "install" in joined
    assert "enable-linger" in joined
    assert sum("apt-get" in v for v in escalated.values()) == 2
    play = yaml.safe_load((ansible_dir() / "playbooks" / "converge.yml").read_text())
    assert "become" not in yaml.safe_dump(play)


def test_a_package_name_that_looks_like_an_option_is_a_package_not_an_option(home: Path) -> None:
    # Without "--", dpkg-query would take "-f=ii" as a format option and list every package
    # as installed; as a package name it does not exist.
    found = section(baseline_packages=["dpkg", "-f=ii"])
    assert found.packages["-f=ii"] == "missing"


def test_apt_and_dpkg_calls_end_option_parsing_before_the_package_names() -> None:
    argvs: dict[str, list[str]] = {}
    for name in ("main.yml", "probe.yml"):
        for task in tasks_of(ROLE / "tasks" / name):
            command = task.get("ansible.builtin.command")
            if isinstance(command, dict) and isinstance(command.get("argv"), (list, str)):
                argvs[str(task["name"])] = (
                    command["argv"] if isinstance(command["argv"], list) else [command["argv"]]
                )
    install = " ".join(map(str, argvs["install the missing packages"]))
    assert "'--'" in install and install.index("'--'") < install.index("+ baseline_apt_names")
    dpkg = argvs["installed apt packages"]
    assert dpkg[-2:] == ["--", "{{ item }}"]


def probe_argvs() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for task in tasks_of(ROLE / "tasks" / "main.yml"):
        command = task.get("ansible.builtin.command")
        if task.get("register", "").startswith("baseline_sudo_probe") and command:
            argv = command["argv"]
            if isinstance(argv, str):  # a Jinja list expression: read its quoted literals
                argv = re.findall(r"'([^']*)'", argv)
                argv.insert(0, "sudo")
            out[str(task["register"])] = argv
    return out


def test_the_privilege_probes_ask_sudo_about_the_commands_that_will_really_run() -> None:
    # `sudo -n true` fails under a sudoers that allows only apt-get and loginctl although the real
    # command would work; `sudo -n -l -- CMD` asks exactly about the command and never runs it.
    argvs = probe_argvs()
    assert set(argvs) == {
        "baseline_sudo_probe_apt_update",
        "baseline_sudo_probe_apt_install",
        "baseline_sudo_probe_linger",
    }
    for argv in argvs.values():
        assert argv[1:4] == ["-n", "-l", "--"] and "true" not in argv
    assert argvs["baseline_sudo_probe_apt_update"][4:6] == ["apt-get", "update"]
    assert argvs["baseline_sudo_probe_apt_install"][4:6] == ["apt-get", "install"]
    assert argvs["baseline_sudo_probe_linger"][4:6] == ["loginctl", "enable-linger"]
