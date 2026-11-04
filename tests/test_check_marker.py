"""The pending-marker pattern, proven with real ``ansible-playbook`` on a throwaway playbook.

Roles that cannot simulate their work in check mode (it needs network or a subprocess) report the
pending step with a marker task: it runs only in check mode, only when the work is pending, and
Ansible reports it as ``changed``. In apply it never runs, so apply's change counts stay honest.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from herdr_fleet.report import ParsedEvents, TaskEvent, parse_events
from herdr_fleet.runner import ansible_dir, ansible_playbook_executable

PLAYBOOK = """\
- hosts: all
  gather_facts: false
  tasks:
    - name: probe the target
      ansible.builtin.stat:
        path: "{{ target }}"
      register: probe

    - name: stage the thing
      ansible.builtin.copy:
        content: staged
        dest: "{{ target }}"
        mode: "0644"
      when: not ansible_check_mode and not probe.stat.exists

    - name: stage the thing (pending)
      ansible.builtin.command:
        argv: [/bin/true]
      changed_when: true
      check_mode: false
      when: ansible_check_mode and not probe.stat.exists
"""


def run(tmp_path: Path, target: Path, *, check: bool) -> ParsedEvents:
    playbook = tmp_path / "pb.yml"
    playbook.write_text(PLAYBOOK)
    inventory = tmp_path / "inventory.ini"
    inventory.write_text("h ansible_connection=local ansible_python_interpreter=auto_silent\n")
    env = dict(os.environ)
    env.update(
        ANSIBLE_CALLBACK_PLUGINS=str(ansible_dir() / "callback_plugins"),
        ANSIBLE_STDOUT_CALLBACK="fleet_json",
        ANSIBLE_LOCALHOST_WARNING="False",
        ANSIBLE_NOCOLOR="True",
    )
    command = [
        str(ansible_playbook_executable()),
        "-i",
        str(inventory),
        "-e",
        f"target={target}",
        *(["--check"] if check else []),
        str(playbook),
    ]
    done = subprocess.run(command, capture_output=True, text=True, check=False, env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    return parse_events(done.stdout)


def statuses(tasks: tuple[TaskEvent, ...]) -> dict[str, str]:
    return {t.task: t.status for t in tasks}


def test_pending_work_is_reported_changed_in_check_mode_and_nothing_is_written(
    tmp_path: Path,
) -> None:
    target = tmp_path / "thing"
    events = run(tmp_path, target, check=True)
    assert statuses(events.tasks)["stage the thing (pending)"] == "changed"
    assert "stage the thing" not in statuses(events.tasks)  # the real work did not run
    assert not target.exists()


def test_marker_is_silent_in_check_mode_when_nothing_is_pending(tmp_path: Path) -> None:
    target = tmp_path / "thing"
    target.write_text("already there")
    events = run(tmp_path, target, check=True)
    assert "stage the thing (pending)" not in statuses(events.tasks)
    assert not [t for t in events.tasks if t.status in ("changed", "would_change")]


def test_marker_never_runs_in_apply_so_change_counts_stay_honest(tmp_path: Path) -> None:
    target = tmp_path / "thing"
    events = run(tmp_path, target, check=False)
    assert target.read_text() == "staged"
    changed = [t.task for t in events.tasks if t.status == "changed"]
    assert changed == ["stage the thing"]


def test_a_second_apply_changes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "thing"
    run(tmp_path, target, check=False)
    events = run(tmp_path, target, check=False)
    assert not [t for t in events.tasks if t.status == "changed"]


@pytest.mark.parametrize("pending_before", [True, False])
def test_check_mode_after_a_real_apply_is_clean(tmp_path: Path, pending_before: bool) -> None:
    target = tmp_path / "thing"
    if pending_before:
        assert any(t.status == "changed" for t in run(tmp_path, target, check=True).tasks)
    run(tmp_path, target, check=False)
    assert not [t for t in run(tmp_path, target, check=True).tasks if t.status == "changed"]
