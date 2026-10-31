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
