"""Baseline role against real apt inside a real container (the only place apt ever runs).

The host is never touched. The container keeps ``--cap-drop ALL`` and ``no-new-privileges`` and
gets back only the capabilities apt and dpkg need to work as root (see ``APT_CAPS``). The image
has no sudo, so the unprivileged user cannot escalate, which is exactly the case to prove.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from tests.containers.conftest import BOOTSTRAP, HOME
from tests.containers.runtime import Completed, DockerRuntime

pytestmark = [pytest.mark.container, pytest.mark.network]

APT_CAPS = ("CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "SETUID", "SETGID")
LOCK = "/repo/tests/containers/fixtures/fleet.lock.yaml"
CONFIG = f"{HOME}/inputs/baseline-config.yaml"
CONFIG_TEXT = """\
version: 1
hosts:
  host-a:
    os: debian
    roles: [baseline]
    connection:
      address: localhost
      user: fleet
baseline:
  extra_packages: [jq]
"""
INSTALL_JQ = "package jq: expected installed, found missing"


@pytest.fixture(scope="module")
def box() -> Iterator[DockerRuntime]:
    reason = DockerRuntime.unavailable_reason()
    if reason is not None:
        pytest.skip(f"docker unavailable: {reason}")
    runtime = DockerRuntime(extra_caps=APT_CAPS)
    try:
        runtime.start()
        boot = runtime.sh(BOOTSTRAP, timeout=1200)
        assert boot.returncode == 0, f"bootstrap failed:\n{boot.stdout}\n{boot.stderr}"
        runtime.write_file(CONFIG, CONFIG_TEXT)
        yield runtime
    finally:
        print(f"[container-harness] removed: {', '.join(runtime.stop()) or 'nothing'}")


@pytest.fixture(scope="module")
def sudo_box() -> Iterator[DockerRuntime]:
    """A container whose image has sudo (granting nothing yet); the tests write the sudoers rule."""
    reason = DockerRuntime.unavailable_reason()
    if reason is not None:
        pytest.skip(f"docker unavailable: {reason}")
    runtime = DockerRuntime(extra_caps=APT_CAPS, with_sudo=True)
    try:
        runtime.start()
        boot = runtime.sh(BOOTSTRAP, timeout=1200)
        assert boot.returncode == 0, f"bootstrap failed:\n{boot.stdout}\n{boot.stderr}"
        runtime.write_file(CONFIG, CONFIG_TEXT)
        yield runtime
    finally:
        print(f"[container-harness] removed: {', '.join(runtime.stop()) or 'nothing'}")


def cli(command: str, *extra: str) -> list[str]:
    return [
        "herdr-fleet", command, "--lock", LOCK, "--config", CONFIG,
        "--connection", "local", "--json", *extra,
    ]  # fmt: skip


def check(box: DockerRuntime, *, root: bool = False) -> Completed:
    run = box.exec_as_root if root else box.exec
    return run(cli("plan", "--check"))


def apply(box: DockerRuntime, *, root: bool = False) -> Completed:
    run = box.exec_as_root if root else box.exec
    return run(cli("apply", "--yes", "--limit", "host-a"))


def baseline_messages(result: Completed) -> list[str]:
    (host,) = json.loads(result.stdout)["hosts"]
    role = next(r for r in host["roles"] if r["role"] == "baseline")
    return [d["message"] for d in role["differences"]]


def has_jq(box: DockerRuntime) -> bool:
    return box.sh("dpkg-query -W jq").returncode == 0


def test_baseline_installs_through_real_apt_only_as_root(box: DockerRuntime) -> None:
    assert box.sh("command -v sudo").returncode != 0  # the user really cannot escalate
    assert not has_jq(box)

    pending = check(box)
    assert pending.returncode == 2, pending.stdout + pending.stderr
    assert INSTALL_JQ in baseline_messages(pending)
    assert "install packages jq (pending)" in pending.stdout
    assert not has_jq(box)  # check mode installed nothing

    refused = apply(box)
    assert refused.returncode != 0
    assert "sudo apt-get install -y jq" in refused.stdout + refused.stderr
    assert not has_jq(box)  # nothing was escalated

    installed = apply(box, root=True)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert has_jq(box)
    assert box.sh("jq --version").returncode == 0

    again = apply(box, root=True)
    assert again.returncode == 0, again.stdout + again.stderr
    assert json.loads(again.stdout)["changes_made"] == 0
    assert check(box, root=True).returncode == 0

    # The unprivileged user now only owes its own directories; no root is involved any more.
    owed = check(box)
    assert INSTALL_JQ not in baseline_messages(owed)
    done = apply(box)
    assert done.returncode == 0, done.stdout + done.stderr
    assert check(box).returncode == 0


SUDOERS = "/etc/sudoers.d/fleet"
APT_UPDATE = "/usr/bin/apt-get update -q"
APT_INSTALL_JQ = "/usr/bin/apt-get install -y --no-install-recommends -- jq"


def grant(box: DockerRuntime, *commands: str) -> None:
    rule = f"fleet ALL=(root) NOPASSWD: {', '.join(commands)}\n" if commands else ""
    done = box.exec_as_root(
        [
            "bash",
            "-c",
            f"printf %s '{rule}' > {SUDOERS} && chmod 0440 {SUDOERS} && /usr/sbin/visudo -c -q",
        ]
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_restricted_sudoers_that_allows_exactly_the_commands_installs_without_become(
    sudo_box: DockerRuntime,
) -> None:
    box = sudo_box
    assert box.sh("command -v sudo").returncode == 0 and box.exec(["id", "-u"]).stdout == "1000\n"
    assert not has_jq(box)

    # No sudo rights at all: refused with the one command to run by hand, nothing installed.
    grant(box)
    denied = apply(box)
    assert denied.returncode != 0
    assert "sudo apt-get install -y jq" in denied.stdout + denied.stderr
    assert not has_jq(box)

    # Rights for the update but not for the install of jq: the install probe refuses before
    # anything privileged runs.
    grant(box, APT_UPDATE)
    partial = apply(box)
    assert partial.returncode != 0
    assert "sudo apt-get install -y jq" in partial.stdout + partial.stderr
    assert not has_jq(box)

    # `sudo -n true` is refused here (only these two commands are allowed), yet the probes and
    # the privileged tasks use the very same `sudo -n -- CMD` argv, so apply succeeds.
    grant(box, APT_UPDATE, APT_INSTALL_JQ)
    assert box.sh("sudo -n true").returncode != 0
    installed = apply(box)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert has_jq(box)
    again = apply(box)
    assert again.returncode == 0, again.stdout + again.stderr
    assert json.loads(again.stdout)["changes_made"] == 0


def test_a_run_with_unrestricted_passwordless_sudo_writes_nothing_outside_ansible_scratch(
    sudo_box: DockerRuntime,
) -> None:
    """The CI runner case: sudo works without a password. Check and refused apply stay read-only."""
    box = sudo_box
    grant(box, "ALL")
    assert box.sh("sudo -n true").returncode == 0
    fresh = f"{HOME}/emptyhome"
    box.sh(f"rm -rf {fresh} && mkdir -p {fresh}")

    def listing() -> str:
        found = box.sh(f"cd {fresh} && find . -mindepth 1 -not -path './.ansible*' | sort")
        return found.stdout

    run = box.exec(["env", f"HOME={fresh}", *cli("plan", "--check")])
    assert run.returncode == 2, run.stdout + run.stderr
    assert listing() == "", listing()
