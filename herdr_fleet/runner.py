"""Run the packaged converge playbook with the ansible that ships in this environment."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from pathlib import Path

import yaml

from herdr_fleet.inventory import JsonObject


class Mode(StrEnum):
    CHECK = "check"  # read-only: --check --diff, fingerprint included
    APPLY = "apply"  # converge for real, fingerprint left to the verify run
    VERIFY = "verify"  # fingerprint only


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str


def ansible_dir() -> Path:
    return Path(str(resources.files("herdr_fleet").joinpath("ansible")))


def ansible_playbook_executable() -> Path:
    """The ansible-playbook installed next to this interpreter, never one from the system."""
    return Path(sys.executable).parent / "ansible-playbook"


def _mode_args(mode: Mode) -> list[str]:
    return {
        Mode.CHECK: ["--check", "--diff"],
        Mode.APPLY: ["--skip-tags", "fingerprint"],
        Mode.VERIFY: ["--tags", "fingerprint"],
    }[mode]


def _environment(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        ANSIBLE_CONFIG=str(root / "ansible.cfg"),
        ANSIBLE_ROLES_PATH=str(root / "roles"),
        ANSIBLE_CALLBACK_PLUGINS=str(root / "callback_plugins"),
        ANSIBLE_STDOUT_CALLBACK="fleet_json",
        ANSIBLE_LOCALHOST_WARNING="False",
        ANSIBLE_NOCOLOR="True",
    )
    return env


EXIT_COMMAND_NOT_RUNNABLE = 127


def run_playbook(
    inventory: JsonObject,
    extra_vars: JsonObject,
    mode: Mode,
    *,
    executable: Path | None = None,
) -> RunResult:
    root = ansible_dir()
    with tempfile.TemporaryDirectory(prefix="herdr-fleet-") as tmp:
        inventory_file = Path(tmp) / "inventory.yml"
        vars_file = Path(tmp) / "extra-vars.json"
        inventory_file.write_text(yaml.safe_dump(inventory))
        vars_file.write_text(json.dumps(extra_vars))
        command = [
            str(executable or ansible_playbook_executable()),
            "-i",
            str(inventory_file),
            "-e",
            f"@{vars_file}",
            *_mode_args(mode),
            str(root / "playbooks" / "converge.yml"),
        ]
        try:
            done = subprocess.run(
                command, capture_output=True, text=True, check=False, env=_environment(root)
            )
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            return RunResult(
                EXIT_COMMAND_NOT_RUNNABLE, "", f"cannot run {command[0]}: {reason}"
            )
    return RunResult(done.returncode, done.stdout, done.stderr)
