"""The harness itself: no sudo, a non-root user, and the real host HOME is not exposed."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.containers.runtime import ContainerRuntime

pytestmark = [pytest.mark.container]


def test_runs_as_unprivileged_user_without_sudo(runtime: ContainerRuntime) -> None:
    done = runtime.sh("id -u; command -v sudo || echo no-sudo")
    uid, sudo = done.stdout.split()
    assert uid != "0"
    assert sudo == "no-sudo"


def test_repo_is_read_only(runtime: ContainerRuntime) -> None:
    done = runtime.sh("touch /repo/should-not-exist 2>&1; echo rc=$?")
    assert "rc=0" not in done.stdout
    assert not (Path(__file__).resolve().parents[2] / "should-not-exist").exists()


def test_real_host_home_is_not_visible(runtime: ContainerRuntime) -> None:
    host_home = str(Path.home())
    lines = runtime.sh("cat /proc/self/mountinfo").stdout.splitlines()
    targets = [
        line.split()[4] for line in lines if line.strip()
    ]  # mount points inside the container
    assert host_home not in targets, "the host HOME must never be mounted into a test container"
    assert not any(t.startswith(host_home + "/") for t in targets)
    listing = runtime.sh(f"ls -A {host_home} 2>&1; echo rc=$?").stdout
    assert "rc=0" not in listing
    assert runtime.sh("echo $HOME").stdout.strip() == "/home/fleet"


def test_user_config_dirs_do_not_exist_in_the_container_home(runtime: ContainerRuntime) -> None:
    for name in (".ssh", ".claude", ".aws", ".gnupg", ".npmrc", ".gitconfig"):
        done = runtime.sh(f"test -e /home/fleet/{name}; echo rc=$?")
        assert done.stdout.strip() == "rc=1", f"{name} must not exist in the test home"
