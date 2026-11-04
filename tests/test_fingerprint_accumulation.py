"""Roles add their section to one ``fleet_fingerprint`` fact; the last event holds them all.

Real ``ansible-playbook`` with the real callback and the real converge seed pattern: a first task
seeds system and machine, each role then ``combine``s its section in.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from herdr_fleet.report import parse_events
from herdr_fleet.runner import ansible_dir, ansible_playbook_executable

PLAYBOOK = """\
- hosts: all
  gather_facts: false
  tasks:
    - name: seed
      ansible.builtin.set_fact:
        fleet_fingerprint: {system: Linux, machine: x86_64}
    - name: role one
      ansible.builtin.set_fact:
        fleet_fingerprint: "{{ fleet_fingerprint | combine({'herdr': {'version': '0.9.1'}}) }}"
    - name: role two
      ansible.builtin.set_fact:
        fleet_fingerprint: >-
          {{ fleet_fingerprint | combine({'baseline': {'python3_version': '3.11.2'}}) }}
"""


def test_last_fingerprint_event_is_the_superset(tmp_path: Path) -> None:
    (tmp_path / "pb.yml").write_text(PLAYBOOK)
    (tmp_path / "inv.ini").write_text("h ansible_connection=local\n")
    env = dict(os.environ)
    env.update(
        ANSIBLE_CALLBACK_PLUGINS=str(ansible_dir() / "callback_plugins"),
        ANSIBLE_STDOUT_CALLBACK="fleet_json",
        ANSIBLE_LOCALHOST_WARNING="False",
    )
    done = subprocess.run(
        [
            str(ansible_playbook_executable()),
            "-i",
            str(tmp_path / "inv.ini"),
            str(tmp_path / "pb.yml"),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    fp = parse_events(done.stdout).fingerprints["h"]
    assert fp.sections == {"herdr": {"version": "0.9.1"}, "baseline": {"python3_version": "3.11.2"}}
    assert fp.clis == {}
