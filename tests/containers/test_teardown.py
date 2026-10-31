"""The harness removes its own container even when `docker run` failed after creating it."""

from __future__ import annotations

import subprocess

import pytest

from tests.containers.runtime import DockerRuntime

pytestmark = [pytest.mark.container]


def test_docker_stop_removes_a_container_left_by_a_failed_run() -> None:
    reason = DockerRuntime.unavailable_reason()
    if reason:
        pytest.skip(reason)
    runtime = DockerRuntime()
    base = ["docker", "create", "--name", runtime.container, "busybox:latest", "true"]
    # `create` leaves a container behind exactly like a run whose start step failed
    made = subprocess.run(base, capture_output=True, text=True, check=False)
    if made.returncode != 0:
        pytest.skip(f"cannot create a probe container offline: {made.stderr.strip()[:120]}")
    removed = runtime.stop()
    assert f"container {runtime.container}" in removed
    gone = subprocess.run(
        ["docker", "container", "inspect", runtime.container], capture_output=True, check=False
    )
    assert gone.returncode != 0
