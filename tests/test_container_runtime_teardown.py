"""Teardown of the container harness itself (no container needed)."""

from __future__ import annotations

import os
import stat

from tests.containers.runtime import ApptainerRuntime


def test_apptainer_stop_removes_read_only_trees_and_reports_truthfully() -> None:
    runtime = ApptainerRuntime()
    locked = runtime.workdir / "home" / "ro-dir"
    locked.mkdir(parents=True)
    (locked / "file").write_text("x")
    os.chmod(locked, stat.S_IRUSR | stat.S_IXUSR)  # a read-only dir resists rmtree
    removed = runtime.stop()
    assert not runtime.workdir.exists()
    assert any(str(runtime.workdir) in r for r in removed)
